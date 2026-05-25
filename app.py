"""Streamlit frontend for Intern-Search scrapers."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any

import streamlit as st

from utils.config import MAX_PAGES
from utils.excel_writer import save_excel


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"

SOURCES = [
    {"name": "naukri", "label": "Naukri.com", "module": "scrapers.naukri", "filename": "naukri.xlsx"},
    {"name": "ncs", "label": "NCS (National Career Service)", "module": "scrapers.ncs", "filename": "ncs.xlsx"},
    {
        "name": "google_jobs",
        "label": "Google Jobs (SerpAPI)",
        "module": "scrapers.google_jobs",
        "filename": "google_jobs.xlsx",
    },
    {
        "name": "company_disability",
        "label": "Company Disability Pages",
        "module": "scrapers.company_disability",
        "filename": "company_disability.xlsx",
    },
    {
        "name": "atypical",
        "label": "Atypical Advantage",
        "module": "scrapers.atypical",
        "filename": "atypical.xlsx",
    },
    {
        "name": "swarajability",
        "label": "SwarajAbility",
        "module": "scrapers.swarajability",
        "filename": "swarajability.xlsx",
    },
]


def _run_source(source: dict[str, str]) -> dict[str, Any]:
    try:
        module = importlib.import_module(source["module"])
        stats = module.scrape(max_pages=MAX_PAGES, output_dir=str(OUTPUT_DIR))
        stats.setdefault("error", "")
        return stats
    except Exception as exc:  # noqa: BLE001 - isolate each source
        output_path = OUTPUT_DIR / source["filename"]
        try:
            save_excel([], output_path)
        except Exception as save_exc:  # noqa: BLE001 - preserve original error
            return {
                "source": source["name"],
                "total_scraped": 0,
                "killed_by_filter": 0,
                "saved_rows": 0,
                "output_file": str(output_path),
                "error": f"{exc}; also failed to write empty workbook: {save_exc}",
            }
        return {
            "source": source["name"],
            "total_scraped": 0,
            "killed_by_filter": 0,
            "saved_rows": 0,
            "output_file": str(output_path),
            "error": str(exc),
        }


def _load_bytes(path: Path) -> bytes:
    if not path.exists():
        return b""
    return path.read_bytes()


def _render_downloads(sources: list[dict[str, str]], has_run: bool) -> None:
    st.subheader("Downloads")
    if not has_run:
        st.caption("Run scrapers to enable downloads.")
        return
    if not sources:
        st.info("Select at least one source to enable downloads.")
        return

    available = False
    for source in sources:
        file_path = OUTPUT_DIR / source["filename"]
        data = _load_bytes(file_path)
        if not data:
            st.info(f"{source['label']}: no output file found.")
            continue
        available = True
        st.download_button(
            label=f"Download {source['label']} XLSX",
            data=data,
            file_name=source["filename"],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if not available:
        st.warning("Run the scrapers to generate downloadable XLSX files.")


st.set_page_config(page_title="Internship-Search", page_icon="\U0001F50D", layout="wide")

st.title("Internship-Search")
st.caption("Disability-friendly job and internship scraper")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

if "has_run" not in st.session_state:
    st.session_state.has_run = False
if "last_run_sources" not in st.session_state:
    st.session_state.last_run_sources = []

panel = st.container()
left_col, right_col = panel.columns([2, 1], gap="small")

with left_col:
    st.subheader("Sources")
    selected_sources: list[dict[str, str]] = []
    for source in SOURCES:
        if st.checkbox(source["label"], value=True, key=f"src_{source['name']}"):
            selected_sources.append(source)

    if st.button("Run Scrapers", type="primary", disabled=not selected_sources):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for source in selected_sources:
            output_path = OUTPUT_DIR / source["filename"]
            if output_path.exists():
                output_path.unlink()
        with st.spinner("Running selected scrapers..."):
            [_run_source(source) for source in selected_sources]
        st.session_state.has_run = True
        st.session_state.last_run_sources = list(selected_sources)
        st.success("Done")
        st.success("Something --------")
        print("Something --------")

with right_col:
    _render_downloads(st.session_state.last_run_sources, st.session_state.has_run)
