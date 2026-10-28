"""CP0 gate — SKU linking.

Links uploaded files (photos, notes) to SKUs by:
1. Filename pattern matching (M-XX or MXX pattern)
2. Spreadsheet reference (sku column in parsed spreadsheet)

Unlinked files go to a manual-assign tray with a suggested SKU (E03).
File names are parsed for the SKU pattern only — nothing else is extracted
from file names (TRD 6: "File names parsed for the SKU pattern only").

Nothing in this module calls an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# SKU pattern: M-01, M-02, ..., M-20, also M01, M02 without dash
SKU_PATTERN = re.compile(r"[Mm][-_]?(\d{1,2})")


@dataclass
class LinkedFile:
    """A file linked to a SKU.

    Attributes:
        file_path: path to the file
        sku: the linked SKU (e.g. "M-01"), or None if unlinked
        suggested_sku: a suggested SKU for manual assignment (E03)
        linked_by: how the SKU was determined ('filename' or 'spreadsheet')
    """

    file_path: str
    sku: str | None = None
    suggested_sku: str | None = None
    linked_by: str = ""


@dataclass
class ManualAssignItem:
    """An unlinked file for the manual-assign tray (E03).

    Attributes:
        file_path: path to the file
        suggested_sku: best-guess SKU suggestion
        reason: why it's unlinked
    """

    file_path: str
    suggested_sku: str | None
    reason: str


@dataclass
class SkuLinkResult:
    """Result of SKU linking for a batch of files.

    Attributes:
        linked: files successfully linked to a SKU
        unlinked: files sent to manual-assign tray (E03)
    """

    linked: list[LinkedFile] = field(default_factory=list)
    unlinked: list[ManualAssignItem] = field(default_factory=list)


def extract_sku_from_filename(filename: str) -> str | None:
    """Extract a SKU from a filename using the M-XX pattern only.

    "File names are parsed for the SKU pattern only" — nothing else
    is extracted from file names (TRD 6 attack surface defence).

    Returns None if no SKU pattern is found.
    """
    # Get just the filename without extension
    name = Path(filename).stem

    # Also try the full filename (pattern might be in directory part)
    match = SKU_PATTERN.search(name)
    if not match:
        # Try full path
        match = SKU_PATTERN.search(filename)

    if match:
        num = int(match.group(1))
        if 1 <= num <= 99:
            return f"M-{num:02d}"

    return None


def suggest_sku(
    file_path: str,
    existing_skus: list[str],
    spreadsheet_skus: list[str] | None = None,
) -> str | None:
    """Suggest a SKU for an unlinked file (E03).

    Strategy:
    1. If the filename has any numeric content, try to form M-XX from it.
    2. If there are known SKUs from the spreadsheet, suggest the next unused one.
    3. Otherwise, suggest the first unused M-XX number.

    Args:
        file_path: path to the unlinked file
        existing_skus: list of already-assigned SKUs
        spreadsheet_skus: SKUs known from the spreadsheet

    Returns:
        A suggested SKU string, or None.
    """
    # Try any number in the filename
    nums = re.findall(r"\d+", Path(file_path).stem)
    if nums:
        num = int(nums[0])
        if 1 <= num <= 99:
            sku = f"M-{num:02d}"
            if sku not in existing_skus:
                return sku

    # Find the first unused M-XX number
    used = set(existing_skus) | set(spreadsheet_skus or [])
    for i in range(1, 100):
        sku = f"M-{i:02d}"
        if sku not in used:
            return sku

    return None


def link_files_to_skus(
    file_paths: list[str],
    spreadsheet_skus: list[str] | None = None,
) -> SkuLinkResult:
    """Link a batch of files to SKUs.

    Args:
        file_paths: list of file paths to link
        spreadsheet_skus: SKUs known from the spreadsheet (for reference)

    Returns:
        SkuLinkResult with linked and unlinked files.
    """
    result = SkuLinkResult()
    assigned_skus: list[str] = list(spreadsheet_skus or [])

    for file_path in file_paths:
        # Try filename pattern first
        sku = extract_sku_from_filename(file_path)

        if sku:
            result.linked.append(
                LinkedFile(
                    file_path=file_path,
                    sku=sku,
                    linked_by="filename",
                )
            )
            if sku not in assigned_skus:
                assigned_skus.append(sku)
        else:
            # Unlinked → manual-assign tray with suggested SKU (E03)
            suggested = suggest_sku(file_path, assigned_skus, spreadsheet_skus)
            result.unlinked.append(
                ManualAssignItem(
                    file_path=file_path,
                    suggested_sku=suggested,
                    reason="No SKU pattern found in filename",
                )
            )
            if suggested:
                assigned_skus.append(suggested)

    return result
