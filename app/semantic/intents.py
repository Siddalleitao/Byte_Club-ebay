"""Chat intents mapped to semantic queries (TRD 4).

    Seller says                         Intent          Semantic action
    "Which parts still need weight?"    status_query    parts where weight is Missing
    "Why is M-07 not ready?"            explain         failed checklist items for M-07
    "It's 2018" (to an open question)   answer          donor_year = 2018 as seller-confirmed
    "Change M-04 to right side"         correction      position = right, then propagate
    "Make it fit all Corollas"          policy_request  no write; guardrail reply
    "Is it cleared for UK customs?"     out_of_scope    checklist summary plus disclaimer

The fast-tier router turns a message into one typed IntentRequest (its only
job: classify and copy the value). `execute_intent` runs the semantic action
through ChatTools only, so the access policy applies, and returns a typed
IntentResult. There is no text-to-SQL.

LLMs propose, rules decide (Rule a): the value the router copied is only a
proposal. It is re-derived here from the seller's own text with the glossary
and plausibility rules; if the text does not support it, nothing is stored
as a value. Answers and corrections never write the attribute themselves:
they return a LedgerUpdate for orchestration to hand to the domain ledger.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, Field

from app.domain.cp1_rules import PART_NUMBER_PATTERN, YEAR_MAX, YEAR_MIN
from app.domain.enums import AttributeStatus
from app.domain.ledger import REQUIRED_FIELDS
from app.domain.propagation import NOT_CORRECTABLE, rule_for_field
from app.domain.provenance import detect_hedged_answer
from app.semantic.access_policy import ChatTools
from app.semantic.entity_model import (
    ActionView,
    AttributeView,
    ChecklistItemView,
    PartFieldRow,
    QuestionView,
)

_REFERENCE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reference"

Lang = Literal["en", "ar"]


class Intent(str, Enum):
    STATUS_QUERY = "status_query"
    EXPLAIN = "explain"
    ANSWER = "answer"
    CORRECTION = "correction"
    POLICY_REQUEST = "policy_request"
    OUT_OF_SCOPE = "out_of_scope"


class PolicyTopic(str, Enum):
    FITMENT = "fitment"
    HIDE_DEFECT = "hide_defect"
    FUNCTION_CLAIM = "function_claim"
    AUTHENTICITY = "authenticity"
    CONDITION_NEW = "condition_new"
    STATUS_CHANGE = "status_change"
    ADMIN = "admin"
    EXPORT = "export"
    OTHER = "other"


# ── Requests (router output; data only) ─────────────────────


class StatusQueryRequest(BaseModel):
    intent: Literal["status_query"] = "status_query"
    field: str  # field name or business term, e.g. "weight" / "الوزن"
    statuses: list[AttributeStatus] = Field(default_factory=lambda: [AttributeStatus.MISSING])
    lang: Lang = "en"


class ExplainRequest(BaseModel):
    intent: Literal["explain"] = "explain"
    sku: str
    lang: Lang = "en"


class AnswerRequest(BaseModel):
    intent: Literal["answer"] = "answer"
    question_id: str
    text: str = Field(min_length=1)  # the seller's message, verbatim
    value: str | None = None  # router's proposed value, copied from the text
    lang: Lang = "en"


class CorrectionRequest(BaseModel):
    intent: Literal["correction"] = "correction"
    sku: str
    field: str
    text: str = Field(min_length=1)
    value: str | None = None
    lang: Lang = "en"


class PolicyRequest(BaseModel):
    intent: Literal["policy_request"] = "policy_request"
    topic: PolicyTopic = PolicyTopic.OTHER
    text: str
    sku: str | None = None
    lang: Lang = "en"


class OutOfScopeRequest(BaseModel):
    intent: Literal["out_of_scope"] = "out_of_scope"
    topic: Literal["customs", "legal", "other"] = "other"
    text: str
    sku: str | None = None
    lang: Lang = "en"


IntentRequest = Annotated[
    Union[
        StatusQueryRequest,
        ExplainRequest,
        AnswerRequest,
        CorrectionRequest,
        PolicyRequest,
        OutOfScopeRequest,
    ],
    Field(discriminator="intent"),
]


# ── Results ─────────────────────────────────────────────────


class LedgerUpdate(BaseModel):
    """A seller-confirmed value for orchestration to apply through the domain ledger."""

    sku: str
    field: str
    value: str | int | list[str] | dict[str, str]
    evidence_id: str
    status: Literal["Seller-confirmed"] = "Seller-confirmed"


class StatusQueryResult(BaseModel):
    intent: Literal["status_query"] = "status_query"
    field: str
    statuses: list[AttributeStatus]
    parts: list[PartFieldRow]
    count: int


class ExplainResult(BaseModel):
    intent: Literal["explain"] = "explain"
    sku: str
    found: bool
    part_status: str | None = None
    checklist_evaluated: bool = False
    ready: bool | None = None
    readiness_status: str | None = None  # TRD 8 status from the latest evaluation
    failed_checklist_items: list[ChecklistItemView] = Field(default_factory=list)
    next_actions: list[ActionView] = Field(default_factory=list)  # top three, ranked
    warnings: list[str] = Field(default_factory=list)
    blocking_fields: list[AttributeView] = Field(default_factory=list)
    open_questions: list[QuestionView] = Field(default_factory=list)


class AnswerResult(BaseModel):
    intent: Literal["answer"] = "answer"
    question_id: str
    sku: str | None = None
    field: str | None = None
    accepted: bool
    reason: str | None = None  # why not accepted: hedged, ambiguous_position, not_understood, ...
    normalised_value: str | int | list[str] | None = None
    evidence_id: str | None = None
    question_status: str | None = None
    ledger_update: LedgerUpdate | None = None
    additional_updates: list[LedgerUpdate] = Field(default_factory=list)  # e.g. dimensions with weight


class CorrectionResult(BaseModel):
    intent: Literal["correction"] = "correction"
    sku: str
    field: str
    accepted: bool
    reason: str | None = None
    old_value: str | None = None
    normalised_value: str | int | list[str] | None = None
    evidence_id: str | None = None
    ledger_update: LedgerUpdate | None = None
    additional_updates: list[LedgerUpdate] = Field(default_factory=list)
    recompute: list[str] = Field(default_factory=list)
    llm_needed: bool = False


class PolicyResult(BaseModel):
    intent: Literal["policy_request"] = "policy_request"
    topic: PolicyTopic
    allowed: Literal[False] = False
    reason: str
    reply: str
    alternative: str
    reply_en: str
    reply_ar: str
    wrote_state: Literal[False] = False


class ChecklistSummary(BaseModel):
    checklist_version: str
    purpose: str
    disclaimer: str
    sku: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)  # item_id, description, result
    ready_count: int | None = None
    part_count: int | None = None


class OutOfScopeResult(BaseModel):
    intent: Literal["out_of_scope"] = "out_of_scope"
    topic: str
    reply: str
    reply_en: str
    reply_ar: str
    checklist_summary: ChecklistSummary | None = None


IntentResult = Union[
    StatusQueryResult,
    ExplainResult,
    AnswerResult,
    CorrectionResult,
    PolicyResult,
    OutOfScopeResult,
]


# ── Value parsing (rules decide) ────────────────────────────

# field -> controlled vocabulary that decides its value
_VOCAB_FIELDS = {
    "position": "position",
    "side": "position",
    "test_status": "test_status",
    "functionality": "test_status",
    "condition": "condition",
}
_MULTI_VOCAB_FIELDS = {"features": "features"}
_YEAR_FIELDS = {"year", "donor_year"}
_PART_NUMBER_FIELDS = {"part_number"}
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_PART_NUMBER_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{4,19}[A-Za-z0-9]")
_MAX_FREE_TEXT = 300

# Condition answers are stored in English for the UK listing; the seller's own
# words (any language) stay on the seller_answer evidence row.
_CONDITION_FIELDS = {"condition_notes", "condition", "defect"}
_DEFECT_WORDS = {
    "broken_glass": "broken glass",
    "crack": "cracked",
    "broken": "broken",
    "scratch": "scratched",
    "missing_component": "component missing",
    "none": "no damage",
}

# Packed weight and dimensions, in English or Arabic units (E38 checks range later).
MEASUREMENT_FIELDS = ("weight", "dimensions")
_ARABIC_UNITS = [
    ("كيلوغرام", "kg"), ("كيلوجرام", "kg"), ("كيلو", "kg"), ("كجم", "kg"), ("كغ", "kg"),
    ("غرام", "g"), ("جرام", "g"), ("رطل", "lb"),
    ("سنتيمتر", "cm"), ("سم", "cm"), ("ملم", "mm"), ("مم", "mm"), ("إنش", "in"), ("انش", "in"),
]
_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|kgs|kilograms?|g|grams?|lb|lbs|pounds?)(?![a-z])", re.I)
_DIMS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:x|×|\*|by)\s*(\d+(?:\.\d+)?)\s*(?:x|×|\*|by)\s*(\d+(?:\.\d+)?)\s*"
    r"(cm|mm|in|inch|inches)(?![a-z])", re.I,
)


def measurement_text(text: str, digits: Callable[[str], str]) -> str:
    """Normalise a measurement answer: digits, Arabic decimal separator, units, 'by'."""
    out = digits(text).replace("٫", ".").replace("،", ",")
    for arabic, unit in _ARABIC_UNITS:
        out = out.replace(arabic, f" {unit} ")
    return re.sub(r"\s+في\s+", " x ", out)


def parse_weight(text: str) -> str | None:
    m = _WEIGHT_RE.search(text)
    if not m:
        return None
    unit = m.group(2).lower()
    unit = "kg" if unit.startswith("k") else "lb" if unit.startswith(("lb", "pound")) else "g"
    return f"{m.group(1)} {unit}"


def parse_dimensions(text: str) -> str | None:
    m = _DIMS_RE.search(text)
    if not m:
        return None
    unit = m.group(4).lower()
    unit = "in" if unit.startswith("in") else unit
    return f"{m.group(1)} x {m.group(2)} x {m.group(3)} {unit}"


@dataclass(frozen=True)
class ParsedValue:
    value: str | int | list[str] | None
    accepted: bool
    reason: str | None = None
    companions: dict[str, Any] | None = None  # extra fields the same answer sets, e.g. feature_tests


_QUALIFIERS = r"(?:lower|upper|left|right|front|rear|inner|outer|top|bottom)"
_TEST_FIELDS = {"test_status", "functionality"}


def _component_phrase(g, text: str, component: str) -> str:
    """The component as written in an English flag, with its qualifier ("Lower mounting tab")."""
    phrases = sorted(g.vocabularies["component"].raw[component]["en"], key=len, reverse=True)
    for phrase in phrases:
        m = re.search(rf"(?:\b{_QUALIFIERS}\s+)?\b{re.escape(phrase)}\b", text, re.I)
        if m:
            return m.group(0)
    return g.vocabularies["component"].meta[component].get("label", component.replace("_", " "))


def describe_condition(g, text: str, defects: tuple[str, ...], current: str | None) -> str:
    """English condition value for the listing: where, what, and the photo it was seen in.

    "لسان التثبيت مشروخ" answering the flag "Lower mounting tab may have crack (photo 3)"
    → "Lower mounting tab: cracked (photo 3)". The seller's own words stay on the evidence row.
    """
    words = ", ".join(_DEFECT_WORDS[d] for d in defects)
    answered = g.normalise("component", text).value
    flagged = g.normalise("component", current).value if current else None
    subject = None
    if flagged and answered in (None, flagged):
        subject = _component_phrase(g, current, flagged)
    elif answered:
        subject = g.vocabularies["component"].meta[answered].get("label", answered)
    value = f"{subject}: {words}" if subject else words
    photo = re.search(r"\(photo \d+\)", current or "", re.I)
    if photo and (flagged and answered in (None, flagged)):
        value += f" {photo.group(0)}"
    return value[0].upper() + value[1:]


def parse_feature_tests(g, text: str) -> dict[str, str]:
    """Per-feature test results from a reply such as "fold works, heater not tested" (E29)."""
    results: dict[str, str] = {}
    for clause in re.split(r"[,،;؛]|\band\b|\sو\s", text):
        features = g.normalise("features", clause).values
        status = g.normalise("test_status", clause).value
        if features and status:
            for feature in features:
                results[feature] = status
    return results


def parse_value(
    tools: ChatTools, field: str, text: str, proposed: str | None, current: str | None = None
) -> ParsedValue:
    """Derive a field value from the seller's own text. Never guesses.

    `current` is the field's present value (e.g. the photo flag a condition answer confirms).
    """
    g = tools.glossary
    text_n = g.normalise_digits(text)
    proposed_n = g.normalise_digits(proposed).strip() if proposed else None

    if field in _TEST_FIELDS:
        per_feature = parse_feature_tests(g, text_n)
        if per_feature:
            statuses = set(per_feature.values())
            value = statuses.pop() if len(statuses) == 1 else "partly_tested"
            return ParsedValue(value, True, companions={"feature_tests": per_feature})

    if field in _VOCAB_FIELDS:
        match = g.normalise(_VOCAB_FIELDS[field], text_n)
        if match.forbidden and match.value is None:
            return ParsedValue(None, False, "forbidden_value")
        if match.ambiguous:
            return ParsedValue(None, False, f"ambiguous_{_VOCAB_FIELDS[field]}")
        if match.value is None:
            return ParsedValue(None, False, "conflicting_values" if match.values else "not_understood")
        return ParsedValue(match.value, True)

    if field in _MULTI_VOCAB_FIELDS:
        match = g.normalise(_MULTI_VOCAB_FIELDS[field], text_n)
        if not match.values:
            return ParsedValue(None, False, "not_understood")
        return ParsedValue(sorted(match.values), True)

    if field in _YEAR_FIELDS:
        years = sorted({int(y) for y in _YEAR_RE.findall(text_n)})
        if proposed_n and proposed_n.isdigit():
            years = [y for y in years if y == int(proposed_n)]
        if len(years) != 1:
            return ParsedValue(None, False, "not_understood" if not years else "conflicting_values")
        if not YEAR_MIN <= years[0] <= YEAR_MAX:
            return ParsedValue(None, False, "out_of_range")
        return ParsedValue(years[0], True)

    if field in _PART_NUMBER_FIELDS:
        if "?" in text_n:
            return ParsedValue(None, False, "unreadable_part_number")
        tokens = {t.upper() for t in _PART_NUMBER_TOKEN_RE.findall(text_n)
                  if PART_NUMBER_PATTERN.match(t) and any(c.isdigit() for c in t)}
        if proposed_n:
            tokens = {t for t in tokens if t == proposed_n.upper()}
        if len(tokens) != 1:
            return ParsedValue(None, False, "not_understood" if not tokens else "conflicting_values")
        return ParsedValue(tokens.pop(), True)

    if field in _CONDITION_FIELDS:
        match = g.normalise("defect", text_n)
        if match.values:
            if "none" in match.values and len(match.values) > 1:
                return ParsedValue(None, False, "conflicting_values")
            if match.values == ("none",):
                return ParsedValue("No damage (seller-confirmed)", True)
            return ParsedValue(describe_condition(g, text_n, match.values, current), True)
        if any("\u0600" <= ch <= "\u06FF" for ch in text_n):
            return ParsedValue(None, False, "not_understood")  # never store untranslated Arabic as a value

    if field == "weight":
        value = parse_weight(measurement_text(text, g.normalise_digits))
        return ParsedValue(value, value is not None, None if value else "not_understood")

    if field == "dimensions":
        value = parse_dimensions(measurement_text(text, g.normalise_digits))
        return ParsedValue(value, value is not None, None if value else "not_understood")

    # Free-text fields (price, condition notes in English, ...):
    # the proposal must appear in the seller's text (E48: not in source → dropped).
    if proposed_n:
        if g.fold(proposed_n) and g.fold(proposed_n) in g.fold(text_n):
            return ParsedValue(proposed_n[:_MAX_FREE_TEXT], True)
        return ParsedValue(None, False, "value_not_in_text")
    return ParsedValue(text_n.strip()[:_MAX_FREE_TEXT], True)


# ── Intent handlers ─────────────────────────────────────────


def _status_query(tools: ChatTools, req: StatusQueryRequest) -> StatusQueryResult:
    field = tools.glossary.field_name(req.field)
    rows = tools.parts_by_field_status(field, req.statuses)
    return StatusQueryResult(field=field, statuses=req.statuses, parts=rows, count=len(rows))


def _explain(tools: ChatTools, req: ExplainRequest) -> ExplainResult:
    part = tools.get_part(req.sku)
    if part is None:  # unknown, or another seller's SKU: same answer, no leak
        return ExplainResult(sku=req.sku, found=False)
    checklist = tools.get_checklist(req.sku)
    attrs = tools.get_attributes(req.sku)
    present = {a.field for a in attrs}
    blocking = [
        a for a in attrs
        if a.status == AttributeStatus.CONFLICTING
        or (a.status == AttributeStatus.MISSING and a.field in REQUIRED_FIELDS)
    ]
    blocking += [
        AttributeView(sku=req.sku, field=f, status=AttributeStatus.MISSING)
        for f in sorted(REQUIRED_FIELDS - present)
    ]
    return ExplainResult(
        sku=req.sku,
        found=True,
        part_status=part.status,
        checklist_evaluated=checklist is not None,
        ready=checklist.passed if checklist else None,
        readiness_status=checklist.status if checklist else None,
        failed_checklist_items=[i for i in (checklist.items if checklist else []) if i.result == "fail"],
        next_actions=checklist.actions[:3] if checklist else [],
        warnings=checklist.warnings if checklist else [],
        blocking_fields=blocking,
        open_questions=tools.get_questions(req.sku, status="open"),
    )


def _answer(tools: ChatTools, req: AnswerRequest) -> AnswerResult:
    question = tools.get_question(req.question_id)
    if question is None:
        return AnswerResult(question_id=req.question_id, accepted=False, reason="unknown_question")
    if question.status != "open":
        return AnswerResult(question_id=req.question_id, sku=question.sku, field=question.field,
                            accepted=False, reason="question_not_open", question_status=question.status)

    field = question.field
    if field is None:  # free-form question: keep the answer as evidence, no field value
        parsed = ParsedValue(None, True)
    else:
        current = next(iter(tools.get_attributes(question.sku, field=field)), None)
        parsed = parse_value(tools, field, req.text, req.value, current.value if current else None)
    if parsed.accepted and detect_hedged_answer(req.text):  # E41: stored, but unconfirmed
        parsed = ParsedValue(parsed.value, False, "hedged")

    evidence_id = tools.write_seller_answer(
        question.sku, req.text, field=field, question_id=question.question_id,
        normalised_value=_as_text(parsed.value), accepted=parsed.accepted,
        reason=parsed.reason, lang=req.lang,
    )
    status = question.status
    update = None
    extra: list[LedgerUpdate] = []
    if parsed.accepted:
        status = tools.set_question_status(
            question.question_id, "answered", answer_text=req.text, answer_evidence_id=evidence_id
        ).status
        if field is not None:
            update = LedgerUpdate(sku=question.sku, field=field, value=parsed.value, evidence_id=evidence_id)
        for name, value in (parsed.companions or {}).items():
            extra.append(LedgerUpdate(sku=question.sku, field=name, value=value, evidence_id=evidence_id))
        if field in MEASUREMENT_FIELDS:  # "1.8 kg, 45 x 30 x 25 cm" answers both
            for other in MEASUREMENT_FIELDS:
                companion = parse_value(tools, other, req.text, None) if other != field else None
                if companion and companion.accepted:
                    extra.append(LedgerUpdate(sku=question.sku, field=other, value=companion.value,
                                              evidence_id=evidence_id))
    return AnswerResult(
        question_id=question.question_id, sku=question.sku, field=field,
        accepted=parsed.accepted, reason=parsed.reason, normalised_value=parsed.value,
        evidence_id=evidence_id, question_status=status, ledger_update=update,
        additional_updates=extra,
    )


def _correction(tools: ChatTools, req: CorrectionRequest) -> CorrectionResult:
    field = tools.glossary.field_name(req.field)
    if field in NOT_CORRECTABLE:
        return CorrectionResult(sku=req.sku, field=field, accepted=False, reason="not_correctable")
    if tools.get_part(req.sku) is None:
        return CorrectionResult(sku=req.sku, field=field, accepted=False, reason="unknown_part")

    current = next(iter(tools.get_attributes(req.sku, field=field)), None)
    parsed = parse_value(tools, field, req.text, req.value, current.value if current else None)
    if parsed.accepted and detect_hedged_answer(req.text):
        parsed = ParsedValue(parsed.value, False, "hedged")

    evidence_id = tools.write_seller_answer(
        req.sku, req.text, field=field, normalised_value=_as_text(parsed.value),
        accepted=parsed.accepted, reason=parsed.reason, lang=req.lang,
    )
    rule = rule_for_field(field)
    return CorrectionResult(
        sku=req.sku, field=field, accepted=parsed.accepted, reason=parsed.reason,
        old_value=current.value if current else None, normalised_value=parsed.value,
        evidence_id=evidence_id,
        ledger_update=(
            LedgerUpdate(sku=req.sku, field=field, value=parsed.value, evidence_id=evidence_id)
            if parsed.accepted else None
        ),
        additional_updates=[
            LedgerUpdate(sku=req.sku, field=name, value=value, evidence_id=evidence_id)
            for name, value in (parsed.companions or {}).items()
        ] if parsed.accepted else [],
        recompute=list(rule.recompute) if rule and parsed.accepted else [],
        llm_needed=bool(rule and rule.needs_llm and parsed.accepted),
    )


def _policy(tools: ChatTools, req: PolicyRequest) -> PolicyResult:
    copy = _refusal_copy()["policy"][req.topic.value]
    tools.log_refusal(req.topic.value, req.text, sku=req.sku)
    lang = _reply_lang(tools, req.lang)
    return PolicyResult(
        topic=req.topic,
        reason=copy["reason"],
        reply=copy[lang],
        alternative=copy[f"alternative_{lang}"],
        reply_en=f"{copy['en']} {copy['alternative_en']}",
        reply_ar=f"{copy['ar']} {copy['alternative_ar']}",
    )


def _out_of_scope(tools: ChatTools, req: OutOfScopeRequest) -> OutOfScopeResult:
    copy = _refusal_copy()["out_of_scope"][req.topic]
    summary = None
    if req.topic in ("customs", "legal"):
        checklist = _checklist_reference()
        descriptions = {i["id"]: i["description"] for i in checklist.get("items", [])}
        summary = ChecklistSummary(
            checklist_version=str(checklist.get("version", "")),
            purpose=checklist.get("purpose", ""),
            disclaimer=checklist.get("disclaimer", "").strip(),
        )
        latest = tools.get_checklist(req.sku) if req.sku else None
        if latest is not None:
            summary.sku = req.sku
            summary.items = [
                {"item_id": i.item_id, "description": descriptions.get(i.item_id), "result": i.result}
                for i in latest.items
            ]
        else:
            summary.ready_count = tools.get_metrics()["ready_count"].value
            summary.part_count = len(tools.list_parts())
    lang = _reply_lang(tools, req.lang)
    return OutOfScopeResult(
        topic=req.topic, reply=copy[lang], reply_en=copy["en"], reply_ar=copy["ar"],
        checklist_summary=summary,
    )


_HANDLERS = {
    Intent.STATUS_QUERY.value: _status_query,
    Intent.EXPLAIN.value: _explain,
    Intent.ANSWER.value: _answer,
    Intent.CORRECTION.value: _correction,
    Intent.POLICY_REQUEST.value: _policy,
    Intent.OUT_OF_SCOPE.value: _out_of_scope,
}


def execute_intent(tools: ChatTools, request: IntentRequest) -> IntentResult:
    """Run the semantic action for one routed chat intent."""
    return _HANDLERS[request.intent](tools, request)


# ── Helpers ─────────────────────────────────────────────────


def _as_text(value: str | int | list[str] | None) -> str | None:
    if value is None:
        return None
    return ", ".join(value) if isinstance(value, list) else str(value)


def _reply_lang(tools: ChatTools, message_lang: str) -> str:
    setting = tools.get_profile().reply_language
    return message_lang if setting == "match_user" else setting


@lru_cache(maxsize=1)
def _refusal_copy() -> dict[str, Any]:
    return yaml.safe_load((_REFERENCE_DIR / "refusal_copy.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _checklist_reference() -> dict[str, Any]:
    return yaml.safe_load((_REFERENCE_DIR / "checklist_v0.1.yaml").read_text(encoding="utf-8"))
