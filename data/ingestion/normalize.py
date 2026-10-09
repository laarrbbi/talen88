"""Stage 3 — NORMALIZE. Coerce mapped values into canonical types/vocabulary.

Pure functions, no DB, no AI. Dates (ISO strings, US/Euro slash formats, Python
date/datetime objects, and Excel serial numbers) become `YYYY-MM-DD`; money becomes
a single base currency (USD); free-text grades/levels become the canonical integer
level; controlled-vocab strings are lower-cased, alias-folded, and checked against
`data.canonical.ENUMS` so a bad value fails HERE — loudly — before it could ever reach
the DB CHECK constraint.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .. import canonical

# ccy -> USD per 1 unit (inverse of the generator's USD->ccy table; license/treasury
# rates in a real system). Used to fold compensation onto a single base currency.
USD_PER_UNIT = {
    "USD": 1.0, "GBP": 1.27, "EUR": 1.09, "SGD": 0.74, "HKD": 0.128, "JPY": 0.0067,
}

# Excel's day 0 is 1899-12-30 (the serial system, incl. its 1900 leap-year quirk).
_EXCEL_EPOCH = datetime(1899, 12, 30)

# Source grade/level labels -> canonical integer level (1..6). Extend per HRIS.
LEVEL_GRADE_MAP = {
    "ic1": 1, "ic2": 2, "ic3": 3, "ic4": 4, "ic5": 5,
    "analyst": 1, "associate": 2, "senior associate": 3, "avp": 3,
    "vp": 4, "svp": 5, "director": 5, "managing director": 6, "md": 6,
    "l1": 1, "l2": 2, "l3": 3, "l4": 4, "l5": 5, "l6": 6,
}

# Controlled-vocab aliases: source spelling -> canonical enum value.
ENUM_ALIASES = {
    "employment_type": {
        "full time": "full_time", "fulltime": "full_time", "ft": "full_time",
        "part time": "part_time", "parttime": "part_time", "pt": "part_time",
        "contract": "contractor", "contingent": "contractor",
    },
    "status": {
        "active": "active", "employed": "active",
        "leave": "on_leave", "on leave": "on_leave", "loa": "on_leave",
        "terminated": "terminated", "term": "terminated", "separated": "terminated",
    },
    "business_travel_frequency": {
        "non-travel": "none", "no travel": "none", "rarely": "none",
        "travel_rarely": "occasional", "occasional": "occasional",
        "travel_frequently": "frequent", "frequent": "frequent",
    },
}


def to_iso_date(value: Any) -> str | None:
    """Normalize many date encodings to an ISO `YYYY-MM-DD` string (or None)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return excel_serial_to_date(value)
    text = str(value).strip()
    # A bare number-as-text is an Excel serial.
    if text.isdigit():
        return excel_serial_to_date(int(text))
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y",
                "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date value: {value!r}")


def excel_serial_to_date(serial: int | float) -> str:
    """Convert an Excel serial day number to an ISO date string."""
    return (_EXCEL_EPOCH + timedelta(days=int(serial))).date().isoformat()


def to_base_currency(amount: Any, currency: str | None) -> float | None:
    """Convert an amount in `currency` to the USD base. Unknown currency fails loudly."""
    if amount is None or amount == "":
        return None
    code = (currency or "USD").strip().upper()
    if code not in USD_PER_UNIT:
        raise ValueError(f"unknown currency for FX conversion: {code!r}")
    return round(float(amount) * USD_PER_UNIT[code], 2)


def normalize_level(value: Any) -> int | None:
    """Map a numeric level or a grade label to the canonical integer level."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    mapped = LEVEL_GRADE_MAP.get(text.lower())
    if mapped is None:
        raise ValueError(f"unmappable level/grade: {value!r}")
    return mapped


def normalize_enum(field: str, value: Any) -> str | None:
    """Lower-case + alias-fold a controlled-vocab value, then validate it. Raises on a
    value that is not in `canonical.ENUMS[field]` (bad vocab fails before the DB)."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    text = ENUM_ALIASES.get(field, {}).get(text, text)
    canonical.validate_enum(field, text)  # ValueError if still out-of-vocab
    return text
