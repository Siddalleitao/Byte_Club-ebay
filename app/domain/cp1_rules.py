"""CP1 checkpoint — plain rules validation (no LLM).

CP1 is deterministic. It validates the Intake agent's extracted JSON
against schema rules and source-or-drop logic.

Edge cases handled:
  E12: Ambiguous characters (0/O, 8/B, 1/I) — never guess
  E13: OCR and vision reading different part numbers → Conflicting, keep both
  E14: Multiple numbers on one label, only OEM pattern used for lookup
  E15: Aftermarket brand on OEM donor → recorded as seen, no OEM claim
  E23: Vision position estimate disagrees with text → flagged
  E47: Malformed JSON → one repair attempt, then Missing
  E48: Text value not in source or OCR text → dropped and logged

Per Rule (g): domain services never call an LLM. This module is pure logic.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import AttributeStatus, EvidenceKind, TrustTier

logger = logging.getLogger(__name__)

# ── Validation constants ─────────────────────────────────────

YEAR_MIN = 1990
YEAR_MAX = 2027

# Part-number pattern: alphanumeric with dashes, 6-20 chars
# Must not contain '?' to be "verified" — '?' means unreadable (E12)
PART_NUMBER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{4,19}[A-Z0-9]$", re.IGNORECASE)

# Fields that can be extracted and their allowed values
ALLOWED_POSITIONS = {"left", "right", "driver", "passenger"}
ALLOWED_FEATURES = {"heated", "power-fold", "indicator", "camera"}
ALLOWED_PART_TYPES = {
    "side-view mirror", "door mirror", "wing mirror",
    "mirror", "mirror assembly",
    "airbag module", "headlight", "taillight", "bumper", "fender",
}

# Length caps (from text normaliser, applied to all string fields)
FIELD_LENGTH_CAPS: dict[str, int] = {
    "part_number": 30,
    "make": 50,
    "model": 50,
    "brand": 50,
    "colour": 30,
    "condition_notes": 500,
    "storage_location": 20,
    "part_type": 50,
    "default": 2000,
}


# ── Field result ─────────────────────────────────────────────


@dataclass
class FieldResult:
    """Result of validating a single extracted field.

    Attributes:
        name: field name (e.g. 'part_number', 'year', 'position')
        value: the validated value (or None if dropped/missing)
        status: Extracted, Conflicting, or Missing
        source: the source pointer (e.g. 'sheet:B14', 'photo:M-01_p1.jpg')
        conflicts: list of conflicting values with their sources (for E13, E16)
        dropped_reason: why a value was dropped (E48: not in source/OCR)
        edge_cases: which edge-case IDs this field triggered
    """

    name: str
    value: Any = None
    status: AttributeStatus = AttributeStatus.MISSING
    source: str | None = None
    conflicts: list[dict[str, str]] = field(default_factory=list)
    dropped_reason: str | None = None
    edge_cases: list[str] = field(default_factory=list)


@dataclass
class CP1ValidationResult:
    """Complete result of CP1 validation for a part.

    Attributes:
        sku: the part SKU
        fields: validated field results
        edge_cases: all edge-case IDs triggered
        evidence_rows: evidence rows to write (OCR text, conflicts, etc.)
        json_repaired: whether JSON repair was attempted (E47)
        json_repair_succeeded: whether the repair succeeded
    """

    sku: str
    fields: list[FieldResult] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)
    evidence_rows: list[dict] = field(default_factory=list)
    json_repaired: bool = False
    json_repair_succeeded: bool = False


# ── JSON repair (E47) ────────────────────────────────────────


def repair_json(raw: str) -> tuple[str, bool]:
    """Attempt one JSON repair pass (E47).

    Returns (repaired_text, success).
    If the text is already valid JSON, returns (text, True).
    """
    # Try as-is first
    try:
        json.loads(raw)
        return raw, True
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)

    # Try to parse again
    try:
        json.loads(cleaned)
        return cleaned, True
    except json.JSONDecodeError:
        return cleaned, False


# ── Source-or-drop validation (E48) ──────────────────────────


def _normalize_for_search(text: str) -> str:
    """Normalise text for source-search matching (case-insensitive, no spaces)."""
    return re.sub(r"\s+", "", text).lower()


def value_in_source(value: str, source_texts: list[str]) -> bool:
    """E48: Check that a text value appears in at least one source or OCR text.

    If the value does not appear in any source text, it must be dropped and logged.
    Numeric values are checked loosely (as substrings of normalised source text).
    """
    if not value or not source_texts:
        return False

    value_norm = _normalize_for_search(str(value))

    for source_text in source_texts:
        source_norm = _normalize_for_search(source_text)
        if value_norm in source_norm:
            return True

    # For part numbers, also check with dashes removed
    value_nodash = value_norm.replace("-", "")
    for source_text in source_texts:
        source_nodash = _normalize_for_search(source_text).replace("-", "")
        if value_nodash in source_nodash:
            return True

    return False


# ── Part number validation (E12, E13, E14, E15) ──────────────


def validate_part_number(
    vision_number: str | None,
    ocr_number: str | None,
    *,
    vision_source: str = "vision",
    ocr_source: str = "ocr",
) -> FieldResult:
    """Validate part number from vision and OCR sources.

    E12: Ambiguous characters → '?' in the result, never guess
    E13: OCR and vision disagree → Conflicting, keep both
    E14: Only OEM pattern used for lookup (handled by OCR tool)
    E15: Aftermarket brand → recorded as seen, no OEM claim
    """
    edge_cases: list[str] = []
    conflicts: list[dict[str, str]] = []

    # Check for ambiguous characters (E12)
    has_ambiguous = False
    for num in [vision_number, ocr_number]:
        if num and "?" in num:
            has_ambiguous = True
            if "E12" not in edge_cases:
                edge_cases.append("E12")

    # If both are None → Missing
    if not vision_number and not ocr_number:
        return FieldResult(
            name="part_number",
            value=None,
            status=AttributeStatus.MISSING,
            edge_cases=edge_cases,
        )

    # If only one source has a number
    if vision_number and not ocr_number:
        return FieldResult(
            name="part_number",
            value=vision_number,
            status=AttributeStatus.EXTRACTED,
            source=vision_source,
            edge_cases=edge_cases,
        )

    if ocr_number and not vision_number:
        return FieldResult(
            name="part_number",
            value=ocr_number,
            status=AttributeStatus.EXTRACTED,
            source=ocr_source,
            edge_cases=edge_cases,
        )

    # Both sources have a number — check if they agree
    assert vision_number is not None and ocr_number is not None

    # Normalise for comparison
    v_norm = vision_number.upper().replace(" ", "")
    o_norm = ocr_number.upper().replace(" ", "")

    if v_norm == o_norm:
        # Agreement → Extracted (or Unverified if ambiguous)
        status = AttributeStatus.UNVERIFIED if has_ambiguous else AttributeStatus.EXTRACTED
        return FieldResult(
            name="part_number",
            value=vision_number,
            status=status,
            source=f"{vision_source}+{ocr_source}",
            edge_cases=edge_cases,
        )

    # Disagreement → Conflicting (E13)
    edge_cases.append("E13")
    conflicts.append({"value": vision_number, "source": vision_source})
    conflicts.append({"value": ocr_number, "source": ocr_source})

    # Use the vision number as the primary value but mark Conflicting
    return FieldResult(
        name="part_number",
        value=vision_number,
        status=AttributeStatus.CONFLICTING,
        source=vision_source,
        conflicts=conflicts,
        edge_cases=edge_cases,
    )


# ── Position validation (E23) ────────────────────────────────


def validate_position(
    text_position: str | None,
    vision_position: str | None,
    *,
    text_source: str = "sheet",
    vision_source: str = "vision",
) -> FieldResult:
    """Validate position from text and vision sources.

    E23: A vision position estimate that disagrees with the text is flagged.
    """
    edge_cases: list[str] = []

    # Normalise positions
    def _norm(pos: str | None) -> str | None:
        if not pos:
            return None
        pos = pos.lower().strip()
        if pos in ("driver", "driver side", "driver-side"):
            return "driver"
        if pos in ("passenger", "passenger side", "passenger-side"):
            return "passenger"
        if pos in ("left", "l"):
            return "left"
        if pos in ("right", "r"):
            return "right"
        return pos

    text_pos = _norm(text_position)
    vision_pos = _norm(vision_position)

    # If only one source has position
    if text_pos and not vision_pos:
        return FieldResult(
            name="position",
            value=text_pos,
            status=AttributeStatus.EXTRACTED,
            source=text_source,
        )

    if vision_pos and not text_pos:
        return FieldResult(
            name="position",
            value=vision_pos,
            status=AttributeStatus.EXTRACTED,
            source=vision_source,
        )

    if not text_pos and not vision_pos:
        return FieldResult(name="position", value=None, status=AttributeStatus.MISSING)

    # Both have position — check agreement
    assert text_pos is not None and vision_pos is not None

    # "driver" and "left" are compatible in LHD markets
    compatible = _positions_compatible(text_pos, vision_pos)

    if compatible:
        # Use the explicit left/right if available, otherwise driver/passenger
        value = text_pos if text_pos in ("left", "right") else vision_pos
        return FieldResult(
            name="position",
            value=value,
            status=AttributeStatus.EXTRACTED,
            source=f"{text_source}+{vision_source}",
        )

    # Disagreement (E23)
    edge_cases.append("E23")
    return FieldResult(
        name="position",
        value=text_pos,  # text takes precedence
        status=AttributeStatus.CONFLICTING,
        source=text_source,
        conflicts=[
            {"value": text_pos, "source": text_source},
            {"value": vision_pos, "source": vision_source},
        ],
        edge_cases=edge_cases,
    )


def _positions_compatible(text_pos: str, vision_pos: str) -> bool:
    """Check if text and vision positions are compatible.

    In LHD markets (GCC, US, EU): driver = left, passenger = right.
    In RHD markets (UK, Japan): driver = right, passenger = left.
    We don't know the market at CP1, so driver/passenger is ambiguous
    with left/right. We only flag a conflict when left != right.
    """
    # Explicit left vs right disagreement
    lr = {"left", "right"}
    if text_pos in lr and vision_pos in lr:
        return text_pos == vision_pos

    # driver/passenger vs left/right — ambiguous, not a conflict
    return True


# ── Year validation ──────────────────────────────────────────


def validate_year(
    year: int | None,
    source: str | None = None,
) -> FieldResult:
    """Validate year is in range 1990–2027."""
    if year is None:
        return FieldResult(name="year", value=None, status=AttributeStatus.MISSING)

    if not isinstance(year, int):
        return FieldResult(
            name="year",
            value=None,
            status=AttributeStatus.MISSING,
            dropped_reason=f"Year is not an integer: {year}",
        )

    if year < YEAR_MIN or year > YEAR_MAX:
        return FieldResult(
            name="year",
            value=None,
            status=AttributeStatus.MISSING,
            source=source,
            dropped_reason=f"Year {year} outside range {YEAR_MIN}-{YEAR_MAX}",
        )

    return FieldResult(
        name="year",
        value=year,
        status=AttributeStatus.EXTRACTED,
        source=source,
    )


# ── Donor year conflict detection (E16) ──────────────────────


def validate_donor_year(
    sources: list[dict[str, Any]],
) -> FieldResult:
    """Validate donor year from multiple sources.

    E16: If sources disagree on donor year, mark Conflicting and keep all values.
    """
    years: list[dict[str, Any]] = []
    for s in sources:
        year = s.get("year") or s.get("donor_year")
        if year is not None:
            years.append({"value": int(year), "source": s.get("source", "unknown")})

    if not years:
        return FieldResult(name="donor_year", value=None, status=AttributeStatus.MISSING)

    # Check for conflicting years
    unique_years = {y["value"] for y in years}
    if len(unique_years) > 1:
        # Conflicting (E16)
        return FieldResult(
            name="donor_year",
            value=None,  # No single value when conflicting
            status=AttributeStatus.CONFLICTING,
            source=years[0]["source"],
            conflicts=years,
            edge_cases=["E16"],
        )

    # All agree
    year_val = years[0]["value"]
    if year_val < YEAR_MIN or year_val > YEAR_MAX:
        return FieldResult(
            name="donor_year",
            value=None,
            status=AttributeStatus.MISSING,
            source=years[0]["source"],
            dropped_reason=f"Donor year {year_val} outside range {YEAR_MIN}-{YEAR_MAX}",
        )

    return FieldResult(
        name="donor_year",
        value=year_val,
        status=AttributeStatus.EXTRACTED,
        source=" + ".join(y["source"] for y in years),
    )


# ── String field validation with length cap ──────────────────


def validate_string_field(
    name: str,
    value: str | None,
    source: str | None,
    *,
    source_texts: list[str] | None = None,
    allowed_values: set[str] | None = None,
) -> FieldResult:
    """Validate a string field with length cap and source-or-drop (E48)."""
    if not value:
        return FieldResult(name=name, value=None, status=AttributeStatus.MISSING)

    value = str(value).strip()

    # Length cap
    cap = FIELD_LENGTH_CAPS.get(name, FIELD_LENGTH_CAPS["default"])
    if len(value) > cap:
        value = value[:cap]

    # Allowed values check
    if allowed_values and value.lower() not in allowed_values:
        return FieldResult(
            name=name,
            value=None,
            status=AttributeStatus.MISSING,
            source=source,
            dropped_reason=f"Value '{value}' not in allowed values for {name}",
        )

    # Source-or-drop check (E48)
    if source_texts is not None and not value_in_source(value, source_texts):
        return FieldResult(
            name=name,
            value=None,
            status=AttributeStatus.MISSING,
            source=source,
            dropped_reason=f"E48: Value '{value}' not found in any source text",
            edge_cases=["E48"],
        )

    return FieldResult(
        name=name,
        value=value,
        status=AttributeStatus.EXTRACTED,
        source=source,
    )


# ── Full CP1 validation ──────────────────────────────────────


def validate_part(
    extracted: dict[str, Any],
    *,
    sku: str,
    source_texts: list[str],
    ocr_result: dict[str, Any] | None = None,
    vision_result: dict[str, Any] | None = None,
    sheet_row: dict[str, Any] | None = None,
    note_text: str | None = None,
) -> CP1ValidationResult:
    """Run full CP1 validation on extracted JSON.

    This is the main entry point for CP1. It takes the Intake agent's
    extracted JSON and validates every field against the rules.

    Args:
        extracted: the extracted JSON dict from the Intake agent
        sku: the part SKU
        source_texts: all source texts (spreadsheet, notes, OCR) for E48 checks
        ocr_result: OCR result dict (for E13 conflict detection)
        vision_result: vision result dict (for E13 conflict detection)
        sheet_row: the spreadsheet row data (for year/position source)
        note_text: the donor note text (for donor year source)

    Returns:
        CP1ValidationResult with all validated fields.
    """
    result = CP1ValidationResult(sku=sku)
    edge_cases: list[str] = []

    # ── E47: JSON repair already handled by call_json, but check here too ──
    if not isinstance(extracted, dict):
        result.json_repaired = True
        result.json_repair_succeeded = False
        result.edge_cases.append("E47")
        return result

    # ── Part number (E12, E13, E14, E15) ──
    vision_pn = vision_result.get("part_number") if vision_result else extracted.get("part_number")
    ocr_pn = None
    if ocr_result:
        ocr_pn = ocr_result.get("oem_number")

    pn_field = validate_part_number(
        vision_number=vision_pn,
        ocr_number=ocr_pn,
        vision_source="photo:vision",
        ocr_source="photo:ocr",
    )
    result.fields.append(pn_field)
    edge_cases.extend(pn_field.edge_cases)

    # E15: aftermarket brand
    is_aftermarket = extracted.get("is_aftermarket", False)
    if is_aftermarket:
        if "E15" not in edge_cases:
            edge_cases.append("E15")
        brand_field = FieldResult(
            name="brand",
            value=extracted.get("brand"),
            status=AttributeStatus.EXTRACTED,
            source="photo:vision",
            edge_cases=["E15"],
        )
        result.fields.append(brand_field)

    # ── Position (E23) ──
    text_pos = None
    if sheet_row:
        text_pos = sheet_row.get("side")
    vision_pos = vision_result.get("position") if vision_result else extracted.get("position")

    pos_field = validate_position(
        text_position=text_pos,
        vision_position=vision_pos,
        text_source="spreadsheet",
        vision_source="photo:vision",
    )
    result.fields.append(pos_field)
    edge_cases.extend(pos_field.edge_cases)

    # ── Year ──
    year_val = None
    year_source = None
    if sheet_row and sheet_row.get("year"):
        try:
            year_val = int(sheet_row["year"])
            year_source = "spreadsheet"
        except (ValueError, TypeError):
            pass
    if year_val is None and extracted.get("year"):
        try:
            year_val = int(extracted["year"])
            year_source = "intake_agent"
        except (ValueError, TypeError):
            pass

    year_field = validate_year(year_val, year_source)
    result.fields.append(year_field)

    # ── Donor year (E16: conflict across sources) ──
    donor_year_sources: list[dict[str, Any]] = []
    if sheet_row and sheet_row.get("year"):
        try:
            donor_year_sources.append({"year": int(sheet_row["year"]), "source": "spreadsheet"})
        except (ValueError, TypeError):
            pass
    # Parse donor year from note text
    if note_text:
        note_year = _extract_year_from_text(note_text)
        if note_year:
            donor_year_sources.append({"year": note_year, "source": "donor_note"})
    # Donor vehicle CSV year
    if extracted.get("donor_year"):
        donor_year_sources.append({"year": extracted["donor_year"], "source": "donor_vehicle"})

    donor_year_field = validate_donor_year(donor_year_sources)
    result.fields.append(donor_year_field)
    edge_cases.extend(donor_year_field.edge_cases)

    # ── Make ──
    make_val = sheet_row.get("make") if sheet_row else extracted.get("make")
    make_field = validate_string_field(
        "make", make_val, "spreadsheet" if sheet_row else "intake_agent",
        source_texts=source_texts,
    )
    result.fields.append(make_field)

    # ── Model ──
    model_val = sheet_row.get("model") if sheet_row else extracted.get("model")
    model_field = validate_string_field(
        "model", model_val, "spreadsheet" if sheet_row else "intake_agent",
        source_texts=source_texts,
    )
    result.fields.append(model_field)

    # ── Part type ──
    pt_val = sheet_row.get("part_type") if sheet_row else extracted.get("part_type")
    pt_field = validate_string_field(
        "part_type", pt_val, "spreadsheet" if sheet_row else "intake_agent",
        source_texts=source_texts,
        allowed_values=ALLOWED_PART_TYPES,
    )
    result.fields.append(pt_field)

    # ── Colour ──
    colour_val = vision_result.get("colour") if vision_result else extracted.get("colour")
    colour_field = validate_string_field(
        "colour", colour_val, "photo:vision" if colour_val else None,
        source_texts=source_texts,
    )
    result.fields.append(colour_field)

    # ── Condition notes ──
    cn_val = vision_result.get("condition_notes") if vision_result else extracted.get("condition_notes")
    # Also check donor note for condition info
    if note_text and not cn_val:
        cn_val = _extract_condition_from_note(note_text)
    cn_field = validate_string_field(
        "condition_notes", cn_val,
        "photo:vision" if vision_result and vision_result.get("condition_notes") else "donor_note",
        source_texts=source_texts,
    )
    result.fields.append(cn_field)

    # ── Features ──
    features = []
    if vision_result and vision_result.get("features"):
        features = vision_result["features"]
    elif extracted.get("features"):
        features = extracted["features"]
    features = [f for f in features if f in ALLOWED_FEATURES]
    result.fields.append(FieldResult(
        name="features",
        value=features if features else None,
        status=AttributeStatus.EXTRACTED if features else AttributeStatus.MISSING,
        source="photo:vision" if features else None,
    ))

    # ── Storage location ──
    sl_val = extracted.get("storage_location")
    sl_field = validate_string_field(
        "storage_location", sl_val, "inventory" if sl_val else None,
        source_texts=source_texts,
    )
    result.fields.append(sl_field)

    # ── Price ──
    price_val = extracted.get("price")
    if price_val is not None:
        try:
            price_val = float(price_val)
        except (ValueError, TypeError):
            price_val = None
    result.fields.append(FieldResult(
        name="price",
        value=price_val,
        status=AttributeStatus.EXTRACTED if price_val is not None else AttributeStatus.MISSING,
        source="inventory" if price_val is not None else None,
    ))

    # ── Label readable ──
    label_readable = True
    if vision_result:
        label_readable = vision_result.get("label_readable", True)
    result.fields.append(FieldResult(
        name="label_readable",
        value=label_readable,
        status=AttributeStatus.EXTRACTED,
        source="photo:vision",
    ))

    enforce_source_rules(result.fields)

    # Create evidence rows for conflicts (E13: keep both crops)
    for f in result.fields:
        if f.status == AttributeStatus.CONFLICTING and f.conflicts:
            for conflict in f.conflicts:
                result.evidence_rows.append({
                    "kind": EvidenceKind.OCR_TEXT.value,
                    "source_ref": conflict.get("source", "unknown"),
                    "content": f"{f.name}: {conflict.get('value', '')}",
                    "raw_data": {"conflict": True, "field": f.name, **conflict},
                })

    # Log dropped fields (E48)
    for f in result.fields:
        if f.dropped_reason:
            logger.info(
                "CP1 dropped field %s for %s: %s",
                f.name, sku, f.dropped_reason,
            )
            result.evidence_rows.append({
                "kind": "dropped_field",
                "source_ref": f.source or "unknown",
                "content": f"{f.name} dropped: {f.dropped_reason}",
                "raw_data": {"field": f.name, "reason": f.dropped_reason},
            })

    result.edge_cases = list(set(edge_cases))
    return result


# ── Source rules (TRD 6) ─────────────────────────────────────

OCR_FIELDS = frozenset({"part_number", "part_numbers"})
SELLER_OR_TEST_ONLY = frozenset({"test_status", "functionality", "authenticity", "fitment", "compatibility"})


def _from_ocr(source: str | None) -> bool:
    return any(part.strip().lower().startswith(("ocr", "photo:ocr", "label ocr"))
               for part in re.split(r"\+", source or ""))


def enforce_source_rules(fields: list[FieldResult]) -> list[FieldResult]:
    """Drop values from sources that may not establish them (TRD 6 surface "Text in a photo").

    OCR text may populate only the part number. Test status, function,
    authenticity and fitment never come from intake at all (Rules c, d).
    """
    for f in fields:
        if f.value is None:
            continue
        if f.name in SELLER_OR_TEST_ONLY:
            f.dropped_reason = "only from a seller answer, a test record or a reference lookup (Rules c, d)"
        elif _from_ocr(f.source) and f.name not in OCR_FIELDS and not _has_non_ocr_source(f.source):
            f.dropped_reason = "OCR text may populate only the part number (TRD 6)"
        else:
            continue
        f.value, f.status = None, AttributeStatus.MISSING
    return fields


def _has_non_ocr_source(source: str | None) -> bool:
    return any(not part.strip().lower().startswith(("ocr", "photo:ocr", "label ocr"))
               for part in re.split(r"\+", source or "") if part.strip())


# ── Helpers ──────────────────────────────────────────────────


def _extract_year_from_text(text: str) -> int | None:
    """Extract a 4-digit year (1990-2027) from text."""
    for match in re.finditer(r"\b(19\d{2}|20[0-2]\d)\b", text):
        year = int(match.group())
        if YEAR_MIN <= year <= YEAR_MAX:
            return year
    return None


def _extract_condition_from_note(text: str) -> str | None:
    """Extract condition notes from a donor note text."""
    # Look for defect/damage keywords
    keywords = [
        "crack", "cracked", "broken", "scratch", "scratched", "scuff", "scuffed",
        "dent", "dented", "chip", "chipped", "wear", "worn", "damage", "damaged",
        "tab", "mounting", "hole",
    ]
    text_lower = text.lower()
    for kw in keywords:
        if kw in text_lower:
            # Find the sentence containing the keyword
            sentences = re.split(r"[.\n]", text)
            for sentence in sentences:
                if kw in sentence.lower():
                    return sentence.strip()
            return text[:200]
    return None
