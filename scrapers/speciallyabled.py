"""SpeciallyAbledJobs.com scraper."""

from __future__ import annotations

import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

from scrapers.common import (
    HEADERS,
    apply_kill_filter,
    apply_money,
    clean_text,
    dedupe_by_apply_url,
    first_href,
    first_text,
    log_progress,
    make_stats,
    output_file,
    request_with_retries,
    soup_text,
)
from utils.config import MAX_PAGES, SLEEP_BETWEEN_REQUESTS
from utils.excel_writer import save_excel
from utils.schema import EXPLICIT_PWD


SOURCE = "speciallyabled"
OUTPUT_FILENAME = "speciallyabled.xlsx"
BASE_URL = "https://www.speciallyabledjobs.com"


def _candidate_urls(page_number: int) -> list[str]:
    if page_number == 1:
        return [
            BASE_URL,
            f"{BASE_URL}/jobs/",
            f"{BASE_URL}/job-listings/",
            f"{BASE_URL}/jobs-listing/",
        ]
    return [
        f"{BASE_URL}/jobs/page/{page_number}/",
        f"{BASE_URL}/job-listings/page/{page_number}/",
        f"{BASE_URL}/jobs/?paged={page_number}",
        f"{BASE_URL}/job-listings/?paged={page_number}",
        f"{BASE_URL}/?paged={page_number}",
    ]


def _salary_from_text(text: str) -> str:
    match = re.search(
        r"(?:salary|stipend|ctc|pay)\s*:?\s*([₹rsinr0-9,\.\s\-to/lpakhmonthannumper]+)",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(match.group(1)) if match else ""


def _looks_like_job_card(card: Tag) -> bool:
    text = soup_text(card).lower()
    if len(text) < 20:
        return False
    return any(token in text for token in ["job", "apply", "salary", "location", "company", "experience"])


def _rows_from_html(html: str, page_url: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        ".job_listing",
        ".job-listing",
        ".job-card",
        ".job-item",
        ".listing",
        "article",
        "li[class*='job']",
        "div[class*='job']",
    ]
    cards: list[Tag] = []
    for selector in selectors:
        for card in soup.select(selector):
            if _looks_like_job_card(card):
                cards.append(card)

    rows: list[dict[str, object]] = []
    seen_card_text: set[str] = set()
    for card in cards:
        text = soup_text(card)
        if text in seen_card_text:
            continue
        seen_card_text.add(text)
        title = first_text(card, ["h1", "h2", "h3", "h4", ".title", ".job-title", "a"])
        if not title:
            continue
        company = first_text(card, [".company", ".company-name", "[class*='company']", "[class*='employer']"])
        location = first_text(card, [".location", ".job-location", "[class*='location']"])
        salary = first_text(card, [".salary", ".job-salary", "[class*='salary']"]) or _salary_from_text(text)
        posted_at = first_text(card, [".date", ".posted", "[class*='posted']", "time"])
        apply_url = first_href(card, page_url, ["a[href*='job']", "a[href*='career']", "a[href]"])
        row = {
            "title": title,
            "company": company,
            "location": location,
            "is_remote": any(token in text.lower() for token in ["work from home", "remote", "wfh"]),
            "type": "job",
            "inclusion_type": EXPLICIT_PWD,
            "stipend_or_salary_raw": salary,
            "duration": "",
            "posted_at": posted_at,
            "apply_url": apply_url,
            "source": SOURCE,
            "description_snippet": text[:300],
        }
        rows.append(apply_money(row))

    if rows:
        return rows

    for link in soup.select("a[href]"):
        title = soup_text(link)
        href = str(link.get("href") or "")
        if len(title) < 5:
            continue
        if not any(token in href.lower() for token in ["job", "career", "vacancy", "opening"]):
            continue
        row = {
            "title": title,
            "company": "",
            "location": "",
            "is_remote": "remote" in title.lower() or "work from home" in title.lower(),
            "type": "job",
            "inclusion_type": EXPLICIT_PWD,
            "stipend_or_salary_raw": "",
            "duration": "",
            "posted_at": "",
            "apply_url": requests.compat.urljoin(page_url, href),
            "source": SOURCE,
            "description_snippet": title[:300],
        }
        rows.append(apply_money(row))
    return rows


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    session = requests.Session()
    session.headers.update(HEADERS)
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    total_scraped = 0
    killed_by_filter = 0
    kept_records: list[dict[str, object]] = []
    last_error = ""

    for page_number in range(1, max_pages + 1):
        page_rows: list[dict[str, object]] = []
        last_error = ""
        for url in _candidate_urls(page_number):
            try:
                response = request_with_retries(session, url)
                page_rows = _rows_from_html(response.text, response.url)
                if page_rows:
                    break
            except Exception as exc:  # noqa: BLE001 - try the next likely page shape
                last_error = str(exc)
                continue

        if not page_rows:
            if last_error:
                print(f"[{SOURCE}] page={page_number} no rows; last error: {last_error}", flush=True)
            if page_number > 1:
                break

        total_scraped += len(page_rows)
        kept_page, killed_page = apply_kill_filter(page_rows)
        killed_by_filter += killed_page
        kept_records.extend(kept_page)
        log_progress(SOURCE, page_number, total_scraped, killed_by_filter)

        if not page_rows:
            break
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    error = last_error if total_scraped == 0 else ""
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path, error)


if __name__ == "__main__":
    print(scrape())
