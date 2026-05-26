"""National Career Service PWD job scraper.

Uses the confirmed API endpoint:
  POST https://betacloud.ncs.gov.in/api/v1/job-posts/search?page=<N>&size=20
  Content-Type: application/json
  Body: {"sortBy": "RELEVANCE", "userId": "", "pwdCandidateWelcome": true}
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

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
from utils.schema import EXPLICIT_PWD


SOURCE = "ncs"
OUTPUT_FILENAME = "ncs.xlsx"
BASE_URL = "https://betacloud.ncs.gov.in"
API_URL = f"{BASE_URL}/api/v1/job-posts/search"
PAGE_SIZE = 20
REQUEST_BODY = {"sortBy": "RELEVANCE", "userId": "", "pwdCandidateWelcome": True}


def _row_from_job(job: dict[str, Any]) -> dict[str, object]:
    title = clean_text(job.get("jobTitle") or job.get("title") or "")
    company = clean_text(
        job.get("organizationName") or job.get("companyName") or job.get("employerName") or ""
    )
    
    locations = job.get("jobLocations") or []
    loc_parts = []
    if isinstance(locations, list):
        for loc in locations:
            if isinstance(loc, dict):
                city = clean_text(loc.get("city"))
                state = clean_text(loc.get("state"))
                if city and city not in loc_parts:
                    loc_parts.append(city)
                if state and state not in loc_parts:
                    loc_parts.append(state)
    location = ", ".join(loc_parts)

    description = clean_text(
        job.get("jobDescription") or job.get("description") or job.get("skillRequired") or ""
    )
    salary_raw = clean_text(job.get("salary") or job.get("salaryRange") or job.get("ctc") or "")
    if not salary_raw:
        min_s = clean_text(job.get("minSalary") or "")
        max_s = clean_text(job.get("maxSalary") or "")
        salary_raw = " - ".join(part for part in [min_s, max_s] if part)

    posted_at = clean_text(
        job.get("postedOn") or job.get("postedDate") or job.get("createdAt") or ""
    )

    job_id = clean_text(job.get("jobPostId") or job.get("jobId") or job.get("id") or "")
    apply_url = f"{BASE_URL}/job-details/{job_id}" if job_id else ""
    
    job_type_raw = clean_text(job.get("jobType") or "job")
    job_type = job_type_raw.replace("_", " ").title()

    row = {
        "title": title,
        "company": company,
        "location": location,
        "is_remote": any(
            token in f"{location} {description}".lower()
            for token in ["work from home", "remote", "wfh"]
        ),
        "type": job_type,
        "inclusion_type": EXPLICIT_PWD,
        "confidence_level": "HIGH",
        "backend_type": "custom_api",
        "stipend_or_salary_raw": salary_raw,
        "duration": "",
        "posted_at": posted_at,
        "apply_url": apply_url,
        "source": SOURCE,
        "description_snippet": description[:300],
    }
    return apply_money(row)


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    session = requests.Session()
    session.headers.update({**HEADERS, "Content-Type": "application/json", "Accept": "application/json, text/plain, */*"})

    all_rows: list[dict[str, object]] = []
    error = ""

    for page_number in range(max_pages):
        params = {"page": page_number, "size": PAGE_SIZE}
        try:
            response = session.post(API_URL, json=REQUEST_BODY, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            error = str(exc)
            print(f"[{SOURCE}] page={page_number} failed: {exc}", flush=True)
            break

        # Extract the content from data.content if present
        data_obj = payload.get("data") or {}
        content = payload.get("content") or (data_obj.get("content") if isinstance(data_obj, dict) else [])
        if not isinstance(content, list):
            content = []

        page_rows = [_row_from_job(job) for job in content if isinstance(job, dict)]
        all_rows.extend(page_rows)

        _, killed_preview = apply_kill_filter(all_rows)
        log_progress(SOURCE, page_number, len(all_rows), killed_preview)

        # Stop if this was the last page
        if payload.get("last") is True or not content:
            break

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    total_scraped = len(all_rows)
    kept_records, killed_by_filter = apply_kill_filter(all_rows)
    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)

    if total_scraped == 0 and not error:
        error = "NCS API returned 0 jobs for pwdCandidateWelcome=true."

    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path, error)


if __name__ == "__main__":
    print(scrape())
