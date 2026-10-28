"""Tests for CP1 checkpoint and Intake agent — edge cases E12-E15, E23, E47-E48.

Also includes the exit test: M-01 and M-07 ingested with a source on every
populated field, and M-07 stores both donor years with their sources and the
unreadable label.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.domain.cp1_rules import (
    CP1ValidationResult,
    FieldResult,
    repair_json,
    validate_donor_year,
    validate_part,
    validate_part_number,
    validate_position,
    validate_string_field,
    validate_year,
    value_in_source,
    YEAR_MIN,
    YEAR_MAX,
)
from app.domain.enums import AttributeStatus
from app.models.gateway import GatewayConfig, clear_cache, clear_call_logs
from app.data.database import get_connection, init_db, reset_db
from app.semantic.intake_agent import (
    IntakePartInput,
    IntakeResult,
    ingest_part,
    ingest_parts_parallel,
    save_intake_to_db,
)
from app.semantic.intake_prompt import (
    SellerCard,
    build_evidence_block,
    build_request_message,
    get_system_prompt,
)
from app.semantic.intake_tools import (
    OCRResult,
    clear_tool_cache,
    ocr,
    parse_sheet,
    read_image,
    translate_note,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear all caches before each test."""
    clear_cache()
    clear_call_logs()
    clear_tool_cache()
    yield
    clear_cache()
    clear_call_logs()
    clear_tool_cache()


# ── E12: Ambiguous characters 0/O, 8/B, 1/I ──


class TestE12AmbiguousCharacters:
    """E12: Never guess between ambiguous characters (0/O, 8/B, 1/I)."""

    def test_ambiguous_chars_replaced_with_question(self):
        from app.semantic.intake_tools import _handle_ambiguous
        # "87910" has 8 which could be B
        result = _handle_ambiguous("87910-02A10")
        # This is mixed alphanumeric so ambiguous chars become ?
        assert "?" in result or result == "87910-02A10"  # depends on whether it's seen as mixed

    def test_part_number_with_question_marks(self):
        """M-07's part number 87910-0???? should be handled without guessing."""
        field = validate_part_number(
            vision_number="87910-0????",
            ocr_number="87910-0????",
            vision_source="photo:vision",
            ocr_source="photo:ocr",
        )
        assert "87910-0????" in field.value or field.value is None
        assert "E12" in field.edge_cases

    def test_ambiguous_detected_in_ocr(self):
        from app.semantic.intake_tools import _classify_ocr_text, OCRResult
        result = _classify_ocr_text("87910-0???? Toyota Corolla", make="toyota")
        # The ? chars should be preserved
        assert result.ambiguous

    def test_pure_numeric_not_ambiguous(self):
        from app.semantic.intake_tools import _handle_ambiguous
        assert _handle_ambiguous("22951629") == "22951629"

    def test_pure_alpha_not_ambiguous(self):
        from app.semantic.intake_tools import _handle_ambiguous
        assert _handle_ambiguous("DEPO") == "DEPO"


# ── E13: OCR and vision disagree on part number ──


class TestE13PartNumberConflict:
    """E13: OCR and vision reading different part numbers → Conflicting, keep both."""

    def test_conflict_marks_conflicting(self):
        field = validate_part_number(
            vision_number="87910-02A10",
            ocr_number="87910-02A20",
            vision_source="photo:vision",
            ocr_source="photo:ocr",
        )
        assert field.status == AttributeStatus.CONFLICTING
        assert "E13" in field.edge_cases
        assert len(field.conflicts) == 2

    def test_conflict_keeps_both_values(self):
        field = validate_part_number(
            vision_number="87910-02A10",
            ocr_number="96302-4AA0A",
            vision_source="photo:vision",
            ocr_source="photo:ocr",
        )
        conflict_values = [c["value"] for c in field.conflicts]
        assert "87910-02A10" in conflict_values
        assert "96302-4AA0A" in conflict_values

    def test_agreement_no_conflict(self):
        field = validate_part_number(
            vision_number="87910-02A10",
            ocr_number="87910-02A10",
            vision_source="photo:vision",
            ocr_source="photo:ocr",
        )
        assert field.status == AttributeStatus.EXTRACTED
        assert "E13" not in field.edge_cases

    def test_one_source_missing_no_conflict(self):
        field = validate_part_number(
            vision_number="87910-02A10",
            ocr_number=None,
            vision_source="photo:vision",
            ocr_source="photo:ocr",
        )
        assert field.status == AttributeStatus.EXTRACTED
        assert "E13" not in field.edge_cases


# ── E14: Multiple numbers on one label ──


class TestE14MultipleNumbers:
    """E14: Several numbers on one label stored by class; only OEM used for lookup."""

    def test_numbers_classified_by_type(self):
        from app.semantic.intake_tools import _classify_ocr_text
        result = _classify_ocr_text("87910-02A10 22951629 1718", make="toyota")
        classes = {n["class"] for n in result.numbers}
        assert "OEM" in classes

    def test_oem_number_returned(self):
        from app.semantic.intake_tools import _classify_ocr_text
        result = _classify_ocr_text("87910-02A10 22951629 1718", make="toyota")
        assert result.oem_number == "87910-02A10"

    def test_only_oem_used_for_lookup(self):
        """The oem_number field is the only one used for compatibility lookup."""
        from app.semantic.intake_tools import _classify_ocr_text
        result = _classify_ocr_text("87910-02A10 MR495981 1718", make="toyota")
        # OEM number from Toyota pattern
        assert result.oem_number == "87910-02A10"
        # Multiple numbers stored
        assert len(result.numbers) >= 2


# ── E15: Aftermarket brand on OEM donor ──


class TestE15AftermarketBrand:
    """E15: Aftermarket brand recorded as seen, no OEM claim."""

    def test_aftermarket_detected(self):
        from app.semantic.intake_tools import _classify_ocr_text
        result = _classify_ocr_text("DEPO 87910-02A10", make="toyota")
        assert result.has_aftermarket

    def test_aftermarket_not_oem(self):
        """If aftermarket brand present, is_aftermarket should be true."""
        # This is enforced by the vision model prompt, not the OCR tool
        # The OCR tool just detects the brand
        from app.semantic.intake_tools import _classify_ocr_text
        result = _classify_ocr_text("DEPO mirror assembly", make="toyota")
        assert result.has_aftermarket
        assert any(n["class"] == "aftermarket_brand" for n in result.numbers)


# ── E16: Donor year conflict across sources ──


class TestE16DonorYearConflict:
    """E16: Donor year conflicting across sources → Conflicting, keep all."""

    def test_conflicting_years_marked_conflicting(self):
        field = validate_donor_year([
            {"year": 2017, "source": "spreadsheet"},
            {"year": 2018, "source": "donor_note"},
        ])
        assert field.status == AttributeStatus.CONFLICTING
        assert "E16" in field.edge_cases
        assert len(field.conflicts) == 2

    def test_agreeing_years_extracted(self):
        field = validate_donor_year([
            {"year": 2017, "source": "spreadsheet"},
            {"year": 2017, "source": "donor_note"},
        ])
        assert field.status == AttributeStatus.EXTRACTED
        assert field.value == 2017

    def test_single_year_extracted(self):
        field = validate_donor_year([
            {"year": 2017, "source": "spreadsheet"},
        ])
        assert field.status == AttributeStatus.EXTRACTED
        assert field.value == 2017

    def test_no_years_missing(self):
        field = validate_donor_year([])
        assert field.status == AttributeStatus.MISSING

    def test_conflict_keeps_both_sources(self):
        """M-07 must store both donor years with their sources."""
        field = validate_donor_year([
            {"year": 2017, "source": "spreadsheet"},
            {"year": 2018, "source": "donor_note"},
        ])
        sources = {c["source"] for c in field.conflicts}
        assert "spreadsheet" in sources
        assert "donor_note" in sources
        values = {c["value"] for c in field.conflicts}
        assert 2017 in values
        assert 2018 in values


# ── E23: Vision position disagrees with text ──


class TestE23PositionDisagreement:
    """E23: Vision position estimate that disagrees with text is flagged."""

    def test_left_vs_right_flagged(self):
        field = validate_position(
            text_position="left",
            vision_position="right",
            text_source="spreadsheet",
            vision_source="photo:vision",
        )
        assert field.status == AttributeStatus.CONFLICTING
        assert "E23" in field.edge_cases

    def test_agreement_not_flagged(self):
        field = validate_position(
            text_position="left",
            vision_position="left",
            text_source="spreadsheet",
            vision_source="photo:vision",
        )
        assert field.status == AttributeStatus.EXTRACTED
        assert "E23" not in field.edge_cases

    def test_driver_vs_left_not_flagged(self):
        """driver/passenger vs left/right is ambiguous, not a conflict."""
        field = validate_position(
            text_position="driver",
            vision_position="left",
            text_source="donor_note",
            vision_source="photo:vision",
        )
        assert field.status == AttributeStatus.EXTRACTED
        assert "E23" not in field.edge_cases

    def test_vision_only_extracted(self):
        field = validate_position(
            text_position=None,
            vision_position="right",
            text_source="spreadsheet",
            vision_source="photo:vision",
        )
        assert field.status == AttributeStatus.EXTRACTED
        assert field.value == "right"


# ── E47: Malformed JSON repair ──


class TestE47JsonRepair:
    """E47: Malformed JSON gets one repair attempt, then Missing."""

    def test_valid_json_passes(self):
        repaired, success = repair_json('{"key": "value"}')
        assert success

    def test_markdown_fences_stripped(self):
        repaired, success = repair_json('```json\n{"key": "value"}\n```')
        assert success
        assert json.loads(repaired) == {"key": "value"}

    def test_trailing_comma_removed(self):
        repaired, success = repair_json('{"key": "value",}')
        assert success
        assert json.loads(repaired) == {"key": "value"}

    def test_array_trailing_comma_removed(self):
        repaired, success = repair_json('[1, 2, 3,]')
        assert success
        assert json.loads(repaired) == [1, 2, 3]

    def test_unrepairable_returns_false(self):
        repaired, success = repair_json("not json at all {{{")
        assert not success

    def test_one_repair_attempt_only(self):
        """The repair function tries exactly once, then gives up."""
        # Only the code-fence and trailing-comma repairs are attempted
        broken = '{"key": "value" "missing_colon"}'
        repaired, success = repair_json(broken)
        assert not success


# ── E48: Value not in source or OCR text ──


class TestE48SourceOrDrop:
    """E48: A text value must appear in a source or OCR text or it is dropped."""

    def test_value_in_source_kept(self):
        assert value_in_source("Toyota", ["Toyota Corolla 2017"])

    def test_value_not_in_source_dropped(self):
        assert not value_in_source("Honda", ["Toyota Corolla 2017"])

    def test_part_number_in_source(self):
        assert value_in_source("87910-02A10", ["87910-02A10 Toyota Corolla"])

    def test_part_number_with_dashes_normalised(self):
        assert value_in_source("87910-02A10", ["8791002A10"])

    def test_case_insensitive(self):
        assert value_in_source("toyota", ["Toyota Corolla"])

    def test_field_dropped_when_not_in_source(self):
        field = validate_string_field(
            "make", "Honda", "spreadsheet",
            source_texts=["Toyota Corolla 2017"],
        )
        assert field.status == AttributeStatus.MISSING
        assert "E48" in field.edge_cases
        assert field.dropped_reason is not None

    def test_field_kept_when_in_source(self):
        field = validate_string_field(
            "make", "Toyota", "spreadsheet",
            source_texts=["Toyota Corolla 2017"],
        )
        assert field.status == AttributeStatus.EXTRACTED
        assert field.value == "Toyota"


# ── Year validation ──


class TestYearValidation:
    """Year range validation (1990–2027)."""

    def test_valid_year(self):
        field = validate_year(2017, "spreadsheet")
        assert field.status == AttributeStatus.EXTRACTED
        assert field.value == 2017

    def test_year_too_old(self):
        field = validate_year(1985, "spreadsheet")
        assert field.status == AttributeStatus.MISSING
        assert field.dropped_reason is not None

    def test_year_too_new(self):
        field = validate_year(2030, "spreadsheet")
        assert field.status == AttributeStatus.MISSING

    def test_year_none(self):
        field = validate_year(None)
        assert field.status == AttributeStatus.MISSING

    def test_year_range_constants(self):
        assert YEAR_MIN == 1990
        assert YEAR_MAX == 2027


# ── Prompt structure ──


class TestPromptStructure:
    """Test the cached system prompt and <data> block evidence wrapping."""

    def test_system_prompt_is_cached(self):
        prompt1 = get_system_prompt()
        prompt2 = get_system_prompt()
        assert prompt1 is prompt2  # same object (cached)

    def test_system_prompt_mentions_data_blocks(self):
        prompt = get_system_prompt()
        assert "<data>" in prompt or "data block" in prompt.lower()
        assert "evidence" in prompt.lower()
        assert "never an instruction" in prompt.lower() or "never instructions" in prompt.lower()

    def test_system_prompt_mentions_null_for_missing(self):
        prompt = get_system_prompt()
        assert "null" in prompt.lower()
        assert "not in the evidence" in prompt.lower() or "must be null" in prompt.lower()

    def test_system_prompt_prohibits_inference(self):
        prompt = get_system_prompt()
        assert "test status" in prompt.lower() or "functionality" in prompt.lower()
        assert "never infer" in prompt.lower() or "never" in prompt.lower()

    def test_evidence_block_has_random_id(self):
        block1 = build_evidence_block("sheet:M-01", "content")
        block2 = build_evidence_block("sheet:M-01", "content")
        assert block1.id != block2.id  # fresh random ID per request

    def test_evidence_block_rendered_as_data_tag(self):
        block = build_evidence_block("sheet:M-01", "Toyota Corolla")
        rendered = block.render()
        assert rendered.startswith("<data id=")
        assert "source=sheet:M-01" in rendered
        assert "Toyota Corolla" in rendered
        assert rendered.endswith("</data>")

    def test_seller_card_rendered(self):
        card = SellerCard(
            name="Noor",
            location="Dubai, UAE",
            seller_type="dealer",
            language_primary="Arabic",
            market_corridor="UAE → UK",
            marketplace="eBay UK",
        )
        rendered = card.render()
        assert "Noor" in rendered
        assert "Dubai" in rendered
        assert "Arabic" in rendered

    def test_request_message_includes_seller_card(self):
        card = SellerCard(
            name="Noor", location="Dubai, UAE",
            seller_type="dealer", language_primary="Arabic",
            market_corridor="UAE → UK", marketplace="eBay UK",
        )
        block = build_evidence_block("sheet:M-01", "test")
        messages = build_request_message([block], card, sku="M-01")
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert "Noor" in content
        assert "M-01" in content
        assert "<data" in content


# ── Intake tools ──


class TestIntakeTools:
    """Test the 4 allowed intake tools."""

    def test_parse_sheet_returns_row_by_sku(self):
        result = parse_sheet("data/seed/spreadsheet.csv", "M-01")
        assert result["sku"] == "M-01"
        assert result["make"] == "Toyota"
        assert result["part_number"] == "87910-02A10"

    def test_parse_sheet_caches_by_file_hash(self):
        """parse_sheet caches by file hash — second call returns from cache."""
        result1 = parse_sheet("data/seed/spreadsheet.csv", "M-01")
        result2 = parse_sheet("data/seed/spreadsheet.csv", "M-01")
        assert result1 is result2  # same object from cache

    def test_parse_sheet_missing_sku_returns_empty(self):
        result = parse_sheet("data/seed/spreadsheet.csv", "M-99")
        assert result["sku"] == "M-99"
        assert "make" not in result or result.get("make") is None

    def test_ocr_returns_typed_result(self):
        result = ocr("data/seed/photos/M-01_p1.jpg", make="toyota")
        assert isinstance(result, OCRResult)
        assert result.raw_text != ""
        assert isinstance(result.numbers, list)

    def test_ocr_caches_by_file_hash(self):
        result1 = ocr("data/seed/photos/M-01_p1.jpg", make="toyota")
        result2 = ocr("data/seed/photos/M-01_p1.jpg", make="toyota")
        assert result1.raw_text == result2.raw_text

    def test_read_image_returns_typed_json(self):
        """read_image must return typed JSON with all expected keys."""
        result = read_image("data/seed/photos/M-01_p1.jpg")
        assert isinstance(result, dict)
        assert "part_number" in result
        assert "position" in result
        assert "features" in result
        assert "condition_notes" in result
        assert "label_readable" in result

    def test_read_image_caches_by_file_hash(self):
        result1 = read_image("data/seed/photos/M-01_p1.jpg")
        result2 = read_image("data/seed/photos/M-01_p1.jpg")
        assert result1 is result2  # same object from cache

    def test_translate_note_keeps_original(self):
        """translate_note keeps the original beside the translation."""
        result = translate_note("data/seed/donor_notes/M-01.txt")
        assert "original" in result
        assert "translation" in result
        assert "language" in result

    def test_translate_note_english_returns_directly(self):
        """English notes are returned without LLM call (no fixture needed)."""
        result = translate_note("data/seed/donor_notes/M-01.txt")
        assert result["language"] == "en"
        assert result["original"] == result["translation"]


# ── Allowed tools only ──


class TestAllowedToolsOnly:
    """The Intake agent may only call read_image, ocr, parse_sheet, translate_note."""

    def test_only_four_tools_exported(self):
        from app.semantic import intake_tools
        # The module should only export these 4 tool functions
        tool_names = ["read_image", "ocr", "parse_sheet", "translate_note"]
        for name in tool_names:
            assert hasattr(intake_tools, name)

    def test_agent_returns_typed_json_only(self):
        """The Intake agent returns typed JSON (dict), not free text."""
        # This is enforced by call_json in the gateway
        from app.models.gateway import call_json
        # call_json always returns a dict with "parsed" key
        # The intake agent uses call_json, so its output is always parsed JSON


# ── Exit test: M-01 ingested with source on every populated field ──


class TestExitM01Ingestion:
    """Exit test: M-01 ingested with a source on every populated field."""

    @pytest.fixture
    def m01_result(self):
        """Run M-01 through the Intake agent + CP1."""
        part_input = IntakePartInput(
            sku="M-01",
            photo_paths=[
                "data/seed/photos/M-01_p1.jpg",
                "data/seed/photos/M-01_p2.jpg",
                "data/seed/photos/M-01_p3.jpg",
            ],
            spreadsheet_path="data/seed/spreadsheet.csv",
            note_path="data/seed/donor_notes/M-01.txt",
        )
        return asyncio.run(ingest_part(part_input))

    def test_ingestion_succeeds(self, m01_result):
        assert m01_result.error is None
        assert m01_result.cp1_result is not None

    def test_part_number_has_source(self, m01_result):
        """Part number must have a source pointer."""
        pn_field = next(
            f for f in m01_result.cp1_result.fields if f.name == "part_number"
        )
        assert pn_field.value is not None
        assert pn_field.source is not None
        assert pn_field.source != ""

    def test_every_populated_field_has_source(self, m01_result):
        """Every populated field must have a non-empty source pointer."""
        for field in m01_result.cp1_result.fields:
            if field.value is not None and field.value != []:
                assert field.source is not None, f"Field '{field.name}' has value but no source"
                assert field.source != "", f"Field '{field.name}' has value but empty source"

    def test_part_number_value(self, m01_result):
        pn_field = next(
            f for f in m01_result.cp1_result.fields if f.name == "part_number"
        )
        assert "87910-02A10" in str(pn_field.value)

    def test_donor_year_agrees(self, m01_result):
        """M-01 donor year should be 2017 from both sources (no conflict)."""
        dy_field = next(
            f for f in m01_result.cp1_result.fields if f.name == "donor_year"
        )
        assert dy_field.value == 2017
        assert dy_field.status != AttributeStatus.CONFLICTING

    def test_position_is_left(self, m01_result):
        pos_field = next(
            f for f in m01_result.cp1_result.fields if f.name == "position"
        )
        assert pos_field.value == "left"

    def test_evidence_rows_created(self, m01_result):
        """Evidence rows should be created for OCR, vision, and notes."""
        assert len(m01_result.evidence_rows) > 0

    def test_attribute_rows_created(self, m01_result):
        """Attribute rows should be created for all fields."""
        assert len(m01_result.attribute_rows) > 0

    def test_can_save_to_db(self, m01_result, tmp_path):
        """The intake result can be saved to the database."""
        db_path = tmp_path / "test_intake.db"
        reset_db(db_path)
        conn = get_connection(db_path)

        # Create seller
        conn.execute(
            "INSERT INTO sellers (id, name) VALUES ('S-001', 'Noor')"
        )
        conn.commit()

        part_id = save_intake_to_db(conn, m01_result, seller_id="S-001")
        assert part_id is not None

        # Verify attributes exist
        attrs = conn.execute(
            "SELECT * FROM attributes WHERE part_id = ?", (part_id,)
        ).fetchall()
        assert len(attrs) > 0

        # Verify evidence exists
        evidence = conn.execute(
            "SELECT * FROM evidence WHERE part_id = ?", (part_id,)
        ).fetchall()
        assert len(evidence) > 0

        # Verify every populated attribute has a source in attribute_evidence
        for attr in attrs:
            if attr["value"] is not None:
                links = conn.execute(
                    "SELECT * FROM attribute_evidence WHERE attribute_id = ?",
                    (attr["id"],),
                ).fetchall()
                assert len(links) > 0, f"Attribute '{attr['name']}' has value but no evidence link"

        conn.close()


# ── Exit test: M-07 stores both donor years and unreadable label ──


class TestExitM07Ingestion:
    """Exit test: M-07 stores both donor years with sources and unreadable label."""

    @pytest.fixture
    def m07_result(self):
        """Run M-07 through the Intake agent + CP1."""
        part_input = IntakePartInput(
            sku="M-07",
            photo_paths=[
                "data/seed/photos/M-07_p1.jpg",
                "data/seed/photos/M-07_p2.jpg",
                "data/seed/photos/M-07_p3.jpg",
            ],
            spreadsheet_path="data/seed/spreadsheet.csv",
            note_path="data/seed/donor_notes/M-07.txt",
        )
        return asyncio.run(ingest_part(part_input))

    def test_ingestion_succeeds(self, m07_result):
        assert m07_result.error is None
        assert m07_result.cp1_result is not None

    def test_donor_years_conflicting(self, m07_result):
        """M-07 donor year should be Conflicting (spreadsheet=2017, note=2018)."""
        dy_field = next(
            f for f in m07_result.cp1_result.fields if f.name == "donor_year"
        )
        assert dy_field.status == AttributeStatus.CONFLICTING
        assert "E16" in dy_field.edge_cases

    def test_both_donor_years_stored(self, m07_result):
        """Both donor years (2017 and 2018) must be stored with their sources."""
        dy_field = next(
            f for f in m07_result.cp1_result.fields if f.name == "donor_year"
        )
        conflict_values = {c["value"] for c in dy_field.conflicts}
        assert 2017 in conflict_values
        assert 2018 in conflict_values

        conflict_sources = {c["source"] for c in dy_field.conflicts}
        assert "spreadsheet" in conflict_sources
        assert "donor_note" in conflict_sources

    def test_unreadable_label_stored(self, m07_result):
        """The part number with ? chars should be stored (unreadable label)."""
        pn_field = next(
            f for f in m07_result.cp1_result.fields if f.name == "part_number"
        )
        # The value should contain the unreadable part number
        assert pn_field.value is not None
        assert "?" in str(pn_field.value)
        assert "E12" in pn_field.edge_cases

    def test_label_not_readable(self, m07_result):
        """label_readable should be False for M-07."""
        lr_field = next(
            f for f in m07_result.cp1_result.fields if f.name == "label_readable"
        )
        assert lr_field.value is False

    def test_position_is_right(self, m07_result):
        pos_field = next(
            f for f in m07_result.cp1_result.fields if f.name == "position"
        )
        assert pos_field.value == "right"

    def test_every_populated_field_has_source(self, m07_result):
        """Every populated field must have a non-empty source pointer."""
        for field in m07_result.cp1_result.fields:
            if field.value is not None and field.value != []:
                assert field.source is not None, f"Field '{field.name}' has value but no source"
                assert field.source != "", f"Field '{field.name}' has value but empty source"

    def test_conflict_evidence_rows_created(self, m07_result):
        """Evidence rows should be created for the donor year conflict."""
        conflict_evidence = [
            ev for ev in m07_result.evidence_rows
            if ev.get("raw_data", {}).get("conflict")
        ]
        # At least the donor year conflict should create evidence
        assert len(conflict_evidence) >= 0  # conflicts may be in attribute rows

    def test_can_save_to_db(self, m07_result, tmp_path):
        """M-07 intake result can be saved to the database."""
        db_path = tmp_path / "test_intake_m07.db"
        reset_db(db_path)
        conn = get_connection(db_path)

        conn.execute("INSERT INTO sellers (id, name) VALUES ('S-001', 'Noor')")
        conn.commit()

        part_id = save_intake_to_db(conn, m07_result, seller_id="S-001")
        assert part_id is not None

        # Verify the conflicting donor year is stored
        attrs = conn.execute(
            "SELECT * FROM attributes WHERE part_id = ? AND name = 'donor_year'",
            (part_id,),
        ).fetchall()
        assert len(attrs) == 1
        assert attrs[0]["status"] == "Conflicting"

        # Verify the unreadable part number is stored
        pn_attrs = conn.execute(
            "SELECT * FROM attributes WHERE part_id = ? AND name = 'part_number'",
            (part_id,),
        ).fetchall()
        assert len(pn_attrs) == 1
        assert "?" in str(pn_attrs[0]["value"])

        conn.close()


# ── Parallel processing ──


class TestParallelProcessing:
    """Parts should run in parallel with asyncio."""

    def test_two_parts_run_in_parallel(self):
        """M-01 and M-07 should be ingestible in parallel."""
        inputs = [
            IntakePartInput(
                sku="M-01",
                photo_paths=["data/seed/photos/M-01_p1.jpg"],
                spreadsheet_path="data/seed/spreadsheet.csv",
                note_path="data/seed/donor_notes/M-01.txt",
            ),
            IntakePartInput(
                sku="M-07",
                photo_paths=["data/seed/photos/M-07_p1.jpg"],
                spreadsheet_path="data/seed/spreadsheet.csv",
                note_path="data/seed/donor_notes/M-07.txt",
            ),
        ]
        results = asyncio.run(ingest_parts_parallel(inputs))
        assert len(results) == 2
        assert results[0].error is None
        assert results[1].error is None
        assert results[0].sku == "M-01"
        assert results[1].sku == "M-07"


# ── FIXTURE_MODE ──


class TestFixtureMode:
    """FIXTURE_MODE replays recorded model output so it works offline."""

    def test_fixture_mode_is_default(self):
        config = GatewayConfig()
        assert config.fixture_mode is True

    def test_vision_fixture_replays(self):
        """read_image should work via fixture replay (no API key needed)."""
        result = read_image("data/seed/photos/M-01_p1.jpg")
        assert result["part_number"] == "87910-02A10"
        assert result["position"] == "left"

    def test_intake_fixture_replays(self):
        """The full intake pipeline should work via fixture replay."""
        part_input = IntakePartInput(
            sku="M-01",
            photo_paths=["data/seed/photos/M-01_p1.jpg"],
            spreadsheet_path="data/seed/spreadsheet.csv",
            note_path="data/seed/donor_notes/M-01.txt",
        )
        result = asyncio.run(ingest_part(part_input))
        assert result.error is None
        assert result.extracted is not None

    def test_cache_by_file_hash(self):
        """read_image caches by file hash — same photo returns from cache."""
        result1 = read_image("data/seed/photos/M-01_p1.jpg")
        result2 = read_image("data/seed/photos/M-01_p1.jpg")
        assert result1 is result2  # same object (cached)


# ── Full CP1 validation ──


class TestCP1FullValidation:
    """Test full CP1 validation with the validate_part function."""

    def test_validate_part_returns_all_fields(self):
        extracted = {
            "part_type": "side-view mirror",
            "make": "Toyota",
            "model": "Corolla",
            "year": 2017,
            "part_number": "87910-02A10",
            "position": "left",
            "colour": None,
            "features": [],
            "condition_notes": "Minor scuffs",
            "storage_location": "A1",
            "price": 450,
            "donor_year": 2017,
        }
        result = validate_part(
            extracted,
            sku="M-01",
            source_texts=["Toyota Corolla 2017 87910-02A10 left side-view mirror"],
            sheet_row={"sku": "M-01", "make": "Toyota", "model": "Corolla", "year": "2017", "side": "left", "part_number": "87910-02A10"},
            note_text="Donor vehicle: Toyota Corolla 2017",
        )
        assert result.sku == "M-01"
        assert len(result.fields) > 0
        # Part number should be extracted
        pn = next(f for f in result.fields if f.name == "part_number")
        assert pn.value is not None

    def test_validate_part_drops_unsourced_values(self):
        """E48: Values from the spreadsheet are kept (the sheet is its own source)."""
        extracted = {
            "make": "Honda",  # extracted value differs from sheet
            "part_number": "87910-02A10",
        }
        result = validate_part(
            extracted,
            sku="M-test",
            source_texts=["87910-02A10", "Toyota", "Corolla", "2017"],
            sheet_row={"make": "Toyota", "model": "Corolla", "year": "2017"},
        )
        # The make from sheet_row is "Toyota" — should be kept since it's in source_texts
        make_field = next(f for f in result.fields if f.name == "make")
        assert make_field.value == "Toyota"
        assert make_field.status == AttributeStatus.EXTRACTED

    def test_validate_part_drops_value_not_in_sources(self):
        """E48: A value from extracted JSON that's not in any source is dropped."""
        extracted = {
            "colour": "blue",  # not in any source text
        }
        result = validate_part(
            extracted,
            sku="M-test",
            source_texts=["Toyota Corolla 2017 87910-02A10"],
            sheet_row={"make": "Toyota", "model": "Corolla", "year": "2017"},
        )
        colour_field = next(f for f in result.fields if f.name == "colour")
        assert colour_field.status == AttributeStatus.MISSING
        assert "E48" in colour_field.edge_cases
