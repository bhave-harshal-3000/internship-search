"""Filtering helpers shared by all sources."""

from __future__ import annotations

KILL_PHRASES = [
    "voice process",
    "inbound calls",
    "outbound calls",
    "phone support",
    "voice support",
    "telecaller",
    "tele-calling",
    "telecalling",
    "bpo voice",
    "call center",
    "door to door",
    "field sales",
    "field work",
    "customer support voice",
    "calling process",
    "sales executive field",
    "field executive",
    "cold calling",
    "telesales",
]

inclusion_keywords = [
    "pwd",
    "specially abled",
    "disability",
    "disabled",
    "deaf",
    "hearing impaired",
    "speech impaired",
    "differently abled",
    "divyang",
    "handicapped",
    "pwbd",
    "persons with disability",
    "accessible",
    "accessibility",
    "inclusive hiring",
    "reasonable accommodation",
    "assistive technology",
    "special needs",
    "sign language",
    "neurodiversity",
    "neurodiverse",
]


def compact_text(*parts: object) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def kill_filter(*parts: object) -> bool:
    """Return True when the listing should be discarded."""

    text = compact_text(*parts)
    return any(phrase in text for phrase in KILL_PHRASES)


def has_inclusion_keyword(*parts: object) -> bool:
    text = compact_text(*parts)
    return any(keyword in text for keyword in inclusion_keywords)

