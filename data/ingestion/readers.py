"""Stage 1 — LAND. Raw source files -> list[dict] records (one dict per source row).

Format readers only: no mapping, no normalization, no validation. Each reader returns
the source's own column names and string-ish values; later stages interpret them. The
Excel reader handles the awkward real-world cases the spec calls out — multiple sheets,
a header that is not the first row, and Excel serial-number dates (left as-is here and
converted in the normalize stage).
"""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

Record = dict[str, Any]


def read_csv(path: str | Path) -> list[Record]:
    """Read a CSV into records keyed by the header row."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def read_json(path: str | Path) -> list[Record]:
    """Read a JSON array of objects, or an object with a top-level `records`/`data` list."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        for key in ("records", "data", "rows", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("JSON source must be a list of objects or wrap one under records/data")
    return [dict(obj) for obj in payload]


def read_xml(path: str | Path) -> list[Record]:
    """Read a flat XML document: each direct child of the root is one record, and that
    record's own child elements are its fields (tag -> text)."""
    root = ET.parse(str(path)).getroot()
    records: list[Record] = []
    for node in list(root):
        row: Record = {child.tag: (child.text or "").strip() for child in node}
        row.update(node.attrib)  # element attributes are fields too
        records.append(row)
    return records


def read_excel(
    path: str | Path, *, sheet: str | None = None, header_row: int = 0
) -> list[Record]:
    """Read one sheet of an XLSX workbook into records.

    `header_row` is the zero-based index of the row that holds the column names (so a
    title banner above the header is tolerated). Excel serial dates arrive as numbers
    and are converted later by `normalize.excel_serial_to_date`. Uses pandas/openpyxl.
    """
    import pandas as pd

    frame = pd.read_excel(
        path, sheet_name=(0 if sheet is None else sheet), header=header_row,
        dtype=object, engine="openpyxl",
    )
    records: list[Record] = []
    for raw in frame.to_dict(orient="records"):
        row: Record = {}
        for key, value in raw.items():
            row[str(key)] = None if pd.isna(value) else value
        records.append(row)
    return records


def excel_sheet_names(path: str | Path) -> list[str]:
    """List the worksheet names in an XLSX workbook (multi-sheet discovery)."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True)
    try:
        return list(book.sheetnames)
    finally:
        book.close()


# Dispatch table used by the pipeline when an adapter only names a format.
READERS = {"csv": read_csv, "json": read_json, "xml": read_xml, "excel": read_excel}
