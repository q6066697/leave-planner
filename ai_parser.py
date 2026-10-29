"""Optional free-text vacation wish parser (OpenAI).

The model only *suggests* dates as strict JSON. Validation is always done by
rules_engine. The API key is read from the OPENAI_API_KEY environment variable
(a local .env file is loaded into the environment if present).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date

from rules_engine import PartInput

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

DEFAULT_MODEL = "gpt-4o-mini"
UNAVAILABLE_MESSAGE = "AI-разбор недоступен, введите даты вручную"


class AIParserError(Exception):
    pass


@dataclass
class Suggestion:
    parts: list[PartInput]
    comment: str = ""


def is_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def build_prompt(text: str, year: int, norm: int, today: date, max_parts: int, min_part: int) -> list[dict]:
    system = (
        "Ты помощник по планированию отпусков в белорусской компании. "
        "Преобразуй пожелание сотрудника в периоды отпуска. "
        f"Год планирования: {year}. Сегодня: {today.isoformat()}. "
        f"Норма отпуска: {norm} календарных дней. Отпуск можно разделить не более чем на {max_parts} части, "
        f"одна из частей — не менее {min_part} дней. Если сотрудник не указал длительность, распредели всю норму. "
        "Государственные праздники не учитывай — система продлит отпуск сама. "
        "Ответь строго JSON-объектом без пояснений вокруг него, формат: "
        '{"periods": [{"start": "YYYY-MM-DD", "days": <целое число календарных дней>}], '
        '"comment": "<коротко по-русски, как ты понял пожелание>"}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def parse_response(raw: str, year: int, max_parts: int = 2) -> Suggestion:
    """Validate the model's JSON. Raises AIParserError on any malformed output."""
    try:
        data = json.loads(raw)
        periods = data["periods"]
        if not isinstance(periods, list) or not periods:
            raise ValueError("empty periods")
        parts = []
        for p in periods[:max_parts]:
            start = date.fromisoformat(str(p["start"]))
            days = int(p["days"])
            if days < 1 or start.year != year:
                raise ValueError(f"bad period {p}")
            parts.append(PartInput(start, days))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AIParserError(f"Не удалось разобрать ответ модели: {exc}") from exc
    return Suggestion(sorted(parts, key=lambda p: p.start), str(data.get("comment", "")))


def parse_wish(text: str, year: int, norm: int = 24, today: date | None = None,
               max_parts: int = 2, min_part: int = 14) -> Suggestion:
    if not is_available():
        raise AIParserError(UNAVAILABLE_MESSAGE)
    if not text.strip():
        raise AIParserError("Опишите пожелание словами.")
    from openai import OpenAI

    client = OpenAI()
    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            messages=build_prompt(text, year, norm, today or date.today(), max_parts, min_part),
            response_format={"type": "json_object"},
            temperature=0,
        )
    except Exception as exc:  # network, auth, quota
        raise AIParserError(f"Ошибка обращения к OpenAI: {exc}") from exc
    return parse_response(response.choices[0].message.content or "", year, max_parts)
