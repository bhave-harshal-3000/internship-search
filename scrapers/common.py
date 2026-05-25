"""Small helpers used by the source scrapers."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from utils.config import RETRY_ATTEMPTS, RETRY_BACKOFF, USER_AGENT
from utils.filters import kill_filter
from utils.money import parse_money


HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def absolute_url(base_url: str, href: str | None) -> str:
    if not href:
        return ""
    return urljoin(base_url, href)


def request_with_retries(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, object] | None = None,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=timeout, headers=headers or HEADERS)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 - retry and surface final failure
            last_error = exc
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
    raise RuntimeError(f"GET failed for {url}: {last_error}") from last_error


def soup_text(node: Tag | None) -> str:
    return clean_text(node.get_text(" ", strip=True)) if node else ""


def first_text(parent: Tag, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = parent.select_one(selector)
        value = soup_text(node)
        if value:
            return value
    return ""


def first_href(parent: Tag, base_url: str, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = parent.select_one(selector)
        if node and node.get("href"):
            return absolute_url(base_url, str(node.get("href")))
    return ""


def text_between(text: str, start: str, stop_labels: Iterable[str]) -> str:
    pattern = re.escape(start) + r"\s*:?\s*(.*?)\s*(?=" + "|".join(re.escape(label) for label in stop_labels) + r"|$)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else ""


def apply_money(record: dict[str, object]) -> dict[str, object]:
    raw = str(record.get("stipend_or_salary_raw") or "")
    low, high, period = parse_money(raw)
    if record.get("type") == "internship":
        record["stipend_min"] = low or ""
        record["stipend_max"] = high if high is not None else ""
    else:
        if period == "monthly" and low is not None:
            low = low * 12
            high = high * 12 if high is not None else high
        record["salary_min"] = low or ""
        record["salary_max"] = high if high is not None else ""
    return record


def dedupe_by_apply_url(records: list[dict[str, object]]) -> list[dict[str, object]]:
    seen_urls: set[str] = set()
    seen_fallbacks: set[tuple[object, object, object]] = set()
    deduped: list[dict[str, object]] = []
    for record in records:
        apply_url = clean_text(record.get("apply_url"))
        if apply_url:
            if apply_url in seen_urls:
                continue
            seen_urls.add(apply_url)
        else:
            fallback = (record.get("title"), record.get("company"), record.get("location"))
            if fallback in seen_fallbacks:
                continue
            seen_fallbacks.add(fallback)
        deduped.append(record)
    return deduped


def apply_kill_filter(records: list[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
    kept: list[dict[str, object]] = []
    killed = 0
    for record in records:
        should_kill = kill_filter(
            record.get("title"),
            record.get("company"),
            record.get("location"),
            record.get("description_snippet"),
        )
        if should_kill:
            killed += 1
        else:
            kept.append(record)
    return kept, killed


def output_file(output_dir: str | Path, filename: str) -> Path:
    return Path(output_dir).resolve() / filename


def make_stats(
    source: str,
    total_scraped: int,
    killed_by_filter: int,
    saved_rows: int,
    output_path: str | Path,
    error: str = "",
) -> dict[str, object]:
    return {
        "source": source,
        "total_scraped": total_scraped,
        "killed_by_filter": killed_by_filter,
        "saved_rows": saved_rows,
        "output_file": str(output_path),
        "error": error,
    }


def log_progress(source: str, page: int, total: int, killed: int) -> None:
    print(f"[{source}] page={page} total_found={total} killed={killed}", flush=True)

