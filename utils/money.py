"""Parse Indian salary and stipend text into numeric INR ranges."""

from __future__ import annotations

import re
from typing import Optional


def _clean(raw_string: str) -> str:
    text = (raw_string or "").lower()
    replacements = {
        "₹": "",
        "rs.": "",
        "rs": "",
        "inr": "",
        ",": "",
        "/-": "",
        "per month": "month",
        "per annum": "annum",
        "per year": "annum",
        "p.a.": "annum",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\bpa\b", "annum", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _period(text: str) -> str:
    if any(token in text for token in ["month", "monthly", "/month", "pm", "stipend"]):
        return "monthly"
    if any(token in text for token in ["lpa", "lakh", "lac", "annum", "annual", "year", "salary"]):
        return "annual"
    return "unknown"


def _multiplier(number_text: str, suffix: str) -> int:
    suffix = suffix.lower().strip()
    if suffix in {"lpa", "lakh", "lakhs", "lac", "lacs"}:
        return 100000
    if suffix == "k":
        return 1000
    return 1


def _to_int(value: float) -> int:
    return int(round(value))


def parse_money(raw_string: str | None) -> tuple[Optional[int], Optional[int], str]:
    """Return (min, max, period) for an Indian money range.

    Examples:
    - "₹15,000 - ₹25,000/month" -> (15000, 25000, "monthly")
    - "3-5 LPA" -> (300000, 500000, "annual")
    - "Negotiable" -> (None, None, "unknown")
    """

    if not raw_string or not str(raw_string).strip():
        return None, None, "unknown"

    text = _clean(str(raw_string))
    if any(token in text for token in ["negotiable", "not disclosed", "unpaid", "performance based"]):
        return None, None, _period(text)

    period = _period(text)

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(k|lpa|lakhs?|lacs?)?\s*(?:-|to|–|—)\s*"
        r"(\d+(?:\.\d+)?)\s*(k|lpa|lakhs?|lacs?)?",
        text,
    )
    if range_match:
        low, low_suffix, high, high_suffix = range_match.groups()
        suffix = high_suffix or low_suffix or ""
        low_value = _to_int(float(low) * _multiplier(low, low_suffix or suffix))
        high_value = _to_int(float(high) * _multiplier(high, high_suffix or suffix))
        if suffix.lower() in {"lpa", "lakh", "lakhs", "lac", "lacs"}:
            period = "annual"
        elif suffix.lower() == "k" and period == "unknown":
            period = "unknown"
        return low_value, high_value, period

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*(k|lpa|lakhs?|lacs?)?", text)
    if single_match:
        number, suffix = single_match.groups()
        value = _to_int(float(number) * _multiplier(number, suffix or ""))
        if (suffix or "").lower() in {"lpa", "lakh", "lakhs", "lac", "lacs"}:
            period = "annual"
        max_value = value if suffix or period in {"monthly", "annual"} else None
        return value, max_value, period

    return None, None, period
