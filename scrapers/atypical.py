"""Atypical Advantage disability job scraper."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

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


SOURCE = "atypical"
OUTPUT_FILENAME = "atypical.xlsx"
LISTING_URL = (
    "https://atypicaladvantage.in/find-a-job?type=all&state=all&location=all"
    "&company=all&departments%5B%5D=&industries%5B%5D=&qualifications%5B%5D="
    "&disabilities%5B%5D=10&disabilities%5B%5D=11"
)


def _line_extract(lines: list[str], label: str) -> str:
    label_clean = label.strip(":").lower()
    for index, line in enumerate(lines):
        line_clean = line.strip().lower().rstrip(":")
        if line_clean == label_clean or line_clean.startswith(label_clean):
            for next_line in lines[index + 1 : index + 4]:
                value = next_line.strip()
                if value and value.lower() not in {"not mentioned", "n/a", "na"}:
                    return value
    return ""


def _pick_apply_link(soup: BeautifulSoup, base_url: str) -> str:
    for anchor in soup.select("a.btn_purple"):
        text = clean_text(anchor.get_text(" ", strip=True)).lower()
        if "apply" in text:
            href = clean_text(anchor.get("href"))
            if href and href != base_url and "login" not in href:
                return urljoin(base_url, href)
    return base_url


def _clean_description(desc_div: BeautifulSoup | None) -> str:
    if not desc_div:
        return ""
    for item in desc_div.find_all("li"):
        item.insert_before("- ")
        item.append(" ")
    return clean_text(desc_div.get_text(" ", strip=True))


def _parse_detail(session: requests.Session, job_url: str) -> dict[str, str]:
    response = request_with_retries(session, job_url, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")

    text_lines = [
        line
        for line in soup.get_text(separator="\n", strip=True).split("\n")
        if line.strip()
    ]

    details = {
        "description": _clean_description(soup.find("div", class_="description-content")),
        "employment_type": _line_extract(text_lines, "Employment Type"),
        "work_modality": _line_extract(text_lines, "Work modality"),
        "location": _line_extract(text_lines, "Location"),
        "stipend": _line_extract(text_lines, "Stipend") or _line_extract(text_lines, "Salary"),
    }
    details["apply_url"] = _pick_apply_link(soup, job_url)
    return details


def _listing_cards(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_="card-body pt-5 pt-md-3")
    listings: list[dict[str, str]] = []
    for card in cards:
        role_elem = card.find("div", class_="font-weight-bold")
        company_elem = card.find("span", style=lambda value: value and "#225c80" in value)
        location_elem = card.find("div", class_="col-md-3 text-center d-none d-md-block")
        link_elem = card.find("a", class_="btn_purple")

        if not link_elem or not link_elem.get("href"):
            continue

        listing = {
            "title": clean_text(role_elem.get_text(" ", strip=True) if role_elem else ""),
            "company": clean_text(company_elem.get_text(" ", strip=True) if company_elem else ""),
            "location": clean_text(location_elem.get_text(" ", strip=True) if location_elem else ""),
            "job_url": clean_text(link_elem.get("href")),
        }
        listings.append(listing)
    return listings


def _infer_type(title: str, employment_type: str) -> str:
    text = f"{title} {employment_type}".lower()
    return "internship" if "intern" in text else "job"


def _is_remote(*parts: str) -> bool:
    text = " ".join(part for part in parts if part).lower()
    return any(token in text for token in ["remote", "work from home", "wfh"])


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    session = requests.Session()
    session.headers.update(HEADERS)

    response = request_with_retries(session, LISTING_URL, timeout=30)
    listings = _listing_cards(response.text)

    total_scraped = 0
    killed_by_filter = 0
    rows: list[dict[str, object]] = []

    for index, listing in enumerate(listings, start=1):
        job_url = listing.get("job_url", "")
        if not job_url:
            continue
        try:
            detail = _parse_detail(session, job_url)
        except Exception as exc:  # noqa: BLE001 - keep partial info when detail fails
            print(f"[{SOURCE}] detail failed: {job_url} ({exc})", flush=True)
            detail = {"description": "", "employment_type": "", "work_modality": "", "location": "", "stipend": "", "apply_url": job_url}

        location = clean_text(detail.get("location") or listing.get("location") or "")
        description = clean_text(detail.get("description"))
        employment_type = clean_text(detail.get("employment_type"))
        work_modality = clean_text(detail.get("work_modality"))

        row = {
            "title": clean_text(listing.get("title")),
            "company": clean_text(listing.get("company")) or "Atypical Advantage",
            "location": location or "",
            "is_remote": _is_remote(location, work_modality, description),
            "type": _infer_type(listing.get("title", ""), employment_type),
            "inclusion_type": EXPLICIT_PWD,
            "confidence_level": "HIGH",
            "backend_type": "custom_api",
            "stipend_or_salary_raw": clean_text(detail.get("stipend")),
            "duration": "",
            "posted_at": "",
            "apply_url": clean_text(detail.get("apply_url")) or job_url,
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        rows.append(apply_money(row))

        total_scraped = len(rows)
        kept_preview, killed_preview = apply_kill_filter(rows)
        killed_by_filter = killed_preview
        log_progress(SOURCE, index, total_scraped, killed_by_filter)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    kept_records, killed_by_filter = apply_kill_filter(rows)
    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    return make_stats(SOURCE, len(rows), killed_by_filter, len(final_records), output_path)


if __name__ == "__main__":
    print(scrape())
