"""Google Jobs scraper — powered by Jooble API."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:
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
)
from utils.config import MAX_PAGES, SLEEP_BETWEEN_REQUESTS
from utils.excel_writer import save_excel
from utils.filters import has_inclusion_keyword
from utils.schema import KEYWORD_PWD


SOURCE = "google_jobs"
OUTPUT_FILENAME = "google_jobs.xlsx"
JOOBLE_API_URL = "https://jooble.org/api/{key}"

PWD_QUERIES = [
    {"keywords": "PWD disability deaf hearing impaired jobs", "location": "India"},
    {"keywords": "speech impaired specially abled jobs internship", "location": "India"},
    {"keywords": "divyang PwBD disability jobs remote", "location": "India"},
]


def _load_api_key() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if load_dotenv:
        load_dotenv(env_path)
    elif env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("JOOBLE_KEY="):
                os.environ.setdefault("JOOBLE_KEY", line.split("=", 1)[1].strip())
    return clean_text(os.getenv("JOOBLE_KEY", ""))


def _is_remote(job: dict[str, Any]) -> bool:
    text = " ".join([
        clean_text(job.get("title")),
        clean_text(job.get("location")),
        clean_text(job.get("snippet")),
        clean_text(job.get("type")),
    ]).lower()
    return any(token in text for token in ["remote", "work from home", "wfh"])


def _listing_type(job: dict[str, Any]) -> str:
    text = " ".join([
        clean_text(job.get("title")),
        clean_text(job.get("snippet")),
        clean_text(job.get("type")),
    ]).lower()
    return "internship" if "intern" in text else "job"


def _rows_from_response(payload: dict[str, Any]) -> list[dict[str, object]]:
    jobs = payload.get("jobs") or []
    if not isinstance(jobs, list):
        return []

    rows: list[dict[str, object]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue

        title = clean_text(job.get("title"))
        company = clean_text(job.get("company"))
        snippet = clean_text(job.get("snippet"))
        location = clean_text(job.get("location"))
        salary = clean_text(job.get("salary"))
        apply_url = clean_text(job.get("link"))
        posted_at = clean_text(job.get("updated") or "")[:10]  # ISO date, trim to YYYY-MM-DD

        if not (title or apply_url):
            continue
        if not has_inclusion_keyword(title, company, snippet):
            continue

        row = {
            "title": title,
            "company": company,
            "location": location,
            "is_remote": _is_remote(job),
            "type": _listing_type(job),
            "inclusion_type": KEYWORD_PWD,
            "stipend_or_salary_raw": salary,
            "duration": "",
            "posted_at": posted_at,
            "apply_url": apply_url,
            "source": SOURCE,
            "description_snippet": snippet[:300],
        }
        rows.append(apply_money(row))
    return rows


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    api_key = _load_api_key()

    if not api_key:
        print(f"[{SOURCE}] JOOBLE_KEY missing in .env; skipping.", flush=True)
        save_excel([], output_path)
        return make_stats(SOURCE, 0, 0, 0, output_path, "JOOBLE_KEY missing")

    url = JOOBLE_API_URL.format(key=api_key)
    session = requests.Session()
    session.headers.update({**HEADERS, "Content-Type": "application/json"})

    total_scraped = 0
    killed_by_filter = 0
    kept_records: list[dict[str, object]] = []

    for query in PWD_QUERIES:
        for page_number in range(1, max_pages + 1):
            body = {**query, "page": page_number}
            try:
                response = session.post(url, json=body, timeout=30)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                # Log full detail to console for debugging — never expose to users
                print(f"[{SOURCE}] query={query['keywords']!r} page={page_number} failed: {exc}", flush=True)
                break

            page_rows = _rows_from_response(payload)
            total_scraped += len(page_rows)
            kept_page, killed_page = apply_kill_filter(page_rows)
            killed_by_filter += killed_page
            kept_records.extend(kept_page)
            log_progress(f"{SOURCE}:{query['keywords'][:30]}", page_number, total_scraped, killed_by_filter)

            # Jooble returns totalCount — stop when we've fetched all pages
            total_count = payload.get("totalCount") or 0
            if not page_rows or page_number * 20 >= int(total_count):
                break

            time.sleep(SLEEP_BETWEEN_REQUESTS)

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path)


if __name__ == "__main__":
    print(scrape())
