"""SwarajAbility PWD job scraper using their public API."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from scrapers.common import (
    HEADERS,
    apply_kill_filter,
    apply_money,
    clean_text,
    dedupe_by_apply_url,
    log_progress,
    make_stats,
    output_file,
    request_with_retries,
)
from utils.config import MAX_PAGES, SLEEP_BETWEEN_REQUESTS
from utils.excel_writer import save_excel
from utils.schema import EXPLICIT_PWD


SOURCE = "swarajability"
OUTPUT_FILENAME = "swarajability.xlsx"
API_URL = (
    "https://v2-api.swarajability.org/api/v1/common/jobs/home"
    "?limit=10000000000"
    "&disabilityTypeIds=4,3,10,11,12,13,19,18,14,15,16,17,23,22,21,20,18"
    "&lang=en"
)

TARGET_DISABILITY_IDS = {10, 11}
TARGET_DISABILITY_NAMES = {"hearing", "speech", "deaf", "dumb", "hard of hearing", "language"}


def _clean_html(value: object) -> str:
    if not value:
        return ""
    return clean_text(BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True))


def _matches_target(disabilities: list[object]) -> bool:
    for disability in disabilities:
        if isinstance(disability, dict):
            if disability.get("id") in TARGET_DISABILITY_IDS:
                return True
            name = clean_text(disability.get("name") or disability.get("disabilityName") or "").lower()
            if any(keyword in name for keyword in TARGET_DISABILITY_NAMES):
                return True
    return False


def _salary_raw(job: dict[str, Any]) -> str:
    min_salary = job.get("minSalary")
    max_salary = job.get("maxSalary")
    if min_salary and max_salary:
        return f"INR {min_salary} - {max_salary} per year"
    if min_salary:
        return f"INR {min_salary}+ per year"
    return ""


def _job_type(job: dict[str, Any]) -> str:
    raw = clean_text(job.get("type"))
    return "internship" if raw == "INTERNSHIP" else "job"


def _locations(job: dict[str, Any]) -> str:
    locations = job.get("locations") or []
    parts: list[str] = []
    for location in locations:
        if isinstance(location, dict):
            city = location.get("city")
            state = location.get("state")
            if isinstance(city, dict):
                city = city.get("name")
            if isinstance(state, dict):
                state = state.get("name")
            if city:
                parts.append(str(city))
            if state and str(state) not in parts:
                parts.append(str(state))
    return clean_text(", ".join(parts))


def _is_remote(job: dict[str, Any], description: str, location: str) -> bool:
    raw = clean_text(job.get("type") or "")
    text = f"{raw} {description} {location}".lower()
    return any(token in text for token in ["remote", "wfh", "work from home"])


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    session = requests.Session()
    session.headers.update({**HEADERS, "Accept": "application/json, text/plain, */*"})

    response = request_with_retries(session, API_URL, timeout=45)
    payload = response.json()
    jobs = payload.get("data") or []
    if not isinstance(jobs, list):
        jobs = []

    # Only keep jobs that explicitly match hearing/speech disability
    filtered = [job for job in jobs if _matches_target(job.get("disabilities") or [])]

    rows: list[dict[str, object]] = []
    for job in filtered:
        if not isinstance(job, dict):
            continue
        description = _clean_html(job.get("description"))
        location = _locations(job)
        row = {
            "title": clean_text(job.get("title")),
            "company": clean_text((job.get("recruiter") or {}).get("name")) or "SwarajAbility",
            "location": location,
            "is_remote": _is_remote(job, description, location),
            "type": _job_type(job),
            "inclusion_type": EXPLICIT_PWD,
            "confidence_level": "HIGH",
            "backend_type": "custom_api",
            "stipend_or_salary_raw": _salary_raw(job),
            "duration": "",
            "posted_at": clean_text((job.get("createdAt") or "")[:10]),
            "apply_url": clean_text(
                f"https://portal.swarajability.org/apply-job/{job.get('id')}" if job.get("id") else ""
            ),
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        if row["title"] or row["apply_url"]:
            rows.append(apply_money(row))

    total_scraped = len(rows)
    kept_records, killed_by_filter = apply_kill_filter(rows)
    log_progress(SOURCE, 1, total_scraped, killed_by_filter)
    time.sleep(SLEEP_BETWEEN_REQUESTS)

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path)


if __name__ == "__main__":
    print(scrape())
