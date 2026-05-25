"""Indeed India keyword-discovery scraper with strict PWD filtering."""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
from utils.filters import has_inclusion_keyword
from utils.schema import KEYWORD_PWD


SOURCE = "indeed"
OUTPUT_FILENAME = "indeed.xlsx"

# Search-discovery URLs only. A row is saved only if the listing text itself
# contains an explicit inclusion keyword.
SEARCH_URLS = [
    "https://in.indeed.com/jobs?q=disability%2C+deaf%2C+speech+impaired%2C+data+entry&l=Mumbai%2C+Maharashtra",
    "https://in.indeed.com/jobs?q=deaf+hearing+impaired+speech+impaired+internship&l=India",
]


def _page_url(base_url: str, page_number: int) -> str:
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if page_number > 1:
        query["start"] = str((page_number - 1) * 10)
    else:
        query.pop("start", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _is_remote(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["remote", "work from home", "wfh"])


def _listing_type(text: str) -> str:
    return "internship" if "intern" in text.lower() else "job"


def _extract_cards(page) -> list[dict[str, object]]:
    jobs = page.evaluate(
        """
        () => {
          const links = Array.from(document.querySelectorAll(
            'h2.jobTitle a, a[data-jk], a[href*="/rc/clk"], a[href*="/pagead/clk"], a[href*="/viewjob"]'
          ));
          const cards = [];
          const seen = new Set();
          for (const link of links) {
            const card =
              link.closest('div.job_seen_beacon') ||
              link.closest('li') ||
              link.closest('div[class*="card"]') ||
              link.closest('div[class*="result"]') ||
              link.parentElement;
            if (!card || seen.has(card)) continue;
            seen.add(card);
            cards.push({ link, card });
          }

          const pickText = (el, selectors) => {
            for (const selector of selectors) {
              const node = el.querySelector(selector);
              const text = node && node.innerText ? node.innerText.trim() : '';
              if (text) return text;
            }
            return '';
          };

          return cards.map(({ link, card }) => {
            const text = card.innerText || '';
            return {
              title: pickText(card, ['h2.jobTitle', 'h2 a', 'span[title]', 'a[data-jk]', 'a']),
              company: pickText(card, ['[data-testid="company-name"]', '.companyName', '[class*="company"]']),
              location: pickText(card, ['[data-testid="text-location"]', '.companyLocation', '[class*="location"]']),
              salary: pickText(card, ['[data-testid="attribute_snippet_testid"]', '.salary-snippet-container', '[class*="salary"]']),
              posted_at: pickText(card, ['.date', '[data-testid="myJobsStateDate"]', '[class*="date"]']),
              apply_url: link.href || '',
              description: pickText(card, ['.job-snippet', '[class*="snippet"]']) || text,
              text
            };
          }).filter((item) => item.title || item.apply_url);
        }
        """
    )

    rows: list[dict[str, object]] = []
    for job in jobs:
        text = clean_text(job.get("text"))
        description = clean_text(job.get("description"))
        title = clean_text(job.get("title"))
        if not has_inclusion_keyword(title, description, text):
            continue

        row = {
            "title": title,
            "company": clean_text(job.get("company")),
            "location": clean_text(job.get("location")),
            "is_remote": _is_remote(text),
            "type": _listing_type(text),
            "inclusion_type": KEYWORD_PWD,
            "stipend_or_salary_raw": clean_text(job.get("salary")),
            "duration": "",
            "posted_at": clean_text(job.get("posted_at")),
            "apply_url": clean_text(job.get("apply_url")),
            "source": SOURCE,
            "description_snippet": description[:300],
        }
        rows.append(apply_money(row))
    return rows


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    output_path = output_file(output_dir, OUTPUT_FILENAME)
    total_seen = 0
    killed_by_filter = 0
    kept_records: list[dict[str, object]] = []
    last_error = ""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900})
        page = context.new_page()

        try:
            for search_url in SEARCH_URLS:
                for page_number in range(1, max_pages + 1):
                    try:
                        page.goto(_page_url(search_url, page_number), wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(2500)
                        page_records = _extract_cards(page)
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
        finally:
            context.close()
            browser.close()

    final_records = dedupe_by_apply_url(kept_records)
    save_excel(final_records, output_path)
    error = last_error if total_seen == 0 and last_error else ""
    return make_stats(SOURCE, total_seen, killed_by_filter, len(final_records), output_path, error)


if __name__ == "__main__":
    print(scrape())

