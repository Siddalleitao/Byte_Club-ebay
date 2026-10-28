"""Intake agent — extracts structured attributes from part evidence.

For each part the agent:
1. Calls read_image on each photo (main-tier vision model)
2. Calls ocr on the primary photo (EasyOCR cross-check on part-number label)
3. Calls parse_sheet for the spreadsheet row
4. Calls translate_note for Arabic notes (keeps original beside translation)
5. Builds a prompt with <data> blocks and sends to the main-tier model
6. Runs CP1 validation on the result
7. Returns typed JSON only

Runs in parallel with asyncio, never on the chat path.
Caches by file hash (FIXTURE_MODE replays recorded model output).

Allowed tools: read_image, ocr, parse_sheet, translate_note only.
The agent returns typed JSON only. It never mutates part status (Rule b).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.data.audit import write_audit
from app.domain.cp1_rules import (
    CP1ValidationResult,
    FieldResult,
    validate_part,
)
from app.domain.enums import AttributeStatus, EvidenceKind, PartStatus, TrustTier
from app.models.gateway import GatewayConfig, ModelTier, call_json
from app.semantic.intake_prompt import (
    SellerCard,
    build_evidence_block,
    build_request_message,
    get_system_prompt,
)
from app.semantic.intake_tools import (
    OCRResult,
    parse_sheet,
    read_image,
    translate_note,
)
from app.semantic.intake_tools import ocr as ocr_tool
from app.guardrails.quarantine import record_quarantine
from app.guardrails.screen import screen_text

logger = logging.getLogger(__name__)


@dataclass
class IntakePartInput:
    """Input data for ingesting a single part.

    Attributes:
        sku: the part SKU (e.g. 'M-01')
        photo_paths: list of photo file paths
        spreadsheet_path: path to the spreadsheet
        note_path: path to the donor note (optional)
        donor_vehicle_csv: path to donor_vehicle.csv (optional)
        inventory_csv: path to inventory.csv (optional)
        part_id: existing part ID in the database (optional)
        seller_id: seller ID
    """

    sku: str
    photo_paths: list[str] = field(default_factory=list)
    spreadsheet_path: str | None = None
    note_path: str | None = None
    donor_vehicle_csv: str | None = None
    inventory_csv: str | None = None
    part_id: str | None = None
    seller_id: str | None = None


@dataclass
class IntakeResult:
    """Result of ingesting a single part.

    Attributes:
        sku: the part SKU
        part_id: the part ID (created if not provided)
        extracted: the raw extracted JSON from the model
        cp1_result: the CP1 validation result
        vision_results: per-photo vision results
        ocr_result: the OCR result
        sheet_row: the spreadsheet row
        translated_note: the translated note (if Arabic)
        evidence_rows: evidence rows to write to DB
        attribute_rows: attribute rows to write to DB
        error: error message if ingestion failed
    """

    sku: str
    part_id: str | None = None
    extracted: dict[str, Any] = field(default_factory=dict)
    cp1_result: CP1ValidationResult | None = None
    vision_results: list[dict[str, Any]] = field(default_factory=list)
    ocr_result: OCRResult | None = None
    sheet_row: dict[str, Any] = field(default_factory=dict)
    translated_note: dict[str, Any] = field(default_factory=dict)
    evidence_rows: list[dict] = field(default_factory=list)
    attribute_rows: list[dict] = field(default_factory=list)
    error: str | None = None


async def ingest_part(
    part_input: IntakePartInput,
    *,
    config: GatewayConfig | None = None,
    seller_card: SellerCard | None = None,
) -> IntakeResult:
    """Ingest a single part through the Intake agent + CP1.

    This is async so multiple parts can run in parallel.
    The actual LLM calls are synchronous (the gateway is sync), but
    the async wrapper allows parallel execution via asyncio.gather.
    """
    result = IntakeResult(sku=part_input.sku)
    config = config or GatewayConfig()
    seller_card = seller_card or SellerCard.from_context_file()

    try:
        # Run all tool calls in parallel
        vision_task = _run_vision_tools(part_input, config)
        ocr_task = _run_ocr_tool(part_input)
        sheet_task = _run_sheet_tool(part_input)
        note_task = _run_note_tool(part_input, config)

        vision_results, ocr_result, sheet_row, translated_note = await asyncio.gather(
            vision_task, ocr_task, sheet_task, note_task,
        )

        result.vision_results = vision_results
        result.ocr_result = ocr_result
        result.sheet_row = sheet_row
        result.translated_note = translated_note

        # Build evidence blocks for the model prompt
        evidence_blocks = _build_evidence_blocks(
            vision_results, ocr_result, sheet_row, translated_note, part_input,
        )

        # Build and send the intake request
        system = get_system_prompt()
        messages = build_request_message(evidence_blocks, seller_card, sku=part_input.sku)

        model_result = call_json(
            tier=ModelTier.MAIN,
            messages=messages,
            system=system,
            part_id=part_input.part_id,
            config=config,
        )
        result = build_intake_result(
            part_input, model_result.get("parsed", {}),
            vision_results=vision_results, ocr_result=ocr_result,
            sheet_row=sheet_row, translated_note=translated_note,
        )

    except Exception as e:
        result.error = str(e)
        logger.exception("Intake failed for %s", part_input.sku)

    return result


def build_intake_result(
    part_input: IntakePartInput,
    extracted: dict[str, Any],
    *,
    vision_results: list[dict[str, Any]],
    ocr_result: OCRResult | None,
    sheet_row: dict[str, str] | None,
    translated_note: dict[str, Any] | None,
) -> IntakeResult:
    """Everything after the model call: CP1 validation (rules), then attribute and evidence rows.

    The model's JSON is T3: it only becomes attributes through CP1.
    """
    result = IntakeResult(sku=part_input.sku, part_id=part_input.part_id)
    result.vision_results, result.ocr_result = vision_results, ocr_result
    result.sheet_row, result.translated_note = sheet_row, translated_note
    result.extracted = extracted or {}
    source_texts = _collect_source_texts(vision_results, ocr_result, sheet_row, translated_note, part_input)
    primary_vision = vision_results[0] if vision_results else None
    note_text = None
    if translated_note:
        note_text = translated_note.get("translation") or translated_note.get("original")
    result.cp1_result = validate_part(
        result.extracted,
        sku=part_input.sku,
        source_texts=source_texts,
        ocr_result={
            "oem_number": ocr_result.oem_number if ocr_result else None,
            "raw_text": ocr_result.raw_text if ocr_result else "",
        },
        vision_result=primary_vision,
        sheet_row=sheet_row if sheet_row else None,
        note_text=note_text,
    )
    result.attribute_rows = _build_attribute_rows(part_input, result.cp1_result)
    result.evidence_rows = _build_evidence_rows(
        part_input, result.cp1_result, vision_results, ocr_result, translated_note or {},
    )
    return result


async def ingest_parts_parallel(
    parts: list[IntakePartInput],
    *,
    config: GatewayConfig | None = None,
    seller_card: SellerCard | None = None,
) -> list[IntakeResult]:
    """Ingest multiple parts in parallel with asyncio.

    Never runs on the chat path — this is a background checkpoint.
    """
    config = config or GatewayConfig()
    seller_card = seller_card or SellerCard.from_context_file()

    tasks = [ingest_part(p, config=config, seller_card=seller_card) for p in parts]
    return await asyncio.gather(*tasks)


# ── Tool runners (async wrappers) ────────────────────────────


async def _run_vision_tools(
    part_input: IntakePartInput,
    config: GatewayConfig,
) -> list[dict[str, Any]]:
    """Run read_image on each photo in parallel."""
    if not part_input.photo_paths:
        return []

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(
            None,
            lambda p=path: read_image(p, part_id=part_input.part_id, config=config),
        )
        for path in part_input.photo_paths
    ]
    return await asyncio.gather(*tasks)


async def _run_ocr_tool(part_input: IntakePartInput) -> OCRResult | None:
    """Run OCR on the first photo (the label photo)."""
    if not part_input.photo_paths:
        return None

    make = None
    if part_input.spreadsheet_path:
        sheet = parse_sheet(part_input.spreadsheet_path, part_input.sku)
        make = sheet.get("make")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: ocr_tool(part_input.photo_paths[0], make=make),
    )


async def _run_sheet_tool(part_input: IntakePartInput) -> dict[str, Any]:
    """Run parse_sheet for the spreadsheet row."""
    if not part_input.spreadsheet_path:
        return {}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: parse_sheet(part_input.spreadsheet_path, part_input.sku),
    )


async def _run_note_tool(
    part_input: IntakePartInput,
    config: GatewayConfig,
) -> dict[str, Any]:
    """Run translate_note for the donor note."""
    if not part_input.note_path:
        return {}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: translate_note(part_input.note_path, part_id=part_input.part_id, config=config),
    )


# ── Evidence block builder ───────────────────────────────────


def _build_evidence_blocks(
    vision_results: list[dict[str, Any]],
    ocr_result: OCRResult | None,
    sheet_row: dict[str, Any],
    translated_note: dict[str, Any],
    part_input: IntakePartInput,
) -> list:
    """Build evidence blocks for the model prompt."""
    blocks = []

    # Spreadsheet row
    if sheet_row:
        sheet_json = json.dumps(sheet_row, ensure_ascii=False, indent=2)
        blocks.append(build_evidence_block(
            source=f"sheet:{part_input.sku}",
            content=sheet_json,
        ))

    # OCR text
    if ocr_result and ocr_result.raw_text:
        blocks.append(build_evidence_block(
            source=f"ocr:{Path(part_input.photo_paths[0]).name if part_input.photo_paths else 'unknown'}",
            content=f"OCR text: {ocr_result.raw_text}\nNumbers found: {json.dumps(ocr_result.numbers)}",
        ))

    # Vision results (per photo)
    for i, vr in enumerate(vision_results):
        photo_name = Path(part_input.photo_paths[i]).name if i < len(part_input.photo_paths) else f"photo_{i}"
        blocks.append(build_evidence_block(
            source=f"photo:{photo_name}",
            content=f"Vision reading: {json.dumps(vr, ensure_ascii=False)}",
        ))

    # Donor note
    if translated_note:
        original = translated_note.get("original", "")
        translation = translated_note.get("translation", "")
        blocks.append(build_evidence_block(
            source=f"note:{Path(part_input.note_path).name if part_input.note_path else 'unknown'}",
            content=f"Original: {original}\nTranslation: {translation}",
        ))

    return blocks


# ── Source text collector ────────────────────────────────────


def _collect_source_texts(
    vision_results: list[dict[str, Any]],
    ocr_result: OCRResult | None,
    sheet_row: dict[str, Any],
    translated_note: dict[str, Any],
    part_input: IntakePartInput,
) -> list[str]:
    """Collect all source texts for E48 validation."""
    texts: list[str] = []

    # Spreadsheet values
    if sheet_row:
        for v in sheet_row.values():
            if v:
                texts.append(str(v))

    # OCR text
    if ocr_result and ocr_result.raw_text:
        texts.append(ocr_result.raw_text)

    # Vision results
    for vr in vision_results:
        for v in vr.values():
            if isinstance(v, str) and v:
                texts.append(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        texts.append(item)
                    elif isinstance(item, dict):
                        for iv in item.values():
                            if isinstance(iv, str):
                                texts.append(iv)

    # Note text
    if translated_note:
        texts.append(translated_note.get("original", ""))
        texts.append(translated_note.get("translation", ""))

    return texts


# ── Attribute and evidence row builders ──────────────────────


def _build_attribute_rows(
    part_input: IntakePartInput,
    cp1_result: CP1ValidationResult,
) -> list[dict]:
    """Build attribute rows for writing to the database."""
    rows = []
    for field in cp1_result.fields:
        rows.append({
            "id": str(uuid.uuid4()),
            "part_id": part_input.part_id,
            "name": field.name,
            "value": str(field.value) if field.value is not None else None,
            "status": field.status.value,
            "source": field.source,
        })
    return rows


def _build_evidence_rows(
    part_input: IntakePartInput,
    cp1_result: CP1ValidationResult,
    vision_results: list[dict[str, Any]],
    ocr_result: OCRResult | None,
    translated_note: dict[str, Any],
) -> list[dict]:
    """Build evidence rows for writing to the database."""
    rows = []

    # OCR evidence
    if ocr_result:
        rows.append({
            "id": str(uuid.uuid4()),
            "part_id": part_input.part_id,
            "kind": EvidenceKind.OCR_TEXT.value,
            "source_ref": f"ocr:{part_input.sku}",
            "content": ocr_result.raw_text,
            "raw_data": {
                "numbers": ocr_result.numbers,
                "oem_number": ocr_result.oem_number,
                "ambiguous": ocr_result.ambiguous,
                "has_aftermarket": ocr_result.has_aftermarket,
            },
        })

    # Vision evidence (per photo)
    for i, vr in enumerate(vision_results):
        photo_name = Path(part_input.photo_paths[i]).name if i < len(part_input.photo_paths) else f"photo_{i}"
        rows.append({
            "id": str(uuid.uuid4()),
            "part_id": part_input.part_id,
            "kind": EvidenceKind.PHOTO_REGION.value,
            "source_ref": f"photo:{photo_name}",
            "content": json.dumps(vr, ensure_ascii=False),
            "raw_data": vr,
        })

    # Donor note evidence
    if translated_note:
        rows.append({
            "id": str(uuid.uuid4()),
            "part_id": part_input.part_id,
            "kind": EvidenceKind.DONOR_NOTE.value if translated_note.get("language") == "en" else EvidenceKind.ARABIC_NOTE.value,
            "source_ref": f"note:{Path(part_input.note_path).name if part_input.note_path else 'unknown'}",
            "content": translated_note.get("translation", ""),
            "raw_data": {
                "original": translated_note.get("original", ""),
                "language": translated_note.get("language", "unknown"),
            },
        })

    # Spreadsheet evidence
    if cp1_result and cp1_result.sku:
        rows.append({
            "id": str(uuid.uuid4()),
            "part_id": part_input.part_id,
            "kind": EvidenceKind.SPREADSHEET_CELL.value,
            "source_ref": f"sheet:{cp1_result.sku}",
            "content": json.dumps(cp1_result.sku, ensure_ascii=False),
            "raw_data": {"sku": cp1_result.sku},
        })

    # CP1 conflict evidence
    for ev in cp1_result.evidence_rows:
        rows.append({
            "id": str(uuid.uuid4()),
            "part_id": part_input.part_id,
            "kind": ev.get("kind", EvidenceKind.OCR_TEXT.value),
            "source_ref": ev.get("source_ref", "cp1"),
            "content": ev.get("content", ""),
            "raw_data": ev.get("raw_data", {}),
        })

    return rows


_SCREENED_KINDS = {EvidenceKind.OCR_TEXT.value, EvidenceKind.DONOR_NOTE.value, EvidenceKind.ARABIC_NOTE.value,
                   EvidenceKind.SPREADSHEET_CELL.value}

# ── Database writer ──────────────────────────────────────────


def save_intake_to_db(
    conn: sqlite3.Connection,
    result: IntakeResult,
    *,
    seller_id: str = "S-001",
) -> str:
    """Save intake results to the database.

    Creates the part record if it doesn't exist, then writes
    attribute and evidence rows.

    Returns the part_id.
    """
    part_id = result.part_id
    if not part_id:
        # Attach to the seller's existing part for this SKU (e.g. created by seeding or CP0)
        existing = conn.execute(
            "SELECT id FROM parts WHERE seller_id = ? AND sku = ?", (seller_id, result.sku)
        ).fetchone()
        part_id = existing[0] if existing else None
    if not part_id:
        part_id = str(uuid.uuid4())
        # Create the part record
        conn.execute(
            """INSERT INTO parts (id, seller_id, sku, title, part_type, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (part_id, seller_id, result.sku, f"Part {result.sku}", "side-view mirror", PartStatus.INGESTED.value),
        )

    # Part status is not written here: only the supervisor sets a status (Rule b).

    # Write attribute rows
    for attr in result.attribute_rows:
        attr["part_id"] = part_id  # ensure part_id is set
        conn.execute(
            """INSERT OR REPLACE INTO attributes (id, part_id, name, value, status)
               VALUES (?, ?, ?, ?, ?)""",
            (attr["id"], part_id, attr["name"], attr["value"], attr["status"]),
        )
        # Ledger history: every value is a version with its source (TRD 3).
        conn.execute(
            """INSERT INTO attribute_versions
               (id, attribute_id, part_id, version, value, status, source, reason, actor)
               VALUES (?, ?, ?, (SELECT COALESCE(MAX(version), 0) + 1 FROM attribute_versions
                                 WHERE attribute_id = ?), ?, ?, ?, 'intake extraction', 'intake_agent')""",
            (str(uuid.uuid4()), attr["id"], part_id, attr["id"], attr["value"], attr["status"],
             attr.get("source")),
        )

    # Write evidence rows; ingested text (OCR, notes, sheet cells) is screened and quarantined (E53).
    for ev in result.evidence_rows:
        ev["part_id"] = part_id  # ensure part_id is set
        if ev["kind"] in _SCREENED_KINDS and ev.get("content"):
            screen = screen_text(str(ev["content"]), source=ev["source_ref"])
            if screen.quarantine:
                record_quarantine(conn, screen, channel="ingest", tier=TrustTier.T2,
                                  seller_id=seller_id, part_id=part_id)
        raw_data = ev.get("raw_data", {})
        raw_json = json.dumps(raw_data, ensure_ascii=False) if isinstance(raw_data, dict) else str(raw_data)
        conn.execute(
            """INSERT INTO evidence (id, part_id, kind, source_ref, content, raw_data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ev["id"], part_id, ev["kind"], ev["source_ref"], ev["content"], raw_json),
        )

    # Write attribute_evidence links
    for attr in result.attribute_rows:
        if attr["value"] is not None:
            # Find the matching evidence by source
            source = attr.get("source", "")
            if not source:
                continue
            # Match evidence by source — try multiple matching strategies
            for ev in result.evidence_rows:
                ev_source = ev.get("source_ref", "")
                # Direct substring match
                if source in ev_source or ev_source in source:
                    conn.execute(
                        """INSERT INTO attribute_evidence (id, attribute_id, evidence_id, claim_type, claim_value)
                           VALUES (?, ?, ?, ?, ?)""",
                        (str(uuid.uuid4()), attr["id"], ev["id"], "fact", attr["value"]),
                    )
                    break
            else:
                # Fallback: match by evidence kind
                kind_map = {
                    "part_number": "ocr_text",
                    "condition_notes": "photo_region",
                    "position": "photo_region",
                    "colour": "photo_region",
                    "features": "photo_region",
                    "label_readable": "photo_region",
                    "make": "spreadsheet_cell",
                    "model": "spreadsheet_cell",
                    "year": "spreadsheet_cell",
                    "part_type": "spreadsheet_cell",
                    "donor_year": "donor_note",
                    "storage_location": "spreadsheet_cell",
                    "price": "spreadsheet_cell",
                }
                expected_kind = kind_map.get(attr["name"])
                if expected_kind:
                    for ev in result.evidence_rows:
                        if ev.get("kind") == expected_kind:
                            conn.execute(
                                """INSERT INTO attribute_evidence (id, attribute_id, evidence_id, claim_type, claim_value)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (str(uuid.uuid4()), attr["id"], ev["id"], "fact", attr["value"]),
                            )
                            break

    # Log audit event
    write_audit(conn, part_id=part_id, event_type="extract", actor="intake_agent", action="fields extracted",
                source="photos, spreadsheet and donor note (CP1)", field=None, before=None,
                after=sorted(a["name"] for a in conn.execute(
                    "SELECT name FROM attributes WHERE part_id = ? AND value IS NOT NULL", (part_id,)).fetchall()),
                detail={"sku": result.sku,
                        "edge_cases": result.cp1_result.edge_cases if result.cp1_result else []})

    conn.commit()
    return part_id
