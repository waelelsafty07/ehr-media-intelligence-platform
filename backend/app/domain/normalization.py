"""Deterministic normalization of dates, gender, MRN and names. No LLM, no guessing.

Each function returns Normalized(value, outcome, reason). `reason` is a short code, never the input.
Ambiguous input (e.g. 03/04/2020) is INVALID rather than guessed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from app.domain.canonical import Gender

T = TypeVar("T")


class Outcome(StrEnum):
    OK = "ok"  # already canonical
    RECOVERED = "recovered"  # unambiguous formatting problem fixed
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class Normalized(Generic[T]):
    value: T | None
    outcome: Outcome
    reason: str | None = None


_BAD = object()
_MISSING_TOKENS = {"", "n/a", "na", "null", "none", "unknown", "unk", "-", "--", "?"}


def _coerce(raw: Any) -> str | None | object:
    """Text of a scalar; None if absent; _BAD for unsupported types (lists, dicts, bools)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return _BAD
    if isinstance(raw, (str, int)):
        return str(raw).strip()
    return _BAD


# ---------------------------------------------------------------- dates
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_DATETIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_YMD = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")
_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_XYY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$")
_MONTH_FORMATS = ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y")


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _parse_date_text(text: str) -> tuple[date | None, Outcome, str | None]:
    if m := _ISO.match(text):
        d = _make(*map(int, m.groups()))
        return (d, Outcome.OK, None) if d else (None, Outcome.INVALID, "not_a_real_date")
    for pattern, outcome in ((_ISO_DATETIME, Outcome.RECOVERED), (_YMD, Outcome.RECOVERED),
                             (_COMPACT, Outcome.RECOVERED)):
        if m := pattern.match(text):
            y, mo, d_ = (int(g) for g in m.groups()[:3])
            d = _make(y, mo, d_)
            return (d, outcome, None) if d else (None, Outcome.INVALID, "not_a_real_date")
    if m := _XYY.match(text):
        a, b, y = (int(g) for g in m.groups())
        if a > 12 and b <= 12:
            day, month = a, b  # only DD/MM is possible
        elif b > 12 and a <= 12:
            day, month = b, a  # only MM/DD is possible
        elif a <= 12 and b <= 12 and a != b:
            return None, Outcome.INVALID, "ambiguous_day_month"
        elif a == b and a <= 12:
            day, month = a, b  # same either way
        else:
            return None, Outcome.INVALID, "not_a_real_date"
        d = _make(y, month, day)
        return (d, Outcome.RECOVERED, None) if d else (None, Outcome.INVALID, "not_a_real_date")
    for fmt in _MONTH_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), Outcome.RECOVERED, None
        except ValueError:
            continue
    return None, Outcome.INVALID, "unrecognized_format"


def normalize_date(raw: Any, *, today: date | None = None, min_year: int = 1900) -> Normalized[date]:
    today = today or date.today()
    if isinstance(raw, datetime):
        raw = raw.date()
    if isinstance(raw, date):
        value, outcome, reason = raw, Outcome.OK, None
    else:
        text = _coerce(raw)
        if text is _BAD:
            return Normalized(None, Outcome.INVALID, "unsupported_type")
        if text is None or str(text).casefold() in _MISSING_TOKENS:
            return Normalized(None, Outcome.MISSING)
        value, outcome, reason = _parse_date_text(str(text))
    if value is None:
        return Normalized(None, Outcome.INVALID, reason)
    if value > today:
        return Normalized(None, Outcome.INVALID, "in_the_future")
    if value.year < min_year:
        return Normalized(None, Outcome.INVALID, "implausible_year")
    return Normalized(value, outcome, reason)


# ---------------------------------------------------------------- gender
_GENDER_MAP = {
    **dict.fromkeys(("m", "male", "man", "boy"), Gender.MALE),
    **dict.fromkeys(("f", "female", "woman", "girl"), Gender.FEMALE),
    **dict.fromkeys(("o", "other", "nonbinary", "non binary", "non-binary", "nb", "x", "intersex"),
                    Gender.OTHER),
    **dict.fromkeys(("u", "unk", "unknown", "unspecified", "undisclosed", "not specified"),
                    Gender.UNKNOWN),
}
_GENDER_MISSING = {"", "none", "null", "n/a", "na", "-", "--", "?"}


def normalize_gender(raw: Any) -> Normalized[Gender]:
    text = _coerce(raw)
    if text is _BAD:
        return Normalized(Gender.UNKNOWN, Outcome.INVALID, "unsupported_type")
    if text is None or str(text).casefold() in _GENDER_MISSING:
        return Normalized(Gender.UNKNOWN, Outcome.MISSING)
    key = str(text).casefold().replace(".", "").strip()
    if key in _GENDER_MAP:
        return Normalized(_GENDER_MAP[key], Outcome.OK)
    return Normalized(Gender.UNKNOWN, Outcome.INVALID, "unrecognized_value")  # never guess


# ---------------------------------------------------------------- MRN
_MRN_PREFIX = re.compile(r"^MRN[\s:#._\-]*")
_MRN_VALID = re.compile(r"^[A-Z0-9]{3,20}$")


def normalize_mrn(raw: Any) -> Normalized[str]:
    """Uppercase, drop an 'MRN' prefix and separators; keep leading zeros."""
    text = _coerce(raw)
    if text is _BAD:
        return Normalized(None, Outcome.INVALID, "unsupported_type")
    if text is None or str(text).casefold() in _MISSING_TOKENS:
        return Normalized(None, Outcome.MISSING)
    original = str(text)
    cleaned = re.sub(r"[\s\-_.]", "", _MRN_PREFIX.sub("", original.upper()))
    if not cleaned:
        return Normalized(None, Outcome.MISSING, "prefix_only")
    if not _MRN_VALID.match(cleaned):
        return Normalized(None, Outcome.INVALID, "invalid_characters_or_length")
    return Normalized(cleaned, Outcome.OK if cleaned == original else Outcome.RECOVERED)


# ---------------------------------------------------------------- names
def normalize_name(raw: Any) -> Normalized[str]:
    text = _coerce(raw)
    if text is _BAD:
        return Normalized(None, Outcome.INVALID, "unsupported_type")
    if text is None or str(text).casefold() in _MISSING_TOKENS:
        return Normalized(None, Outcome.MISSING)
    name = " ".join(str(text).split())
    if name.count(",") == 1:  # "Last, First" -> "First Last"
        last, first = (p.strip() for p in name.split(","))
        if first and last:
            return Normalized(f"{first} {last}", Outcome.RECOVERED)
    return Normalized(name, Outcome.OK)


def name_key(full_name: str) -> str:
    """Exact-comparison key: accent/case/punctuation/token-order insensitive. NOT fuzzy matching."""
    s = unicodedata.normalize("NFKD", full_name).casefold()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(sorted(re.findall(r"\w+", s)))
