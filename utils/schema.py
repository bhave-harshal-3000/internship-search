"""Shared schema and row normalization."""

from __future__ import annotations

from typing import Any, Mapping


COLUMNS = [
    "title",
    "company",
    "location",
    "is_remote",
    "type",
    "inclusion_type",
    "confidence_level",
    "backend_type",
    "stipend_or_salary_raw",
    "stipend_min",
    "stipend_max",
    "salary_min",
    "salary_max",
    "duration",
    "posted_at",
    "apply_url",
    "source",
    "description_snippet",
]

EXPLICIT_PWD = "explicit_pwd"
REMOTE_ACCESSIBLE = "remote_accessible"
KEYWORD_PWD = "keyword_pwd"
COMPANY_DISABILITY_PROGRAM = "company_disability_program"
INCLUSIVE_HIRING = "inclusive_hiring"
AI_ACCESSIBLE = "ai_accessible"


def normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a row with every schema column present and unknown fields removed."""

    normalized: dict[str, Any] = {}
    for column in COLUMNS:
        value = row.get(column, "")
        if value is None:
            value = ""
        if column == "description_snippet" and isinstance(value, str):
            value = value.strip()[:300]
        normalized[column] = value
    return normalized


def normalize_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_row(row) for row in rows]

