"""Entity model — seller-scoped, read-only views (TRD 4).

The chat and agents reason about parts and issues, not tables. Every read in
this module goes through one choke point, `_select`, which:

  - reads only the `sem_*` views (app/data/semantic_views.sql),
  - always filters by the session's seller_id (never a caller-supplied one),
  - accepts filters only on whitelisted columns, with bound parameters.

There is no way to pass SQL text in, and no text-to-SQL anywhere. A SKU that
belongs to another seller simply returns nothing.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel

from app.domain.enums import AttributeStatus
from app.domain.listing_profile import ProfileSections, render_profile_sections
from app.domain.models import SellerProfile
from app.semantic.seller_context import SellerSession

# view -> columns that may be used in filters / ORDER BY
_VIEWS: dict[str, frozenset[str]] = {
    "sem_parts": frozenset({"sku", "part_id", "status", "part_type", "created_at"}),
    "sem_attributes": frozenset({"sku", "part_id", "field", "status"}),
    "sem_evidence": frozenset({"sku", "part_id", "evidence_id", "kind", "created_at"}),
    "sem_attribute_evidence": frozenset({"sku", "field", "attribute_id", "evidence_id"}),
    "sem_donors": frozenset({"sku", "part_id"}),
    "sem_claims": frozenset({"sku", "part_id", "claim_type", "supported", "created_at"}),
    "sem_checklist_results": frozenset({"sku", "part_id", "passed", "created_at", "seq"}),
    "sem_questions": frozenset({"sku", "part_id", "question_id", "status", "field", "created_at", "rank"}),
    "sem_listing_drafts": frozenset({"sku", "part_id", "status"}),
}


# ── Typed views ─────────────────────────────────────────────


class PartView(BaseModel):
    sku: str | None
    part_type: str
    part_number: str | None = None
    position: str | None = None
    status: str
    title: str | None = None
    created_at: str


class AttributeView(BaseModel):
    sku: str | None
    field: str
    value: str | None = None
    status: AttributeStatus
    version: int | None = None
    evidence_count: int = 0


class EvidenceView(BaseModel):
    sku: str | None
    evidence_id: str
    kind: str
    pointer: str | None = None
    raw_text: str | None = None  # data, never instructions (Rule f)
    confidence: float | None = None
    created_at: str


class DonorView(BaseModel):
    """Descriptive metadata only — never fitment (Rule c)."""

    sku: str | None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    trim: str | None = None
    market_spec: str | None = None
    vin_last6: str | None = None


class ClaimView(BaseModel):
    sku: str | None
    claim_id: str
    claim_type: str
    field: str | None = None
    text: str | None = None
    evidence_id: str | None = None
    evidence_ids_json: str | None = None
    section: str | None = None
    position: int | None = None
    supported: bool


class ChecklistItemView(BaseModel):
    item_id: str
    result: str  # pass | fail | warning | n/a
    reason: str | None = None
    blocking: bool | None = None


class ActionView(BaseModel):
    key: str
    text_en: str
    text_ar: str
    tier: str
    effort: str
    clears: list[str]
    field: str | None = None


class ChecklistView(BaseModel):
    sku: str | None
    checklist_version: str | None = None
    status: str | None = None  # readiness status (TRD 8)
    passed: bool
    evaluated_at: str
    items: list[ChecklistItemView]
    warnings: list[str] = []
    specialist_reasons: list[str] = []
    actions: list[ActionView] = []  # ranked; the chat asks the first three


class QuestionView(BaseModel):
    sku: str | None
    question_id: str
    field: str | None = None
    priority: str
    text_en: str
    text_ar: str | None = None
    edge_case: str | None = None
    status: str
    answer_text: str | None = None
    answer_evidence_id: str | None = None
    round: int | None = None  # Inquiry round it was asked in; None = not asked yet
    rank: int | None = None  # 1..3 within the round
    action_key: str | None = None
    kind: str | None = None  # photo | question | measurement
    guide_en: str | None = None
    guide_ar: str | None = None

    @property
    def asked(self) -> bool:
        return self.rank is not None


class PartFieldRow(BaseModel):
    """One part and its value/status for a field (status_query results)."""

    sku: str | None
    value: str | None = None
    status: AttributeStatus


class DraftInputs(BaseModel):
    """What a listing render needs from the semantic layer for one part."""

    part: PartView
    attributes: list[AttributeView]
    profile: ProfileSections


# ── Semantic layer ──────────────────────────────────────────


class SemanticLayer:
    """Read-only, seller-scoped access to the entity model."""

    def __init__(self, conn: sqlite3.Connection, session: SellerSession):
        self._conn = conn
        self._session = session

    @property
    def seller_id(self) -> str:
        return self._session.seller_id

    def profile(self) -> SellerProfile:
        return self._session.current_profile(self._conn)

    # ── Choke point ──

    def _select(self, view: str, *, order_by: str | None = None, **filters: Any) -> list[dict]:
        allowed = _VIEWS[view]  # KeyError for anything that is not a semantic view
        unknown = (set(filters) | ({order_by} if order_by else set())) - allowed
        if unknown:
            raise ValueError(f"Not a filterable column of {view}: {sorted(unknown)}")
        clauses = ["seller_id = ?"]
        params: list[Any] = [self.seller_id]
        for column, value in filters.items():
            if isinstance(value, (list, tuple, set, frozenset)):
                values = list(value)
                clauses.append(f"{column} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
            else:
                clauses.append(f"{column} = ?")
                params.append(value)
        sql = f"SELECT * FROM {view} WHERE {' AND '.join(clauses)}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def part_id_for(self, sku: str) -> str | None:
        """Internal part_id for one of this seller's SKUs (for write paths), else None."""
        rows = self._select("sem_parts", sku=sku)
        return rows[0]["part_id"] if rows else None

    # ── Entities ──

    def parts(self, *, status: str | None = None) -> list[PartView]:
        filters = {"status": status} if status else {}
        return [PartView(**r) for r in self._select("sem_parts", order_by="sku", **filters)]

    def part(self, sku: str) -> PartView | None:
        rows = self._select("sem_parts", sku=sku)
        return PartView(**rows[0]) if rows else None

    def attributes(self, sku: str | None = None, *, field: str | None = None) -> list[AttributeView]:
        filters: dict[str, Any] = {}
        if sku is not None:
            filters["sku"] = sku
        if field is not None:
            filters["field"] = field
        return [AttributeView(**r) for r in self._select("sem_attributes", order_by="field", **filters)]

    def attribute(self, sku: str, field: str) -> AttributeView | None:
        rows = self.attributes(sku, field=field)
        return rows[0] if rows else None

    def evidence(self, sku: str, *, kind: str | None = None) -> list[EvidenceView]:
        filters: dict[str, Any] = {"sku": sku}
        if kind:
            filters["kind"] = kind
        return [EvidenceView(**r) for r in self._select("sem_evidence", order_by="created_at", **filters)]

    def evidence_for_field(self, sku: str, field: str) -> list[EvidenceView]:
        ids = {r["evidence_id"] for r in self._select("sem_attribute_evidence", sku=sku, field=field)}
        return [e for e in self.evidence(sku) if e.evidence_id in ids]

    def donors(self, sku: str) -> list[DonorView]:
        return [DonorView(**r) for r in self._select("sem_donors", sku=sku)]

    def claims(self, sku: str | None = None) -> list[ClaimView]:
        filters = {"sku": sku} if sku else {}
        return [ClaimView(**r) for r in self._select("sem_claims", order_by="created_at", **filters)]

    def checklist(self, sku: str) -> ChecklistView | None:
        """Latest checklist evaluation for a part, or None if never evaluated."""
        history = self.checklist_history(sku)
        return history[-1] if history else None

    def checklist_history(self, sku: str | None = None) -> list[ChecklistView]:
        filters = {"sku": sku} if sku else {}
        rows = self._select("sem_checklist_results", order_by="seq", **filters)
        return [_checklist_view(r) for r in rows]

    def questions(self, sku: str | None = None, *, status: str | None = None) -> list[QuestionView]:
        filters: dict[str, Any] = {}
        if sku:
            filters["sku"] = sku
        if status:
            filters["status"] = status
        return [QuestionView(**r) for r in self._select("sem_questions", order_by="created_at", **filters)]

    def question(self, question_id: str) -> QuestionView | None:
        rows = self._select("sem_questions", question_id=question_id)
        return QuestionView(**rows[0]) if rows else None

    # ── Business queries ──

    def parts_by_field_status(
        self, field: str, statuses: list[AttributeStatus]
    ) -> list[PartFieldRow]:
        """Parts whose `field` is in one of `statuses`.

        A part with no row for the field counts as Missing (no source).
        """
        by_sku = {a.sku: a for a in self.attributes(field=field)}
        wanted = set(statuses)
        rows: list[PartFieldRow] = []
        for part in self.parts():
            attr = by_sku.get(part.sku)
            status = attr.status if attr else AttributeStatus.MISSING
            if status in wanted:
                rows.append(PartFieldRow(sku=part.sku, value=attr.value if attr else None, status=status))
        return rows

    def draft_inputs(self, sku: str) -> DraftInputs | None:
        """Inputs for rendering the next draft of a part, including profile sections."""
        part = self.part(sku)
        if part is None:
            return None
        return DraftInputs(
            part=part,
            attributes=self.attributes(sku),
            profile=render_profile_sections(self.profile()),
        )


def _checklist_view(row: dict) -> ChecklistView:
    """Parse a checklist_results row.

    checklist_json is written by the readiness engine (app/domain/readiness.py):
    {"version", "status", "items": [{"item_id", "result", "reason", "blocking"}],
     "warnings", "specialist_reasons", "actions"}; a dict of items keyed by ID is also accepted.
    """
    data = json.loads(row["checklist_json"] or "{}")
    raw_items = data.get("items", [])
    if isinstance(raw_items, dict):
        raw_items = [{"item_id": k, **v} for k, v in raw_items.items()]
    items = [
        ChecklistItemView(
            item_id=str(i.get("item_id") or i.get("id")),
            result=str(i.get("result") or i.get("status") or ("pass" if i.get("passed") else "fail")),
            reason=i.get("reason"),
            blocking=i.get("blocking"),
        )
        for i in raw_items
    ]
    return ChecklistView(
        sku=row["sku"],
        checklist_version=data.get("version") or data.get("checklist_version"),
        status=data.get("status"),
        passed=bool(row["passed"]),
        evaluated_at=row["created_at"],
        items=items,
        warnings=data.get("warnings", []),
        specialist_reasons=data.get("specialist_reasons", []),
        actions=[ActionView(**a) for a in data.get("actions", [])],
    )

