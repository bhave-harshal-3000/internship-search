"""Naukri PWD job scraper."""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from scrapers.common import (
    apply_kill_filter,
    apply_money,
    clean_text,
    dedupe_by_apply_url,
    log_progress,
    make_stats,
    output_file,
)
from utils.config import MAX_PAGES, SLEEP_BETWEEN_REQUESTS, USER_AGENT
from utils.excel_writer import save_excel
from utils.schema import EXPLICIT_PWD


SOURCE = "naukri"
OUTPUT_FILENAME = "naukri.xlsx"
PAGE_LOAD_DELAY_MS = 8000
SCROLL_STEP_DELAY_MS = 900
POST_SCROLL_DELAY_MS = 3000
NAUKRI_SLEEP_BETWEEN_PAGES = max(SLEEP_BETWEEN_REQUESTS, 6)
START_URLS = [
    "https://www.naukri.com/jobs-in-india?candidateType=pw_disability",
    "https://www.naukri.com/jobs-in-india?candidateType=pw_disability&jobType=WFH",
]


def _page_url(base_url: str, page_number: int) -> str:
    if page_number == 1:
        return base_url
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if re.search(r"-\d+$", path):
        path = re.sub(r"-\d+$", f"-{page_number}", path)
    else:
        path = f"{path}-{page_number}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _apply_stealth(page) -> None:
    """Apply stealth helpers when playwright-stealth is installed."""

    try:
        from playwright_stealth import stealth_sync
    except ImportError:
        return

    try:
        stealth_sync(page)
        print(f"[{SOURCE}] playwright-stealth applied", flush=True)
    except Exception as exc:  # noqa: BLE001 - stealth is best-effort
        print(f"[{SOURCE}] playwright-stealth skipped: {exc}", flush=True)


def _install_browser_patches(context) -> None:
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = window.chrome || { runtime: {} };
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
          window.navigator.permissions.query = (parameters) => (
            parameters && parameters.name === 'notifications'
              ? Promise.resolve({ state: Notification.permission })
              : originalQuery(parameters)
          );
        }
        """
    )


def _human_scroll(page) -> None:
    for _ in range(5):
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(SCROLL_STEP_DELAY_MS)
    page.mouse.wheel(0, -700)
    page.wait_for_timeout(POST_SCROLL_DELAY_MS)


def _extract_cards(page, source_url: str, listing_type: str) -> list[dict[str, object]]:
    jobs = page.evaluate(
        """
        () => {
          const selectors = [
            'article',
            'div.srp-jobtuple-wrapper',
            'div.jobTuple',
            'div[class*="jobTuple"]',
            'div[class*="srp-jobtuple"]'
          ];
          const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
          const pickText = (el, sels) => {
            for (const sel of sels) {
              const node = el.querySelector(sel);
              const text = node && node.innerText ? node.innerText.trim() : '';
              if (text) return text;
            }
            return '';
          };
          const pickHref = (el) => {
            const node = el.querySelector('a[href*="job-listings"], a.title, a[href]');
            return node && node.href ? node.href : '';
          };
          const records = nodes.map((el) => {
            const text = el.innerText || '';
            const title = pickText(el, ['a.title', 'a[class*="title"]', 'h2 a', 'h3 a', 'a[href*="job-listings"]']);
            return {
              title,
              company: pickText(el, ['a.comp-name', 'a[class*="comp-name"]', '.companyName', '[class*="company"]']),
              location: pickText(el, ['.locWdth', '.location', '[class*="loc"]']),
              salary: pickText(el, ['.sal-wrap', '.salary', '[class*="sal"]']),
              posted_at: pickText(el, ['.job-post-day', '.postedDate', '[class*="post"]']),
              description: pickText(el, ['.job-desc', '.job-description', '[class*="job-desc"]']) || text,
              apply_url: pickHref(el),
              text
            };
          }).filter((job) => job.title || job.apply_url);

          if (records.length) return records;

          return Array.from(document.querySelectorAll('a[href*="job-listings"]')).map((a) => ({
            title: a.innerText ? a.innerText.trim() : '',
            company: '',
            location: '',
            salary: '',
            posted_at: '',
            description: '',
            apply_url: a.href,
            text: a.innerText || ''
          })).filter((job) => job.title || job.apply_url);
        }
        """
    )

    rows: list[dict[str, object]] = []
    is_wfh_url = "jobType=WFH" in source_url
    for job in jobs:
        location = clean_text(job.get("location"))
        description = clean_text(job.get("description"))[:300]
        is_remote = is_wfh_url or any(token in location.lower() for token in ["remote", "wfh", "work from home"])
        record = {
            "title": clean_text(job.get("title")),
            "company": clean_text(job.get("company")),
            "location": location,
            "is_remote": bool(is_remote),
            "type": listing_type,
            "inclusion_type": EXPLICIT_PWD,
            "stipend_or_salary_raw": clean_text(job.get("salary")),
            "duration": "",
            "posted_at": clean_text(job.get("posted_at")),
            "apply_url": clean_text(job.get("apply_url")),
            "source": SOURCE,
            "description_snippet": description,
        }
        rows.append(apply_money(record))
    return rows


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    output_path = output_file(output_dir, OUTPUT_FILENAME)
    total_scraped = 0
    killed_by_filter = 0
    kept_records: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-IN,en;q=0.9",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        _install_browser_patches(context)
        page = context.new_page()
        _apply_stealth(page)

        try:
            for start_url in START_URLS:
                for page_number in range(1, max_pages + 1):
                    url = _page_url(start_url, page_number)
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(PAGE_LOAD_DELAY_MS)
                        _human_scroll(page)
                        page_records = _extract_cards(page, start_url, "job")
                    except Exception as exc:  # noqa: BLE001 - save whatever was collected
                        print(f"[{SOURCE}] page={page_number} failed: {exc}", flush=True)
                        break
                    if not page_records and page_number > 1:
                        break

                    total_scraped += len(page_records)
                    kept_page, killed_page = apply_kill_filter(page_records)
                    killed_by_filter += killed_page
                    kept_records.extend(kept_page)
                    log_progress(SOURCE, page_number, total_scraped, killed_by_filter)

                    if not page_records:
                        break
                    time.sleep(NAUKRI_SLEEP_BETWEEN_PAGES)
        finally:
            context.close()
            browser.close()

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    error = "No job cards found; Naukri may be blocking headless scraping or changed its search rendering." if total_scraped == 0 else ""
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path, error)


if __name__ == "__main__":
    print(scrape())
