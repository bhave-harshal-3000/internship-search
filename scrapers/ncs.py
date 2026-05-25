"""National Career Service PWD job scraper."""

from __future__ import annotations

import time
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.common import (
    HEADERS,
    absolute_url,
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
from utils.config import MAX_PAGES, SLEEP_BETWEEN_REQUESTS, USER_AGENT
from utils.excel_writer import save_excel
from utils.schema import EXPLICIT_PWD


SOURCE = "ncs"
OUTPUT_FILENAME = "ncs.xlsx"
BASE_URL = "https://betacloud.ncs.gov.in"
LISTING_URL = f"{BASE_URL}/job-listing"
FILTER_PARAMS = {"sortBy": "NEWEST", "pwdCandidateWelcome": "true"}


def _value(payload: dict[str, Any], names: list[str]) -> str:
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            if isinstance(value, (list, tuple)):
                return clean_text("; ".join(str(item) for item in value))
            return clean_text(value)
    return ""


def _rows_from_json(payload: Any) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return

        title = _value(value, ["jobTitle", "title", "jobName", "vacancyName", "designation"])
        company = _value(value, ["companyName", "employerName", "organisationName", "organizationName", "orgName"])
        location = _value(value, ["location", "jobLocation", "locations", "city", "state"])
        description = _value(value, ["jobDescription", "description", "jobDesc", "skills", "skillRequired"])
        salary = _value(value, ["salary", "salaryRange", "salaryText", "ctc"])
        if not salary:
            min_salary = _value(value, ["minSalary", "minimumSalary"])
            max_salary = _value(value, ["maxSalary", "maximumSalary"])
            salary = " - ".join(item for item in [min_salary, max_salary] if item)
        posted_at = _value(value, ["postedOn", "postedDate", "createdDate", "datePosted"])
        apply_url = _value(value, ["applyUrl", "jobUrl", "url", "detailUrl"])
        if apply_url:
            apply_url = urljoin(BASE_URL, apply_url)
        else:
            job_id = _value(value, ["jobId", "id", "vacancyId"])
            apply_url = f"{BASE_URL}/job-details/{job_id}" if job_id else ""

        if title and (company or location or description or salary):
            row = {
                "title": title,
                "company": company,
                "location": location,
                "is_remote": any(token in f"{location} {description}".lower() for token in ["work from home", "remote", "wfh"]),
                "type": "job",
                "inclusion_type": EXPLICIT_PWD,
                "stipend_or_salary_raw": salary,
                "duration": "",
                "posted_at": posted_at,
                "apply_url": apply_url,
                "source": SOURCE,
                "description_snippet": description[:300],
            }
            rows.append(apply_money(row))

        for child in value.values():
            walk(child)

    walk(payload)
    return rows


def _rows_from_html(html: str, page_url: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(
        "article, div[class*='job-card'], div[class*='JobCard'], "
        "div[class*='jobCard'], div[class*='search-result'], li[class*='job']"
    )
    rows: list[dict[str, object]] = []
    for card in cards:
        card_text = soup_text(card)
        title = first_text(card, ["h1", "h2", "h3", "h4", "a"])
        if not title or len(card_text) < 40:
            continue
        company = first_text(card, ["[class*='company']", "[class*='employer']", "[class*='organization']"])
        location = first_text(card, ["[class*='location']", "[class*='city']"])
        salary = first_text(card, ["[class*='salary']", "[class*='ctc']"])
        posted_at = first_text(card, ["[class*='posted']", "[class*='date']"])
        apply_url = first_href(card, page_url, ["a[href]"])
        row = {
            "title": title,
            "company": company,
            "location": location,
            "is_remote": any(token in card_text.lower() for token in ["work from home", "remote", "wfh"]),
            "type": "job",
            "inclusion_type": EXPLICIT_PWD,
            "stipend_or_salary_raw": salary,
            "duration": "",
            "posted_at": posted_at,
            "apply_url": apply_url,
            "source": SOURCE,
            "description_snippet": card_text[:300],
        }
        rows.append(apply_money(row))

    if rows:
        return rows

    # The older NCS page is server-rendered text with stable labels.
    text = soup_text(soup)
    pattern = re.compile(
        r"#####\s*(?P<title>.*?)\s+Company:\s*(?P<company>.*?)\s+"
        r"Job Location:\s*(?P<location>.*?)\s+Salary:\s*(?P<salary>.*?)\s+"
        r"(?:Skill Required:\s*(?P<skills>.*?)\s+)?Job Description:\s*(?P<description>.*?)\s+"
        r"Posted On\s*(?P<posted>\d{1,2}/\d{1,2}/\d{4})",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        title = clean_text(match.group("title"))
        company = clean_text(match.group("company"))
        location = clean_text(match.group("location"))
        salary = clean_text(match.group("salary"))
        skills = clean_text(match.group("skills"))
        description = clean_text(f"{skills} {match.group('description')}")
        posted_at = clean_text(match.group("posted"))
        if not title:
            continue
        row = {
            "title": title,
            "company": company,
            "location": location,
            "is_remote": any(token in f"{location} {description}".lower() for token in ["work from home", "remote", "wfh"]),
            "type": "job",
            "inclusion_type": EXPLICIT_PWD,
            "stipend_or_salary_raw": salary,
            "duration": "",
            "posted_at": posted_at,
            "apply_url": page_url,
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        rows.append(apply_money(row))
    return rows


def _requests_scrape(max_pages: int) -> list[dict[str, object]]:
    session = requests.Session()
    session.headers.update(HEADERS)
    rows: list[dict[str, object]] = []

    for page_number in range(1, max_pages + 1):
        params = {**FILTER_PARAMS, "page": page_number}
        response = request_with_retries(session, LISTING_URL, params=params)
        page_rows: list[dict[str, object]] = []
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            page_rows = _rows_from_json(response.json())
        else:
            page_rows = _rows_from_html(response.text, response.url)

        rows.extend(page_rows)
        _, killed_preview = apply_kill_filter(rows)
        log_progress(f"{SOURCE}:requests", page_number, len(rows), killed_preview)
        if not page_rows and page_number > 1:
            break
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    return rows


def _dom_rows(page) -> list[dict[str, object]]:
    cards = page.evaluate(
        """
        () => {
          const nodes = Array.from(document.querySelectorAll(
            'article, div[class*="job-card"], div[class*="JobCard"], div[class*="jobCard"], li[class*="job"]'
          ));
          return nodes.map((el) => ({
            text: el.innerText || '',
            href: (el.querySelector('a[href]') || {}).href || ''
          })).filter((item) => item.text && item.text.length > 40);
        }
        """
    )
    rows: list[dict[str, object]] = []
    for card in cards:
        text = clean_text(card.get("text"))
        title = clean_text(text.split("\n")[0] if "\n" in text else text[:100])
        row = {
            "title": title,
            "company": "",
            "location": "",
            "is_remote": any(token in text.lower() for token in ["work from home", "remote", "wfh"]),
            "type": "job",
            "inclusion_type": EXPLICIT_PWD,
            "stipend_or_salary_raw": "",
            "duration": "",
            "posted_at": "",
            "apply_url": absolute_url(BASE_URL, card.get("href")),
            "source": SOURCE,
            "description_snippet": text[:300],
        }
        rows.append(apply_money(row))
    return rows


def _playwright_scrape(max_pages: int) -> list[dict[str, object]]:
    from playwright.sync_api import sync_playwright

    json_payloads: list[Any] = []

    def capture_response(response) -> None:
        url = response.url.lower()
        if "job" not in url:
            return
        try:
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                json_payloads.append(response.json())
        except Exception:
            return

    rows: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.on("response", capture_response)
        try:
            page.goto(f"{LISTING_URL}?sortBy=NEWEST&pwdCandidateWelcome=true", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            for page_number in range(1, max_pages + 1):
                for payload in json_payloads:
                    rows.extend(_rows_from_json(payload))
                json_payloads.clear()
                if not rows:
                    rows.extend(_dom_rows(page))
                _, killed_preview = apply_kill_filter(rows)
                log_progress(f"{SOURCE}:playwright", page_number, len(rows), killed_preview)

                next_button = page.get_by_role("button", name="Next")
                if next_button.count() == 0 or page_number >= max_pages:
                    break
                try:
                    next_button.first.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    time.sleep(SLEEP_BETWEEN_REQUESTS)
                except Exception:
                    break
        finally:
            context.close()
            browser.close()

    return rows


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    all_records: list[dict[str, object]]

    try:
        all_records = _requests_scrape(max_pages)
    except Exception as exc:
        print(f"[{SOURCE}] requests path failed: {exc}", flush=True)
        all_records = []

    if not all_records:
        try:
            all_records = _playwright_scrape(max_pages)
        except Exception as exc:
            print(f"[{SOURCE}] Playwright fallback failed: {exc}", flush=True)
            all_records = []

    total_scraped = len(all_records)
    kept_records, killed_by_filter = apply_kill_filter(all_records)
    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    log_progress(SOURCE, max_pages, total_scraped, killed_by_filter)
    error = "NCS pwdCandidateWelcome=true filter returned 0 jobs." if total_scraped == 0 else ""
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path, error)


if __name__ == "__main__":
    print(scrape())
