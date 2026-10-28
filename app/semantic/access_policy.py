"""Chat access policy and the chat tool layer (TRD 4, TRD 6 defence layer 5).

The chat may read all of the seller's own records. It may write only:
  - seller_answer evidence, and
  - question status (with the answer text and its evidence link).

It cannot write part status, compatibility references, checklist versions or
results, attributes, drafts, the seller profile, or exports. Those are code
paths behind the UI (Rule b).

This is enforced in code, not in the prompt: `ChatTools` is the only object
the chat concierge receives, its write methods check the policy, and every
call by name goes through an allowlist. A denied call raises AccessDenied
and is written to the audit log with what was attempted.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.domain.enums import AttributeStatus, EvidenceKind
from app.semantic import metrics
from app.semantic.entity_model import (
    AttributeView,
    ChecklistView,
    ClaimView,
    DonorView,
    EvidenceView,
    PartFieldRow,
    PartView,
    QuestionView,
    SemanticLayer,
)
from app.semantic.glossary import Glossary, get_glossary
from app.semantic.seller_context import SellerSession

MAX_ANSWER_CHARS = 2000


class AccessDenied(PermissionError):
    """A chat tool call the access policy does not allow."""


class ChatAccessPolicy:
    """What the chat may read and write. Least privilege."""

    READ_TOOLS: frozenset[str] = frozenset({
        "list_parts",
        "get_part",
        "get_attributes",
        "get_evidence",
        "get_donors",
        "get_claims",
        "get_checklist",
        "get_checklist_history",
        "get_questions",
        "get_question",
        "parts_by_field_status",
        "get_metrics",
        "get_profile",
    })
    WRITE_TOOLS: frozenset[str] = frozenset({"write_seller_answer", "set_question_status"})
    WRITABLE_EVIDENCE_KINDS: frozenset[str] = frozenset({EvidenceKind.SELLER_ANSWER.value})
    QUESTION_STATUSES: frozenset[str] = frozenset({"open", "answered", "skipped"})

    # Writes that exist elsewhere in the system and are explicitly not the chat's.
    FORBIDDEN_WRITES: dict[str, str] = {
        "set_part_status": "part status is computed by deterministic rules",
        "write_compat_ref": "the compatibility reference is read-only reference data",
        "set_checklist_version": "the checklist version is system configuration",
        "write_checklist_result": "readiness is computed by the checklist engine",
        "export_listing": "export needs the seller's approval in the review screen",
        "approve_listing": "approval is the seller's click in the review screen",
        "write_attribute": "attributes change only through the evidence ledger",
        "write_draft": "drafts are rendered by the listing service",
        "update_profile": "the profile is edited on the seller-details screen",
    }

    @classmethod
    def allows(cls, tool: str) -> bool:
        return tool in cls.READ_TOOLS or tool in cls.WRITE_TOOLS

    @classmethod
    def reason_denied(cls, tool: str) -> str:
        return cls.FORBIDDEN_WRITES.get(tool, "not an allowed chat tool")


class ChatTools:
    """The chat's only handle on PartProof data. Seller-scoped by its session."""

    def __init__(self, conn: sqlite3.Connection, session: SellerSession, glossary: Glossary | None = None):
        self._conn = conn
        self._session = session
        self._layer = SemanticLayer(conn, session)
        self.glossary = glossary or get_glossary()

    @property
    def seller_id(self) -> str:
        return self._session.seller_id

    # ── Dispatch ──

    def call(self, tool: str, **kwargs: Any) -> Any:
        """Call a tool by name, as the chat concierge does. Unknown or forbidden → AccessDenied."""
        if not ChatAccessPolicy.allows(tool):
            self._deny(tool, ChatAccessPolicy.reason_denied(tool), kwargs)
        return getattr(self, tool)(**kwargs)

    # ── Reads (all seller-scoped by the semantic layer) ──

    def list_parts(self, *, status: str | None = None) -> list[PartView]:
        return self._layer.parts(status=status)

    def get_part(self, sku: str) -> PartView | None:
        return self._layer.part(sku)

    def get_attributes(self, sku: str, *, field: str | None = None) -> list[AttributeView]:
        return self._layer.attributes(sku, field=field)

    def get_evidence(self, sku: str, *, kind: str | None = None) -> list[EvidenceView]:
        return self._layer.evidence(sku, kind=kind)

    def get_donors(self, sku: str) -> list[DonorView]:
        return self._layer.donors(sku)

    def get_claims(self, sku: str) -> list[ClaimView]:
        return self._layer.claims(sku)

    def get_checklist(self, sku: str) -> ChecklistView | None:
        return self._layer.checklist(sku)

    def get_checklist_history(self) -> list[ChecklistView]:
        return self._layer.checklist_history()

    def get_questions(self, sku: str | None = None, *, status: str | None = None) -> list[QuestionView]:
        return self._layer.questions(sku, status=status)

    def get_question(self, question_id: str) -> QuestionView | None:
        return self._layer.question(question_id)

    def parts_by_field_status(self, field: str, statuses: list[AttributeStatus]) -> list[PartFieldRow]:
        return self._layer.parts_by_field_status(field, statuses)

    def get_metrics(self) -> dict[str, metrics.MetricValue]:
        return metrics.compute_metrics(self._layer)

    def get_profile(self):
        return self._layer.profile()

    # ── Writes (the only two the chat has) ──

    def write_seller_answer(
        self,
        sku: str,
        text: str,
        *,
        field: str | None,
        question_id: str | None = None,
        normalised_value: str | None = None,
        accepted: bool,
        reason: str | None = None,
        lang: str | None = None,
        kind: str = EvidenceKind.SELLER_ANSWER.value,
    ) -> str:
        """Store the seller's own words as seller_answer evidence. Returns evidence_id.

        The raw text is stored as data (Rule f); the normalised value and
        whether it was accepted go in raw_data for the ledger and audit.
        """
        if kind not in ChatAccessPolicy.WRITABLE_EVIDENCE_KINDS:
            self._deny("write_evidence", f"chat may not write {kind!r} evidence", {"sku": sku, "kind": kind})
        part_id = self._layer.part_id_for(sku)
        if part_id is None:
            self._deny("write_seller_answer", "part is not one of this seller's records", {"sku": sku})

        evidence_id = str(uuid.uuid4())
        source = f"chat:{self.seller_id}:{question_id or 'correction'}"
        self._conn.execute(
            """INSERT INTO evidence (id, part_id, kind, source_ref, content, confidence, raw_data)
               VALUES (?, ?, ?, ?, ?, NULL, ?)""",
            (
                evidence_id,
                part_id,
                EvidenceKind.SELLER_ANSWER.value,
                source,
                text[:MAX_ANSWER_CHARS],
                json.dumps({
                    "field": field,
                    "question_id": question_id,
                    "normalised_value": normalised_value,
                    "accepted": accepted,
                    "reason": reason,
                    "lang": lang,
                }, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return evidence_id

    def set_question_status(
        self,
        question_id: str,
        status: str,
        *,
        answer_text: str | None = None,
        answer_evidence_id: str | None = None,
    ) -> QuestionView:
        if status not in ChatAccessPolicy.QUESTION_STATUSES:
            self._deny("set_question_status", f"{status!r} is not a question status", {"question_id": question_id})
        question = self._layer.question(question_id)
        if question is None:
            self._deny("set_question_status", "question is not one of this seller's records", {"question_id": question_id})
        if answer_evidence_id is not None:
            linked = [e for e in self._layer.evidence(question.sku, kind=EvidenceKind.SELLER_ANSWER.value)
                      if e.evidence_id == answer_evidence_id]
            if not linked:
                self._deny("set_question_status", "answer evidence must be this part's seller_answer",
                           {"question_id": question_id})

        answered_at = datetime.now(timezone.utc).isoformat() if status == "answered" else None
        self._conn.execute(
            """UPDATE questions SET status = ?, answer_text = COALESCE(?, answer_text),
                   answer_evidence_id = COALESCE(?, answer_evidence_id), answered_at = ?
               WHERE id = ?""",
            (status, answer_text, answer_evidence_id, answered_at, question_id),
        )
        self._conn.commit()
        return self._layer.question(question_id)

    # ── Audit ──

    def log_refusal(self, topic: str, text: str, *, sku: str | None = None) -> None:
        """Log a policy refusal with the text that triggered it (TRD 6, defence layer 10)."""
        self._audit("refusal", {"topic": topic, "text": text[:MAX_ANSWER_CHARS], "sku": sku})

    def _deny(self, tool: str, reason: str, args: dict[str, Any]) -> None:
        self._audit("access_denied", {"tool": tool, "reason": reason, "args": sorted(args)})
        raise AccessDenied(f"Chat may not call {tool!r}: {reason}")

    def _audit(self, event_type: str, detail: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
               VALUES (NULL, ?, 'chat', ?)""",
            (event_type, json.dumps({"seller_id": self.seller_id, **detail}, ensure_ascii=False)),
        )
        self._conn.commit()
