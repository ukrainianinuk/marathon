"""Виклики Claude API: аналіз тональності, детекція звернень, генерація відповідей."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

SENTIMENT_CATEGORIES = ("positive", "neutral", "frustrated", "question", "urgent")

_TASK_HASHTAG_RE = re.compile(r"#\s*день\s*(\d{1,2})\b", re.IGNORECASE)

_ANALYSIS_SYSTEM_PROMPT = """\
Ти асистент, що аналізує повідомлення учасників платного 20-денного навчального марафону \
в Telegram-групі. Організаторка марафону — україномовна, аудиторія — діаспора у Великій \
Британії та Ірландії.

Для кожного повідомлення визнач:
1. sentiment — одна з категорій: positive, neutral, frustrated, question, urgent.
   - urgent: людина явно фрустрована, засмучена, хоче покинути марафон, скаржиться на щось \
критичне, або в кризовому емоційному стані.
   - frustrated: незадоволення, роздратування, але без ознак негайного виходу з марафону.
   - question: нейтральне запитання без негативних емоцій.
   - positive: подяка, захоплення, позитивний фідбек.
   - neutral: інформаційне повідомлення, що не потребує реакції.
2. addressed_to_admin — true, якщо повідомлення явно звернене до організаторки марафону \
(пряме питання їй, прохання про допомогу, скарга, тег), false — якщо це звичайне спілкування \
між учасниками чи повідомлення в нікуди.

Відповідай ЛИШЕ у форматі JSON без жодного додаткового тексту:
{"sentiment": "...", "addressed_to_admin": true/false}
"""

_REPLY_SYSTEM_PROMPT = """\
Ти допомагаєш організаторці україномовного навчального марафону для діаспори у Великій \
Британії та Ірландії відповідати учасникам у Telegram. Стиль відповідей: теплий, підтримуючий, \
турботливий, не занадто формальний — як приятелька, що вболіває за успіх учасниці/учасника. \
Відповіді короткі (1-3 речення), українською мовою.

Згенеруй РІВНО 2 короткі варіанти відповіді на повідомлення учасника нижче. Відповідай ЛИШЕ \
у форматі JSON без жодного додаткового тексту:
{"variants": ["варіант 1", "варіант 2"]}
"""


@dataclass
class AnalysisResult:
    sentiment: str
    addressed_to_admin: bool


class ClaudeServiceError(Exception):
    pass


class ClaudeService:
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def _call(self, system: str, user_text: str, max_tokens: int) -> str:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_text}],
            )
        except anthropic.RateLimitError as exc:
            logger.warning("Claude API rate limit: %s", exc)
            raise ClaudeServiceError("rate_limit") from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            logger.warning("Claude API connection issue: %s", exc)
            raise ClaudeServiceError("connection") from exc
        except anthropic.APIStatusError as exc:
            logger.warning("Claude API status error: %s", exc)
            raise ClaudeServiceError("status") from exc
        return response.content[0].text

    async def analyze_message(self, text: str) -> AnalysisResult:
        """Визначає тональність і чи звернене повідомлення до організаторки.

        У разі збою Claude API повертає безпечний фолбек (neutral / не звернене),
        щоб не зупиняти обробку інших повідомлень.
        """
        try:
            raw = await self._call(_ANALYSIS_SYSTEM_PROMPT, text, max_tokens=200)
            data = json.loads(_extract_json(raw))
            sentiment = data.get("sentiment", "neutral")
            if sentiment not in SENTIMENT_CATEGORIES:
                sentiment = "neutral"
            return AnalysisResult(sentiment=sentiment, addressed_to_admin=bool(data.get("addressed_to_admin", False)))
        except (ClaudeServiceError, json.JSONDecodeError, KeyError, IndexError) as exc:
            logger.error("Не вдалося проаналізувати повідомлення через Claude API: %s", exc)
            return AnalysisResult(sentiment="neutral", addressed_to_admin=False)

    async def generate_reply_suggestions(self, text: str) -> list[str]:
        """Повертає 2 варіанти відповіді. У разі збою — порожній список (адмін пише сама)."""
        try:
            raw = await self._call(_REPLY_SYSTEM_PROMPT, text, max_tokens=400)
            data = json.loads(_extract_json(raw))
            variants = data.get("variants", [])
            return [str(v) for v in variants[:2]]
        except (ClaudeServiceError, json.JSONDecodeError, KeyError, IndexError) as exc:
            logger.error("Не вдалося згенерувати варіанти відповіді через Claude API: %s", exc)
            return []

    @staticmethod
    def detect_task_day(text: str | None, max_day: int) -> int | None:
        """Детектує звіт про виконання завдання дня за хештегом #деньN (деталі узгоджено з користувачем)."""
        if not text:
            return None
        match = _TASK_HASHTAG_RE.search(text)
        if not match:
            return None
        day = int(match.group(1))
        if 1 <= day <= max_day:
            return day
        return None


def _extract_json(raw: str) -> str:
    """Claude інколи обгортає JSON у markdown-код-блок — прибираємо зайве."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()
