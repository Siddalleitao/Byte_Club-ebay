"""Part facts — one read of a part's ledger state, for readiness and rendering.

Effective status (TRD 3): an attribute counts as Verified when it is stored
as Verified, or when it is Extracted and its current version was recorded
from two or more independent source families that agree. CP1 records
agreeing sources joined with "+" (e.g. "spreadsheet+photo:vision"); sources
that disagree are stored as Conflicting instead. Two readers of the same
photo (vision and OCR) are one family, not two.

Per Rule (g): pure logic + DB reads. No LLM.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from app.domain.enums import AttributeStatus, EvidenceKind
from app.domain.ledger import LOW_RISK_FIELDS

CONFIRMED = (AttributeStatus.SELLER_CONFIRMED, AttributeStatus.VERIFIED)

# Canonical field → names the same business field is stored under.
ALIASES: dict[str, tuple[str, ...]] = {
    "position": ("position", "side"),
    "test_status": ("test_status", "functionality"),
    "condition": ("condition_notes", "condition", "defect"),
    "donor_year": ("donor_year",),
    "donor_model": ("donor_model",),
    "donor_make": ("donor_make",),
    "part_number": ("part_number",),
    "part_type": ("part_type",),
}
DONOR_FIELDS = ("donor_year", "donor_model", "donor_make", "year", "model", "make")

_SOURCE_FAMILY = {
    "sheet": "spreadsheet",
    "spreadsheet": "spreadsheet",
    "photo": "photo",
    "ocr": "photo",
    "vision": "photo",
    "label ocr": "photo",
    "note": "donor_note",
    "donor_note": "donor_note",
    "donor note": "donor_note",
    "donor_vehicle": "donor_vehicle",
    "inventory": "inventory",
}


def source_families(source: str | None) -> set[str]:
    """Independent source families named in a CP1 source pointer such as 'spreadsheet+photo:vision'."""
    families: set[str] = set()
    for part in re.split(r"\s*\+\s*", source or ""):
        head = part.split(":", 1)[0].strip().lower()
        if head:
            families.add(_SOURCE_FAMILY.get(head, head))
    return families


@dataclass
class AttrFact:
    name: str
    value: str | None
    status: AttributeStatus
    source: str | None
    effective_status: AttributeStatus

    @property
    def confirmed(self) -> bool:
        return self.effective_status in CONFIRMED

    @property
    def is_fact(self) -> bool:
        """Can be stated as a plain fact in a listing (TRD 3)."""
        return self.confirmed or (
            self.effective_status == AttributeStatus.EXTRACTED and self.name in LOW_RISK_FIELDS
        )


@dataclass
class Photo:
    evidence_id: str
    pointer: str | None
    views: list[str]


@dataclass
class PartFacts:
    part_id: str
    seller_id: str
    sku: str | None
    part_type: str | None
    status: str
    attrs: dict[str, AttrFact]
    donors: list[dict]
    photos: list[Photo]
    questions: list[dict]
    claims: list[dict]
    seller_answers: list[dict]
    product_family: str = "side_view_mirror"
    inventory_currency: str | None = None
    listing_currency: str | None = None
    seller_overrides: int = 0
    draft: dict | None = None
    extra: dict = field(default_factory=dict)

    def get(self, canonical: str) -> AttrFact | None:
        for name in ALIASES.get(canonical, (canonical,)):
            if name in self.attrs:
                return self.attrs[name]
        return None

    def open_questions(self) -> list[dict]:
        return [q for q in self.questions if q["status"] == "open"]

    def seller_rounds(self, field_name: str) -> int:
        """How many times the seller has answered about this field (TRD 8 'seller rounds')."""
        names = set(ALIASES.get(field_name, (field_name,))) | {field_name}
        return sum(1 for a in self.seller_answers if a.get("field") in names)


_AMOUNT = re.compile(r"\d+(?:\.\d+)?")
_CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR", "د.إ": "AED"}
_CURRENCY_CODE = re.compile(r"\b(GBP|AED|USD|EUR)\b", re.I)


def price_amount(value: str | None) -> float | None:
    m = _AMOUNT.search(value or "")
    return float(m.group(0)) if m else None


def price_currency(facts: PartFacts, value: str, source: str | None) -> str | None:
    """The currency a price is labelled in: symbol or code in the value, a currency
    attribute, or the inventory currency for prices from the inventory sheet (E40)."""
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in value:
            return code
    m = _CURRENCY_CODE.search(value)
    if m:
        return m.group(1).upper()
    currency_attr = facts.attrs.get("currency")
    if currency_attr and currency_attr.value:
        return currency_attr.value.upper()
    if source and source.split(":")[0].strip().lower() == "inventory":
        return facts.inventory_currency
    return None


def load_part_facts(conn: sqlite3.Connection, part_id: str) -> PartFacts:
    part = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if part is None:
        raise LookupError(f"No part {part_id!r}")

    attrs: dict[str, AttrFact] = {}
    for row in conn.execute("SELECT * FROM attributes WHERE part_id = ?", (part_id,)).fetchall():
        version = conn.execute(
            "SELECT source FROM attribute_versions WHERE attribute_id = ? ORDER BY version DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        status = AttributeStatus(row["status"])
        source = version["source"] if version else None
        effective = status
        if status == AttributeStatus.EXTRACTED and row["value"] is not None and len(source_families(source)) >= 2:
            effective = AttributeStatus.VERIFIED
        attrs[row["name"]] = AttrFact(row["name"], row["value"], status, source, effective)

    evidence = [dict(r) for r in conn.execute(
        "SELECT id, kind, source_ref, raw_data FROM evidence WHERE part_id = ?", (part_id,)
    ).fetchall()]
    photos = []
    seller_answers = []
    for ev in evidence:
        raw = _json(ev["raw_data"])
        if ev["kind"] == EvidenceKind.PHOTO_REGION.value:
            photos.append(Photo(ev["id"], ev["source_ref"], list(raw.get("views", []))))
        elif ev["kind"] == EvidenceKind.SELLER_ANSWER.value:
            seller_answers.append(raw)

    profile = conn.execute(
        "SELECT product_family, inventory_currency, listing_currency FROM seller_profiles WHERE seller_id = ?",
        (part["seller_id"],),
    ).fetchone()
    overrides = conn.execute(
        """SELECT COUNT(*) AS c FROM audit_events
           WHERE part_id = ? AND event_type = 'seller_change'
             AND json_extract(detail_json, '$.marks_for_review') = 1""",
        (part_id,),
    ).fetchone()["c"]
    draft = conn.execute(
        "SELECT * FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1", (part_id,)
    ).fetchone()

    return PartFacts(
        part_id=part_id,
        seller_id=part["seller_id"],
        sku=part["sku"],
        part_type=part["part_type"],
        status=part["status"],
        attrs=attrs,
        donors=[dict(r) for r in conn.execute(
            "SELECT * FROM donor_vehicles WHERE part_id = ? ORDER BY created_at", (part_id,)
        ).fetchall()],
        photos=photos,
        questions=[dict(r) for r in conn.execute(
            "SELECT * FROM questions WHERE part_id = ?", (part_id,)
        ).fetchall()],
        claims=[dict(r) for r in conn.execute(
            "SELECT * FROM claims WHERE part_id = ?", (part_id,)
        ).fetchall()],
        seller_answers=seller_answers,
        product_family=profile["product_family"] if profile else "side_view_mirror",
        inventory_currency=profile["inventory_currency"] if profile else None,
        listing_currency=profile["listing_currency"] if profile else None,
        seller_overrides=overrides,
        draft=dict(draft) if draft else None,
    )


def _json(raw: str | None) -> dict:
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
