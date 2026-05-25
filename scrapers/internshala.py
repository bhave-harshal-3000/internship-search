"""Internshala work-from-home internship scraper."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

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


def _rows_from_json(payload: Any) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def value(item: dict[str, Any], names: list[str]) -> str:
        lowered = {str(key).lower(): val for key, val in item.items()}
        for name in names:
            found = lowered.get(name.lower())
            if found not in (None, ""):
                if isinstance(found, (list, tuple)):
                    return clean_text("; ".join(str(part) for part in found))
                return clean_text(found)
        return ""

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return

        title = value(node, ["title", "profile_name", "internshipTitle", "profile"])
        company = value(node, ["company_name", "companyName", "employer_name"])
        location = value(node, ["location", "locations", "location_names"])
        stipend = value(node, ["stipend", "stipendText", "salary"])
        duration = value(node, ["duration", "durationText"])
        posted_at = value(node, ["posted_at", "postedOn", "status", "date"])
        description = value(node, ["description", "internship_description", "details", "skills"])
        apply_url = value(node, ["url", "internship_url", "apply_url"])
        internship_id = value(node, ["id", "internship_id"])
        if apply_url and apply_url.startswith("/"):
            apply_url = f"https://internshala.com{apply_url}"
        elif internship_id and not apply_url:
            apply_url = f"https://internshala.com/internship/detail/{internship_id}"

        if title and (company or stipend or description):
            row = {
                "title": title,
                "company": company,
                "location": location or "Work From Home",
                "is_remote": True,
                "type": "internship",
                "inclusion_type": REMOTE_ACCESSIBLE,
                "stipend_or_salary_raw": stipend,
                "duration": duration,
                "posted_at": posted_at,
                "apply_url": apply_url,
                "source": SOURCE,
                "description_snippet": description[:300],
            }
            rows.append(apply_money(row))

        for child in node.values():
            walk(child)

    walk(payload)
    return rows


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
        stipend = clean_text(internship.get("stipend"))
        row = {
            "title": title,
            "company": clean_text(internship.get("company")),
            "location": clean_text(internship.get("location")) or "Work From Home",
            "is_remote": True,
            "type": "internship",
            "inclusion_type": REMOTE_ACCESSIBLE,
            "stipend_or_salary_raw": stipend,
            "duration": clean_text(internship.get("duration")),
            "posted_at": clean_text(internship.get("posted_at")),
            "apply_url": clean_text(internship.get("apply_url")),
            "source": SOURCE,
            "description_snippet": text[:300],
        }
        rows.append(apply_money(row))
    return rows


def _fallback_page_url(page_number: int) -> str:
    separator = "&" if "?" in BASE_URL else "?"
    return f"{BASE_URL}{separator}page={page_number}"


def _scrape_with_playwright(max_pages: int) -> list[dict[str, object]]:
    from playwright.sync_api import sync_playwright

    json_payloads: list[Any] = []

    def capture_response(response) -> None:
        url = response.url.lower()
        if not any(token in url for token in ["internship", "algolia", "search"]):
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
            for page_number in range(1, max_pages + 1):
                url = _page_url(page_number)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2500)
                    page_rows = _extract_cards(page)
                    if not page_rows and page_number > 1:
                        page.goto(_fallback_page_url(page_number), wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(2000)
                        page_rows = _extract_cards(page)
                except Exception as exc:  # noqa: BLE001 - save whatever was collected
                    print(f"[{SOURCE}] page={page_number} failed: {exc}", flush=True)
                    break

                for payload in json_payloads:
                    page_rows.extend(_rows_from_json(payload))
                json_payloads.clear()

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
    return rows


def _apply_inclusion_policy(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Save Internshala WFH rows for external review/classification."""

    for record in records:
        record["inclusion_type"] = REMOTE_ACCESSIBLE
    return records


def scrape(max_pages: int = MAX_PAGES, output_dir: str | Path = "output") -> dict[str, object]:
    output_path = output_file(output_dir, OUTPUT_FILENAME)
    all_records = _scrape_with_playwright(max_pages)
    total_scraped = len(all_records)

    kept_records, killed_by_filter = apply_kill_filter(all_records)
    deduped_records = dedupe_by_apply_url(kept_records)
    final_records = _apply_inclusion_policy(deduped_records)

    save_excel(final_records, output_path)
    log_progress(SOURCE, max_pages, total_scraped, killed_by_filter)
    return make_stats(SOURCE, total_scraped, killed_by_filter, len(final_records), output_path)


if __name__ == "__main__":
    print(scrape())
