"""Supervisor — a LangGraph state machine per part, plus a background queue (TRD 5).

Each part is one graph thread (thread_id = part_id) moving through fixed states:

  Ingested → Extracted → Reconciled ⇄ Awaiting seller (CP3)
           → Drafted → Guarded → Evaluated → Awaiting approval (CP7) → Approved → Exported

Code decides what runs next: every edge is a Python routing function over
deterministic results (readiness, the guard check). No agent chooses a node,
and only `_set_status` in this module writes `parts.status`, so no agent can
skip a checkpoint or set a status (Rules a and b).

Human-in-the-loop: CP3 (seller confirmation) and CP7 (approval) are LangGraph
interrupts. A part waiting at one does not hold up the others: each part is
its own thread, and the PipelineQueue runs parts concurrently on asyncio
workers. A correction resumes the waiting part with only the affected fields,
which go back to Reconciled; nothing else re-runs.

Per Rule (g), only this tier calls the model layer (via the intake agent and
the Inquiry agent, with E46 timeouts and template fallbacks).
"""

from __future__ import annotations

import asyncio
import json
import logging
import operator
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.data.database import get_connection
from app.domain.compatibility import check_part
from app.domain.cp2 import run_cp2
from app.domain.enums import PartStatus
from app.domain.guard import run_guard
from app.domain.propagation_engine import ensure_fresh_draft, render_draft
from app.domain.readiness import ReadinessStatus, evaluate_part
from app.orchestration.inquiry_agent import run_inquiry
from app.orchestration.listing_agent import SummaryScheduler
from app.orchestration.llm_policy import ModelClient
from app.semantic.intake_agent import IntakePartInput, ingest_part, save_intake_to_db

logger = logging.getLogger(__name__)

SEED_DIR = Path("data/seed")
APPROVER = "seller"  # CP7: only the seller's click approves


class PartState(TypedDict, total=False):
    part_id: str
    sku: str
    status: str
    readiness: str | None
    affected_fields: list[str]
    round: int
    checkpoint: dict | None  # what the next CP3/CP7 interrupt shows the seller
    approved_by: str | None
    approval_rejected: bool
    export_path: str | None
    history: Annotated[list[dict], operator.add]


# ── Helpers (the only status writer) ────────────────────────


def _conn(config: RunnableConfig) -> sqlite3.Connection:
    return get_connection(config["configurable"]["db_path"])


def _set_status(conn: sqlite3.Connection, part_id: str, status: PartStatus, reason: str) -> dict:
    before = conn.execute("SELECT status FROM parts WHERE id = ?", (part_id,)).fetchone()["status"]
    conn.execute("UPDATE parts SET status = ?, updated_at = datetime('now') WHERE id = ?", (status.value, part_id))
    conn.execute(
        """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
           VALUES (?, 'status_change', 'supervisor', ?)""",
        (part_id, json.dumps({"before": before, "after": status.value, "reason": reason})),
    )
    conn.commit()
    return {"from": before, "to": status.value, "reason": reason,
            "at": datetime.now(timezone.utc).isoformat()}


def _evaluate(conn: sqlite3.Connection, part_id: str):
    result = evaluate_part(conn, part_id, check_part(conn, part_id))
    conn.commit()
    return result


# ── Nodes ───────────────────────────────────────────────────


async def extract(state: PartState, config: RunnableConfig) -> PartState:
    """Intake agent (background, heavy). E46: a failed extraction leaves fields Missing."""
    conn = _conn(config)
    try:
        part_id, sku = state["part_id"], state["sku"]
        done = conn.execute(
            "SELECT 1 FROM audit_events WHERE part_id = ? AND event_type = 'extract'", (part_id,)
        ).fetchone()
        reason = "already extracted"
        if not done:
            try:
                result = await ingest_part(IntakePartInput(
                    sku=sku,
                    photo_paths=[str(SEED_DIR / "photos" / f"{sku}_p{i}.jpg") for i in (1, 2, 3)],
                    spreadsheet_path=str(SEED_DIR / "spreadsheet.csv"),
                    note_path=str(SEED_DIR / "donor_notes" / f"{sku}.txt"),
                    part_id=part_id,
                ))
                if result.cp1_result is None:
                    raise RuntimeError(result.error or "intake returned no result")
                save_intake_to_db(conn, result)
                reason = "intake agent + CP1"
            except Exception as exc:  # E46: never block the pipeline on the model
                logger.info("intake failed for %s: %s", sku, exc)
                conn.execute(
                    """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
                       VALUES (?, 'extract_failed', 'supervisor', ?)""",
                    (part_id, json.dumps({"error": str(exc)[:300], "effect": "fields stay Missing"})),
                )
                conn.commit()
                reason = "extraction unavailable (E46); fields stay Missing"
        return {"history": [_set_status(conn, part_id, PartStatus.EXTRACTED, reason)]}
    finally:
        conn.close()


async def reconcile(state: PartState, config: RunnableConfig) -> PartState:
    """CP2 (full on first pass; affected fields only after a correction), then Inquiry questions."""
    conn = _conn(config)
    try:
        part_id = state["part_id"]
        affected = state.get("affected_fields") or []
        if not affected:
            run_cp2(conn, part_id)  # first pass; corrections already re-ran CP2 for their fields (E43)
        model = config["configurable"].get("model")
        inquiry = run_inquiry(conn, part_id, model=model)
        reason = f"affected fields: {', '.join(affected)}" if affected else "full reconciliation"
        return {
            "readiness": inquiry.status,
            "round": inquiry.round,
            "affected_fields": [],
            "history": [_set_status(conn, part_id, PartStatus.RECONCILED, reason)],
        }
    finally:
        conn.close()


async def awaiting_seller(state: PartState, config: RunnableConfig) -> PartState:
    """Enter CP3: record the status and what the seller will be asked."""
    conn = _conn(config)
    try:
        part_id = state["part_id"]
        change = _set_status(conn, part_id, PartStatus.AWAITING_SELLER, "seller confirmation (CP3)")
        questions = [dict(r) for r in conn.execute(
            """SELECT id, rank, action_key, question_text, text_ar, guide_en, guide_ar FROM questions
               WHERE part_id = ? AND status = 'open' AND rank IS NOT NULL ORDER BY rank""",
            (part_id,),
        ).fetchall()]
    finally:
        conn.close()
    return {"history": [change], "checkpoint": {
        "checkpoint": "CP3",
        "sku": state["sku"],
        "readiness": state.get("readiness"),
        "specialist_review": state.get("readiness") == ReadinessStatus.SPECIALIST_REVIEW.value,
        "questions": questions,
    }}


async def cp3(state: PartState, config: RunnableConfig) -> PartState:
    """CP3 interrupt. Resume with {"fields": [...]} after the seller answers or corrects."""
    payload = interrupt(state["checkpoint"]) or {}
    return {"affected_fields": list(payload.get("fields") or []) or ["seller_answer"], "checkpoint": None}


async def draft(state: PartState, config: RunnableConfig) -> PartState:
    """Listing agent: templated draft from the ledger now; the summary paragraph in the background."""
    conn = _conn(config)
    try:
        render_draft(conn, state["part_id"], actor="supervisor")
        _schedule_summary(conn, state["part_id"], config)
        return {"history": [_set_status(conn, state["part_id"], PartStatus.DRAFTED, "templated draft rendered")]}
    finally:
        conn.close()


def _schedule_summary(conn: sqlite3.Connection, part_id: str, config: RunnableConfig) -> None:
    summaries = config["configurable"].get("summaries")
    row = conn.execute(
        "SELECT summary_stale FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1", (part_id,)
    ).fetchone()
    if summaries is not None and row is not None and row["summary_stale"]:
        summaries.schedule(part_id)


async def guard(state: PartState, config: RunnableConfig) -> PartState:
    """CP5 rules on the draft: claim-evidence binding, forbidden claims, personal data."""
    conn = _conn(config)
    try:
        draft_row, _ = ensure_fresh_draft(conn, state["part_id"])  # E49: a stale draft is re-rendered first
        if draft_row is not None:
            run_guard(conn, draft_row["id"])
            conn.commit()
            _schedule_summary(conn, state["part_id"], config)
        result = _evaluate(conn, state["part_id"])
        failed = {i.item_id for i in result.items if i.result == "fail"} & {"C13", "C15"}
        if failed:
            return {"readiness": result.status.value, "history": [
                {"guard_failed": sorted(failed), "at": datetime.now(timezone.utc).isoformat()}]}
        return {"readiness": result.status.value,
                "history": [_set_status(conn, state["part_id"], PartStatus.GUARDED, "CP5 guard passed")]}
    finally:
        conn.close()


async def evaluate(state: PartState, config: RunnableConfig) -> PartState:
    """CP6 readiness."""
    conn = _conn(config)
    try:
        result = _evaluate(conn, state["part_id"])
        return {"readiness": result.status.value,
                "history": [_set_status(conn, state["part_id"], PartStatus.EVALUATED, result.status.value)]}
    finally:
        conn.close()


async def awaiting_approval(state: PartState, config: RunnableConfig) -> PartState:
    """Enter CP7: record the status and the draft awaiting approval."""
    conn = _conn(config)
    try:
        change = _set_status(conn, state["part_id"], PartStatus.AWAITING_APPROVAL, "final approval (CP7)")
        draft_row = conn.execute(
            "SELECT id, version, title FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1",
            (state["part_id"],),
        ).fetchone()
    finally:
        conn.close()
    return {"history": [change], "checkpoint": {
        "checkpoint": "CP7", "sku": state["sku"], "draft": dict(draft_row) if draft_row else None,
    }}


async def cp7(state: PartState, config: RunnableConfig) -> PartState:
    """CP7 interrupt. Resume with {"approve": true, "actor": "seller"} (the seller's click)
    or, for a late correction, {"fields": [...]}."""
    payload = interrupt(state["checkpoint"]) or {}
    now = datetime.now(timezone.utc).isoformat()
    if payload.get("fields"):
        return {"affected_fields": list(payload["fields"]), "approved_by": None, "checkpoint": None}
    if not payload.get("approve") or payload.get("actor") != APPROVER:
        return {"approved_by": None, "approval_rejected": True, "history": [
            {"approval_rejected": f"approval must come from the {APPROVER}, got {payload.get('actor')!r}", "at": now}]}
    conn = _conn(config)
    try:  # the approval gate re-checks readiness, including C14 (summary critic)
        result = _evaluate(conn, state["part_id"])
    finally:
        conn.close()
    if not result.ready or result.approval_pending:
        return {"approved_by": None, "approval_rejected": False, "readiness": result.status.value, "history": [
            {"approval_blocked": result.status.value, "pending": result.approval_pending, "at": now}]}
    return {"approved_by": APPROVER, "approval_rejected": False, "checkpoint": None}


async def approve(state: PartState, config: RunnableConfig) -> PartState:
    conn = _conn(config)
    try:
        conn.execute("UPDATE listing_drafts SET status = 'approved' WHERE part_id = ?", (state["part_id"],))
        conn.commit()
        return {"history": [_set_status(conn, state["part_id"], PartStatus.APPROVED, "approved by the seller")]}
    finally:
        conn.close()


async def export(state: PartState, config: RunnableConfig) -> PartState:
    """Mock eBay export of the approved draft (JSON)."""
    conn = _conn(config)
    try:
        row = dict(conn.execute(
            "SELECT * FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1", (state["part_id"],)
        ).fetchone())
        export_dir = Path(config["configurable"].get("export_dir") or "data/exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        path = export_dir / f"{state['sku']}.json"
        path.write_text(json.dumps({
            "marketplace": "eBay UK (mock)",
            "sku": state["sku"],
            "title": row["title"],
            "description": row["description"],
            "item_specifics": json.loads(row["item_specifics_json"] or "{}"),
            "compatibility": json.loads(row["compatibility_json"] or "[]"),
            "price": row["price"],
            "currency": row["currency"],
            "draft_version": row["version"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        conn.execute("UPDATE listing_drafts SET status = 'exported' WHERE id = ?", (row["id"],))
        conn.commit()
        return {"export_path": str(path),
                "history": [_set_status(conn, state["part_id"], PartStatus.EXPORTED, "mock export written")]}
    finally:
        conn.close()


# ── Routing: code decides what runs next ────────────────────


def _after_reconcile(state: PartState) -> str:
    return "draft" if state.get("readiness") == ReadinessStatus.READY.value else "awaiting_seller"


def _after_guard(state: PartState) -> str:
    return "evaluate" if state["history"][-1].get("to") == PartStatus.GUARDED.value else "awaiting_seller"


def _after_evaluate(state: PartState) -> str:
    return "awaiting_approval" if state.get("readiness") == ReadinessStatus.READY.value else "awaiting_seller"


def _after_cp7(state: PartState) -> str:
    if state.get("approved_by") == APPROVER:
        return "approve"
    if state.get("affected_fields"):
        return "reconcile"
    if state.get("approval_rejected"):
        return "cp7"  # not the seller's click: keep waiting, status unchanged
    return "awaiting_seller"  # approval gate failed: back to the seller


def build_graph() -> StateGraph:
    g = StateGraph(PartState)
    for name, fn in (("extract", extract), ("reconcile", reconcile),
                     ("awaiting_seller", awaiting_seller), ("cp3", cp3),
                     ("draft", draft), ("guard", guard), ("evaluate", evaluate),
                     ("awaiting_approval", awaiting_approval), ("cp7", cp7),
                     ("approve", approve), ("export", export)):
        g.add_node(name, fn)
    g.add_edge(START, "extract")
    g.add_edge("extract", "reconcile")
    g.add_conditional_edges("reconcile", _after_reconcile, ["draft", "awaiting_seller"])
    g.add_edge("awaiting_seller", "cp3")
    g.add_edge("cp3", "reconcile")
    g.add_edge("draft", "guard")
    g.add_conditional_edges("guard", _after_guard, ["evaluate", "awaiting_seller"])
    g.add_conditional_edges("evaluate", _after_evaluate, ["awaiting_approval", "awaiting_seller"])
    g.add_edge("awaiting_approval", "cp7")
    g.add_conditional_edges("cp7", _after_cp7, ["approve", "reconcile", "cp7", "awaiting_seller"])
    g.add_edge("approve", "export")
    g.add_edge("export", END)
    return g


# ── Supervisor API ──────────────────────────────────────────


class ApprovalLocked(RuntimeError):
    """CP7 approval attempted before the summary critic (CP5b) passed."""


class Supervisor:
    """Runs and resumes part graphs. One graph thread per part."""

    def __init__(self, db_path: str | Path, *, model: ModelClient | None = None, export_dir: str | Path | None = None):
        self.db_path = str(db_path)
        self.model = model
        self.export_dir = str(export_dir) if export_dir else None
        self.summaries = SummaryScheduler(db_path, writer=model, critic=model)
        self.graph = build_graph().compile(checkpointer=InMemorySaver())

    def _config(self, part_id: str) -> dict:
        return {"configurable": {"thread_id": part_id, "db_path": self.db_path, "model": self.model,
                                 "export_dir": self.export_dir, "summaries": self.summaries}}

    async def start(self, part_id: str) -> dict[str, Any]:
        """Run a part from Ingested until it reaches a checkpoint or the end."""
        conn = get_connection(self.db_path)
        try:
            sku = conn.execute("SELECT sku FROM parts WHERE id = ?", (part_id,)).fetchone()["sku"]
            change = _set_status(conn, part_id, PartStatus.INGESTED, "pipeline started")
        finally:
            conn.close()
        await self.graph.ainvoke({"part_id": part_id, "sku": sku, "history": [change]}, self._config(part_id))
        return await self.snapshot(part_id)

    async def snapshot(self, part_id: str) -> dict[str, Any]:
        state = await self.graph.aget_state(self._config(part_id))
        interrupts = [i.value for t in state.tasks for i in t.interrupts]
        values = dict(state.values)
        conn = get_connection(self.db_path)
        try:
            status = conn.execute("SELECT status FROM parts WHERE id = ?", (part_id,)).fetchone()["status"]
        finally:
            conn.close()
        return {
            "part_id": part_id,
            "sku": values.get("sku"),
            "status": status,
            "readiness": values.get("readiness"),
            "waiting_for": interrupts[0]["checkpoint"] if interrupts else None,
            "interrupt": interrupts[0] if interrupts else None,
            "next": list(state.next),
            "history": values.get("history", []),
            "export_path": values.get("export_path"),
        }

    async def waiting_for(self, part_id: str) -> str | None:
        return (await self.snapshot(part_id))["waiting_for"]

    async def resume(self, part_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.graph.ainvoke(Command(resume=payload), self._config(part_id))
        return await self.snapshot(part_id)

    async def notify_change(self, part_id: str, fields: list[str]) -> dict[str, Any] | None:
        """A seller answer or correction changed `fields`: send only those back to Reconciled."""
        if await self.waiting_for(part_id) in ("CP3", "CP7"):
            return await self.resume(part_id, {"fields": fields})
        return None

    async def approve(self, part_id: str, *, actor: str) -> dict[str, Any]:
        """CP7. Only the seller's click approves; anything else is recorded and ignored.

        The Approve button stays locked until the summary critic (CP5b) has passed.
        """
        if await self.waiting_for(part_id) != "CP7":
            raise RuntimeError("part is not awaiting approval")
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT summary_status, summary_stale FROM listing_drafts WHERE part_id = ? "
                "ORDER BY created_at DESC LIMIT 1", (part_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or row["summary_stale"] or row["summary_status"] not in ("critic_passed", "template"):
            raise ApprovalLocked("the summary paragraph is updating; approval unlocks when the critic passes it")
        return await self.resume(part_id, {"approve": True, "actor": actor})


# ── Background queue ────────────────────────────────────────


class PipelineQueue:
    """asyncio workers for heavy pipeline work; parts move independently."""

    def __init__(self, supervisor: Supervisor, *, workers: int = 4):
        self.supervisor = supervisor
        self.workers = workers
        self._queue: asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future]] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._worker()) for _ in range(self.workers)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def submit(self, job: Callable[[], Awaitable[Any]]) -> asyncio.Future[Any]:
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((job, future))
        return future

    def submit_part(self, part_id: str) -> asyncio.Future[dict[str, Any]]:
        return self.submit(lambda: self.supervisor.start(part_id))

    async def join(self) -> None:
        await self._queue.join()

    async def _worker(self) -> None:
        while True:
            job, future = await self._queue.get()
            try:
                result = await job()
                if not future.done():
                    future.set_result(result)
            except Exception as exc:  # one part's failure never stops the others
                logger.exception("pipeline job failed")
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._queue.task_done()
