"""CP6 Readiness engine — checklist C01–C15, status rules and action ranking (TRD 8).

Status rules, first match wins:
  1. Specialist review required — excluded part type (E39), part number maps to
     conflicting vehicles or sides (E34), or a conflict still open after two
     seller rounds.
  2. Information missing — any blocking item fails.
  3. Ready for seller review — every blocking item passes.

Warnings ("donor not recorded", "compatibility not verified", a seller
override, a failed non-blocking item) are shown but never block. Approved is
not a readiness status: only the seller's click sets it (CP7).

Action priority: identity (part number, position, donor) → disclosure →
shipping → commercial; within a tier the action that clears the most items
first, then the quickest (photo before question before measurement). The
chat asks the top three; the dashboard lists all.

Items and their blocking flags come from checklist_v0.1.yaml. Per Rule (g):
no LLM; status is computed from the ledger only (Rule a).
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum

from app.data.reference_data import Checklist, get_checklist
from app.domain.compatibility import CompatResult, CompatStatus, NO_LABEL_VALUE
from app.domain.enums import AttributeStatus
from app.domain.glossary import get_glossary
from app.domain.guard import FITMENT_LANGUAGE, forbidden_phrases
from app.domain.part_facts import DONOR_FIELDS, PartFacts, load_part_facts, price_amount, price_currency

SPECIALIST_ROUNDS = 2  # a conflict still open after this many seller answers goes to a specialist


class ReadinessStatus(str, Enum):
    SPECIALIST_REVIEW = "Specialist review required"
    INFORMATION_MISSING = "Information missing"
    READY = "Ready for seller review"


TIERS = {"identity": 1, "disclosure": 2, "shipping": 3, "commercial": 4}
EFFORT = {"photo": 1, "question": 2, "measurement": 3}


@dataclass
class Action:
    key: str
    text_en: str
    text_ar: str
    tier: str
    effort: str
    clears: list[str]
    field: str | None = None

    def sort_key(self) -> tuple:
        return (TIERS[self.tier], -len(self.clears), EFFORT[self.effort], self.key)


@dataclass
class ItemResult:
    item_id: str
    description: str
    result: str  # pass | fail | pending
    reason: str | None
    blocking: bool
    blocks: str  # ready | approval


@dataclass
class ReadinessResult:
    part_id: str
    sku: str | None
    checklist_version: str
    checklist_checksum: str
    status: ReadinessStatus
    items: list[ItemResult]
    warnings: list[str]
    specialist_reasons: list[str]
    actions: list[Action]
    approval_pending: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status == ReadinessStatus.READY

    def failed(self, *, blocking_only: bool = True) -> list[str]:
        return [i.item_id for i in self.items
                if i.result == "fail" and (i.blocking or not blocking_only) and i.blocks == "ready"]

    def top_actions(self, n: int = 3) -> list[Action]:
        """What the chat asks next."""
        return self.actions[:n]

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.checklist_version,
            "checksum": self.checklist_checksum,
            "status": self.status.value,
            "items": [asdict(i) for i in self.items],
            "warnings": self.warnings,
            "specialist_reasons": self.specialist_reasons,
            "approval_pending": self.approval_pending,
            "actions": [asdict(a) for a in self.actions],
        }


@dataclass
class _Outcome:
    passed: bool | None  # None = pending
    reason: str | None = None
    actions: list[Action] = field(default_factory=list)
    specialist: str | None = None


# ── Helpers ─────────────────────────────────────────────────

_HEDGE = re.compile(r"\b(may|might|possible|possibly|suspected|maybe|likely)\b|\?|ربما|قد يكون|محتمل", re.I)
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|kgs|kilograms?|g|grams?|lb|lbs|pounds?)\b", re.I)
_DIMS = re.compile(
    r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(cm|mm|in|inch|inches)\b", re.I
)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s-]{8,}\d(?!\w)")
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
REQUIRED_VIEWS = ("front", "back", "label", "mounting_points")
COMPONENTS = ("glass", "cover", "indicator", "wiring_plug")


def _defects(text: str | None) -> list[str]:
    match = get_glossary().normalise("defect", text)
    return [v for v in match.values if v != "none"]


def defect_flagged(facts: PartFacts) -> tuple[bool, str | None]:
    """An unconfirmed condition observation that suggests damage (photo, OCR or note)."""
    cond = facts.get("condition")
    if cond is None or cond.value is None or cond.confirmed:
        return False, None
    if _defects(cond.value) or _HEDGE.search(cond.value):
        return True, cond.value
    return False, None


def _weight_kg(value: str | None) -> float | None:
    m = _WEIGHT.search(value or "")
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("g"):
        return number / 1000
    if unit.startswith(("lb", "pound")):
        return number * 0.453592
    return number


def _dims_cm(value: str | None) -> list[float] | None:
    m = _DIMS.search(value or "")
    if not m:
        return None
    factor = {"cm": 1.0, "mm": 0.1}.get(m.group(4).lower(), 2.54)
    return [float(m.group(i)) * factor for i in (1, 2, 3)]


def _donor_candidates(facts: PartFacts, name: str) -> list[str]:
    values: set[str] = set()
    spreadsheet_name = {"donor_year": "year", "donor_model": "model", "donor_make": "make"}.get(name)
    for attr_name in (name, spreadsheet_name):
        attr = facts.attrs.get(attr_name) if attr_name else None
        if attr and attr.value:
            values.add(str(attr.value))
    column = {"donor_year": "year", "donor_model": "model", "donor_make": "make",
              "year": "year", "model": "model", "make": "make"}.get(name)
    for donor in facts.donors:
        if column and donor.get(column):
            values.add(str(donor[column]))
    return sorted(values)


# ── Actions ─────────────────────────────────────────────────


def _act(key: str, en: str, ar: str, tier: str, effort: str, clears: list[str], field_name: str | None = None) -> Action:
    return Action(key, en, ar, tier, effort, clears, field_name)


def _retake_label(item: str) -> Action:
    return _act("retake_label_photo", "Retake the label photo close up, in daylight",
                "أعد تصوير ملصق رقم القطعة عن قرب وفي ضوء النهار", "identity", "photo", [item], "part_number")


# ── Item evaluators ─────────────────────────────────────────

Evaluator = Callable[[PartFacts, CompatResult, Checklist], _Outcome]


def _c01(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    g = get_glossary()
    attr = facts.get("part_type")
    text = (attr.value if attr and attr.value else None) or facts.part_type
    part_type = g.normalise("part_type", text).value
    if part_type and g.is_excluded_part_type(part_type):
        return _Outcome(False, f"excluded type: {text}", specialist="excluded part type (E39)")
    if part_type != facts.product_family:
        return _Outcome(False, f"part type not in scope: {text or 'not recorded'}", [
            _act("confirm_part_type", "What type of part is this?", "ما نوع هذه القطعة؟",
                 "identity", "question", ["C01"], "part_type")])
    return _Outcome(True)


def _c02(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    brand = facts.attrs.get("brand")
    if brand and brand.value and brand.status not in (AttributeStatus.MISSING, AttributeStatus.CONFLICTING):
        return _Outcome(True)
    return _Outcome(False, "brand not recorded", [
        _act("confirm_brand", "What brand is marked on the label (or none visible)?",
             "ما العلامة التجارية الظاهرة على الملصق (أو لا يوجد)؟", "identity", "question", ["C02"], "brand")])


def _c03(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    pn = facts.get("part_number")
    if pn and pn.value == NO_LABEL_VALUE and pn.status == AttributeStatus.SELLER_CONFIRMED:
        return _Outcome(True, "seller confirms no label")
    if pn is None or not pn.value:
        return _Outcome(False, "part number missing", [_retake_label("C03")])
    if "?" in pn.value:
        return _Outcome(False, f"part number unreadable: {pn.value}", [_retake_label("C03")])
    if pn.status == AttributeStatus.CONFLICTING:
        return _Outcome(False, "part number sources disagree", [_retake_label("C03")])
    if not pn.confirmed:
        return _Outcome(False, f"part number {pn.value} not verified", [
            _act("confirm_part_number", f"Confirm the part number on the label: {pn.value}?",
                 f"أكّد رقم القطعة على الملصق: {pn.value}؟", "identity", "question", ["C03"], "part_number")])
    return _Outcome(True)


def _c04(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    pos = facts.get("position")
    ask = _act("confirm_position", "Is this the left or right mirror, as seen from the driver's seat?",
               "هل هذه المرآة اليسرى أم اليمنى، كما تُرى من مقعد السائق؟", "identity", "question", ["C04"], "position")
    if pos is None or pos.value not in ("left", "right"):
        return _Outcome(False, "position missing or ambiguous", [ask])
    if not pos.confirmed:
        return _Outcome(False, f"position {pos.value} not confirmed", [ask])
    return _Outcome(True)


def _c05(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    conflicting = [n for n in DONOR_FIELDS if n in facts.attrs
                   and facts.attrs[n].status == AttributeStatus.CONFLICTING]
    if not conflicting:
        return _Outcome(True)
    actions = []
    for name in conflicting:
        label = name.replace("donor_", "").replace("_", " ")
        options = " or ".join(_donor_candidates(facts, name)) or "which value"
        actions.append(_act(f"confirm_{name}", f"Confirm donor {label}: {options}?",
                            f"أكّد {_AR_DONOR.get(label, label)} السيارة المانحة: {options.replace(' or ', ' أو ')}؟",
                            "identity", "question", ["C05"], name))
    return _Outcome(False, f"conflicting donor field: {', '.join(conflicting)}", actions)


_AR_DONOR = {"year": "سنة", "model": "طراز", "make": "صانع"}


def _c06(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    flagged, text = defect_flagged(facts)
    open_condition = [q for q in facts.open_questions() if q.get("edge_case") in ("E24", "E25", "E26", "E27")]
    if not flagged and not open_condition:
        return _Outcome(True)
    defect = (_defects(text) or ["damaged"])[0]
    word = {"crack": "cracked", "broken": "broken", "scratch": "scratched",
            "broken_glass": "broken", "missing_component": "missing"}.get(defect, "damaged")
    detail = text or open_condition[0]["question_text"] or f"open {open_condition[0]['edge_case']} condition question"
    return _Outcome(False, f"defect flag not confirmed: {detail}", [
        _act("closeup_defect", f"Close-up of the flagged area ({detail}): is it {word}?",
             f"صورة قريبة للمنطقة المشار إليها ({detail}): هل هي متضررة؟",
             "disclosure", "photo", ["C06"], "condition_notes")])


def _c07(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    attr = facts.get("test_status")
    value = get_glossary().normalise("test_status", attr.value).value if attr and attr.value else None
    if value is None and attr and attr.value in get_glossary().allowed_values("test_status"):
        value = attr.value
    if value and attr.confirmed:
        return _Outcome(True)
    reason = "test status not recorded" if attr is None else f"test status not confirmed ({attr.value})"
    return _Outcome(False, reason, [
        _act("confirm_test_status", "Was the mirror tested (fold, heater, adjustment)?",
             "هل تم فحص المرآة (الطي، التسخين، الضبط)؟", "disclosure", "question", ["C07"], "test_status")])


def _c08(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    attr = facts.attrs.get("included_components")
    marked: dict = {}
    if attr and attr.value and attr.status not in (AttributeStatus.MISSING, AttributeStatus.CONFLICTING):
        try:
            marked = json.loads(attr.value)
        except json.JSONDecodeError:
            marked = {}
    unmarked = [c for c in COMPONENTS if marked.get(c) not in ("present", "missing")]
    if not unmarked:
        return _Outcome(True)
    return _Outcome(False, f"components not marked: {', '.join(unmarked)}", [
        _act("confirm_components", "Mark each part present or missing: glass, cover, indicator, wiring plug",
             "حدّد لكل جزء موجود أم مفقود: الزجاج، الغطاء، الإشارة، قابس الأسلاك",
             "disclosure", "question", ["C08"], "included_components")])


def _c09(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    views = {v for p in facts.photos for v in p.views}
    missing = [v for v in REQUIRED_VIEWS if v not in views]
    cond = facts.get("condition")
    has_defect = bool(_defects(cond.value if cond else None)) or defect_flagged(facts)[0]
    if has_defect and "defect" not in views:
        missing.append("defect close-up")
    if not missing:
        return _Outcome(True)
    names = ", ".join(m.replace("_", " ") for m in missing)
    return _Outcome(False, f"photos missing: {names}", [
        _act("add_photos", f"Add photos of: {names}", f"أضف صورًا لـ: {names}",
             "commercial", "photo", ["C09"])])


def _c10(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    weight, dims = facts.attrs.get("weight"), facts.attrs.get("dimensions")
    measure = _act("weigh_and_measure", "Weigh and measure the packed box", "زِن الصندوق المغلّف وقِس أبعاده",
                   "shipping", "measurement", ["C10"], "weight")
    if not weight or not weight.value or not dims or not dims.value:
        missing = [n for n, a in (("weight", weight), ("dimensions", dims)) if not a or not a.value]
        return _Outcome(False, f"missing: {', '.join(missing)}", [measure])
    if AttributeStatus.CONFLICTING in (weight.status, dims.status):
        return _Outcome(False, "weight or dimensions conflict", [measure])
    lo_w, hi_w = checklist.shipping_ranges.get("weight_kg", [0.2, 15.0])
    lo_d, hi_d = checklist.shipping_ranges.get("dimension_cm", [5.0, 100.0])
    kg, cm = _weight_kg(weight.value), _dims_cm(dims.value)
    recheck = _act("recheck_measurements", "Re-check the packed weight and size, and their units (kg/lb, cm/inch)",
                   "أعد التحقق من الوزن والأبعاد ووحداتها (كغ/رطل، سم/إنش)",
                   "shipping", "measurement", ["C10"], "weight")
    if kg is None or cm is None:
        return _Outcome(False, "weight or dimensions without a unit", [recheck])
    if not lo_w <= kg <= hi_w or not all(lo_d <= d <= hi_d for d in cm):
        return _Outcome(False, f"outside configured range (E38): {weight.value}, {dims.value}", [recheck])
    return _Outcome(True)


def _c11(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    price = facts.attrs.get("price")
    ask = _act("confirm_price", "Confirm the price and its currency", "أكّد السعر وعملته",
               "commercial", "question", ["C11"], "price")
    if not price or not price.value or price.status == AttributeStatus.CONFLICTING:
        return _Outcome(False, "price missing", [ask])
    amount = price_amount(price.value)
    if amount is None or amount <= 0:
        return _Outcome(False, f"price unclear: {price.value}", [ask])
    if not price_currency(facts, price.value, price.source):
        return _Outcome(False, "currency not labelled (E40)", [ask])
    return _Outcome(True)


def _c12(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    if compat.lookup_done:
        return _Outcome(True, compat.label)
    if compat.status == CompatStatus.CONFLICTING:
        return _Outcome(False, f"reference matches disagree ({compat.reason})")
    if compat.status == CompatStatus.SIDE_MISMATCH:
        ref_side = next((f.position for f in compat.fitments if f.position), "?")
        return _Outcome(False, f"reference lists {compat.part_number} as {ref_side}, part is {compat.position}", [
            _act("check_side_vs_reference",
                 f"The reference lists part number {compat.part_number} as a {ref_side} mirror, but it is "
                 f"recorded as {compat.position}. Check the side and the label.",
                 f"المرجع يذكر رقم القطعة {compat.part_number} كمرآة {_AR_SIDE.get(ref_side, ref_side)}، لكنها مسجّلة "
                 f"{_AR_SIDE.get(compat.position or '', compat.position)}. تحقّق من الجهة والملصق.",
                 "identity", "question", ["C12", "C04"], "position")])
    return _Outcome(False, f"lookup not possible: {compat.reason}", [_retake_label("C12")])


_AR_SIDE = {"left": "يسرى", "right": "يمنى"}


def _kept_claims(facts: PartFacts) -> list[dict]:
    """Claims that are in the draft (CP5 removals and retired sentences excluded)."""
    draft_id = facts.draft["id"] if facts.draft else None
    return [c for c in facts.claims
            if (c.get("status") or "kept") == "kept" and (c.get("draft_id") in (None, draft_id))]


def _draft_texts(facts: PartFacts) -> list[str]:
    texts = [c.get("claim_value") or "" for c in _kept_claims(facts)]
    if facts.draft:
        texts += [facts.draft.get(k) or "" for k in ("title", "description", "donor_block", "disclosure_block")]
    return texts


def _c13(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    """CP5 passed: no kept claim is unsupported, forbidden or an unverified fitment claim."""
    claims = _kept_claims(facts)
    unsupported = [c for c in claims
                   if not c.get("evidence_id") and json.loads(c.get("evidence_ids_json") or "[]") == []
                   and json.loads(c.get("sources_json") or "[]") == []]
    forbidden = sorted({p for t in _draft_texts(facts) for p in forbidden_phrases(t)})
    fitment = [c for c in claims if c.get("claim_type") != "verified_compat"
               and FITMENT_LANGUAGE.search(c.get("claim_value") or "")]
    if not unsupported and not forbidden and not fitment:
        return _Outcome(True)
    bits = ([f"{len(unsupported)} claim(s) without evidence"] if unsupported else []) + \
           ([f"forbidden: {', '.join(forbidden)}"] if forbidden else []) + \
           ([f"{len(fitment)} fitment claim(s) without a verified lookup"] if fitment else [])
    return _Outcome(False, "; ".join(bits), [
        _act("review_claims", "Remove or support the flagged claims in the draft",
             "احذف الادعاءات المشار إليها في المسودة أو ادعمها بدليل", "disclosure", "question", ["C13"])])


def _c14(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    """CP5b critic on the LLM summary paragraph. Gates approval only."""
    if facts.draft is None:
        return _Outcome(None, "no draft yet")
    status = facts.draft.get("summary_status")
    if facts.draft.get("summary_stale") or status not in ("critic_passed", "template"):
        return _Outcome(None, "summary paragraph updating; the critic (CP5b) has not passed it yet")
    return _Outcome(True, "critic passed" if status == "critic_passed" else "template summary (rules-checked)")


def _c15(facts: PartFacts, compat: CompatResult, checklist: Checklist) -> _Outcome:
    exposed = [t for t in _draft_texts(facts) if _PHONE.search(t) or _VIN.search(t)]
    if not exposed:
        return _Outcome(True)
    return _Outcome(False, "phone number or VIN visible in the draft", [
        _act("redact_personal_data", "Remove the phone number or VIN from the draft",
             "احذف رقم الهاتف أو رقم الهيكل من المسودة", "disclosure", "question", ["C15"])])


EVALUATORS: dict[str, Evaluator] = {
    "C01": _c01, "C02": _c02, "C03": _c03, "C04": _c04, "C05": _c05,
    "C06": _c06, "C07": _c07, "C08": _c08, "C09": _c09, "C10": _c10,
    "C11": _c11, "C12": _c12, "C13": _c13, "C14": _c14, "C15": _c15,
}


# ── Evaluation ──────────────────────────────────────────────


def evaluate(facts: PartFacts, compat: CompatResult, checklist: Checklist | None = None) -> ReadinessResult:
    """Evaluate the checklist and status for one part. Pure; no I/O."""
    checklist = checklist or get_checklist()
    if {i.item_id for i in checklist.items} != set(EVALUATORS):
        raise RuntimeError(
            f"Checklist {checklist.version} items and readiness evaluators differ: "
            f"{sorted({i.item_id for i in checklist.items} ^ set(EVALUATORS))}"
        )

    items: list[ItemResult] = []
    actions: dict[str, Action] = {}
    specialist: list[str] = []
    warnings: list[str] = []
    approval_pending: list[str] = []

    for item in checklist.items:
        outcome = EVALUATORS[item.item_id](facts, compat, checklist)
        result = "pending" if outcome.passed is None else ("pass" if outcome.passed else "fail")
        items.append(ItemResult(item.item_id, item.description, result, outcome.reason, item.blocking, item.blocks))
        if outcome.specialist and item.on_fail == "specialist_review":
            specialist.append(outcome.specialist)
        if item.blocks == "approval":
            if result != "pass":
                approval_pending.append(item.item_id)
            continue
        if result == "fail" and not item.blocking:
            warnings.append(f"{item.description}: {outcome.reason}")
            continue
        if result == "fail":
            for action in outcome.actions:
                merged = actions.setdefault(action.key, action)
                if merged is not action:
                    merged.clears = sorted(set(merged.clears) | set(action.clears))

    if compat.specialist_review:
        specialist.append(f"part number maps to conflicting vehicles or sides (E34: {compat.reason})")
    for name, attr in facts.attrs.items():
        rounds = facts.seller_rounds(name)
        if attr.status == AttributeStatus.CONFLICTING and rounds >= SPECIALIST_ROUNDS:
            specialist.append(f"{name} conflict still open after {rounds} seller rounds")

    if not facts.donors and not any(n in facts.attrs for n in DONOR_FIELDS):
        warnings.append("donor not recorded")
    if compat.status != CompatStatus.VERIFIED:
        warnings.append("compatibility not verified")
    if facts.seller_overrides:
        warnings.append("seller override shown in the review summary")

    failed_blocking = any(i.result == "fail" and i.blocking and i.blocks == "ready" for i in items)
    if specialist:
        status = ReadinessStatus.SPECIALIST_REVIEW
    elif failed_blocking:
        status = ReadinessStatus.INFORMATION_MISSING
    else:
        status = ReadinessStatus.READY

    failing = {i.item_id for i in items if i.result == "fail" and i.blocking and i.blocks == "ready"}
    for action in actions.values():
        action.clears = sorted(c for c in action.clears if c in failing)
    ranked = sorted(actions.values(), key=Action.sort_key) if status != ReadinessStatus.SPECIALIST_REVIEW else []
    return ReadinessResult(
        part_id=facts.part_id,
        sku=facts.sku,
        checklist_version=checklist.version,
        checklist_checksum=checklist.checksum,
        status=status,
        items=items,
        warnings=warnings,
        specialist_reasons=specialist,
        actions=ranked,
        approval_pending=approval_pending,
    )


def evaluate_part(
    conn: sqlite3.Connection,
    part_id: str,
    compat: CompatResult,
    *,
    checklist: Checklist | None = None,
    persist: bool = True,
) -> ReadinessResult:
    """Evaluate one part from the ledger and store the result. Does not commit."""
    result = evaluate(load_part_facts(conn, part_id), compat, checklist)
    if persist:
        conn.execute(
            """INSERT INTO checklist_results (id, part_id, listing_id, checklist_json, passed)
               VALUES (lower(hex(randomblob(16))), ?, NULL, ?, ?)""",
            (part_id, json.dumps(result.to_json(), ensure_ascii=False), int(result.ready)),
        )
    return result
