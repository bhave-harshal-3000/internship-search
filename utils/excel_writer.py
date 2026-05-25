"""Excel output helper."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd
from openpyxl.styles import Font

from .schema import COLUMNS, normalize_rows


def rows_to_dataframe(rows: list[Mapping[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(normalize_rows(rows), columns=COLUMNS)


def save_excel(rows: list[Mapping[str, object]], output_path: str | Path) -> pd.DataFrame:
    """Save rows to a formatted XLSX file and return the DataFrame."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = rows_to_dataframe(rows)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="jobs")
        worksheet = writer.sheets["jobs"]
        worksheet.freeze_panes = "A2"

        for cell in worksheet[1]:
            cell.font = Font(bold=True)

        for column_cells in worksheet.columns:
            header = str(column_cells[0].value or "")
            max_length = len(header)
            for cell in column_cells[1:]:
                value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(value))
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max_length + 2, 60)

    return df

