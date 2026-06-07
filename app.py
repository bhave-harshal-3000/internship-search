"""Streamlit frontend for DeafLink scrapers."""

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
    {"name": "ncs", "label": "NCS (National Career Service)", "module": "scrapers.ncs", "filename": "ncs.xlsx"},
    {
        "name": "google_jobs",
        "label": "Google Jobs (SerpAPI)",
        "module": "scrapers.google_jobs",
        "filename": "google_jobs.xlsx",
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
    {"name": "naukri", "label": "Naukri.com", "module": "scrapers.naukri", "filename": "naukri.xlsx"},
]


def _run_source(source: dict[str, str]) -> dict[str, Any]:
    try:
        module = importlib.import_module(source["module"])
        stats = module.scrape(max_pages=MAX_PAGES, output_dir=str(OUTPUT_DIR))
        stats.setdefault("error", "")
        return stats
    except Exception as exc:  # noqa: BLE001
        output_path = OUTPUT_DIR / source["filename"]
        try:
            save_excel([], output_path)
        except Exception as save_exc:  # noqa: BLE001
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


def _parse_emails(raw: str) -> list[str]:
    """Return a cleaned list of valid-looking email addresses from a comma-separated string."""
    emails = []
    for part in raw.split(","):
        email = part.strip()
        if email and "@" in email and "." in email.split("@")[-1]:
            emails.append(email)
    return emails


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


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="DeafLink Job Search", page_icon="🔍", layout="wide")

st.markdown("""
<style>
  /* Reduce default top padding but keep breathing room */
  .block-container { padding-top: 1.8rem !important; padding-bottom: 1rem !important; }
  /* Tight checkboxes only */
  div[data-testid="stCheckbox"] { margin-bottom: -4px !important; }
  /* Section label spacing */
  .section-label { margin-top: 1.4rem !important; margin-bottom: 0.3rem !important; font-size: 0.95rem; }
  /* Input field — slightly narrower so it doesn't touch the right column */
  div[data-testid="stTextInput"] > div { max-width: 95% !important; }
  /* Downloads column: left border as separator + padding */
  div[data-testid="stHorizontalBlock"] > div:last-child {
    border-left: 1px solid rgba(250,250,250,0.12) !important;
    padding-left: 2rem !important;
    margin-top: 0.4rem !important;
  }
  /* Reduce hr height */
  hr { margin: 0.8rem 0 !important; border-color: rgba(250,250,250,0.1) !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("### 🔍 DeafLink — Internship + Job Search")
st.caption("Disability-friendly job search for speech and hearing impaired candidates.")

# Spacer between title and body
st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ── Session state ─────────────────────────────────────────────────────────────
if "has_run" not in st.session_state:
    st.session_state.has_run = False
if "last_run_sources" not in st.session_state:
    st.session_state.last_run_sources = []
if "last_stats" not in st.session_state:
    st.session_state.last_stats = []

# ── Layout ────────────────────────────────────────────────────────────────────
panel = st.container()
left_col, right_col = panel.columns([2, 1], gap="small")

with left_col:
    # ── Recipients ────────────────────────────────────────────────────────────
    st.markdown("**📧 Email Recipients** — enter one or more addresses, comma-separated")
    recipient_input = st.text_input(
        label="Recipient email(s)",
        placeholder="e.g.  student@college.edu, coordinator@ngo.org",
        label_visibility="collapsed",
        key="recipient_input",
    )
    parsed_emails = _parse_emails(recipient_input)

    # Dynamic styling and validation message
    if parsed_emails:
        st.markdown(
            """
            <style>
                div[data-testid="stTextInput"] input {
                    border-color: #28a745 !important;
                    box-shadow: 0 0 0 0.2rem rgba(40, 167, 69, 0.25) !important;
                }
            </style>
            """,
            unsafe_allow_html=True
        )
        st.markdown("<p style='color: #28a745; margin-top: -12px; margin-bottom: 4px; font-size: 13px; font-weight: 500;'>✅ Valid email address(es) entered.</p>", unsafe_allow_html=True)
    elif recipient_input:
        st.markdown(
            """
            <style>
                div[data-testid="stTextInput"] input {
                    border-color: #dc3545 !important;
                    box-shadow: 0 0 0 0.2rem rgba(220, 53, 69, 0.25) !important;
                }
            </style>
            """,
            unsafe_allow_html=True
        )
        st.markdown("<p style='color: #dc3545; margin-top: -12px; margin-bottom: 4px; font-size: 13px; font-weight: 500;'>❌ No valid email addresses found. Check the format.</p>", unsafe_allow_html=True)

    # Divider before Sources
    st.markdown("---")

    # ── Sources ───────────────────────────────────────────────────────────────
    st.markdown("**Sources**")
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
            all_stats = [_run_source(source) for source in selected_sources]

        st.session_state.has_run = True
        st.session_state.last_run_sources = list(selected_sources)
        st.session_state.last_stats = all_stats
        st.success("Scraping complete!")

        # ── Send email if recipients provided ─────────────────────────────────
        if parsed_emails:
            with st.spinner(f"Sending results to {len(parsed_emails)} recipient(s)..."):
                try:
                    from utils.mailer import send_email
                    send_email(parsed_emails, all_stats, OUTPUT_DIR)
                    st.success(f"✅ Email sent to: {', '.join(parsed_emails)}")
                except Exception as mail_exc:  # noqa: BLE001
                    st.error(f"❌ Email failed: {mail_exc}")
        else:
            st.info("No recipient email entered — skipping email delivery.")

with right_col:
    _render_downloads(st.session_state.last_run_sources, st.session_state.has_run)
