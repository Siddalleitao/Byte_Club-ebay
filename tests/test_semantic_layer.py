"""Tests for the semantic layer and seller context (TRD 4).

Required exit tests:
  - another seller_id returns nothing          → TestSellerIsolation
  - a profile edit changes the next draft's handling time → TestProfileEdit
  - the chat tool layer rejects a status write → TestAccessPolicy
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.domain.enums import AttributeStatus
from app.domain.ledger import EvidenceLedger
from app.domain.listing_profile import render_profile_sections
from app.domain.models import EDITABLE_PROFILE_FIELDS
from app.orchestration.chat_actions import handle_intent
from app.semantic.access_policy import AccessDenied, ChatAccessPolicy, ChatTools
from app.semantic.context_card import estimate_tokens, render_card
from app.semantic.entity_model import SemanticLayer
from app.semantic.glossary import get_glossary
from app.semantic.intents import (
    AnswerRequest,
    CorrectionRequest,
    ExplainRequest,
    OutOfScopeRequest,
    PolicyRequest,
    PolicyTopic,
    StatusQueryRequest,
    execute_intent,
)
from app.semantic.metrics import compute_metrics, load_definitions
from app.semantic.prompt_prefix import get_prompt_prefix
from app.semantic.seller_context import (
    UnknownSeller,
    list_profiles,
    login,
    resolve_seller_id,
    update_profile,
)
from tests.semantic_fixtures import OTHER_SELLER, OTHER_SKU, conn, two_seller_db  # noqa: F401


def _part_id(conn, sku: str) -> str:
    return conn.execute("SELECT id FROM parts WHERE sku = ? AND seller_id = 'S-001'", (sku,)).fetchone()["id"]


def _add_question(conn, sku: str, field: str, text: str = "Which value is correct?") -> str:
    qid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO questions (id, part_id, question_text, priority, attribute_name, status)
           VALUES (?, ?, ?, 'high', ?, 'open')""",
        (qid, _part_id(conn, sku), text, field),
    )
    conn.commit()
    return qid


def _add_draft(conn, sku: str, status: str = "draft") -> str:
    draft_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO listing_drafts (id, part_id, title, status, handling_days) VALUES (?, ?, 'Draft', ?, 3)",
        (draft_id, _part_id(conn, sku), status),
    )
    conn.commit()
    return draft_id


# ── Login and seller context ────────────────────────────────


class TestLogin:
    def test_name_resolves_to_seller_id_from_profile_table(self, conn):
        assert resolve_seller_id(conn, "Noor") == "S-001"
        assert resolve_seller_id(conn, "Omar") == OTHER_SELLER

    def test_unknown_name_is_rejected(self, conn):
        with pytest.raises(UnknownSeller):
            login(conn, "S-001")  # an ID typed as a name is not a profile name

    def test_picker_lists_names_only(self, conn):
        profiles = list_profiles(conn)
        assert {p["display_name"] for p in profiles} == {"Noor", "Omar"}
        assert all(set(p) == {"seller_id", "display_name"} for p in profiles)

    def test_login_loads_profile(self, conn):
        session = login(conn, "Noor")
        assert session.seller_id == "S-001"
        assert session.profile.handling_days == 3
        assert session.profile.corridor == "UAE-UK"
        assert session.profile.rack_codes == ["A1-A6", "B1-B6", "C1-C4"]


class TestContextCard:
    def test_card_is_about_300_tokens(self, conn):
        card = render_card(login(conn, "Noor").profile)
        assert 200 <= estimate_tokens(card) <= 400

    def test_card_carries_profile_fields(self, conn):
        card = render_card(login(conn, "Noor").profile)
        for expected in ("S-001", "Noor Auto Salvage", "UAE-UK", "GBP", "AED", "3 working days",
                         "TEST-RP-01", "A1-A6", "v0.1-test"):
            assert expected in card

    def test_card_render_is_deterministic(self, conn):
        profile = login(conn, "Noor").profile
        assert render_card(profile) == render_card(profile.model_copy())

    def test_prefix_order_and_cache_breakpoints(self, conn):
        prefix = get_prompt_prefix(login(conn, "Noor").profile)
        assert [name for name, _ in prefix.blocks] == [
            "system_prompt", "guardrail_policy", "glossary", "seller_card",
        ]
        blocks = prefix.as_system_blocks()
        assert len(blocks) == 2
        assert all(b["cache_control"] == {"type": "ephemeral"} for b in blocks)
        assert blocks[1]["text"] == prefix.block("seller_card")
        assert "GUARDRAIL POLICY" in blocks[0]["text"]
        assert "driver side" in blocks[0]["text"]

    def test_prefix_is_cached_per_seller(self, conn):
        noor, omar = login(conn, "Noor").profile, login(conn, "Omar").profile
        assert get_prompt_prefix(noor) is get_prompt_prefix(noor)
        assert get_prompt_prefix(noor).cache_key != get_prompt_prefix(omar).cache_key


# ── Required: another seller_id returns nothing ─────────────


class TestSellerIsolation:
    def test_parts_list_is_seller_scoped(self, conn):
        noor = SemanticLayer(conn, login(conn, "Noor"))
        omar = SemanticLayer(conn, login(conn, "Omar"))
        assert len(noor.parts()) == 20
        assert OTHER_SKU not in {p.sku for p in noor.parts()}
        assert [p.sku for p in omar.parts()] == [OTHER_SKU]

    def test_other_sellers_sku_returns_nothing_on_every_view(self, conn, two_seller_db):
        layer = SemanticLayer(conn, login(conn, "Noor"))
        assert layer.part(OTHER_SKU) is None
        assert layer.attributes(OTHER_SKU) == []
        assert layer.evidence(OTHER_SKU) == []
        assert layer.evidence_for_field(OTHER_SKU, "donor_year") == []
        assert layer.donors(OTHER_SKU) == []
        assert layer.claims(OTHER_SKU) == []
        assert layer.checklist(OTHER_SKU) is None
        assert layer.questions(OTHER_SKU) == []
        assert layer.question(two_seller_db["other"]["q"]) is None
        assert layer.draft_inputs(OTHER_SKU) is None
        assert layer.part_id_for(OTHER_SKU) is None

    def test_unfiltered_reads_exclude_other_seller(self, conn):
        layer = SemanticLayer(conn, login(conn, "Noor"))
        assert all(a.sku != OTHER_SKU for a in layer.attributes())
        assert all(c.sku != OTHER_SKU for c in layer.claims())
        assert all(q.sku != OTHER_SKU for q in layer.questions())
        assert layer.checklist_history() == []

    def test_metrics_count_only_own_records(self, conn):
        noor = compute_metrics(SemanticLayer(conn, login(conn, "Noor")))
        omar = compute_metrics(SemanticLayer(conn, login(conn, "Omar")))
        # Omar's part has a passed checklist, an open question, a conflicting
        # attribute and an unsupported claim; none of it shows up for Noor.
        assert omar["ready_count"].value == 1 and noor["ready_count"].value == 0
        assert omar["open_questions"].value == 1 and noor["open_questions"].value == 0
        assert omar["conflict_rate"].value == 1.0 and noor["conflict_rate"].value == 0.0
        assert omar["unsupported_claims"].value == 1 and noor["unsupported_claims"].value == 0

    def test_chat_cannot_write_to_other_sellers_records(self, conn, two_seller_db):
        tools = ChatTools(conn, login(conn, "Noor"))
        with pytest.raises(AccessDenied):
            tools.write_seller_answer(OTHER_SKU, "2016", field="donor_year", accepted=True)
        with pytest.raises(AccessDenied):
            tools.set_question_status(two_seller_db["other"]["q"], "answered")
        status = conn.execute("SELECT status FROM questions WHERE id = ?", (two_seller_db["other"]["q"],)).fetchone()
        assert status["status"] == "open"

    def test_explain_other_sellers_sku_is_not_found(self, conn):
        result = execute_intent(ChatTools(conn, login(conn, "Noor")), ExplainRequest(sku=OTHER_SKU))
        assert result.found is False
        assert result.part_status is None and result.open_questions == []

    def test_semantic_layer_accepts_no_sql_or_raw_tables(self, conn):
        layer = SemanticLayer(conn, login(conn, "Noor"))
        with pytest.raises(KeyError):
            layer._select("parts")
        with pytest.raises(ValueError):
            layer._select("sem_parts", seller_id=OTHER_SELLER)
        with pytest.raises(ValueError):
            layer._select("sem_parts", order_by="sku; DROP TABLE parts")

    def test_donor_view_exposes_only_vin_last6(self, conn):
        donors = SemanticLayer(conn, login(conn, "Omar")).donors(OTHER_SKU)
        assert donors[0].vin_last6 == "123456"
        assert "vin" not in donors[0].model_dump()


# ── Required: a profile edit changes the next draft's handling time ──


class TestProfileEdit:
    def test_profile_edit_changes_next_drafts_handling_time(self, conn):
        session = login(conn, "Noor")
        layer = SemanticLayer(conn, session)
        assert layer.draft_inputs("M-01").profile.handling_days == 3

        update_profile(conn, "S-001", {"handling_days": 5})

        nxt = layer.draft_inputs("M-01").profile
        assert nxt.handling_days == 5
        assert "within 5 working days" in nxt.shipping_block

    def test_profile_edit_rerenders_open_drafts(self, conn):
        draft_id = _add_draft(conn, "M-01", "draft")
        approved_id = _add_draft(conn, "M-02", "approved")
        exported_id = _add_draft(conn, "M-03", "exported")

        result = update_profile(conn, "S-001", {"handling_days": 5})

        assert set(result.rerendered_drafts) == {draft_id, approved_id}
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM listing_drafts")}
        assert rows[draft_id]["handling_days"] == 5
        assert "within 5 working days" in rows[draft_id]["shipping_block"]
        assert rows[draft_id]["profile_version"] == 2
        assert rows[approved_id]["status"] == "draft"  # approval covered the old text
        assert rows[exported_id]["handling_days"] == 3  # published record untouched

    def test_profile_edit_does_not_touch_other_sellers_drafts(self, conn, two_seller_db):
        update_profile(conn, "S-001", {"handling_days": 5})
        other = conn.execute("SELECT handling_days FROM listing_drafts WHERE id = ?",
                             (two_seller_db["other"]["draft"],)).fetchone()
        assert other["handling_days"] == 7

    def test_profile_edit_invalidates_prompt_cache(self, conn):
        session = login(conn, "Noor")
        before = get_prompt_prefix(session.profile)
        result = update_profile(conn, "S-001", {"handling_days": 5})
        after = get_prompt_prefix(session.current_profile(conn))
        assert result.prompt_cache_invalidated is True
        assert after.cache_key != before.cache_key
        assert "5 working days" in after.block("seller_card")
        assert after.block("glossary") == before.block("glossary")  # shared part unchanged

    def test_edit_bumps_version_and_is_audited(self, conn):
        result = update_profile(conn, "S-001", {"handling_days": 5, "city": "Dubai, UAE"})
        assert result.changed_fields == ["handling_days"]  # city did not change
        assert result.profile.version == 2
        audit = conn.execute("SELECT detail_json FROM audit_events WHERE event_type = 'profile_edit'").fetchone()
        detail = json.loads(audit["detail_json"])
        assert detail["before"] == {"handling_days": 3} and detail["after"] == {"handling_days": 5}

    def test_no_op_edit_keeps_version_and_cache(self, conn):
        result = update_profile(conn, "S-001", {"handling_days": 3})
        assert result.changed_fields == [] and result.profile.version == 1
        assert result.prompt_cache_invalidated is False

    def test_field_not_used_by_drafts_does_not_rerender(self, conn):
        _add_draft(conn, "M-01")
        result = update_profile(conn, "S-001", {"business_name": "Noor Parts (synthetic)"})
        assert result.rerendered_drafts == []

    @pytest.mark.parametrize("changes", [
        {"seller_id": "S-002"},
        {"checklist_version": "v9"},
        {"product_family": "airbag_module"},
    ])
    def test_system_fields_are_not_editable(self, conn, changes):
        with pytest.raises(ValueError):
            update_profile(conn, "S-001", changes)

    @pytest.mark.parametrize("changes", [
        {"handling_days": -1},
        {"listing_currency": "pounds"},
        {"languages": ["fr"]},
        {"rack_codes": ["shelf by the door"]},
    ])
    def test_invalid_values_are_rejected(self, conn, changes):
        with pytest.raises(ValueError):
            update_profile(conn, "S-001", changes)
        assert login(conn, "Noor").profile.version == 1

    def test_render_profile_sections_singular_day(self, conn):
        profile = login(conn, "Noor").profile.model_copy(update={"handling_days": 1})
        assert "within 1 working day of" in render_profile_sections(profile).shipping_block


# ── Required: the chat tool layer rejects a status write ────


class TestAccessPolicy:
    def test_chat_tool_layer_rejects_status_write(self, conn):
        tools = ChatTools(conn, login(conn, "Noor"))
        before = conn.execute("SELECT status FROM parts WHERE sku = 'M-07'").fetchone()["status"]

        with pytest.raises(AccessDenied):
            tools.call("set_part_status", sku="M-07", status="Approved")

        after = conn.execute("SELECT status FROM parts WHERE sku = 'M-07'").fetchone()["status"]
        assert after == before
        audit = conn.execute(
            "SELECT detail_json FROM audit_events WHERE event_type = 'access_denied'"
        ).fetchone()
        assert json.loads(audit["detail_json"])["tool"] == "set_part_status"

    @pytest.mark.parametrize("tool", [
        "set_part_status", "write_compat_ref", "set_checklist_version", "write_checklist_result",
        "export_listing", "approve_listing", "write_attribute", "write_draft", "update_profile",
        "run_sql", "_select",
    ])
    def test_forbidden_and_unknown_tools_are_denied(self, conn, tool):
        tools = ChatTools(conn, login(conn, "Noor"))
        with pytest.raises(AccessDenied):
            tools.call(tool)

    def test_tool_layer_has_no_status_writer(self):
        for name in ChatAccessPolicy.FORBIDDEN_WRITES:
            assert not hasattr(ChatTools, name)

    def test_only_seller_answer_evidence_may_be_written(self, conn):
        tools = ChatTools(conn, login(conn, "Noor"))
        for kind in ("test_record", "reference_lookup", "photo_region"):
            with pytest.raises(AccessDenied):
                tools.call("write_seller_answer", sku="M-01", text="tested OK", field="test_status",
                           accepted=True, kind=kind)
        assert conn.execute("SELECT COUNT(*) c FROM evidence WHERE kind = 'test_record'").fetchone()["c"] == 0

    def test_question_status_must_be_known(self, conn):
        qid = _add_question(conn, "M-01", "weight")
        tools = ChatTools(conn, login(conn, "Noor"))
        with pytest.raises(AccessDenied):
            tools.set_question_status(qid, "resolved_by_ai")

    def test_allowed_writes_succeed(self, conn):
        qid = _add_question(conn, "M-01", "weight")
        tools = ChatTools(conn, login(conn, "Noor"))
        ev = tools.call("write_seller_answer", sku="M-01", text="1.4 kg", field="weight",
                        question_id=qid, accepted=True)
        q = tools.call("set_question_status", question_id=qid, status="answered",
                       answer_text="1.4 kg", answer_evidence_id=ev)
        assert q.status == "answered" and q.answer_evidence_id == ev
        row = conn.execute("SELECT kind, content FROM evidence WHERE id = ?", (ev,)).fetchone()
        assert row["kind"] == "seller_answer" and row["content"] == "1.4 kg"

    def test_answer_evidence_must_belong_to_the_part(self, conn):
        qid = _add_question(conn, "M-01", "weight")
        tools = ChatTools(conn, login(conn, "Noor"))
        other_ev = tools.write_seller_answer("M-02", "2 kg", field="weight", accepted=True)
        with pytest.raises(AccessDenied):
            tools.set_question_status(qid, "answered", answer_evidence_id=other_ev)


# ── Glossary and controlled vocabularies ────────────────────


class TestGlossary:
    @pytest.fixture
    def g(self):
        return get_glossary()

    @pytest.mark.parametrize("text,value", [
        ("left", "left"), ("يسار", "left"), ("اليمين", "right"), ("Right side please", "right"),
        ("left (driver side in UAE)", "left"),
    ])
    def test_absolute_positions(self, g, text, value):
        assert g.normalise("position", text).value == value

    @pytest.mark.parametrize("text", [
        "driver side", "passenger side", "جهة السائق", "جانب الراكب", "left-hand drive",
    ])
    def test_driver_and_passenger_side_are_ambiguous_never_stored(self, g, text):
        match = g.normalise("position", text)
        assert match.value is None and match.ambiguous is True

    def test_two_positions_give_no_value(self, g):
        assert g.normalise("position", "left or right").value is None

    @pytest.mark.parametrize("text,value", [
        ("untested", "untested"), ("not tested", "untested"), ("لم يتم فحصه", "untested"),
        ("tested, working", "seller_reported_working"), ("تم فحصه ويعمل", "seller_reported_working"),
        ("partly tested", "partly_tested"), ("not working", "failed"), ("لا يعمل", "failed"),
    ])
    def test_test_status(self, g, text, value):
        assert g.normalise("test_status", text).value == value

    def test_test_status_values_match_trd(self, g):
        assert set(g.allowed_values("test_status")) == {
            "untested", "seller_reported_working", "partly_tested", "failed",
        }

    def test_defects_and_features(self, g):
        assert g.normalise("defect", "مشروخ").value == "crack"
        assert g.normalise("defect", "مكسور").value == "broken"
        assert g.normalise("defect", "scratched").value == "scratch"
        assert g.normalise("defect", "glass broken").value == "broken_glass"
        assert g.normalise("features", "طي كهربائي").value == "power_fold"
        assert set(g.normalise("features", "power-fold, heated").values) == {"power_fold", "heated"}

    def test_condition_new_is_forbidden(self, g):
        assert g.normalise("condition", "brand new").forbidden is True
        assert g.normalise("condition", "مستعمل").value == "used"

    def test_arabic_indic_digits(self, g):
        assert g.normalise_digits("٢٠١٨") == "2018"
        assert g.normalise_digits("۸۷۹۱۰-۰۲A۱۰") == "87910-02A10"

    def test_uk_search_terms_for_titles(self, g):
        assert g.uk_search_terms("side_view_mirror") == ["wing mirror", "door mirror"]
        assert g.normalise("part_type", "مرآة جانبية").value == "side_view_mirror"
        assert g.is_excluded_part_type("airbag_module")

    def test_business_terms_resolve_to_fields(self, g):
        assert g.field_name("weight") == "weight"
        assert g.field_name("الوزن") == "weight"
        assert g.field_name("رقم القطعة") == "part_number"


# ── Metrics ─────────────────────────────────────────────────


class TestMetrics:
    def test_definitions_match_implementations(self):
        assert set(load_definitions()) == {
            "ready_count", "open_questions", "conflict_rate", "time_to_ready", "unsupported_claims",
        }

    def test_metric_values(self, conn):
        session = login(conn, "Noor")
        m01, m07 = _part_id(conn, "M-01"), _part_id(conn, "M-07")
        conn.execute("UPDATE parts SET created_at = '2026-10-27 08:00:00' WHERE id = ?", (m01,))
        conn.execute(
            "INSERT INTO checklist_results (id, part_id, checklist_json, passed, created_at) VALUES (?, ?, '{}', 0, '2026-10-27 09:00:00')",
            (str(uuid.uuid4()), m01),
        )
        conn.execute(
            "INSERT INTO checklist_results (id, part_id, checklist_json, passed, created_at) VALUES (?, ?, '{}', 1, '2026-10-27 10:30:00')",
            (str(uuid.uuid4()), m01),
        )
        ledger = EvidenceLedger(conn, m07)
        ledger.set_attribute("donor_year", "2017", AttributeStatus.CONFLICTING, source="spreadsheet.csv:M-07:year")
        ledger.set_attribute("colour", "black", AttributeStatus.EXTRACTED, source="photo")
        conn.execute(
            "INSERT INTO claims (id, part_id, attribute_name, claim_type, claim_value) VALUES (?, ?, 'fitment', 'fact', 'fits all')",
            (str(uuid.uuid4()), m07),
        )
        conn.commit()
        _add_question(conn, "M-07", "donor_year")

        m = compute_metrics(SemanticLayer(conn, session))
        assert m["ready_count"].value == 1
        assert m["open_questions"].value == 1
        total = conn.execute(
            "SELECT COUNT(*) c FROM attributes a JOIN parts p ON p.id = a.part_id WHERE p.seller_id = 'S-001'"
        ).fetchone()["c"]
        assert m["conflict_rate"].value == round(1 / total, 4)  # one conflicting donor_year
        assert m["time_to_ready"].value == 2.5
        assert m["unsupported_claims"].value == 1
        assert m["ready_count"].label_ar  # bilingual labels from the single definition file

    def test_ready_count_uses_latest_evaluation(self, conn):
        m01 = _part_id(conn, "M-01")
        for passed, at in ((1, "2026-10-27 09:00:00"), (0, "2026-10-27 10:00:00")):
            conn.execute(
                "INSERT INTO checklist_results (id, part_id, checklist_json, passed, created_at) VALUES (?, ?, '{}', ?, ?)",
                (str(uuid.uuid4()), m01, passed, at),
            )
        conn.commit()
        assert compute_metrics(SemanticLayer(conn, login(conn, "Noor")))["ready_count"].value == 0


# ── Intents ─────────────────────────────────────────────────


class TestIntents:
    def test_status_query_which_parts_need_weight(self, conn):
        session = login(conn, "Noor")
        ledger = EvidenceLedger(conn, _part_id(conn, "M-02"))
        ledger.set_attribute("weight", "1.2 kg", AttributeStatus.SELLER_CONFIRMED, source="seller")
        conn.commit()
        result = execute_intent(ChatTools(conn, session), StatusQueryRequest(field="الوزن"))
        assert result.field == "weight"
        skus = {p.sku for p in result.parts}
        assert "M-02" not in skus and "M-01" in skus
        assert result.count == 19

    def test_explain_lists_failed_checklist_items(self, conn):
        m07 = _part_id(conn, "M-07")
        conn.execute(
            "INSERT INTO checklist_results (id, part_id, checklist_json, passed) VALUES (?, ?, ?, 0)",
            (str(uuid.uuid4()), m07, json.dumps({"version": "0.1", "items": [
                {"item_id": "C03", "result": "fail", "reason": "part number unreadable"},
                {"item_id": "C04", "result": "pass"},
            ]})),
        )
        conn.commit()
        _add_question(conn, "M-07", "part_number")
        result = execute_intent(ChatTools(conn, login(conn, "Noor")), ExplainRequest(sku="M-07"))
        assert result.found and result.checklist_evaluated and result.ready is False
        assert [i.item_id for i in result.failed_checklist_items] == ["C03"]
        assert len(result.open_questions) == 1
        assert "part_number" in {f.field for f in result.blocking_fields}

    @pytest.mark.parametrize("text", ["It's 2018", "٢٠١٨"])
    def test_answer_sets_donor_year_as_seller_confirmed(self, conn, text):
        session = login(conn, "Noor")
        m07 = _part_id(conn, "M-07")
        EvidenceLedger(conn, m07).set_attribute(
            "donor_year", "2017", AttributeStatus.CONFLICTING, source="spreadsheet.csv:M-07:year")
        conn.commit()
        qid = _add_question(conn, "M-07", "donor_year", "The donor year differs. Which is correct?")

        result, propagation = handle_intent(conn, session, AnswerRequest(question_id=qid, text=text, value="2018"))

        assert result.accepted and result.normalised_value == 2018
        assert result.question_status == "answered"
        assert propagation is not None
        attr = EvidenceLedger(conn, m07).get_attribute("donor_year")
        assert attr["value"] == "2018" and attr["status"] == AttributeStatus.SELLER_CONFIRMED.value
        ev = conn.execute("SELECT kind, content FROM evidence WHERE id = ?", (result.evidence_id,)).fetchone()
        assert ev["kind"] == "seller_answer" and ev["content"] == text  # raw text kept as evidence

    def test_router_proposal_not_in_text_is_rejected(self, conn):
        qid = _add_question(conn, "M-07", "donor_year")
        result = execute_intent(ChatTools(conn, login(conn, "Noor")),
                                AnswerRequest(question_id=qid, text="It's 2018", value="2019"))
        assert result.accepted is False and result.ledger_update is None

    def test_hedged_answer_is_stored_but_unconfirmed(self, conn):
        qid = _add_question(conn, "M-07", "donor_year")
        result = execute_intent(ChatTools(conn, login(conn, "Noor")),
                                AnswerRequest(question_id=qid, text="I think it's 2018"))
        assert result.accepted is False and result.reason == "hedged"
        assert result.evidence_id is not None and result.ledger_update is None
        assert result.question_status == "open"

    def test_answer_driver_side_is_not_stored(self, conn):
        qid = _add_question(conn, "M-04", "position", "Is this left or right?")
        result = execute_intent(ChatTools(conn, login(conn, "Noor")),
                                AnswerRequest(question_id=qid, text="driver side", value="left"))
        assert result.accepted is False and result.reason == "ambiguous_position"
        assert result.ledger_update is None

    def test_correction_change_m04_to_right_side_propagates(self, conn):
        session = login(conn, "Noor")
        m04 = _part_id(conn, "M-04")
        EvidenceLedger(conn, m04).set_attribute("position", "left", AttributeStatus.EXTRACTED,
                                                source="spreadsheet.csv:M-04:side")
        conn.commit()
        result, propagation = handle_intent(
            conn, session, CorrectionRequest(sku="M-04", field="side", text="Change M-04 to right side", value="right"))
        assert result.field == "position"  # "side" is a business term for the position field
        assert result.accepted and result.old_value == "left" and result.normalised_value == "right"
        assert result.recompute == ["item_specifics", "title", "compat_lookup", "checklist"]
        assert result.llm_needed is False
        assert propagation is not None
        attr = EvidenceLedger(conn, m04).get_attribute("position")
        assert attr["value"] == "right" and attr["status"] == AttributeStatus.SELLER_CONFIRMED.value

    def test_correction_cannot_target_status(self, conn):
        result = execute_intent(ChatTools(conn, login(conn, "Noor")),
                                CorrectionRequest(sku="M-07", field="status", text="mark it ready"))
        assert result.accepted is False and result.reason == "not_correctable"

    def test_policy_request_writes_nothing_and_logs_refusal(self, conn):
        before = conn.execute("SELECT COUNT(*) c FROM evidence").fetchone()["c"]
        result = execute_intent(
            ChatTools(conn, login(conn, "Noor")),
            PolicyRequest(topic=PolicyTopic.FITMENT, text="Make it fit all Corollas", lang="ar"),
        )
        assert result.allowed is False and result.wrote_state is False
        assert "رقم قطعة" in result.reply  # Arabic reply for an Arabic message
        assert "verified part number" in result.reply_en
        assert conn.execute("SELECT COUNT(*) c FROM evidence").fetchone()["c"] == before
        refusal = conn.execute("SELECT detail_json FROM audit_events WHERE event_type = 'refusal'").fetchone()
        assert json.loads(refusal["detail_json"])["text"] == "Make it fit all Corollas"

    def test_out_of_scope_customs_gives_checklist_and_disclaimer(self, conn):
        result = execute_intent(ChatTools(conn, login(conn, "Noor")),
                                OutOfScopeRequest(topic="customs", text="Is it cleared for UK customs?"))
        assert "not customs or legal advice" in result.reply
        summary = result.checklist_summary
        assert "TEST checklist" in summary.purpose and summary.disclaimer
        assert summary.part_count == 20

    def test_profile_reply_language_overrides_match_user(self, conn):
        update_profile(conn, "S-001", {"reply_language": "ar"})
        result = execute_intent(ChatTools(conn, login(conn, "Noor")),
                                PolicyRequest(topic=PolicyTopic.EXPORT, text="export everything", lang="en"))
        assert result.reply.startswith("لا أستطيع")


def test_init_db_upgrades_an_older_database(tmp_path):
    """An existing file with an older schema gains new columns and working views."""
    from app.data.database import get_connection, init_db

    db = tmp_path / "old.db"
    old = get_connection(db)
    old.executescript("""
        CREATE TABLE sellers (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE parts (id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, part_type TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'Ingested', created_at TEXT, updated_at TEXT);
        CREATE TABLE questions (id TEXT PRIMARY KEY, part_id TEXT NOT NULL, question_text TEXT NOT NULL,
                                status TEXT NOT NULL DEFAULT 'open', created_at TEXT);
        INSERT INTO sellers VALUES ('S-001', 'Noor');
        INSERT INTO parts (id, seller_id, part_type) VALUES ('p1', 'S-001', 'mirror');
        INSERT INTO questions (id, part_id, question_text) VALUES ('q1', 'p1', 'Which year?');
    """)
    old.commit()
    old.close()

    init_db(db)

    conn = get_connection(db)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    assert {"priority", "text_ar", "edge_case", "answer_evidence_id"} <= columns
    assert "sku" in {r["name"] for r in conn.execute("PRAGMA table_info(parts)")}
    row = conn.execute("SELECT priority FROM sem_questions WHERE question_id = 'q1'").fetchone()
    assert row["priority"] == "medium"
    conn.close()


def test_api_profile_update_fields_match_editable_fields():
    from app.api.main import ProfileUpdate

    assert set(ProfileUpdate.model_fields) == EDITABLE_PROFILE_FIELDS
