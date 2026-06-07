"""Email delivery helper for DeafLink scraper results."""

from __future__ import annotations

import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
PREVIEW_LIMIT = 10  # Number of listings shown per source in the email body


def load_credentials() -> tuple[str, str]:
    sender = os.getenv("EMAIL", "").strip()
    password = os.getenv("PASS", "").strip()
    return sender, password


def _source_label(name: str) -> str:
    return {
        "ncs": "NCS (National Career Service)",
        "atypical": "Atypical Advantage",
        "swarajability": "SwarajAbility",
        "google_jobs": "Google Jobs",
        "naukri": "Naukri.com",
        "indeed": "Indeed",
        "simplyhired": "SimplyHired",
    }.get(name, name.replace("_", " ").title())


# Friendly fallback messages per source shown to end-users when a scraper fails
_SOURCE_FRIENDLY_ERROR: dict[str, str] = {
    "google_jobs": "Google Jobs data could not be fetched. Please try again later.",
    "naukri": "Naukri.com listings could not be retrieved. The site may be temporarily unavailable.",
    "ncs": "NCS portal did not return results. Please try again later.",
    "atypical": "Atypical Advantage listings could not be fetched. Please try again later.",
    "swarajability": "SwarajAbility listings could not be fetched. Please try again later.",
}


def _safe_error(source: str, raw_error: str) -> str:
    """Return a user-safe error string — no URLs, API keys, file paths or stack traces."""
    if not raw_error:
        return ""
    import re
    is_technical = bool(
        re.search(r"https?://|[A-Za-z]:\\|/usr/|Traceback|Error:|api_key|\bkey\b", raw_error, re.I)
    )
    if is_technical:
        return _SOURCE_FRIENDLY_ERROR.get(source, "This source encountered an error. Please try again later.")
    return raw_error[:120]


def _summary_table(stats_list: list[dict[str, Any]]) -> str:
    rows = ""
    for s in stats_list:
        safe_err = _safe_error(s["source"], s.get("error", ""))
        error_cell = f'<span style="color:#dc3545;">⚠ {safe_err}</span>' if safe_err else "✓"
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e9ecef;">{_source_label(s['source'])}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e9ecef;text-align:center;">{s.get('total_scraped', 0)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e9ecef;text-align:center;">{s.get('killed_by_filter', 0)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e9ecef;text-align:center;font-weight:600;color:#198754;">{s.get('saved_rows', 0)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e9ecef;">{error_cell}</td>
        </tr>"""
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <thead>
        <tr style="background:#f8f9fa;">
          <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #dee2e6;">Source</th>
          <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #dee2e6;">Scraped</th>
          <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #dee2e6;">Filtered Out</th>
          <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #dee2e6;">Saved</th>
          <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #dee2e6;">Status</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _render_table(df: pd.DataFrame, keep_cols: list[str]) -> str:
    header_cells = "".join(
        f'<th style="padding:8px 10px;text-align:left;background:#e9ecef;border-bottom:2px solid #dee2e6;">{c.replace("_", " ").title()}</th>'
        for c in keep_cols
    )
    job_rows = ""
    for _, row in df.iterrows():
        cells = ""
        for col in keep_cols:
            val = str(row[col])
            if col == "apply_url" and val.startswith("http"):
                cells += f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;"><a href="{val}" style="color:#0d6efd;text-decoration:none;">Apply</a></td>'
            else:
                cells += f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;">{val}</td>'
        job_rows += f"<tr>{cells}</tr>"
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr>{header_cells}</tr></thead>
      <tbody>{job_rows}</tbody>
    </table>"""


def _jobs_preview(stats_list: list[dict[str, Any]], output_dir: Path) -> str:
    """Render a flat list of all job listings per source."""

    sections = ""
    for s in stats_list:
        if not s.get("saved_rows"):
            continue
        xlsx_path = output_dir / Path(s.get("output_file", "")).name
        if not xlsx_path.exists():
            continue
        try:
            df = pd.read_excel(xlsx_path)
        except Exception:
            continue

        keep_cols = [c for c in ["title", "company", "location", "type", "apply_url"] if c in df.columns]
        df = df[keep_cols].fillna("")
        if df.empty:
            continue

        total = len(df)
        full_table = _render_table(df, keep_cols)

        sections += f"""
        <div style="margin-top:24px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;background:#ffffff;">
          <div style="font-size:15px;font-weight:600;color:#1e293b;padding:14px 18px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">
            📁 {_source_label(s['source'])} &mdash; {total} listings
          </div>
          <div style="padding:16px;overflow-x:auto;">
            {full_table}
          </div>
        </div>"""
    return sections


def build_html(stats_list: list[dict[str, Any]], output_dir: Path) -> str:
    from datetime import date
    total_saved = sum(s.get("saved_rows", 0) for s in stats_list)
    n_sources = len(stats_list)
    today = date.today().strftime("%d %b %Y")

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="width:100%;max-width:100%;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#1a56db,#0ea5e9);padding:28px 32px;">
      <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">🔍 DeafLink Job Search Results</h1>
      <p style="margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:14px;">{today} &mdash; {n_sources} source(s) &mdash; {total_saved} jobs saved</p>
    </div>

    <!-- Body -->
    <div style="padding:28px 32px;">

      <h2 style="font-size:16px;color:#343a40;margin-top:0;">Summary</h2>
      {_summary_table(stats_list)}

      <h2 style="font-size:16px;color:#343a40;margin-top:32px;">Job Listings by Source</h2>
      {_jobs_preview(stats_list, output_dir)}

      <!-- Footer -->
      <div style="margin-top:36px;padding-top:20px;border-top:1px solid #e9ecef;color:#6c757d;font-size:12px;">
        Full results are attached as <strong>XLSX files</strong>.<br>
        Powered by <strong>DeafLink</strong> — Disability-Friendly Job &amp; Internship Search.
      </div>
    </div>
  </div>
</body>
</html>"""



def send_email(
    recipients: list[str],
    stats_list: list[dict[str, Any]],
    output_dir: str | Path,
) -> None:
    """Build and send the results email with XLSX attachments."""
    sender, password = load_credentials()
    if not sender or not password:
        raise RuntimeError("EMAIL or PASS not set in .env")

    output_dir = Path(output_dir)
    from datetime import date
    total_saved = sum(s.get("saved_rows", 0) for s in stats_list)

    msg = MIMEMultipart("mixed")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = (
        f"DeafLink Results — {date.today().strftime('%d %b %Y')} "
        f"({len(stats_list)} sources, {total_saved} jobs saved)"
    )

    html_body = build_html(stats_list, output_dir)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Attach every non-empty XLSX that was produced
    for s in stats_list:
        xlsx_path = output_dir / Path(s.get("output_file", "")).name
        if xlsx_path.exists() and xlsx_path.stat().st_size > 0:
            with open(xlsx_path, "rb") as f:
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=xlsx_path.name)
            msg.attach(part)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())
