"""Internshala work-from-home internship scraper."""

from __future__ import annotations

import time
from pathlib import Path

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
from utils.schema import REMOTE_ACCESSIBLE


SOURCE = "internshala"
OUTPUT_FILENAME = "internshala.xlsx"
BASE_URL = "https://internshala.com/internships/work-from-home-internships/"


def _page_url(page_number: int) -> str:
    if page_number == 1:
        return BASE_URL
    return f"https://internshala.com/internships/work-from-home-internships/page-{page_number}/"


def _extract_cards(page) -> list[dict[str, object]]:
    internships = page.evaluate(
        """
        () => {
          const nodes = Array.from(document.querySelectorAll(
            '.individual_internship, div[class*="individual_internship"], div[class*="internship_meta"]'
          ));
          const pickText = (el, sels) => {
            for (const sel of sels) {
              const node = el.querySelector(sel);
              const text = node && node.innerText ? node.innerText.trim() : '';
              if (text) return text;
            }
            return '';
          };
          const pickHref = (el) => {
            const node = el.querySelector('a[href*="/internship/detail/"], a.job-title-href, a[href]');
            return node && node.href ? node.href : '';
          };
          return nodes.map((el) => {
            const text = el.innerText || '';
            return {
              title: pickText(el, ['h3 a', '.job-title-href', '[class*="profile"]', 'h3', 'h2']),
              company: pickText(el, ['.company_name', '[class*="company"]']),
              location: pickText(el, ['.location_link', '[class*="location"]']),
              stipend: pickText(el, ['.stipend', '[class*="stipend"]']),
              duration: pickText(el, ['.duration', '[class*="duration"]']),
              posted_at: pickText(el, ['.status', '[class*="posted"]', '[class*="status"]']),
              apply_url: pickHref(el),
              description: text
            };
          }).filter((item) => item.title || item.apply_url);
        }
        """
    )
    rows: list[dict[str, object]] = []
    for internship in internships:
        text = clean_text(internship.get("description"))
        title = clean_text(internship.get("title"))
        if not title:
            title = text[:100]
        row = {
            "title": title,
            "company": clean_text(internship.get("company")),
            "location": clean_text(internship.get("location")) or "Work From Home",
            "is_remote": True,
            "type": "internship",
            "inclusion_type": REMOTE_ACCESSIBLE,
            "stipend_or_salary_raw": clean_text(internship.get("stipend")),
            "duration": clean_text(internship.get("duration")),
            "posted_at": clean_text(internship.get("posted_at")),
            "apply_url": clean_text(internship.get("apply_url")),
            "source": SOURCE,
            "description_snippet": text[:300],
        }
        rows.append(apply_money(row))
    return rows


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    output_path = output_file(output_dir, OUTPUT_FILENAME)
    rows: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900})
        page = context.new_page()
        try:
            for page_number in range(1, max_pages + 1):
                try:
                    page.goto(_page_url(page_number), wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2500)
                    page_rows = _extract_cards(page)
                except Exception as exc:  # noqa: BLE001
                    print(f"[{SOURCE}] page={page_number} failed: {exc}", flush=True)
                    break

                if not page_rows and page_number > 1:
                    break
                rows.extend(page_rows)
                _, killed_preview = apply_kill_filter(rows)
                log_progress(SOURCE, page_number, len(rows), killed_preview)

                if not page_rows:
                    break
                time.sleep(SLEEP_BETWEEN_REQUESTS)
        finally:
            context.close()
            browser.close()

    total_scraped = len(rows)
    kept_records, killed_by_filter = apply_kill_filter(rows)
    final_records = dedupe_by_apply_url(kept_records)
    for record in final_records:
        record["inclusion_type"] = REMOTE_ACCESSIBLE

    save_excel(final_records, output_path)
    log_progress(SOURCE, max_pages, total_scraped, killed_by_filter)
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path)


if __name__ == "__main__":
    print(scrape())
