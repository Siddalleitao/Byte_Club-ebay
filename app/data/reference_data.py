"""Read-only reference data: compatibility reference and checklist (TRD 6, tier 6).

The app never writes these files. The compatibility reference is versioned
and checksummed: loading it verifies the SHA-256 in compat_ref.sha256 and
refuses a tampered file (TRD 6 attack surface "Compatibility dataset").
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REFERENCE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reference"
COMPAT_CSV = REFERENCE_DIR / "compat_ref.csv"
COMPAT_SHA256 = REFERENCE_DIR / "compat_ref.sha256"
CHECKLIST_YAML = REFERENCE_DIR / "checklist_v0.1.yaml"


class ReferenceIntegrityError(RuntimeError):
    """A reference file does not match its recorded checksum."""


def normalise_part_number(part_number: str | None) -> str:
    """Key for exact lookup: trimmed and case-insensitive. No fuzzy matching (Rule c)."""
    return (part_number or "").strip().upper()


# ── Compatibility reference ─────────────────────────────────


@dataclass(frozen=True)
class CompatRow:
    part_number: str
    make: str
    model: str
    year_from: int | None
    year_to: int | None
    market: str | None
    position: str | None
    ref_version: str


@dataclass(frozen=True)
class CompatReference:
    """The approved compatibility reference, indexed for exact part-number lookup."""

    version: str
    checksum: str
    rows_by_part_number: dict[str, tuple[CompatRow, ...]]

    def rows_for(self, part_number: str | None) -> tuple[CompatRow, ...]:
        return self.rows_by_part_number.get(normalise_part_number(part_number), ())

    @property
    def row_count(self) -> int:
        return sum(len(rows) for rows in self.rows_by_part_number.values())


def load_compat_reference(
    csv_path: Path = COMPAT_CSV, checksum_path: Path = COMPAT_SHA256
) -> CompatReference:
    """Load the reference after verifying its checksum. Raises ReferenceIntegrityError."""
    data = csv_path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    expected = checksum_path.read_text(encoding="utf-8").split()[0].strip().lower()
    if actual != expected:
        raise ReferenceIntegrityError(
            f"{csv_path.name} checksum mismatch: expected {expected[:12]}…, got {actual[:12]}…"
        )

    index: dict[str, list[CompatRow]] = {}
    versions: set[str] = set()
    for raw in csv.DictReader(data.decode("utf-8-sig").splitlines()):
        row = CompatRow(
            part_number=raw["part_number"].strip(),
            make=raw["make"].strip(),
            model=raw["model"].strip(),
            year_from=int(raw["year_from"]) if raw.get("year_from") else None,
            year_to=int(raw["year_to"]) if raw.get("year_to") else None,
            market=(raw.get("market") or "").strip() or None,
            position=(raw.get("position") or "").strip().lower() or None,
            ref_version=raw["ref_version"].strip(),
        )
        index.setdefault(normalise_part_number(row.part_number), []).append(row)
        versions.add(row.ref_version)
    if len(versions) != 1:
        raise ReferenceIntegrityError(f"{csv_path.name} mixes reference versions: {sorted(versions)}")

    return CompatReference(
        version=versions.pop(),
        checksum=actual,
        rows_by_part_number={k: tuple(v) for k, v in index.items()},
    )


@lru_cache(maxsize=1)
def get_compat_reference() -> CompatReference:
    """Process-wide verified reference (verified once, at first use / startup)."""
    return load_compat_reference()


# ── Checklist ───────────────────────────────────────────────


@dataclass(frozen=True)
class ChecklistItemDef:
    item_id: str
    description: str
    passes_when: str
    category: str
    blocking: bool
    blocks: str  # "ready" | "approval"
    on_fail: str | None


@dataclass(frozen=True)
class Checklist:
    version: str
    date: str
    purpose: str
    disclaimer: str
    checksum: str
    items: tuple[ChecklistItemDef, ...]
    shipping_ranges: dict[str, Any]

    def item(self, item_id: str) -> ChecklistItemDef:
        return next(i for i in self.items if i.item_id == item_id)


def load_checklist(path: Path = CHECKLIST_YAML) -> Checklist:
    raw_bytes = path.read_bytes()
    data = yaml.safe_load(raw_bytes.decode("utf-8"))
    items = tuple(
        ChecklistItemDef(
            item_id=i["id"],
            description=i["description"],
            passes_when=i.get("passes_when", ""),
            category=i.get("category", ""),
            blocking=bool(i.get("blocking", True)),
            blocks=i.get("blocks", "ready"),
            on_fail=i.get("on_fail"),
        )
        for i in data["items"]
    )
    return Checklist(
        version=str(data["version"]),
        date=str(data.get("date", "")),
        purpose=data.get("purpose", ""),
        disclaimer=(data.get("disclaimer") or "").strip(),
        checksum=hashlib.sha256(raw_bytes).hexdigest(),
        items=items,
        shipping_ranges=data.get("shipping_ranges", {}),
    )


@lru_cache(maxsize=1)
def get_checklist() -> Checklist:
    return load_checklist()
