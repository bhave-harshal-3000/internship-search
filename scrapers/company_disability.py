"""Company disability and inclusive hiring scraper."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

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
from utils.filters import has_inclusion_keyword
from utils.schema import AI_ACCESSIBLE, COMPANY_DISABILITY_PROGRAM, INCLUSIVE_HIRING


SOURCE = "company_disability"
OUTPUT_FILENAME = "company_disability.xlsx"

COMPANY_CONFIG: list[dict[str, str]] = [
    {
        "company": "Amazon",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": COMPANY_DISABILITY_PROGRAM,
        "confidence_level": "HIGH",
    },
    {
        "company": "Microsoft",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": COMPANY_DISABILITY_PROGRAM,
        "confidence_level": "HIGH",
    },
    {
        "company": "Accenture",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "IBM",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": COMPANY_DISABILITY_PROGRAM,
        "confidence_level": "HIGH",
    },
    {
        "company": "Deloitte",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "TCS",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "Infosys",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "Capgemini",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "Cognizant",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "Wipro",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "Google",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": COMPANY_DISABILITY_PROGRAM,
        "confidence_level": "HIGH",
    },
    {
        "company": "EY",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
    {
        "company": "PwC",
        "landing_page": "",
        "strategy": "auto",
        "inclusion_type": INCLUSIVE_HIRING,
        "confidence_level": "MEDIUM",
    },
]


def _format_date(epoch_ms: int | None) -> str:
    if not epoch_ms:
        return ""
    try:
        value = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        return value.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _extract_first(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def _detect_backend(landing_url: str, html: str) -> str:
    combined = f"{landing_url} {html}".lower()
    if "myworkdayjobs.com" in combined or "workdayjobs.com" in combined:
        return "workday"
    if "boards.greenhouse.io" in combined or "greenhouse" in combined:
        return "greenhouse"
    if "jobs.lever.co" in combined or "lever" in combined:
        return "lever"
    if "ashbyhq" in combined:
        return "ashby"
    if "successfactors" in combined:
        return "successfactors"
    return "custom_api"


def _parse_workday_base(workday_url: str) -> tuple[str, str, str]:
    parts = urlsplit(workday_url)
    host = parts.netloc
    path_parts = [part for part in parts.path.split("/") if part]
    site = ""
    if path_parts:
        if path_parts[0].lower() in {"en-us", "en"} and len(path_parts) > 1:
            site = path_parts[1]
        else:
            site = path_parts[0]
    tenant = host.split(".")[0] if host else ""
    return host, tenant, site


def _workday_endpoint(host: str, tenant: str, site: str) -> str:
    return f"https://{host}/wday/cxs/{tenant}/{site}/jobs"


def _rows_from_workday(payload: dict[str, Any], company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    postings = payload.get("jobPostings") or payload.get("searchResults") or []
    if not isinstance(postings, list):
        return []

    rows: list[dict[str, object]] = []
    for job in postings:
        if not isinstance(job, dict):
            continue
        title = clean_text(job.get("title") or job.get("jobTitle"))
        location = clean_text(job.get("locationsText") or job.get("primaryLocation") or "")
        posted_at = clean_text(job.get("postedOn") or job.get("postedDate") or "")
        apply_path = clean_text(job.get("externalPath") or job.get("externalUrl") or "")
        description = clean_text(job.get("shortDescription") or job.get("jobDescription") or "")
        row = {
            "title": title,
            "company": company,
            "location": location,
            "is_remote": "remote" in location.lower() or "work from home" in location.lower(),
            "type": "job",
            "inclusion_type": meta.get("inclusion_type", ""),
            "confidence_level": meta.get("confidence_level", ""),
            "backend_type": meta.get("backend_type", ""),
            "stipend_or_salary_raw": clean_text(job.get("salary") or job.get("salaryText") or ""),
            "duration": "",
            "posted_at": posted_at,
            "apply_url": urljoin(meta.get("base_url", ""), apply_path),
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        if row["title"] or row["apply_url"]:
            rows.append(apply_money(row))
    return rows


def extract_workday_jobs(session: requests.Session, landing_url: str, company: str, meta: dict[str, str], max_pages: int) -> list[dict[str, object]]:
    workday_url = _extract_first([r"https?://[^\s\"']*myworkdayjobs\.com[^\s\"']*"], landing_url)
    if not workday_url:
        workday_url = _extract_first([r"https?://[^\s\"']*myworkdayjobs\.com[^\s\"']*"], meta.get("html", ""))
    if not workday_url:
        return []

    host, tenant, site = _parse_workday_base(workday_url)
    if not host or not tenant or not site:
        return []

    endpoint = _workday_endpoint(host, tenant, site)
    meta["backend_type"] = "workday"
    meta["base_url"] = f"https://{host}"

    rows: list[dict[str, object]] = []
    offset = 0
    page = 0
    total = None
    while page < max_pages:
        page += 1
        payload = request_with_retries(
            session,
            endpoint,
            params={"offset": offset, "limit": 50},
            timeout=30,
        ).json()
        page_rows = _rows_from_workday(payload, company, meta)
        rows.extend(page_rows)

        if total is None:
            total = payload.get("total") or payload.get("totalCount")
        if total is None and payload.get("searchResults"):
            total = len(payload.get("searchResults"))
        if total is not None and offset + 50 >= int(total):
            break
        if not page_rows:
            break
        offset += 50
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    return rows


def _rows_from_greenhouse(payload: dict[str, Any], company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    jobs = payload.get("jobs") or []
    if not isinstance(jobs, list):
        return []

    rows: list[dict[str, object]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        location = ""
        if isinstance(job.get("location"), dict):
            location = clean_text(job.get("location", {}).get("name"))
        description = clean_text(job.get("content") or "")
        row = {
            "title": clean_text(job.get("title")),
            "company": company,
            "location": location,
            "is_remote": "remote" in location.lower() or "work from home" in location.lower(),
            "type": "job",
            "inclusion_type": meta.get("inclusion_type", ""),
            "confidence_level": meta.get("confidence_level", ""),
            "backend_type": meta.get("backend_type", ""),
            "stipend_or_salary_raw": "",
            "duration": "",
            "posted_at": clean_text(job.get("updated_at") or job.get("created_at") or ""),
            "apply_url": clean_text(job.get("absolute_url")),
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        if row["title"] or row["apply_url"]:
            rows.append(apply_money(row))
    return rows


def extract_greenhouse_jobs(session: requests.Session, landing_url: str, company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    slug = ""
    for value in [landing_url, meta.get("html", "")]:
        slug = re.search(r"boards\.greenhouse\.io/([a-z0-9_-]+)", value, flags=re.IGNORECASE)
        if slug:
            slug = slug.group(1)
            break
        slug = ""
    if not slug:
        return []
    meta["backend_type"] = "greenhouse"
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    payload = request_with_retries(session, url, params={"content": "true"}, timeout=30).json()
    return _rows_from_greenhouse(payload, company, meta)


def _rows_from_lever(payload: list[dict[str, Any]], company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for job in payload:
        location = ""
        categories = job.get("categories") or {}
        if isinstance(categories, dict):
            location = clean_text(categories.get("location"))
        description = clean_text(job.get("description") or "")
        requirements = clean_text(" ".join(job.get("lists", []) or []))
        row = {
            "title": clean_text(job.get("text")),
            "company": company,
            "location": location,
            "is_remote": "remote" in location.lower() or "work from home" in location.lower(),
            "type": "job",
            "inclusion_type": meta.get("inclusion_type", ""),
            "confidence_level": meta.get("confidence_level", ""),
            "backend_type": meta.get("backend_type", ""),
            "stipend_or_salary_raw": "",
            "duration": "",
            "posted_at": _format_date(job.get("createdAt")),
            "apply_url": clean_text(job.get("hostedUrl") or job.get("applyUrl") or ""),
            "source": SOURCE,
            "description_snippet": f"{description} {requirements}"[:300],
        }
        if row["title"] or row["apply_url"]:
            rows.append(apply_money(row))
    return rows


def extract_lever_jobs(session: requests.Session, landing_url: str, company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    org = ""
    for value in [landing_url, meta.get("html", "")]:
        match = re.search(r"jobs\.lever\.co/([a-z0-9_-]+)", value, flags=re.IGNORECASE)
        if match:
            org = match.group(1)
            break
    if not org:
        return []
    meta["backend_type"] = "lever"
    url = f"https://api.lever.co/v0/postings/{org}"
    payload = request_with_retries(session, url, params={"mode": "json"}, timeout=30).json()
    if not isinstance(payload, list):
        return []
    return _rows_from_lever(payload, company, meta)


def _rows_from_ashby(payload: dict[str, Any], company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    jobs = payload.get("jobs") or []
    if not isinstance(jobs, list):
        return []
    rows: list[dict[str, object]] = []
    for job in jobs:
        location = clean_text(job.get("locationName") or job.get("location") or "")
        description = clean_text(job.get("descriptionHtml") or job.get("descriptionPlainText") or "")
        row = {
            "title": clean_text(job.get("title")),
            "company": company,
            "location": location,
            "is_remote": "remote" in location.lower() or "work from home" in location.lower(),
            "type": "job",
            "inclusion_type": meta.get("inclusion_type", ""),
            "confidence_level": meta.get("confidence_level", ""),
            "backend_type": meta.get("backend_type", ""),
            "stipend_or_salary_raw": "",
            "duration": "",
            "posted_at": clean_text(job.get("publishedAt") or ""),
            "apply_url": clean_text(job.get("applyUrl") or job.get("jobUrl") or ""),
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        if row["title"] or row["apply_url"]:
            rows.append(apply_money(row))
    return rows


def extract_ashby_jobs(session: requests.Session, landing_url: str, company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    org = ""
    for value in [landing_url, meta.get("html", "")]:
        match = re.search(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", value, flags=re.IGNORECASE)
        if match:
            org = match.group(1)
            break
    if not org:
        return []
    meta["backend_type"] = "ashby"
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
    payload = request_with_retries(session, url, timeout=30).json()
    if not isinstance(payload, dict):
        return []
    return _rows_from_ashby(payload, company, meta)


def extract_custom_html(session: requests.Session, landing_url: str, company: str, meta: dict[str, str]) -> list[dict[str, object]]:
    response = request_with_retries(session, landing_url, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select("a[href]")
    rows: list[dict[str, object]] = []
    for card in cards:
        href = clean_text(card.get("href"))
        if not href or "job" not in href.lower():
            continue
        title = clean_text(card.get_text(" ", strip=True))
        if not title:
            continue
        row = {
            "title": title,
            "company": company,
            "location": "",
            "is_remote": False,
            "type": "job",
            "inclusion_type": meta.get("inclusion_type", ""),
            "confidence_level": meta.get("confidence_level", ""),
            "backend_type": meta.get("backend_type", "custom_api"),
            "stipend_or_salary_raw": "",
            "duration": "",
            "posted_at": "",
            "apply_url": urljoin(landing_url, href),
            "source": SOURCE,
            "description_snippet": "",
        }
        rows.append(apply_money(row))
    return rows


def _infer_inclusion(landing_url: str, html: str) -> tuple[str, str]:
    text = f"{landing_url} {html}".lower()
    if any(token in text for token in ["disability", "pwd", "pwbd", "divyang", "deaf", "accessibility"]):
        return COMPANY_DISABILITY_PROGRAM, "HIGH"
    if any(token in text for token in ["inclusive", "neurodivers", "accessibility"]):
        return INCLUSIVE_HIRING, "MEDIUM"
    return AI_ACCESSIBLE, "LOW"


def _build_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    filtered: list[dict[str, object]] = []
    for row in rows:
        if not has_inclusion_keyword(row.get("title"), row.get("description_snippet")):
            if row.get("confidence_level") == "LOW":
                continue
        filtered.append(row)
    return filtered


def _fetch_html(session: requests.Session, url: str) -> str:
    response = request_with_retries(session, url, timeout=30)
    return response.text


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    session = requests.Session()
    session.headers.update(HEADERS)

    total_scraped = 0
    killed_by_filter = 0
    kept_records: list[dict[str, object]] = []

    for config in COMPANY_CONFIG:
        company = config.get("company", "").strip()
        landing_page = clean_text(config.get("landing_page"))
        if not landing_page:
            print(f"[{company}] missing landing_page; skipping", flush=True)
            continue

        try:
            html = _fetch_html(session, landing_page)
            backend = config.get("strategy") or _detect_backend(landing_page, html)
            inclusion_type = config.get("inclusion_type") or ""
            confidence_level = config.get("confidence_level") or ""
            if not inclusion_type or not confidence_level:
                inferred_type, inferred_confidence = _infer_inclusion(landing_page, html)
                inclusion_type = inclusion_type or inferred_type
                confidence_level = confidence_level or inferred_confidence

            meta = {
                "inclusion_type": inclusion_type,
                "confidence_level": confidence_level,
                "backend_type": backend,
                "html": html,
            }

            if backend == "workday":
                rows = extract_workday_jobs(session, landing_page, company, meta, max_pages)
            elif backend == "greenhouse":
                rows = extract_greenhouse_jobs(session, landing_page, company, meta)
            elif backend == "lever":
                rows = extract_lever_jobs(session, landing_page, company, meta)
            elif backend == "ashby":
                rows = extract_ashby_jobs(session, landing_page, company, meta)
            else:
                rows = extract_custom_html(session, landing_page, company, meta)

            rows = _build_rows(rows)
            total_scraped += len(rows)
            kept_page, killed_page = apply_kill_filter(rows)
            killed_by_filter += killed_page
            kept_records.extend(kept_page)
            log_progress(company or SOURCE, 1, total_scraped, killed_by_filter)
            time.sleep(SLEEP_BETWEEN_REQUESTS)
        except Exception as exc:  # noqa: BLE001 - continue on per-company failures
            print(f"[{company}] failed: {exc}", flush=True)
            continue

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path)


if __name__ == "__main__":
    print(scrape())
