"""Run all DeafLink scrapers sequentially and write source-specific XLSX files."""

from __future__ import annotations

import argparse
import importlib
import multiprocessing as mp
from pathlib import Path
from queue import Empty
from typing import Any

from utils.config import MAX_PAGES, PER_SOURCE_TIMEOUT
from utils.excel_writer import save_excel


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"

SOURCES = [
    {"name": "naukri", "module": "scrapers.naukri", "filename": "naukri.xlsx"},
    {"name": "ncs", "module": "scrapers.ncs", "filename": "ncs.xlsx"},
    {"name": "google_jobs", "module": "scrapers.google_jobs", "filename": "google_jobs.xlsx"},

    {"name": "atypical", "module": "scrapers.atypical", "filename": "atypical.xlsx"},
    {"name": "swarajability", "module": "scrapers.swarajability", "filename": "swarajability.xlsx"},
]


def _worker(module_name: str, max_pages: int, output_dir: str, queue: mp.Queue) -> None:
    try:
        module = importlib.import_module(module_name)
        stats = module.scrape(max_pages=max_pages, output_dir=output_dir)
        queue.put({"ok": True, "stats": stats})
    except Exception as exc:  # noqa: BLE001 - isolate each source
        queue.put({"ok": False, "error": str(exc)})


def _empty_stats(source: dict[str, str], error: str) -> dict[str, Any]:
    output_path = OUTPUT_DIR / source["filename"]
    try:
        save_excel([], output_path)
    except Exception as exc:  # noqa: BLE001 - preserve the original scraper error
        error = f"{error}; also failed to write empty workbook: {exc}"
    return {
        "source": source["name"],
        "total_scraped": 0,
        "killed_by_filter": 0,
        "saved_rows": 0,
        "output_file": str(output_path),
        "error": error,
    }


def _run_source(source: dict[str, str], max_pages: int, timeout: int) -> dict[str, Any]:
    print(f"\n=== Running {source['name']} ===", flush=True)
    queue: mp.Queue = mp.Queue()
    process = mp.Process(target=_worker, args=(source["module"], max_pages, str(OUTPUT_DIR), queue))
    process.start()
    process.join(timeout)

    if process.is_alive():
        process.terminate()
        process.join(10)
        return _empty_stats(source, f"Timed out after {timeout} seconds")

    try:
        result = queue.get_nowait()
    except Empty:
        return _empty_stats(source, "No result returned")

    if result.get("ok"):
        stats = result["stats"]
        stats.setdefault("error", "")
        return stats

    return _empty_stats(source, str(result.get("error") or "Unknown scraper error"))


def _print_summary(stats_rows: list[dict[str, Any]]) -> None:
    print("\nSummary")
    columns = ["source", "total_scraped", "killed_by_filter", "saved_rows", "output_file", "error"]
    widths = {
        column: max(len(column), *(len(str(row.get(column, ""))) for row in stats_rows))
        for column in columns
    }
    header = " | ".join(column.ljust(widths[column]) for column in columns)
    print(header)
    print("-" * len(header))
    for row in stats_rows:
        print(" | ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape PWD and accessible job sources into Excel files.")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES, help=f"Maximum pages per source. Default: {MAX_PAGES}")
    parser.add_argument(
        "--timeout",
        type=int,
        default=PER_SOURCE_TIMEOUT,
        help=f"Seconds before giving up on one source. Default: {PER_SOURCE_TIMEOUT}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []
    for source in SOURCES:
        summary.append(_run_source(source, args.max_pages, args.timeout))
    _print_summary(summary)


if __name__ == "__main__":
    mp.freeze_support()
    main()
