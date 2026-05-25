"""Google Jobs scraper using SerpAPI."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requirements.txt installs python-dotenv
    load_dotenv = None

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
from utils.filters import has_inclusion_keyword
from utils.schema import KEYWORD_PWD


SOURCE = "google_jobs"
OUTPUT_FILENAME = "google_jobs.xlsx"
SERPAPI_URL = "https://serpapi.com/search.json"

# Reduced from the larger prompt list to avoid near-duplicate searches while
# preserving coverage across Indian PWD terminology and communication access.
PWD_QUERIES = [
    "PWD jobs India remote",
    "persons with disability jobs India",
    "specially abled jobs internships India",
    "divyang jobs India",
    "PwBD jobs India",
    "deaf hearing impaired jobs India remote",
    "speech impaired jobs India",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_api_key() -> str:
    env_path = _project_root() / ".env"
    if load_dotenv:
        load_dotenv(env_path)
    elif env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("SERPAPI_KEY="):
                os.environ.setdefault("SERPAPI_KEY", line.split("=", 1)[1].strip())
    return clean_text(os.getenv("SERPAPI_KEY"))


def _first_apply_url(job: dict[str, Any]) -> str:
    apply_options = job.get("apply_options") or []
    if isinstance(apply_options, list):
        for option in apply_options:
            if isinstance(option, dict) and option.get("link"):
                return clean_text(option.get("link"))
    for key in ["share_link", "link", "job_id"]:
        value = clean_text(job.get(key))
        if value:
            return value
    return ""


def _salary_text(job: dict[str, Any]) -> str:
    detected = job.get("detected_extensions") or {}
    if isinstance(detected, dict):
        for key in ["salary", "salary_range", "pay", "stipend"]:
            value = clean_text(detected.get(key))
            if value:
                return value

    extensions = job.get("extensions") or []
    if isinstance(extensions, list):
        for extension in extensions:
            text = clean_text(extension)
            lowered = text.lower()
            if any(token in lowered for token in ["₹", "rs", "inr", "lpa", "lakh", "salary", "stipend"]):
                return text
    return ""


def _posted_at(job: dict[str, Any]) -> str:
    detected = job.get("detected_extensions") or {}
    if isinstance(detected, dict):
        for key in ["posted_at", "date_posted", "posted"]:
            value = clean_text(detected.get(key))
            if value:
                return value

    extensions = job.get("extensions") or []
    if isinstance(extensions, list):
        for extension in extensions:
            text = clean_text(extension)
            if "ago" in text.lower() or "posted" in text.lower():
                return text
    return ""


def _is_remote(job: dict[str, Any]) -> bool:
    detected = job.get("detected_extensions") or {}
    if isinstance(detected, dict) and detected.get("work_from_home"):
        return True
    text = " ".join(
        [
            clean_text(job.get("title")),
            clean_text(job.get("location")),
            clean_text(job.get("description")),
            clean_text(" ".join(str(item) for item in job.get("extensions") or [])),
        ]
    ).lower()
    return any(token in text for token in ["remote", "work from home", "wfh"])


def _listing_type(job: dict[str, Any]) -> str:
    text = " ".join(
        [
            clean_text(job.get("title")),
            clean_text(job.get("description")),
            clean_text(" ".join(str(item) for item in job.get("extensions") or [])),
        ]
    ).lower()
    return "internship" if "intern" in text else "job"


def _rows_from_response(payload: dict[str, Any]) -> list[dict[str, object]]:
    jobs = payload.get("jobs_results") or []
    if not isinstance(jobs, list):
        return []

    rows: list[dict[str, object]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        description = clean_text(job.get("description"))
        row = {
            "title": clean_text(job.get("title")),
            "company": clean_text(job.get("company_name")),
            "location": clean_text(job.get("location")),
            "is_remote": _is_remote(job),
            "type": _listing_type(job),
            "inclusion_type": KEYWORD_PWD,
            "stipend_or_salary_raw": _salary_text(job),
            "duration": "",
            "posted_at": _posted_at(job),
            "apply_url": _first_apply_url(job),
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        if (row["title"] or row["apply_url"]) and has_inclusion_keyword(
            row.get("title"),
            row.get("company"),
            row.get("description_snippet"),
        ):
            rows.append(apply_money(row))
    return rows


def _next_page_token(payload: dict[str, Any]) -> str:
    pagination = payload.get("serpapi_pagination") or {}
    if not isinstance(pagination, dict):
        return ""
    return clean_text(pagination.get("next_page_token"))


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    api_key = _load_api_key()
    if not api_key:
        print("[google_jobs] SERPAPI_KEY missing in .env; skipping Google Jobs scraper.", flush=True)
        save_excel([], output_path)
        return make_stats(SOURCE, 0, 0, 0, output_path, "SERPAPI_KEY missing")

    session = requests.Session()
    session.headers.update(HEADERS)

    total_scraped = 0
    killed_by_filter = 0
    kept_records: list[dict[str, object]] = []

    for query in PWD_QUERIES:
        next_token = ""
        for page_number in range(1, max_pages + 1):
            params = {
                "engine": "google_jobs",
                "q": query,
                "gl": "in",
                "hl": "en",
                "api_key": api_key,
            }
            if next_token:
                params["next_page_token"] = next_token

            response = request_with_retries(session, SERPAPI_URL, params=params, timeout=45)
            payload = response.json()
            page_records = _rows_from_response(payload)

            total_scraped += len(page_records)
            kept_page, killed_page = apply_kill_filter(page_records)
            killed_by_filter += killed_page
            kept_records.extend(kept_page)
            log_progress(f"{SOURCE}:{query}", page_number, total_scraped, killed_by_filter)

            next_token = _next_page_token(payload)
            if not next_token:
                break
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path)


if __name__ == "__main__":
    print(scrape())
