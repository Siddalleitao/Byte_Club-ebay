"""CP0 gate — spreadsheet parser for CSV and XLSX.

Handles:
- E07: Header mapping through the glossary including Arabic headers,
       with a one-time mapping confirmation
- E08: Unmerge cells, skip blank rows, flag duplicate SKUs as Conflicting
- Supports both CSV and XLSX formats
- Applies text normalisation to every cell value

Nothing in this module calls an LLM.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.orchestration.text_normaliser import normalise, detect_language

# ── Standard column names we map headers to ──
STANDARD_COLUMNS = {
    "sku",
    "make",
    "model",
    "year",
    "side",
    "part_number",
    "part_type",
    "notes",
    "description",
    "price",
    "rack_code",
    "condition",
    "weight",
    "dimensions",
}

# ── Header mapping table (English + Arabic → standard) ──
# E07: headers may differ or be in Arabic
HEADER_MAP: dict[str, str] = {
    # English
    "sku": "sku",
    "part number": "part_number",
    "part_number": "part_number",
    "part no": "part_number",
    "part no.": "part_number",
    "pn": "part_number",
    "make": "make",
    "brand": "make",
    "model": "model",
    "year": "year",
    "donor year": "year",
    "side": "side",
    "position": "side",
    "left/right": "side",
    "part type": "part_type",
    "type": "part_type",
    "category": "part_type",
    "notes": "notes",
    "note": "notes",
    "description": "description",
    "desc": "description",
    "price": "price",
    "asking price": "price",
    "asking price (aed)": "price",
    "rack code": "rack_code",
    "rack": "rack_code",
    "rack#": "rack_code",
    "condition": "condition",
    "cond": "condition",
    "weight": "weight",
    "packed weight": "weight",
    "dimensions": "dimensions",
    "dims": "dimensions",
    "size": "dimensions",
    # Arabic headers
    "الرقم": "sku",
    "رقم القطعة": "sku",
    "رقم الجزء": "sku",
    "القطعة رقم": "sku",
    "الماركة": "make",
    "العلامة التجارية": "make",
    "الموديل": "model",
    "النموذج": "model",
    "السنة": "year",
    "سنة السيارة": "year",
    "الجهة": "side",
    "الجانب": "side",
    "يمين/يسار": "side",
    "رقم القطعة الأصلية": "part_number",
    "القطعة رقم على الملصق": "part_number",
    "نوع القطعة": "part_type",
    "النوع": "part_type",
    "ملاحظات": "notes",
    "ملاحظة": "notes",
    "الوصف": "description",
    "السعر": "price",
    "السعر المطلوب": "price",
    "رمز الرف": "rack_code",
    "الرف": "rack_code",
    "الحالة": "condition",
    "الوزن": "weight",
    "الأبعاد": "dimensions",
    "المقاسات": "dimensions",
}


@dataclass
class ParsedSpreadsheet:
    """Result of parsing a spreadsheet file.

    Attributes:
        rows: list of row dicts with standard column keys
        header_mapping: the mapping that was applied (original → standard)
        duplicate_skus: SKUs that appeared more than once (E08)
        blank_rows_skipped: count of blank rows skipped (E08)
        merged_cells_unmerged: count of merged cells handled (E08)
        unmapped_headers: headers that couldn't be mapped (need confirmation per E07)
        edge_cases: list of edge-case IDs triggered
    """

    rows: list[dict[str, str]] = field(default_factory=list)
    header_mapping: dict[str, str] = field(default_factory=dict)
    duplicate_skus: list[str] = field(default_factory=list)
    blank_rows_skipped: int = 0
    merged_cells_unmerged: int = 0
    unmapped_headers: list[str] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)


def _map_header(header: str) -> str | None:
    """Map a spreadsheet header to a standard column name (E07).

    Returns None if the header can't be mapped.
    """
    # Normalise the header for matching
    norm = normalise(header).normalised.strip().lower()

    # Direct lookup
    if norm in HEADER_MAP:
        return HEADER_MAP[norm]

    # Try without spaces/underscores
    compact = re.sub(r"[\s_]+", "", norm)
    for raw, standard in HEADER_MAP.items():
        if re.sub(r"[\s_]+", "", raw) == compact:
            return standard

    return None


def _is_blank_row(row: dict[str, str]) -> bool:
    """Check if a row is entirely blank (E08: skip blank rows)."""
    return all(not v or not v.strip() for v in row.values())


def parse_csv(file_path: str | Path) -> ParsedSpreadsheet:
    """Parse a CSV spreadsheet file.

    Handles E07 (header mapping) and E08 (blank rows, duplicate SKUs).
    """
    file_path = Path(file_path)
    result = ParsedSpreadsheet()
    edge_cases: list[str] = []

    # Read with utf-8-sig to handle BOM
    with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        raw_headers = reader.fieldnames or []

        # Map headers (E07)
        header_mapping: dict[str, str] = {}
        unmapped: list[str] = []
        for raw_h in raw_headers:
            standard = _map_header(raw_h)
            if standard:
                header_mapping[raw_h] = standard
            else:
                unmapped.append(raw_h)

        if unmapped:
            edge_cases.append("E07")

        # Parse rows
        rows: list[dict[str, str]] = []
        blank_count = 0
        sku_counts: dict[str, int] = {}

        for raw_row in reader:
            # Skip blank rows (E08)
            if _is_blank_row(raw_row):
                blank_count += 1
                continue

            # Map and normalise each cell
            mapped_row: dict[str, str] = {}
            for raw_h, value in raw_row.items():
                if raw_h is None:
                    continue
                standard = header_mapping.get(raw_h)
                if standard and value:
                    # Normalise the cell value
                    norm_result = normalise(value)
                    mapped_row[standard] = norm_result.normalised
                elif standard:
                    mapped_row[standard] = ""

            rows.append(mapped_row)

            # Track SKU duplicates (E08)
            sku = mapped_row.get("sku", "").strip()
            if sku:
                sku_counts[sku] = sku_counts.get(sku, 0) + 1

        # Find duplicate SKUs (E08)
        duplicates = [sku for sku, count in sku_counts.items() if count > 1]
        if duplicates:
            edge_cases.append("E08")
        if blank_count > 0:
            edge_cases.append("E08")

        result.rows = rows
        result.header_mapping = header_mapping
        result.duplicate_skus = duplicates
        result.blank_rows_skipped = blank_count
        result.unmapped_headers = unmapped
        result.edge_cases = edge_cases

    return result


def parse_xlsx(file_path: str | Path) -> ParsedSpreadsheet:
    """Parse an XLSX spreadsheet file.

    Handles E07 (header mapping), E08 (unmerge cells, blank rows, duplicate SKUs).
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("openpyxl required for XLSX parsing. Install: pip install openpyxl")

    file_path = Path(file_path)
    result = ParsedSpreadsheet()
    edge_cases: list[str] = []

    wb = load_workbook(str(file_path), data_only=True)
    ws = wb.active

    # Count and unmerge merged cells (E08)
    merged_count = 0
    # Build a map of merged cell top-left values to fill unmerged cells
    merge_map: dict[str, str] = {}
    for merged_range in ws.merged_cells.ranges:
        top_left_cell = ws.cell(row=merged_range.min_row, column=merged_range.min_col)
        top_left_value = str(top_left_cell.value) if top_left_cell.value else ""
        for row in range(merged_range.min_row, merged_range.max_row + 1):
            for col in range(merged_range.min_col, merged_range.max_col + 1):
                merge_map[f"{row},{col}"] = top_left_value
        merged_count += 1

    # Unmerge all cells so we can iterate normally
    # (openpyxl doesn't have a direct unmerge-and-fill, so we use merge_map)
    if merged_count > 0:
        edge_cases.append("E08")

    # Read header row (first row)
    raw_headers: list[str] = []
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=1, column=col).value
        raw_headers.append(str(val) if val else "")

    # Map headers (E07)
    header_mapping: dict[str, str] = {}
    unmapped: list[str] = []
    col_mapping: dict[int, str] = {}  # col index → standard name

    for col_idx, raw_h in enumerate(raw_headers, start=1):
        standard = _map_header(raw_h)
        if standard:
            header_mapping[raw_h] = standard
            col_mapping[col_idx] = standard
        else:
            unmapped.append(raw_h)

    if unmapped:
        edge_cases.append("E07")

    # Parse data rows
    rows: list[dict[str, str]] = []
    blank_count = 0
    sku_counts: dict[str, int] = {}

    for row_idx in range(2, ws.max_row + 1):
        mapped_row: dict[str, str] = {}

        for col_idx in range(1, ws.max_column + 1):
            standard = col_mapping.get(col_idx)
            if not standard:
                continue

            # Get cell value, using merge_map for unmerged values
            key = f"{row_idx},{col_idx}"
            if key in merge_map:
                val = merge_map[key]
            else:
                val = ws.cell(row=row_idx, column=col_idx).value
                val = str(val) if val else ""

            if val:
                norm_result = normalise(val)
                mapped_row[standard] = norm_result.normalised
            else:
                mapped_row[standard] = ""

        # Skip blank rows (E08)
        if _is_blank_row(mapped_row):
            blank_count += 1
            continue

        rows.append(mapped_row)

        # Track SKU duplicates (E08)
        sku = mapped_row.get("sku", "").strip()
        if sku:
            sku_counts[sku] = sku_counts.get(sku, 0) + 1

    # Find duplicate SKUs (E08)
    duplicates = [sku for sku, count in sku_counts.items() if count > 1]
    if duplicates:
        edge_cases.append("E08")

    wb.close()

    result.rows = rows
    result.header_mapping = header_mapping
    result.duplicate_skus = duplicates
    result.blank_rows_skipped = blank_count
    result.merged_cells_unmerged = merged_count
    result.unmapped_headers = unmapped
    result.edge_cases = edge_cases

    return result


def parse_spreadsheet(file_path: str | Path) -> ParsedSpreadsheet:
    """Parse a spreadsheet file, dispatching to CSV or XLSX parser."""
    file_path = Path(file_path)
    ext = file_path.suffix.lower()

    if ext == ".csv":
        return parse_csv(file_path)
    elif ext in (".xlsx", ".xlsm"):
        return parse_xlsx(file_path)
    else:
        raise ValueError(f"Unsupported spreadsheet format: {ext}")
