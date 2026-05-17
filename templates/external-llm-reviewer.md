---
name: external-llm-reviewer
description: >
  Паттерн: вызов SIDE-модели через HTTP API для cross-check ревью.
  Не привязан к конкретному провайдеру.
---

# Паттерн: SIDE-модель как внешний ревьюер

Этот файл описывает **паттерн** подключения SIDE-модели (внешнего LLM-провайдера, отличного от твоего PRIMARY) для независимого ревью кода. Используется в схеме триангуляции — см. `../principles/05-triangulated-review.md`. Терминология PRIMARY / SIDE-A / SIDE-B — см. `../principles/01-philosophy.md` (глоссарий).

Конкретные модели SIDE-A и SIDE-B настраиваются в `agents.config` проекта (пример — в `../agents.config.example` в корне репо).

---

## Идея

У твоего PRIMARY-агента есть слепые пятна. SIDE-модель с другой архитектурой находит другие классы проблем. Вызываешь её через HTTP API, получаешь структурированный отчёт, сравниваешь с PRIMARY-ревью.

---

## Что нужно для реализации

1. **API-ключ** SIDE-провайдера (OpenAI / Google AI / Anthropic / Mistral / xAI / OpenRouter — любой с chat completions API). Хранится в `.env`, не в `agents.config`.
2. **CLI-скрипт или функция**, которая:
   - Принимает diff как входные данные
   - Формирует промпт
   - Делает HTTP-запрос к API
   - Возвращает структурированный ответ

3. **Опционально:** агрегатор, который сводит отчёты нескольких ревьюеров.

---

## Структура промпта для ревью

```
Ты — ревьюер кода. Проверь следующий diff и верни структурированный отчёт.

Контекст проекта:
- Стек: <backend-framework>, <frontend-framework>
- Ключевые инварианты:
  <список инвариантов — то что важно для проекта>

Diff:
<содержимое diff>

Верни JSON:
{
  "verdict": "approve" | "changes_requested" | "blocked",
  "blockers": [{"file": "...", "line": N, "issue": "...", "recommendation": "..."}],
  "warnings": [{"file": "...", "issue": "..."}],
  "notes": [{"comment": "..."}]
}
```

---

## Пример CLI-обёртки (псевдокод)

```python
import httpx
import json
import sys

def review_diff(diff: str, api_key: str, model: str, base_url: str) -> dict:
    prompt = build_review_prompt(diff)
    
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    response.raise_for_status()
    
    content = response.json()["choices"][0]["message"]["content"]
    return json.loads(content)


if __name__ == "__main__":
    diff = sys.stdin.read()  # или из файла
    result = review_diff(diff, api_key=..., model=..., base_url=...)
    print(json.dumps(result, indent=2, ensure_ascii=False))
```

---

## Вызов из агента

```bash
# Получить diff
git diff main...HEAD > /tmp/review.diff

# Вызвать SIDE-A (имя скрипта/путь — на твой выбор; пример для Claude Code)
python3 <your-agent-dir>/scripts/llm/review.py \
  --diff /tmp/review.diff \
  --role side-a \
  --output /tmp/review-side-a.json

# Аналогично для SIDE-B
python3 <your-agent-dir>/scripts/llm/review.py \
  --diff /tmp/review.diff \
  --role side-b \
  --output /tmp/review-side-b.json

# Показать результат
cat /tmp/review-side-a.json | python3 -m json.tool
```

Конкретный провайдер и model id для каждой роли читаются из `agents.config` (секции `[side_a]` и `[side_b]`).

---

## Агрегация результатов (правило 2-из-3)

После получения трёх отчётов (PRIMARY + SIDE-A + SIDE-B):

```python
def aggregate_reviews(primary: dict, side_a: dict, side_b: dict) -> dict:
    all_blockers = primary["blockers"] + side_a["blockers"] + side_b["blockers"]

    # Найти blockers, о которых сообщили 2+ ревьюера
    confirmed_blockers = find_consensus(all_blockers, min_count=2)

    # Blockers только от одного — пометить как "требует проверки"
    unconfirmed = find_unique(all_blockers)

    return {
        "consensus_blockers": confirmed_blockers,    # обязательно фиксить
        "unconfirmed_blockers": unconfirmed,          # проверить вручную
        "warnings": merge_warnings(primary, side_a, side_b),
    }
```

---

## Что делать с результатами

| Ситуация | Действие |
|---|---|
| Blocker подтверждён 2-3 ревьюерами | Фиксить обязательно, это реальная проблема |
| Blocker только у одного | Изучить вручную, вероятен false positive |
| Только warnings | Взвесить, нужны ли правки до мержа |
| Провайдер недоступен | Продолжить с оставшимися, отметить в отчёте |

---

## Ограничения паттерна

- SIDE-модель не знает инвариантов твоего проекта (если не вписать в промпт). PRIMARY-ревьюер остаётся единственным с полным контекстом.
- Большие diff дорого отправлять — ограничь размер (например, первые 3000 строк).
- Разные провайдеры имеют разные rate limits и стоимость — учитывай в `[budget]` секции `agents.config`.
- JSON-ответ может быть невалидным — добавь парсинг с fallback.

---

## Хранение API-ключей

API-ключи SIDE-провайдеров — в `.env` / переменных окружения. Никогда в коде, никогда в `agents.config`, никогда в git. Пример:

```bash
# .env (в .gitignore!)
OPENROUTER_API_KEY=sk-or-...   # один ключ ко всем моделям сразу
# или прямые ключи провайдеров:
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AI...
ANTHROPIC_API_KEY=sk-ant-...
XAI_API_KEY=xai-...
```

```python
import os
# Имя env-переменной читается из agents.config (секции [side_a] / [side_b])
api_key = os.environ[config["side_a"]["api_key_env"]]
```
