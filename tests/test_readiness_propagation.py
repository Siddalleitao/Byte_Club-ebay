"""Tests for CP4 compatibility, the propagation engine and the CP6 readiness engine.

Required exit tests:
  - M-07 produces the ranked list in TRD 8 and reaches Ready only after label,
    donor year, tab, test status and weight are resolved
        → TestM07Journey
  - flipping M-04 from Left to Right updates title, item specifics, compat lookup
    and checklist in one step (J4)
        → TestJ4Flip
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
import uuid

import pytest

from app.data.database import get_connection
from app.data.reference_data import (
    CompatReference,
    CompatRow,
    ReferenceIntegrityError,
    get_checklist,
    get_compat_reference,
    load_compat_reference,
)
from app.domain.compatibility import (
    NOT_VERIFIED_LABEL,
    CompatStatus,
    DonorFact,
    check_part,
    lookup,
)
from app.domain.cp2 import run_cp2
from app.domain.enums import AttributeStatus
from app.domain.ledger import EvidenceLedger
from app.domain.propagation import DEPENDENCY_GRAPH, needs_llm, outputs_for
from app.domain.propagation_engine import (
    on_reference_change,
    propagate,
    record_reference_load,
    render_draft,
)
from app.domain.readiness import (
    EVALUATORS,
    Action,
    ReadinessStatus,
    evaluate_part,
)
from app.semantic.intake_agent import IntakePartInput, ingest_part, save_intake_to_db
from scripts.seed import seed_all


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pp.db"
    summary = seed_all(path)
    conn = get_connection(path)
    yield {"conn": conn, "path": path, "ids": summary["sku_to_part_id"]}
    conn.close()


def _evaluate(conn, part_id):
    result = evaluate_part(conn, part_id, check_part(conn, part_id))
    conn.commit()
    return result


def _first_pass(conn, sku: str) -> str:
    """Intake (recorded fixtures) + CP2 for one SKU, as the background pipeline runs it."""
    result = asyncio.run(ingest_part(IntakePartInput(
        sku=sku,
        photo_paths=[f"data/seed/photos/{sku}_p{i}.jpg" for i in (1, 2, 3)],
        spreadsheet_path="data/seed/spreadsheet.csv",
        note_path=f"data/seed/donor_notes/{sku}.txt",
    )))
    part_id = save_intake_to_db(conn, result)
    run_cp2(conn, part_id)
    return part_id


def _seller_answer(conn, part_id: str, field: str, text: str) -> str:
    ev = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO evidence (id, part_id, kind, source_ref, content, raw_data) VALUES (?, ?, 'seller_answer', 'chat', ?, ?)",
        (ev, part_id, text, json.dumps({"field": field})),
    )
    return ev


def _set(conn, part_id, name, value, status=AttributeStatus.EXTRACTED, source="spreadsheet"):
    EvidenceLedger(conn, part_id).set_attribute(name, value, status, source=source)


# ── CP4 Compatibility ───────────────────────────────────────


def _reference(*rows: tuple) -> CompatReference:
    index: dict[str, list[CompatRow]] = {}
    for pn, make, model, side in rows:
        index.setdefault(pn.upper(), []).append(CompatRow(pn, make, model, 2014, 2019, "GCC", side, "v-test"))
    return CompatReference("v-test", "0" * 64, {k: tuple(v) for k, v in index.items()})


class TestCompatibility:
    def test_exact_match_is_verified_compat_with_fitments_and_version(self):
        ref = get_compat_reference()
        result = lookup(ref, "87910-02a10 ", position="left")  # trimmed, case-insensitive
        assert result.status == CompatStatus.VERIFIED and result.edge_case == "E32"
        assert result.claim_type == "verified_compat"
        assert result.ref_version == "v0.1"
        assert [f.label() for f in result.fitments] == ["Toyota Corolla 2014-2019 GCC Left"]

    def test_no_match_is_donor_info_only(self):
        donor = DonorFact("Ford", "Focus", 2016, "GCC")
        result = lookup(get_compat_reference(), "DB3Z-17683-A", donor=donor)
        assert result.status == CompatStatus.NOT_VERIFIED and result.edge_case == "E33"
        assert result.label == NOT_VERIFIED_LABEL == "compatibility not verified"
        assert result.fitments == []
        assert result.donor == donor  # shown as its own fact
        assert result.lookup_done  # the "not verified" label can be shown (C12)

    def test_donor_is_never_fitment(self):
        result = lookup(get_compat_reference(), None, donor=DonorFact("Toyota", "Corolla", 2018, "GCC"))
        assert result.fitments == [] and result.claim_type is None
        assert result.donor.label.startswith("Donor vehicle (descriptive only")

    def test_unreadable_part_number_is_never_guessed(self):
        result = lookup(get_compat_reference(), "87910-0????")
        assert result.status == CompatStatus.NOT_VERIFIED and result.reason == "part_number_unreadable"
        assert result.fitments == [] and not result.lookup_done

    @pytest.mark.parametrize("rows,reason", [
        ((("X-1", "Toyota", "Corolla", "left"), ("X-1", "Toyota", "Camry", "left")), "matches_disagree_on_vehicle"),
        ((("X-1", "Toyota", "Corolla", "left"), ("X-1", "Toyota", "Corolla", "right")), "matches_disagree_on_side"),
    ])
    def test_disagreeing_matches_go_to_specialist_review(self, rows, reason):
        result = lookup(_reference(*rows), "X-1", position="left")
        assert result.status == CompatStatus.CONFLICTING and result.reason == reason
        assert result.edge_case == "E34" and result.specialist_review

    def test_reference_side_differs_from_part(self):
        result = lookup(get_compat_reference(), "87940-06A10", position="right")
        assert result.status == CompatStatus.SIDE_MISMATCH and not result.lookup_done

    def test_lookup_under_100ms(self):
        ref = get_compat_reference()
        start = time.perf_counter()
        for _ in range(1000):
            result = lookup(ref, "87910-02A10", position="left")
        assert (time.perf_counter() - start) / 1000 * 1000 < 100
        assert result.elapsed_ms < 100

    def test_checksum_is_verified(self, tmp_path):
        csv_copy = tmp_path / "compat_ref.csv"
        shutil.copy("data/reference/compat_ref.csv", csv_copy)
        good = tmp_path / "compat_ref.sha256"
        good.write_text(hashlib.sha256(csv_copy.read_bytes()).hexdigest() + "  compat_ref.csv\n")
        assert load_compat_reference(csv_copy, good).version == "v0.1"

        csv_copy.write_text(csv_copy.read_text() + "DB3Z-17683-A,Ford,Focus,2010,2020,UK,left,v0.1\n")
        with pytest.raises(ReferenceIntegrityError):
            load_compat_reference(csv_copy, good)

    def test_verified_match_promotes_extracted_values_only(self, db):
        conn, m01 = db["conn"], db["ids"]["M-01"]
        _set(conn, m01, "part_number", "87910-02A10", source="photo:vision+photo:ocr")
        _set(conn, m01, "position", "left", source="spreadsheet")
        check_part(conn, m01)
        ledger = EvidenceLedger(conn, m01)
        assert ledger.get_attribute("part_number")["status"] == "Verified"
        assert ledger.get_attribute("position")["status"] == "Verified"
        kinds = {e["kind"] for e in ledger.get_evidence_for_attribute("part_number")}
        assert "reference_lookup" in kinds

    def test_value_extracted_after_the_first_lookup_is_still_promoted(self, db):
        conn, m01 = db["conn"], db["ids"]["M-01"]
        check_part(conn, m01)  # first lookup from the seeded part row: verified, persisted
        _set(conn, m01, "part_number", "87910-02A10", source="photo:vision+photo:ocr")  # intake, later
        check_part(conn, m01)  # same result (unchanged) — must still promote the new Extracted value
        assert EvidenceLedger(conn, m01).get_attribute("part_number")["status"] == "Verified"

    def test_conflicting_value_is_not_promoted(self, db):
        conn, m01 = db["conn"], db["ids"]["M-01"]
        _set(conn, m01, "position", None, status=AttributeStatus.CONFLICTING)
        _set(conn, m01, "part_number", "87910-02A10")
        check_part(conn, m01)
        assert EvidenceLedger(conn, m01).get_attribute("position")["status"] == "Conflicting"

    def test_reference_version_change_reruns_lookups_and_drafts(self, db):
        """E35."""
        conn, m01 = db["conn"], db["ids"]["M-01"]
        _set(conn, m01, "part_number", "87910-02A10")
        render_draft(conn, m01)
        assert record_reference_load(conn, "compat_ref", "v0.1", get_compat_reference().checksum) is False
        conn.execute("UPDATE compat_results SET ref_version = 'v0.0'")
        conn.execute("UPDATE listing_drafts SET ref_version = 'v0.0'")
        conn.commit()

        results = on_reference_change(conn)

        assert [r.part_id for r in results] == [m01]
        assert results[0].recomputed == ["compat_lookup", "item_specifics", "title", "checklist"]
        draft = conn.execute("SELECT ref_version, version FROM listing_drafts WHERE part_id = ?", (m01,)).fetchone()
        assert draft["ref_version"] == "v0.1" and draft["version"] == 2

    def test_record_reference_load_detects_change(self, db):
        conn = db["conn"]
        assert record_reference_load(conn, "compat_ref", "v0.1", "a" * 64) is False  # first load
        assert record_reference_load(conn, "compat_ref", "v0.1", "a" * 64) is False  # same
        assert record_reference_load(conn, "compat_ref", "v0.2", "b" * 64) is True   # changed


# ── Propagation graph ───────────────────────────────────────


class TestPropagationGraph:
    def test_graph_matches_trd3_table(self):
        assert DEPENDENCY_GRAPH["part_number"] == ("compat_lookup", "item_specifics", "title", "description", "checklist")
        assert DEPENDENCY_GRAPH["position"] == ("item_specifics", "title", "compat_lookup", "checklist")
        for donor in ("donor_year", "donor_model", "donor_make"):
            assert DEPENDENCY_GRAPH[donor] == ("donor_block", "title", "checklist")
        assert DEPENDENCY_GRAPH["condition"] == ("disclosure_block", "description", "checklist")
        assert DEPENDENCY_GRAPH["test_status"] == ("disclosure_block", "checklist")
        assert DEPENDENCY_GRAPH["weight"] == DEPENDENCY_GRAPH["dimensions"] == ("shipping_fields", "checklist")
        assert DEPENDENCY_GRAPH["price"] == ("price_field",)

    def test_aliases_and_llm_flags(self):
        assert outputs_for("side") == outputs_for("position")
        assert outputs_for("condition_notes") == outputs_for("condition")
        assert needs_llm(outputs_for("part_number")) == ["description"]
        assert needs_llm(outputs_for("condition_notes")) == ["description"]
        assert needs_llm(outputs_for("position")) == []
        assert needs_llm(outputs_for("price")) == []

    def test_price_touches_the_price_field_only(self, db):
        conn, m01 = db["conn"], db["ids"]["M-01"]
        _set(conn, m01, "price", "450 AED", source="inventory")
        render_draft(conn, m01)
        before = dict(conn.execute("SELECT * FROM listing_drafts WHERE part_id = ?", (m01,)).fetchone())

        result = propagate(conn, m01, "price", "500 AED")

        assert result.recomputed == ["price_field"] and result.readiness is None
        after = dict(conn.execute("SELECT * FROM listing_drafts WHERE part_id = ?", (m01,)).fetchone())
        assert after["price"] == 500.0
        for column in ("title", "item_specifics_json", "donor_block", "disclosure_block", "shipping_json"):
            assert after[column] == before[column]


# ── Required: J4 — flip M-04 from Left to Right in one step ─


def _m04_with_draft(conn, part_id) -> None:
    _set(conn, part_id, "part_number", "87940-06A10", source="photo:ocr+spreadsheet")
    _set(conn, part_id, "position", "left", source="spreadsheet")  # donor note only says "driver side"
    _set(conn, part_id, "part_type", "side-view mirror")
    _set(conn, part_id, "price", "520", source="inventory")
    conn.commit()
    render_draft(conn, part_id)


class TestJ4Flip:
    def test_flip_m04_left_to_right_updates_outputs_in_one_step(self, db):
        conn, m04 = db["conn"], db["ids"]["M-04"]
        _m04_with_draft(conn, m04)
        before = dict(conn.execute("SELECT * FROM listing_drafts WHERE part_id = ?", (m04,)).fetchone())
        assert "Left" in before["title"]
        assert json.loads(before["item_specifics_json"])["Placement on Vehicle"] == "Left"
        audits_before = conn.execute("SELECT COUNT(*) c FROM audit_events WHERE event_type = 'propagate'").fetchone()["c"]
        versions_before = len(EvidenceLedger(conn, m04).get_versions("position"))

        result = propagate(conn, m04, "position", "right",
                           evidence_id=_seller_answer(conn, m04, "position", "Change M-04 to right side"))

        # Exactly the four outputs, in one step
        assert result.recomputed == ["compat_lookup", "item_specifics", "title", "checklist"]
        assert set(result.changes) == {"compat_lookup", "item_specifics", "title", "checklist"}
        assert result.llm_regenerate == []
        after = dict(conn.execute("SELECT * FROM listing_drafts WHERE part_id = ?", (m04,)).fetchone())
        assert "Right" in after["title"] and "Left" not in after["title"]
        assert json.loads(after["item_specifics_json"])["Placement on Vehicle"] == "Right"
        # compat lookup re-ran: the reference lists 87940-06A10 as a left mirror
        assert result.changes["compat_lookup"]["before"]["status"] == "verified"
        assert result.compat.status == CompatStatus.SIDE_MISMATCH
        assert result.readiness.status == ReadinessStatus.INFORMATION_MISSING
        assert result.readiness.top_actions(1)[0].key == "check_side_vs_reference"
        # Unaffected outputs untouched
        for column in ("donor_block", "disclosure_block", "shipping_json", "price", "summary_stale"):
            assert after[column] == before[column]
        # Version history and one AuditEvent
        assert len(EvidenceLedger(conn, m04).get_versions("position")) == versions_before + 1
        assert after["version"] == before["version"] + 1
        history = conn.execute(
            "SELECT version, reason FROM listing_draft_versions WHERE draft_id = ? ORDER BY version", (after["id"],)
        ).fetchall()
        assert [h["version"] for h in history] == [before["version"], after["version"]]
        assert history[-1]["reason"] == "position changed"
        audits = conn.execute(
            "SELECT detail_json FROM audit_events WHERE event_type = 'propagate' ORDER BY id"
        ).fetchall()
        assert len(audits) == audits_before + 1
        detail = json.loads(audits[-1]["detail_json"])
        assert detail["old_value"] == "left" and detail["new_value"] == "right"
        assert conn.execute("SELECT side FROM parts WHERE id = ?", (m04,)).fetchone()["side"] == "right"

    def test_flip_is_atomic(self, db, monkeypatch):
        conn, m04 = db["conn"], db["ids"]["M-04"]
        _m04_with_draft(conn, m04)
        draft_before = dict(conn.execute("SELECT * FROM listing_drafts WHERE part_id = ?", (m04,)).fetchone())

        import app.domain.propagation_engine as engine

        def boom(*args, **kwargs):
            raise RuntimeError("checklist engine down")

        monkeypatch.setattr(engine, "evaluate_part", boom)
        with pytest.raises(RuntimeError):
            propagate(conn, m04, "position", "right")

        assert EvidenceLedger(conn, m04).get_attribute("position")["value"] == "left"
        draft_after = dict(conn.execute("SELECT * FROM listing_drafts WHERE part_id = ?", (m04,)).fetchone())
        assert draft_after == draft_before

    def test_correction_needing_summary_marks_it_stale(self, db):
        conn, m04 = db["conn"], db["ids"]["M-04"]
        _m04_with_draft(conn, m04)
        conn.execute("UPDATE listing_drafts SET summary = 'Clean Camry mirror.', summary_stale = 0")
        conn.commit()
        result = propagate(conn, m04, "condition_notes", "Small scratch on housing")
        assert result.llm_regenerate == ["description"]
        draft = conn.execute("SELECT summary_stale, description FROM listing_drafts WHERE part_id = ?", (m04,)).fetchone()
        assert draft["summary_stale"] == 1
        assert "Clean Camry mirror." in draft["description"]  # old summary shown until regenerated

    def test_correction_revokes_an_approved_draft(self, db):
        conn, m04 = db["conn"], db["ids"]["M-04"]
        _m04_with_draft(conn, m04)
        conn.execute("UPDATE listing_drafts SET status = 'approved'")
        conn.commit()
        propagate(conn, m04, "position", "right")
        assert conn.execute("SELECT status FROM listing_drafts").fetchone()["status"] == "draft"


# ── Readiness engine ────────────────────────────────────────


class TestChecklist:
    def test_checklist_items_and_blocking_flags_from_yaml(self):
        checklist = get_checklist()
        assert [i.item_id for i in checklist.items] == [f"C{n:02d}" for n in range(1, 16)]
        assert set(EVALUATORS) == {i.item_id for i in checklist.items}
        assert checklist.item("C02").blocking is False
        assert checklist.item("C12").blocking is True
        assert checklist.item("C14").blocks == "approval"
        assert checklist.item("C01").on_fail == "specialist_review"
        assert checklist.purpose == "TEST checklist, not customs or legal guidance"

    def test_no_approved_readiness_status(self):
        assert "Approved" not in {s.value for s in ReadinessStatus}

    def test_action_ranking_rules(self):
        def act(key, tier, effort, clears):
            return Action(key, key, key, tier, effort, clears)

        actions = [
            act("measure", "shipping", "measurement", ["C10"]),
            act("question_two", "identity", "question", ["C03", "C12"]),
            act("disclosure_q", "disclosure", "question", ["C07"]),
            act("identity_photo", "identity", "photo", ["C04"]),
            act("disclosure_photo", "disclosure", "photo", ["C06"]),
            act("price", "commercial", "question", ["C11"]),
            act("identity_q", "identity", "question", ["C05"]),
        ]
        ranked = [a.key for a in sorted(actions, key=Action.sort_key)]
        assert ranked == [
            "question_two",      # identity, clears most
            "identity_photo",    # identity, one item, photo before question
            "identity_q",
            "disclosure_photo",
            "disclosure_q",
            "measure",
            "price",
        ]


class TestReadinessRules:
    def test_excluded_type_goes_to_specialist_review(self, db):
        conn, m17 = db["conn"], db["ids"]["M-17"]
        result = _evaluate(conn, m17)
        assert result.status == ReadinessStatus.SPECIALIST_REVIEW
        assert any("E39" in r for r in result.specialist_reasons)
        assert result.actions == []

    def test_conflict_open_after_two_seller_rounds(self, db):
        conn, m13 = db["conn"], db["ids"]["M-13"]
        _set(conn, m13, "donor_year", None, status=AttributeStatus.CONFLICTING)
        _seller_answer(conn, m13, "donor_year", "maybe 2016")
        conn.commit()
        assert _evaluate(conn, m13).status == ReadinessStatus.INFORMATION_MISSING
        _seller_answer(conn, m13, "donor_year", "I think 2017")
        conn.commit()
        result = _evaluate(conn, m13)
        assert result.status == ReadinessStatus.SPECIALIST_REVIEW
        assert any("donor_year conflict still open after 2 seller rounds" in r for r in result.specialist_reasons)

    def test_part_number_mapping_to_conflicting_vehicles(self, db, monkeypatch):
        conn, m01 = db["conn"], db["ids"]["M-01"]
        _set(conn, m01, "part_number", "X-1")
        ref = _reference(("X-1", "Toyota", "Corolla", "left"), ("X-1", "Toyota", "Camry", "left"))
        result = evaluate_part(conn, m01, check_part(conn, m01, ref))
        assert result.status == ReadinessStatus.SPECIALIST_REVIEW
        assert any("E34" in r for r in result.specialist_reasons)

    def test_implausible_weight_fails_with_recheck(self, db):
        conn, m01 = db["conn"], db["ids"]["M-01"]
        _set(conn, m01, "weight", "150 kg", status=AttributeStatus.SELLER_CONFIRMED, source="seller_answer")
        _set(conn, m01, "dimensions", "45 x 30 x 25 cm", status=AttributeStatus.SELLER_CONFIRMED, source="seller_answer")
        result = _evaluate(conn, m01)
        c10 = next(i for i in result.items if i.item_id == "C10")
        assert c10.result == "fail" and "E38" in c10.reason
        assert any(a.key == "recheck_measurements" for a in result.actions)

    def test_readiness_does_not_touch_part_status(self, db):
        conn, m01 = db["conn"], db["ids"]["M-01"]
        conn.execute("UPDATE parts SET status = 'Approved' WHERE id = ?", (m01,))
        _evaluate(conn, m01)
        assert conn.execute("SELECT status FROM parts WHERE id = ?", (m01,)).fetchone()["status"] == "Approved"


# ── Required: M-07 ranked list and path to Ready ────────────


TRD8_M07_ACTIONS = [
    ("retake_label_photo", ["C03", "C12"]),
    ("confirm_donor_year", ["C05"]),
    ("closeup_defect", ["C06"]),
    ("confirm_test_status", ["C07"]),
    ("weigh_and_measure", ["C10"]),
]


class TestM07Journey:
    def test_m07_first_pass_produces_trd8_ranked_list(self, db):
        conn = db["conn"]
        m07 = _first_pass(conn, "M-07")
        result = _evaluate(conn, m07)

        assert result.status == ReadinessStatus.INFORMATION_MISSING
        assert [(a.key, a.clears) for a in result.actions] == TRD8_M07_ACTIONS
        assert [a.key for a in result.top_actions()] == ["retake_label_photo", "confirm_donor_year", "closeup_defect"]
        assert result.actions[0].text_en == "Retake the label photo close up, in daylight"
        assert result.actions[1].text_en == "Confirm donor year: 2017 or 2018?"
        assert "cracked" in result.actions[2].text_en
        assert result.actions[4].text_en == "Weigh and measure the packed box"
        assert "compatibility not verified" in result.warnings  # warning, does not block on its own

    def test_m07_reaches_ready_only_after_the_five_fixes(self, db):
        conn = db["conn"]
        m07 = _first_pass(conn, "M-07")
        steps = [
            ("label", [("part_number", "87910-02A20")]),  # sharper label photo, read and confirmed
            ("donor year", [("donor_year", 2018)]),
            ("tab", [("condition_notes", "Lower mounting tab is cracked")]),
            ("test status", [("test_status", "untested")]),
            ("weight", [("weight", "1.8 kg"), ("dimensions", "45 x 30 x 25 cm")]),
        ]
        remaining = [key for key, _ in TRD8_M07_ACTIONS]
        for i, (label, changes) in enumerate(steps):
            for field_name, value in changes:
                result = propagate(conn, m07, field_name, value,
                                   evidence_id=_seller_answer(conn, m07, field_name, str(value)))
            remaining.pop(0)
            readiness = _evaluate(conn, m07)
            if i < len(steps) - 1:
                assert readiness.status == ReadinessStatus.INFORMATION_MISSING, f"ready too early after {label}"
                assert [a.key for a in readiness.actions] == remaining, f"after {label}"
            else:
                assert readiness.status == ReadinessStatus.READY, readiness.failed()
                assert readiness.actions == []
        # The corrected part number is in the reference and agrees with the right side.
        assert result.compat is None or result.compat.status == CompatStatus.VERIFIED
        assert check_part(conn, m07, persist=False).status == CompatStatus.VERIFIED
        assert "compatibility not verified" not in readiness.warnings
        assert readiness.approval_pending == ["C14"]  # Approved is the seller's click, after CP5b

    def test_m07_ready_with_unverified_compatibility_is_a_warning(self, db):
        conn = db["conn"]
        m07 = _first_pass(conn, "M-07")
        for field_name, value in [("part_number", "87910-0X999"), ("donor_year", 2018),
                                  ("condition_notes", "Lower mounting tab is cracked"),
                                  ("test_status", "untested"), ("weight", "1.8 kg"),
                                  ("dimensions", "45 x 30 x 25 cm")]:
            propagate(conn, m07, field_name, value)
        readiness = _evaluate(conn, m07)
        assert readiness.status == ReadinessStatus.READY
        assert "compatibility not verified" in readiness.warnings
        c12 = next(i for i in readiness.items if i.item_id == "C12")
        assert c12.result == "pass" and c12.reason == NOT_VERIFIED_LABEL
