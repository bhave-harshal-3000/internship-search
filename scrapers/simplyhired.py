"""SimplyHired India keyword-discovery scraper with strict PWD filtering."""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag

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
    soup_text,
)
from utils.config import MAX_PAGES, SLEEP_BETWEEN_REQUESTS
from utils.excel_writer import save_excel
from utils.filters import has_inclusion_keyword
from utils.schema import KEYWORD_PWD


SOURCE = "simplyhired"
OUTPUT_FILENAME = "simplyhired.xlsx"
BASE_URL = "https://www.simplyhired.co.in/search"

# Search-discovery queries only. A row is saved only if the listing text itself
# contains an explicit inclusion keyword.
SEARCHES = [
    {"q": "disability deaf speech impaired", "l": "navi mumbai, maharashtra"},
    {"q": "deaf hearing impaired speech impaired internship", "l": "india"},
]


def _page_url(search: dict[str, str], page_number: int) -> str:
    params = dict(search)
    if page_number > 1:
        params["pn"] = str(page_number)
    return f"{BASE_URL}?{urlencode(params)}"


def _is_remote(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["remote", "work from home", "wfh"])


def _listing_type(text: str) -> str:
    return "internship" if "intern" in text.lower() else "job"


def _salary_from_text(text: str) -> str:
    match = re.search(
        r"(₹\s*[\d,]+(?:\.\d+)?(?:\s*(?:-|to)\s*₹?\s*[\d,]+(?:\.\d+)?)?\s*(?:a year|per year|/year|a month|per month|/month)?)",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(match.group(1)) if match else ""


def _posted_from_text(text: str) -> str:
    match = re.search(r"\b(\d+\s*(?:d|day|days|h|hour|hours)\s*ago|today|just posted)\b", text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""


def _split_company_location(lines: list[str]) -> tuple[str, str]:
    for line in lines[1:5]:
        if " — " in line:
            company, location = line.split(" — ", 1)
            return clean_text(company), clean_text(location)
        if " - " in line:
            company, location = line.split(" - ", 1)
            return clean_text(company), clean_text(location)
    return "", ""


def _card_from_link(link: Tag) -> Tag:
    for parent_name in ["li", "article", "div"]:
        parent = link.find_parent(parent_name)
        if parent and len(soup_text(parent)) > len(soup_text(link)) + 20:
            return parent
    return link


def _extract_cards(html: str, page_url: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select(
        'a[href*="/job/"], a[data-testid*="job"], a[href*="jobkey"], a[href*="jk="]'
    )

    rows: list[dict[str, object]] = []
    seen_texts: set[str] = set()
    for link in links:
        title = clean_text(link.get_text(" ", strip=True))
        if len(title) < 3:
            continue

        card = _card_from_link(link)
        text = soup_text(card)
        if text in seen_texts:
            continue
        seen_texts.add(text)

        if not has_inclusion_keyword(title, text):
            continue

        lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
        company, location = _split_company_location(lines)
        href = str(link.get("href") or "")
        apply_url = requests.compat.urljoin(page_url, href)
        salary = _salary_from_text(text)
        row = {
            "title": title,
            "company": company,
            "location": location,
            "is_remote": _is_remote(text),
            "type": _listing_type(text),
            "inclusion_type": KEYWORD_PWD,
            "stipend_or_salary_raw": salary,
            "duration": "",
            "posted_at": _posted_from_text(text),
            "apply_url": apply_url,
            "source": SOURCE,
            "description_snippet": text[:300],
        }
        rows.append(apply_money(row))
    return rows


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    session = requests.Session()
    session.headers.update(HEADERS)
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    total_seen = 0
    killed_by_filter = 0
    kept_records: list[dict[str, object]] = []
    last_error = ""

    for search in SEARCHES:
        for page_number in range(1, max_pages + 1):
            url = _page_url(search, page_number)
            try:
                response = request_with_retries(session, url, timeout=45)
                page_records = _extract_cards(response.text, response.url)
            except Exception as exc:  # noqa: BLE001 - keep source independent
                last_error = str(exc)
                print(f"[{SOURCE}] page={page_number} failed: {exc}", flush=True)
                break

            total_seen += len(page_records)
            kept_page, killed_page = apply_kill_filter(page_records)
            killed_by_filter += killed_page
            kept_records.extend(kept_page)
            log_progress(SOURCE, page_number, total_seen, killed_by_filter)

            if not page_records and page_number > 1:
                break
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    error = last_error if total_seen == 0 and last_error else ""
    return make_stats(SOURCE, total_seen, killed_by_filter, len(final_records), output_path, error)


if __name__ == "__main__":
    print(scrape())

