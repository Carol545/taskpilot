"""
Date handling for the agent, done in code instead of by the LLM.

Models are bad at calendar maths (wrong weekdays, wrong year), so tools accept
what the user actually said and we convert it here:

    "today", "tomorrow", "day after tomorrow", "in 3 days", "next week"
    "friday", "this friday", "next friday"
    "17 june", "june 17", "17th jun", "17 june 2027"
    "2027-06-17"

and times: "5pm", "5:30 pm", "17:30", "noon", "midnight", "tomorrow at 5pm".

A day and month with no year means the next time that date comes around,
so "17 june" said in November means June next year, never a date in the past.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Users are in India. Change if that's not true for your deployment.
TIMEZONE = ZoneInfo("Asia/Kolkata")

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def now() -> datetime:
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def today() -> date:
    return now().date()


def time_label(t: time) -> str:
    """'5:00 pm'"""
    return t.strftime("%I:%M %p").lstrip("0").lower()


def label(d: date, t: time | None = None) -> str:
    """'Wed 17 Jun 2027' or 'Wed 17 Jun 2027, 5:00 pm'. Tool results include this so the
    model copies it instead of guessing weekdays."""
    text = d.strftime("%a %d %b %Y").replace(" 0", " ")
    return f"{text}, {time_label(t)}" if t else text


def is_past(d: date, t: time | None = None) -> bool:
    """A date-only task is overdue from the next day; a timed one from that minute."""
    if t is None:
        return d < today()
    return datetime.combine(d, t) < now()


def default_date_for(t: time) -> date:
    """A time with no date means today if that time is still ahead, otherwise tomorrow."""
    return today() if datetime.combine(today(), t) > now() else today() + timedelta(days=1)


_TIME_PATTERN = re.compile(
    r"(?:\bat\s+)?(?<![\w:])(?:"
    r"(noon|midday|midnight)"
    r"|(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.?|p\.m\.?)"
    r"|([01]?\d|2[0-3]):(\d{2})"
    r")(?![\w:])",
    re.IGNORECASE,
)


def parse_time(value: object) -> time | None:
    """'5pm', '5:30 pm', '17:30', 'noon'. Raises ValueError with a readable message."""
    if value is None or isinstance(value, time):
        return value
    if not isinstance(value, str):
        raise ValueError("Give the time as text, e.g. '5pm' or '17:30'.")
    text = value.strip().lower()
    text = re.sub(r"^(at|by|before)\s+", "", text)
    if not text:
        return None
    m = _TIME_PATTERN.fullmatch(text)
    if not m:
        if re.fullmatch(r"\d{1,2}", text):
            raise ValueError(f"'{value}' is ambiguous. Ask the user whether they mean am or pm.")
        raise ValueError(f"Couldn't understand the time '{value}'. Use e.g. '5pm', '5:30 pm' or '17:30'.")
    return _time_from_match(m, value)


def _time_from_match(m: re.Match, original: str) -> time:
    word, hour, minute, ampm, hour24, minute24 = m.groups()
    if word:
        return time(0, 0) if word.lower() == "midnight" else time(12, 0)
    if hour24 is not None:
        return time(int(hour24), int(minute24))
    h, mins = int(hour), int(minute or 0)
    if not 1 <= h <= 12 or mins > 59:
        raise ValueError(f"'{original}' is not a real time.")
    if ampm.lower().startswith("p") and h != 12:
        h += 12
    if ampm.lower().startswith("a") and h == 12:
        h = 0
    return time(h, mins)


def split_time(text: str) -> tuple[str, time | None]:
    """'tomorrow at 5pm' -> ('tomorrow', 5:00 pm). Leaves text without a time unchanged."""
    m = _TIME_PATTERN.search(text)
    if not m:
        return text, None
    rest = (text[:m.start()] + " " + text[m.end():]).strip(" ,")
    return re.sub(r"\s+", " ", rest), _time_from_match(m, text)


def _month(word: str) -> int | None:
    word = word.lower().rstrip(".")
    if len(word) < 3:
        return None
    for i, name in enumerate(_MONTHS, start=1):
        if name.startswith(word):
            return i
    return None


def _weekday(word: str) -> int | None:
    word = word.lower()
    if len(word) < 3:
        return None
    for i, name in enumerate(_WEEKDAYS):
        if name.startswith(word):
            return i
    return None


def _next_occurrence(day: int, month: int, ref: date) -> date:
    for year in (ref.year, ref.year + 1, ref.year + 2, ref.year + 3, ref.year + 4):
        try:
            candidate = date(year, month, day)
        except ValueError:  # 29 Feb in a non-leap year
            continue
        if candidate >= ref:
            return candidate
    raise ValueError(f"{day} is not a valid day for that month.")


def parse_due(value: object, ref: date | None = None) -> date | None:
    """Turn what the user said into a date. Raises ValueError with a readable message."""
    if value is None or isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("Give the date as text, e.g. 'tomorrow', '17 june' or '2027-06-17'.")

    ref = ref or today()
    text = re.sub(r"\s+", " ", value.strip().lower().replace(",", " ")).strip()
    text = re.sub(r"^(by|on|due|before)\s+", "", text)
    if not text:
        return None

    # ISO date
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"'{value}' is not a real date.")

    # Common misspellings: users type fast, and the model passes their words through as-is.
    text = re.sub(r"\b(tom+or+o?w+|tmr+w?|tmw|2mor+o?w?|tomm?or+ow|tomorow|tommorow|tommorrow|tomorrw)\b", "tomorrow", text)
    text = re.sub(r"\b(tod+ay|tday|2day)\b", "today", text)
    simple = {"today": 0, "tonight": 0, "tomorrow": 1,
              "day after tomorrow": 2, "the day after tomorrow": 2}
    if text in simple:
        return ref + timedelta(days=simple[text])
    if text in ("next week", "in a week", "in one week"):
        return ref + timedelta(days=7)

    m = re.fullmatch(r"in (\d{1,3}) (day|days|week|weeks)", text)
    if m:
        n = int(m.group(1))
        return ref + timedelta(days=n * (7 if m.group(2).startswith("week") else 1))

    # Weekdays: "friday" / "this friday" = the coming one (today counts), "next friday" = strictly after today.
    m = re.fullmatch(r"(this |next |coming )?([a-z]+)", text)
    if m and _weekday(m.group(2)) is not None:
        target = _weekday(m.group(2))
        ahead = (target - ref.weekday()) % 7
        if m.group(1) == "next " and ahead == 0:
            ahead = 7
        return ref + timedelta(days=ahead)

    # "17 june", "17th jun 2027", "june 17", "jun 17th 2027"
    m = (re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?(?: of)? ([a-z.]+)(?: (\d{4}))?", text)
         or re.fullmatch(r"([a-z.]+) (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", text))
    if m:
        a, b, year = m.groups()
        day_str, month_word = (a, b) if a.isdigit() else (b, a)
        month = _month(month_word)
        if month:
            day = int(day_str)
            if year:
                try:
                    return date(int(year), month, day)
                except ValueError:
                    raise ValueError(f"'{value}' is not a real date.")
            return _next_occurrence(day, month, ref)

    raise ValueError(
        f"Couldn't understand the date '{value}'. Use words like 'tomorrow', 'friday', '17 june', "
        "or the format YYYY-MM-DD."
    )
