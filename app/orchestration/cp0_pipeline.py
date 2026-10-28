"""CP0 gate — upload pipeline orchestrator.

Runs all CP0 steps in sequence for each uploaded item:
1. File gate (photos): type/size, decode, HEIC→JPEG, resize, hash, pHash, blur/brightness
2. Text normaliser: NFKC, strip controls, Arabic-Indic digits, length cap, encoding retry
3. Spreadsheet parser: header mapping, unmerge, skip blanks, duplicate SKU flag
4. SKU linking: filename pattern + spreadsheet ref, manual-assign tray
5. Injection screen (app/guardrails/screen.py): normalise, unmask look-alikes, EN+AR patterns on
   notes, sheet cells, file names and photo metadata; quarantine with text, source and reason
6. Privacy masking: phones/names/plates/VINs
7. Evidence rows: every ingested snippet → Evidence row (kind, pointer, raw_text, language, trust T2)

Every ingested snippet becomes an Evidence row with:
- kind: evidence kind enum
- pointer: file path, cell ref, or photo region
- raw_text: the original text (kept as evidence per E10)
- language: detected language
- trust_tier: T2 (ingested content)

Nothing in process_uploads calls an LLM. save_evidence_to_db may run the fast-tier classifier
(orchestration) on text the screen flagged only by its wording.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.data.audit import write_audit
from app.domain.enums import EvidenceKind, TrustTier
from app.orchestration.file_gate import PhotoGateResult, process_file, check_duplicate
from app.guardrails.quarantine import record_quarantine, seller_of_part
from app.guardrails.screen import ScreenResult, screen_text
from app.orchestration.injection_classifier import classify
from app.orchestration.llm_policy import BACKGROUND_TIMEOUT_S, ModelClient
from app.orchestration.privacy_mask import MaskResult, mask_text
from app.orchestration.sku_linker import SkuLinkResult, link_files_to_skus
from app.orchestration.spreadsheet_parser import ParsedSpreadsheet, parse_spreadsheet
from app.orchestration.text_normaliser import NormalisedText, detect_language, normalise

# ── Edge-case IDs handled by CP0 ──
# Note: E06 (several parts in one photo) requires vision detection at CP1,
# not CP0. It is listed in TRD 2 as a CP0 edge case but cannot be detected
# without an LLM/vision model, so it is handled at CP1 instead.
CP0_EDGE_CASES = [
    "E01", "E02", "E03", "E04", "E05",
    "E07", "E08", "E09", "E10",
    "E51", "E52", "E53",
]


@dataclass
class EvidenceRecord:
    """An evidence row to be inserted into the database.

    Every ingested snippet becomes one of these.
    """

    id: str
    part_id: str | None  # None if not yet linked to a part
    kind: str  # EvidenceKind value
    source_ref: str  # file path, cell ref, photo region
    content: str  # normalised text or description
    raw_text: str  # original text kept as evidence (E10)
    language: str  # detected language
    trust_tier: str  # always T2 for ingested content
    confidence: float | None = None
    injection_quarantined: bool = False
    injection_hits: list[dict] = field(default_factory=list)
    privacy_masked: bool = False


@dataclass
class CP0Result:
    """Complete result of running CP0 on an upload batch.

    Attributes:
        evidence_records: all evidence rows created
        photo_results: per-photo file gate results
        spreadsheet_result: parsed spreadsheet (if any)
        sku_link_result: SKU linking results
        injection_hits: all injection hits found across all text
        quarantined_items: text that was quarantined (E53)
        manual_assign_items: files needing manual SKU assignment (E03)
        edge_cases_triggered: all E-IDs that fired
        retake_requested_photos: photos that need retakes (E05)
        rejected_files: files rejected at the gate (E01)
    """

    evidence_records: list[EvidenceRecord] = field(default_factory=list)
    photo_results: list[PhotoGateResult] = field(default_factory=list)
    spreadsheet_result: ParsedSpreadsheet | None = None
    sku_link_result: SkuLinkResult | None = None
    injection_hits: list[dict] = field(default_factory=list)
    quarantined_items: list[dict] = field(default_factory=list)
    flagged_items: list[dict] = field(default_factory=list)  # instruction-like wording only: classifier decides
    manual_assign_items: list[dict] = field(default_factory=list)
    edge_cases_triggered: list[str] = field(default_factory=list)
    retake_requested_photos: list[str] = field(default_factory=list)
    rejected_files: list[dict] = field(default_factory=list)


def _new_id() -> str:
    return str(uuid.uuid4())


def _add_unique(items: list[str], new_items: list[str]) -> None:
    for item in new_items:
        if item not in items:
            items.append(item)


def _screen(result: "CP0Result", text: str, source: str, sku: str | None, edge_cases: list[str]) -> ScreenResult:
    """Screen one ingested snippet; quarantine or flag it for the classifier (E53)."""
    screen = screen_text(text, source=source)
    item = {"source": source, "sku": sku, "text": text, "reason": screen.reason, "flags": screen.flags,
            "hits": screen.hits, "screen": screen}
    if screen.quarantine:
        result.quarantined_items.append(item)
        for hit in screen.hits:
            result.injection_hits.append({"source": source, **hit})
        if "E53" not in edge_cases:
            edge_cases.append("E53")
    elif screen.needs_classifier:
        result.flagged_items.append(item)
    return screen


def _metadata_text(path: str) -> str:
    """Free-text photo metadata (EXIF description, user comment, XP comment, JPEG comment), read
    before the file gate strips it, so it can be screened."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            values = [exif.get(270), exif.get(37510), exif.get(40092), img.info.get("comment")]
    except Exception:
        return ""
    texts = []
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("utf-16-le" if b"\x00" in value else "utf-8", errors="ignore")
        if value:
            texts.append(str(value).replace("\x00", "").strip())
    return " | ".join(t for t in texts if t)


def process_uploads(
    photo_paths: list[str] | None = None,
    note_paths: list[str] | None = None,
    spreadsheet_path: str | None = None,
    *,
    photo_output_dir: str | Path | None = None,
    existing_photo_hashes: dict[str, str] | None = None,
    existing_phashes: dict[str, str] | None = None,
) -> CP0Result:
    """Run the full CP0 pipeline on a batch of uploads.

    Args:
        photo_paths: paths to photo files to process
        note_paths: paths to text note files to process
        spreadsheet_path: path to a CSV or XLSX spreadsheet
        photo_output_dir: directory for converted photos
        existing_photo_hashes: SHA-256→SKU cache for dedup
        existing_phashes: perceptual-hash→SKU cache for visual dedup

    Returns:
        CP0Result with all evidence records and processing details.
    """
    result = CP0Result()
    edge_cases: list[str] = []

    # ── Step 1: Parse spreadsheet (if provided) ──
    spreadsheet_skus: list[str] = []
    spreadsheet_rows: list[dict[str, str]] = []

    if spreadsheet_path:
        parsed = parse_spreadsheet(spreadsheet_path)
        result.spreadsheet_result = parsed
        _add_unique(edge_cases, parsed.edge_cases)

        for row in parsed.rows:
            sku = row.get("sku", "").strip()
            if sku:
                spreadsheet_skus.append(sku)
            spreadsheet_rows.append(row)

    # ── Step 2: SKU linking for photos and notes ──
    all_files: list[str] = []
    if photo_paths:
        all_files.extend(photo_paths)
    if note_paths:
        all_files.extend(note_paths)

    if all_files:
        sku_result = link_files_to_skus(all_files, spreadsheet_skus)
        result.sku_link_result = sku_result

        # Track unlinked files for manual-assign tray (E03)
        for item in sku_result.unlinked:
            result.manual_assign_items.append({
                "file_path": item.file_path,
                "suggested_sku": item.suggested_sku,
                "reason": item.reason,
            })
            if "E03" not in edge_cases:
                edge_cases.append("E03")

    # Build a file→sku lookup
    file_to_sku: dict[str, str | None] = {}
    if result.sku_link_result:
        for linked in result.sku_link_result.linked:
            file_to_sku[linked.file_path] = linked.sku
        for unlinked in result.sku_link_result.unlinked:
            file_to_sku[unlinked.file_path] = None

    # ── Step 2b: Screen file names and photo metadata (TRD 6 surface "File name or metadata") ──
    for path in all_files:
        _screen(result, Path(path).name, f"file_name:{Path(path).name}", file_to_sku.get(path), edge_cases)
    for path in photo_paths or []:
        metadata = _metadata_text(path)
        if metadata:
            _screen(result, metadata, f"metadata:{Path(path).name}", file_to_sku.get(path), edge_cases)

    # ── Step 3: Process photos through the file gate ──
    if photo_paths:
        phash_cache: dict[str, str] = dict(existing_phashes or {})

        for photo_path in photo_paths:
            gate_result = process_file(
                photo_path,
                output_dir=photo_output_dir,
                existing_hashes=existing_photo_hashes,
            )
            result.photo_results.append(gate_result)

            if not gate_result.accepted:
                result.rejected_files.append({
                    "file_path": photo_path,
                    "reason": gate_result.rejection_reason,
                    "edge_cases": gate_result.edge_cases,
                })
                _add_unique(edge_cases, gate_result.edge_cases)
                continue

            _add_unique(edge_cases, gate_result.edge_cases)

            # Check for duplicates (E04)
            if gate_result.perceptual_hash:
                dup_sku = check_duplicate(gate_result.perceptual_hash, phash_cache)
                if dup_sku:
                    if "E04" not in edge_cases:
                        edge_cases.append("E04")
                # Add to cache
                sku = file_to_sku.get(photo_path)
                if sku:
                    phash_cache[gate_result.perceptual_hash] = sku

            # Check for retake request (E05)
            if gate_result.retake_requested:
                result.retake_requested_photos.append(photo_path)

            # Create evidence record for the photo
            sku = file_to_sku.get(photo_path)
            evidence = EvidenceRecord(
                id=_new_id(),
                part_id=None,  # will be linked later
                kind=EvidenceKind.PHOTO_REGION.value,
                source_ref=gate_result.converted_path or photo_path,
                content=f"Photo: {Path(photo_path).name} ({gate_result.width}x{gate_result.height})",
                raw_text="",
                language="unknown",
                trust_tier=TrustTier.T2.value,
                confidence=gate_result.blur_score / 100.0 if gate_result.blur_score else None,
            )
            result.evidence_records.append(evidence)

    # ── Step 4: Process text notes ──
    if note_paths:
        for note_path in note_paths:
            raw_bytes = Path(note_path).read_bytes()

            # Try decoding for the normaliser
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # E09: retry as Windows-1256
                try:
                    text = raw_bytes.decode("windows-1256")
                except UnicodeDecodeError:
                    text = raw_bytes.decode("utf-8", errors="replace")

            # Normalise (E09, E10)
            norm_result = normalise(text, raw_bytes=raw_bytes)
            if norm_result.encoding_retry != "raw":
                if "E09" not in edge_cases:
                    edge_cases.append("E09")
            if norm_result.had_arabic_indic:
                if "E10" not in edge_cases:
                    edge_cases.append("E10")

            # Detect language
            language = detect_language(norm_result.normalised)

            # Injection screen (E53): hidden characters and look-alikes are checked on the raw text
            screen = _screen(result, text, f"note:{Path(note_path).name}", file_to_sku.get(note_path), edge_cases)

            # Privacy masking (E51, E52)
            mask_result = mask_text(norm_result.normalised)
            privacy_masked = (
                mask_result.phone_numbers_masked > 0
                or mask_result.names_masked > 0
                or mask_result.vins_masked > 0
                or mask_result.plates_masked > 0
            )

            # Determine evidence kind
            if language == "ar":
                kind = EvidenceKind.ARABIC_NOTE.value
            else:
                kind = EvidenceKind.DONOR_NOTE.value

            # Create evidence record
            sku = file_to_sku.get(note_path)
            evidence = EvidenceRecord(
                id=_new_id(),
                part_id=None,
                kind=kind,
                source_ref=note_path,
                content=mask_result.masked_text if privacy_masked else norm_result.normalised,
                raw_text=norm_result.raw_text,  # keep raw text as evidence (E10)
                language=language,
                trust_tier=TrustTier.T2.value,
                injection_quarantined=screen.quarantine,
                injection_hits=screen.hits,
                privacy_masked=privacy_masked,
            )
            result.evidence_records.append(evidence)

    # ── Step 5: Process spreadsheet cells as evidence ──
    for row in spreadsheet_rows:
        sku = row.get("sku", "").strip()
        for col_name, cell_value in row.items():
            if col_name == "sku" or not cell_value:
                continue

            # Normalise the cell value
            norm_result = normalise(cell_value)
            if norm_result.had_arabic_indic:
                if "E10" not in edge_cases:
                    edge_cases.append("E10")

            # Injection screen on every cell (E53)
            screen = _screen(result, cell_value, f"spreadsheet:{sku}:{col_name}", sku or None, edge_cases)

            # Privacy masking
            mask_result = mask_text(norm_result.normalised)
            privacy_masked = mask_result.phone_numbers_masked > 0 or mask_result.names_masked > 0

            language = detect_language(norm_result.normalised)

            evidence = EvidenceRecord(
                id=_new_id(),
                part_id=None,
                kind=EvidenceKind.SPREADSHEET_CELL.value,
                source_ref=f"spreadsheet:{sku}:{col_name}",
                content=mask_result.masked_text if privacy_masked else norm_result.normalised,
                raw_text=norm_result.raw_text,
                language=language,
                trust_tier=TrustTier.T2.value,
                injection_quarantined=screen.quarantine,
                injection_hits=screen.hits,
                privacy_masked=privacy_masked,
            )
            result.evidence_records.append(evidence)

    result.edge_cases_triggered = edge_cases
    return result


def save_evidence_to_db(
    conn: sqlite3.Connection,
    result: CP0Result,
    sku_to_part_id: dict[str, str] | None = None,
    *,
    seller_id: str | None = None,
    classifier: ModelClient | None = None,
) -> int:
    """Save CP0 evidence records to the database.

    Args:
        conn: SQLite connection
        result: CP0Result from process_uploads
        sku_to_part_id: mapping of SKU to part_id for linking evidence

    Returns:
        Number of evidence rows inserted.
    """
    count = 0
    for ev in result.evidence_records:
        # Try to resolve part_id from SKU mapping
        part_id = ev.part_id
        if part_id is None and sku_to_part_id:
            # Look up SKU from source_ref
            source = ev.source_ref
            for sku, pid in sku_to_part_id.items():
                if sku in source:
                    part_id = pid
                    break

        if part_id is None:
            continue  # skip unlinked evidence for now

        raw_data = json.dumps({
            "injection_quarantined": ev.injection_quarantined,
            "injection_hits": ev.injection_hits,
            "privacy_masked": ev.privacy_masked,
            "raw_text": ev.raw_text,
            "language": ev.language,
        })

        conn.execute(
            """INSERT INTO evidence (id, part_id, kind, source_ref, content, confidence, raw_data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ev.id, part_id, ev.kind, ev.source_ref, ev.content, ev.confidence, raw_data),
        )
        count += 1

    # Quarantine records (shown to the seller) with the text, source and reason; audited.
    def part_for(item: dict) -> str | None:
        return (sku_to_part_id or {}).get(item.get("sku") or "")

    for item in result.quarantined_items:
        part_id = part_for(item)
        verdict = classify(item["text"], source=item["source"], model=classifier,
                           timeout_s=BACKGROUND_TIMEOUT_S) if classifier else None
        record_quarantine(conn, item["screen"], channel="ingest", tier=TrustTier.T2,
                          seller_id=seller_id or seller_of_part(conn, part_id), part_id=part_id, verdict=verdict)
    for item in result.flagged_items:  # wording only: the classifier decides; unavailable counts as suspicious
        verdict = classify(item["text"], source=item["source"], model=classifier, timeout_s=BACKGROUND_TIMEOUT_S)
        if verdict != "benign":
            part_id = part_for(item)
            record_quarantine(conn, item["screen"], channel="ingest", tier=TrustTier.T2,
                              seller_id=seller_id or seller_of_part(conn, part_id), part_id=part_id, verdict=verdict)

    # Log rejected files
    for r in result.rejected_files:
        write_audit(conn, seller_id=seller_id, event_type="file_rejected", actor="cp0_gate", action="file rejected",
                    source=f"upload:{Path(r['file_path']).name}", before=r["file_path"], after=None,
                    detail={"file": r["file_path"], "reason": r["reason"]})

    conn.commit()
    return count
