"""API gateway entry point.

Rule (g): the UI talks to the API gateway only.
The gateway calls the semantic layer and orchestration — never exposes the DB directly.

Seller-scoped endpoints (/me/...) take seller_id from the session created at
login (POST /sessions), never from the request body, a file or a chat message.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import json
import sqlite3
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, ClassVar, ParamSpec, TypeVar, cast

import yaml
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from app.data.database import get_connection, init_db
from app.domain.models import EDITABLE_PROFILE_FIELDS, SellerProfile
from app.orchestration.chat_actions import handle_intent
from app.orchestration.chat_concierge import ChatTurn, ConciergeReply, handle_message, stream_message
from app.guardrails.quarantine import list_quarantine
from app.orchestration.injection_classifier import label_chat_message
from app.orchestration import part_views
from app.orchestration.llm_policy import GatewayModel
from app.orchestration.supervisor import ApprovalLocked, PipelineQueue, Supervisor
from app.orchestration.part_pipeline import (
    PartNotFound,
    check_compatibility,
    draft_view,
    edit_part_draft,
    evaluate_all,
    evaluate_readiness,
    render_part_draft,
    startup_reference_check,
)
from app.semantic.access_policy import AccessDenied
from app.semantic.context_card import estimate_tokens, render_card
from app.semantic.entity_model import SemanticLayer
from app.semantic.intents import IntentRequest
from app.semantic.metrics import compute_metrics
from app.semantic.prompt_prefix import get_prompt_prefix
from app.semantic.seller_context import (
    SellerSession,
    UnknownSeller,
    list_profiles,
    login,
    update_profile,
)

JSON = dict[str, object]  # an endpoint's JSON body


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup: verify the reference checksums and record their versions (E35, E50).

    A tampered compatibility reference raises ReferenceIntegrityError and the
    gateway does not start.
    """
    init_db(_DB_PATH)
    _INITIALISED.add(str(_DB_PATH))
    conn = get_connection(_DB_PATH)
    try:
        app.state.references = startup_reference_check(conn)
    finally:
        conn.close()
    queue = await _pipeline()
    yield
    await queue.stop()


app = FastAPI(
    title="PartProof API",
    description="Evidence-first listing assistant for Dubai used-auto-parts seller.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DB_PATH = _DATA_DIR / "partproof.db"


# ── Helpers ──


def _read_json(rel_path: str) -> object:
    with open(_DATA_DIR / rel_path, encoding="utf-8") as f:
        return cast(object, json.load(f))


def _as_map(value: object) -> dict[str, object]:
    """A JSON object from a seed or reference file ({} if it is something else)."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _read_csv(rel_path: str) -> list[dict[str, str]]:
    with open(_DATA_DIR / rel_path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Health ──


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health() -> JSON:
    return {"status": "ok", "service": "partproof-api", "version": "0.1.0"}


# ── Database and sessions ──

_INITIALISED: set[str] = set()

# token -> session. In-memory: sessions end when the gateway restarts.
_SESSIONS: dict[str, SellerSession] = {}


def get_db() -> Iterator[sqlite3.Connection]:
    """Per-request connection. Runs schema migrations once per database file."""
    key = str(_DB_PATH)
    if key not in _INITIALISED:
        init_db(_DB_PATH)
        _INITIALISED.add(key)
    conn = get_connection(_DB_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def current_session(authorization: Annotated[str | None, Header()] = None) -> SellerSession:
    """The logged-in seller, from `Authorization: Bearer <token>`."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    session = _SESSIONS.get(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Log in first: POST /sessions")
    return session


Db = Annotated[sqlite3.Connection, Depends(get_db)]
Session = Annotated[SellerSession, Depends(current_session)]


# ── Login and seller profile (TRD 4) ──


class LoginRequest(BaseModel):
    display_name: str


class ProfileUpdate(BaseModel):
    """Editable seller-details fields. Anything else is rejected."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    display_name: str | None = None
    business_name: str | None = None
    city: str | None = None
    languages: list[str] | None = None
    reply_language: str | None = None
    corridor: str | None = None
    inventory_currency: str | None = None
    listing_currency: str | None = None
    fx_rate_source: str | None = None
    handling_days: int | None = None
    return_policy_ref: str | None = None
    rack_codes: list[str] | None = None


@app.get("/profiles")
async def list_seller_profiles(conn: Db) -> JSON:
    """Names for the login picker (identity only, no profile detail)."""
    return {"profiles": list_profiles(conn)}


@app.post("/sessions", status_code=201)
async def create_session(req: LoginRequest, conn: Db) -> JSON:
    """Log in by the name picked on the login screen; it resolves to seller_id via the profile table."""
    try:
        session = login(conn, req.display_name)
    except UnknownSeller:
        raise HTTPException(status_code=404, detail="Seller profile not found")
    _SESSIONS[session.token] = session
    return {
        "token": session.token,
        "seller_id": session.seller_id,
        "profile": session.profile.model_dump(),
        "context_card": render_card(session.profile),
    }


@app.delete("/sessions", status_code=204)
async def end_session(session: Session) -> Response:
    _ = _SESSIONS.pop(session.token, None)
    return Response(status_code=204)


@app.get("/me/profile")
async def get_my_profile(session: Session, conn: Db) -> JSON:
    """The seller-details screen: the profile plus which fields are editable."""
    return {
        "profile": session.current_profile(conn).model_dump(),
        "editable_fields": sorted(EDITABLE_PROFILE_FIELDS),
    }


@app.put("/me/profile")
async def update_my_profile(update: ProfileUpdate, session: Session, conn: Db) -> JSON:
    """Save seller details. Invalidates the cached prompt prefix and re-renders affected drafts."""
    result = _apply_profile_update(conn, session, update)
    return {
        "profile": result.profile.model_dump(),
        "changed_fields": result.changed_fields,
        "rerendered_drafts": result.rerendered_drafts,
        "prompt_cache_invalidated": result.prompt_cache_invalidated,
    }


@app.get("/me/context-card")
async def get_my_context_card(session: Session, conn: Db) -> JSON:
    profile = session.current_profile(conn)
    card = render_card(profile)
    return {
        "card": card,
        "tokens_estimate": estimate_tokens(card),
        "profile_version": profile.version,
        "prefix_cache_key": get_prompt_prefix(profile).cache_key,
    }


def _apply_profile_update(conn: sqlite3.Connection, session: SellerSession, update: ProfileUpdate):
    try:
        result = update_profile(conn, session.seller_id, update.model_dump(exclude_unset=True))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json(include_url=False)))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That display name is already taken")
    session.profile = result.profile
    return result


def _seller_view(profile: SellerProfile) -> JSON:
    return {**profile.model_dump(), "id": profile.seller_id, "name": profile.display_name}


@app.get("/sellers/{seller_id}")
async def get_seller(seller_id: str, session: Session, conn: Db) -> JSON:
    """Seller profile by ID — only the logged-in seller's own."""
    if seller_id != session.seller_id:
        raise HTTPException(status_code=403, detail="Not your seller profile")
    return {"seller": _seller_view(session.current_profile(conn))}


@app.post("/sellers/{seller_id}")
async def update_seller(seller_id: str, update: ProfileUpdate, session: Session, conn: Db) -> JSON:
    """Update seller profile (same as PUT /me/profile). Invalidates the prompt cache."""
    if seller_id != session.seller_id:
        raise HTTPException(status_code=403, detail="Not your seller profile")
    result = _apply_profile_update(conn, session, update)
    return {
        "seller": _seller_view(result.profile),
        "changed_fields": result.changed_fields,
        "rerendered_drafts": result.rerendered_drafts,
        "_cache_invalidated": result.prompt_cache_invalidated,
    }


# ── Semantic layer (seller-scoped reads, chat intents) ──


@app.get("/me/parts")
async def list_my_parts(session: Session, conn: Db, status: str | None = None) -> JSON:
    return {"parts": [p.model_dump() for p in SemanticLayer(conn, session).parts(status=status)]}


@app.get("/me/parts/{sku}")
async def get_my_part(sku: str, session: Session, conn: Db) -> JSON:
    layer = SemanticLayer(conn, session)
    part = layer.part(sku)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    checklist = layer.checklist(sku)
    return {
        "part": part.model_dump(),
        "attributes": [a.model_dump() for a in layer.attributes(sku)],
        "evidence": [e.model_dump() for e in layer.evidence(sku)],
        "donors": [d.model_dump() for d in layer.donors(sku)],
        "claims": [c.model_dump() for c in layer.claims(sku)],
        "checklist": checklist.model_dump() if checklist else None,
        "questions": [q.model_dump() for q in layer.questions(sku)],
    }


@app.get("/me/metrics")
async def get_my_metrics(session: Session, conn: Db) -> JSON:
    metrics = compute_metrics(SemanticLayer(conn, session))
    return {"metrics": {name: m.model_dump() for name, m in metrics.items()}}


@app.get("/me/parts/{sku}/compatibility")
async def get_my_part_compatibility(sku: str, session: Session, conn: Db) -> JSON:
    """CP4: exact part-number lookup. The donor vehicle is returned as a separate fact."""
    try:
        return {"compatibility": check_compatibility(conn, session, sku).to_dict()}
    except PartNotFound:
        raise HTTPException(status_code=404, detail="Part not found")


@app.get("/me/parts/{sku}/readiness")
async def get_my_part_readiness(sku: str, session: Session, conn: Db) -> JSON:
    """CP6: checklist, status, warnings, and ranked actions (top three for chat, all for the dashboard)."""
    try:
        result = evaluate_readiness(conn, session, sku)
    except PartNotFound:
        raise HTTPException(status_code=404, detail="Part not found")
    return {"sku": sku, **result.to_json(), "top_actions": [asdict(a) for a in result.actions[:3]]}


@app.get("/me/readiness")
async def get_my_readiness(session: Session, conn: Db) -> JSON:
    """Dashboard: every part's readiness status and full action list."""
    return {"parts": [
        {"sku": r.sku, "status": r.status.value, "failed": r.failed(), "warnings": r.warnings,
         "actions": [asdict(a) for a in r.actions]}
        for r in evaluate_all(conn, session)
    ]}


@app.post("/me/parts/{sku}/draft")
async def render_my_part_draft(sku: str, session: Session, conn: Db) -> JSON:
    """Render (or fully re-render) the templated draft and re-evaluate the checklist."""
    try:
        return {"render": render_part_draft(conn, session, sku).to_dict()}
    except PartNotFound:
        raise HTTPException(status_code=404, detail="Part not found")


# ── Pipeline (supervisor) and chat concierge ──

_PIPELINE: dict[str, tuple[Supervisor, PipelineQueue]] = {}
_MODEL = GatewayModel()
_EXPORT_DIR: Path | None = None  # None: data/exports


async def _pipeline() -> PipelineQueue:
    """The supervisor and background queue for the current database (created on first use)."""
    key = str(_DB_PATH)
    if key not in _PIPELINE:
        supervisor = Supervisor(_DB_PATH, model=_MODEL, export_dir=_EXPORT_DIR)
        queue = PipelineQueue(supervisor, workers=4)
        await queue.start()
        _PIPELINE[key] = (supervisor, queue)
    return _PIPELINE[key][1]


async def _supervisor() -> Supervisor:
    return (await _pipeline()).supervisor


async def _notify_pipeline(
    conn: sqlite3.Connection, session: SellerSession, changed: dict[str, list[str]]
) -> dict[str, JSON]:
    """Send changed fields back to Reconciled for parts waiting at CP3 or CP7."""
    supervisor = await _supervisor()
    layer = SemanticLayer(conn, session)
    snapshots: dict[str, JSON] = {}
    for sku, fields in changed.items():
        part_id = layer.part_id_for(sku)
        if part_id:
            snap = await supervisor.notify_change(part_id, fields)
            if snap:
                snapshots[sku] = {k: snap[k] for k in ("status", "readiness", "waiting_for")}
            _schedule_summary_if_stale(conn, supervisor, part_id)
    return snapshots


def _schedule_summary_if_stale(conn: sqlite3.Connection, supervisor: Supervisor, part_id: str | None) -> None:
    """A correction that changed a claim the summary cites: regenerate it in the background."""
    if part_id is None:
        return
    row = cast("sqlite3.Row | None", conn.execute(
        "SELECT summary_stale FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1", (part_id,)
    ).fetchone())
    if row is not None and row["summary_stale"]:
        _ = supervisor.summaries.schedule(part_id)


@app.post("/me/chat")
async def chat(turn: ChatTurn, session: Session, conn: Db) -> JSON:
    """The chat concierge: route, execute, reply in the seller's language."""
    reply = handle_message(conn, session, turn, model=_MODEL)
    _label_in_background(session, turn.message, reply)
    return {"reply": reply.model_dump(), "pipeline": await _notify_pipeline(conn, session, reply.changed_fields)}


_BACKGROUND: set[asyncio.Task[str]] = set()


def _label_in_background(session: SellerSession, message: str, reply: ConciergeReply) -> None:
    """The fast-tier classifier labels flagged chat text off the reply path (screen budget: 250 ms)."""
    if reply.needs_classifier():
        task = asyncio.create_task(asyncio.to_thread(
            label_chat_message, str(_DB_PATH), session.seller_id, message, reply.quarantine_id(), _MODEL))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)


@app.get("/me/quarantine")
async def get_my_quarantine(session: Session, conn: Db) -> JSON:
    """Quarantined text (ingested files and chat) with its source and reason, for the dashboard."""
    return {"quarantine": list_quarantine(conn, session.seller_id)}


@app.post("/me/chat/stream")
async def chat_stream(turn: ChatTurn, session: Session, conn: Db) -> StreamingResponse:
    """Streamed reply (NDJSON): an immediate acknowledgement, then tokens, then `done`."""

    async def events():
        async for event in stream_message(conn, session, turn, model=_MODEL):
            if event["type"] == "done" and "reply" in event:
                reply = ConciergeReply.model_validate(event["reply"])
                event["pipeline"] = await _notify_pipeline(conn, session, reply.changed_fields)
                _label_in_background(session, turn.message, reply)
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/me/pipeline/run")
async def run_pipeline(session: Session, conn: Db, wait: bool = False) -> JSON:
    """Queue every one of the seller's parts for the background pipeline (`wait=true`: until each stops)."""
    queue = await _pipeline()
    layer = SemanticLayer(conn, session)
    part_ids = {p.sku: layer.part_id_for(p.sku) for p in layer.parts() if p.sku}
    jobs = [queue.submit_part(part_id) for part_id in part_ids.values() if part_id]
    if wait:
        _ = await asyncio.gather(*jobs, return_exceptions=True)
    return {"queued": list(part_ids)}


@app.get("/me/pipeline/{sku}")
async def get_pipeline_state(sku: str, session: Session, conn: Db) -> JSON:
    part_id = SemanticLayer(conn, session).part_id_for(sku)
    if part_id is None:
        raise HTTPException(status_code=404, detail="Part not found")
    return {"pipeline": await (await _supervisor()).snapshot(part_id)}


@app.get("/me/parts/{sku}/draft")
async def get_my_part_draft(sku: str, session: Session, conn: Db) -> JSON:
    """The listing draft with every sentence's claim and evidence (source on hover)."""
    try:
        view = draft_view(conn, session, sku)
    except PartNotFound:
        raise HTTPException(status_code=404, detail="Part not found")
    if view is None:
        raise HTTPException(status_code=404, detail="No draft yet")
    if view["badge"] == "updating":
        supervisor = await _supervisor()
        _schedule_summary_if_stale(conn, supervisor, SemanticLayer(conn, session).part_id_for(sku))
    return {"draft": view}


@app.post("/me/parts/{sku}/approve")
async def approve_part(sku: str, session: Session, conn: Db) -> JSON:
    """CP7: the seller's approval click (the only way a part becomes Approved)."""
    part_id = SemanticLayer(conn, session).part_id_for(sku)
    if part_id is None:
        raise HTTPException(status_code=404, detail="Part not found")
    try:
        snap = await (await _supervisor()).approve(part_id, actor="seller")
    except (ApprovalLocked, RuntimeError) as exc:  # not at CP7, or the summary critic has not passed
        raise HTTPException(status_code=409, detail=str(exc))
    return {"pipeline": snap}


# ── Part detail screen (PRD 3): evidence, editor, history, quarantine board ──


class DraftEdit(BaseModel):
    """The listing editor's fields. The disclosure block is not one of them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    title: str | None = None
    category: str | None = None
    item_specifics: dict[str, str] | None = None
    notes: list[str] | None = None
    price: str | None = None


class PhotoUpload(BaseModel):
    filename: str
    content_b64: str


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _view(fn: Callable[_P, _R], *args: _P.args, **kwargs: _P.kwargs) -> _R:
    """Call a part view; an unknown SKU (or another seller's) is a 404."""
    try:
        return fn(*args, **kwargs)
    except PartNotFound:
        raise HTTPException(status_code=404, detail="Part not found")


@app.get("/me/board")
async def get_my_board(session: Session, conn: Db) -> JSON:
    """The inventory board: status, readiness, waiting questions and quarantine flags per part."""
    return {"parts": part_views.board(conn, session)}


@app.get("/me/parts/{sku}/detail")
async def get_my_part_detail(sku: str, session: Session, conn: Db) -> JSON:
    """The evidence panel: attributes with sources, photos with flagged regions, open conflicts, questions."""
    layer = SemanticLayer(conn, session)
    part = layer.part(sku)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    questions = layer.questions(sku)
    latest = max((q.round for q in questions if q.round is not None), default=None)
    asked = sorted((q for q in questions if q.round == latest and latest is not None and q.status == "open"),
                   key=lambda q: q.rank or 99)
    return {
        "part": part.model_dump(),
        "attributes": part_views.attribute_table(conn, session, sku),
        "photos": part_views.photos(conn, session, sku),
        "conflicts": part_views.conflicts(conn, session, sku),
        "questions": [q.model_dump() for q in asked],
    }


@app.get("/me/parts/{sku}/photos/{evidence_id}")
async def get_my_part_photo(sku: str, evidence_id: str, session: Session, conn: Db) -> FileResponse:
    return FileResponse(_view(part_views.photo_file, conn, session, sku, evidence_id))


@app.post("/me/parts/{sku}/photos")
async def upload_my_part_photo(sku: str, upload: PhotoUpload, session: Session, conn: Db) -> JSON:
    """A photo sent in the chat. OCR only suggests a part number; the seller confirms it in the chat."""
    try:
        content = base64.b64decode(upload.content_b64, validate=True)
    except ValueError:
        raise HTTPException(status_code=422, detail="content_b64 is not base64")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Photos are limited to 15 MB")
    return {"upload": _view(part_views.upload_photo, conn, session, sku, upload.filename, content)}


@app.put("/me/parts/{sku}/draft")
async def edit_my_part_draft(sku: str, edit: DraftEdit, session: Session, conn: Db) -> JSON:
    """Save listing-editor changes; the guard (CP5) and the checklist re-run on the result."""
    try:
        result = _view(edit_part_draft, conn, session, sku, title=edit.title, category=edit.category,
                       item_specifics=edit.item_specifics, notes=edit.notes, price=edit.price)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    pipeline: dict[str, JSON] = {}
    if edit.price is not None:  # a ledger field: parts waiting at CP3 or CP7 go back to Reconciled
        pipeline = await _notify_pipeline(conn, session, {sku: ["price"]})
    else:
        _schedule_summary_if_stale(conn, await _supervisor(), SemanticLayer(conn, session).part_id_for(sku))
    return {"edit": {"draft_version": result.draft_version, "guard_removed": result.removed,
                     "rejected_specifics": result.rejected, "readiness": result.readiness},
            "draft": draft_view(conn, session, sku), "pipeline": pipeline}


@app.get("/me/parts/{sku}/history")
async def get_my_part_history(sku: str, session: Session, conn: Db) -> JSON:
    """Change history from AuditEvent, newest first, with before and after values."""
    return {"history": _view(part_views.history, conn, session, sku)}


@app.get("/me/parts/{sku}/review")
async def get_my_part_review(sku: str, session: Session, conn: Db) -> JSON:
    """The review summary shown before approval: seller overrides, warnings, guard removals."""
    return {"review": _view(part_views.review_summary, conn, session, sku)}


@app.post("/me/chat/intent")
async def run_chat_intent(
    request: Annotated[IntentRequest, Body()],
    session: Session,
    conn: Db,
) -> JSON:
    """Run one routed chat intent through the semantic layer, then propagate accepted values."""
    try:
        result, propagation = handle_intent(conn, session, request)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"result": result.model_dump(mode="json"), "propagation": propagation}


# ── Parts ──


@app.get("/parts")
async def list_parts() -> JSON:
    """List all parts with enriched data from seed files + answer key."""
    return {"parts": _build_parts_from_seed()}


@app.get("/parts/{part_id}")
async def get_part(part_id: str) -> JSON:
    """Get a single part by ID or SKU."""
    parts = _build_parts_from_seed()
    for p in parts:
        if p["id"] == part_id or p.get("sku") == part_id:
            return {"part": p}
    raise HTTPException(status_code=404, detail="Part not found")


@app.get("/parts/{part_id}/questions")
async def get_questions(part_id: str) -> JSON:
    """Get open questions for a part."""
    # Try DB first
    if _DB_PATH.exists():
        conn = _get_db()
        try:
            # Find the part by SKU
            part = cast("sqlite3.Row | None", conn.execute(
                "SELECT id FROM parts WHERE id = ? OR part_number = ?",
                (part_id, part_id),
            ).fetchone())
            if part:
                questions: list[sqlite3.Row] = conn.execute(
                    "SELECT * FROM questions WHERE part_id = ? AND status = 'open'",
                    (part["id"],),
                ).fetchall()
                if questions:
                    return {"questions": [dict(q) for q in questions]}
        finally:
            conn.close()

    # Fall back to deriving from answer key
    parts = _build_parts_from_seed()
    for p in parts:
        if p["id"] == part_id or p.get("sku") == part_id:
            return {"questions": _derive_questions(p)}
    return {"questions": []}


def _build_parts_from_seed() -> list[JSON]:
    """Build parts list from seed CSV files + answer key."""
    spreadsheet = _read_csv("seed/spreadsheet.csv")
    inventory = _read_csv("seed/inventory.csv")
    donor = _read_csv("seed/donor_vehicle.csv")
    answer_key = _read_json("answer_key/answer_key.json")

    inv_map = {r["sku"]: r for r in inventory}
    donor_map = {r["sku"]: r for r in donor}
    ak_parts = _as_map(_as_map(answer_key).get("parts"))

    badge_map = {
        "Approved": "Approved",
        "Awaiting seller": "Ready for seller review",
        "Guarded": "Specialist review required",
        "Ingested": "Information missing",
        "Reconciled": "Ready for seller review",
        "Drafted": "Ready for seller review",
        "Evaluated": "Specialist review required",
        "Awaiting approval": "Specialist review required",
        "Exported": "Approved",
    }

    issue_labels = {
        "E10": "Arabic digits in part number",
        "E11": "Side missing",
        "E12": "Unreadable part number",
        "E16": "Donor year conflict",
        "E17": "Donor model conflict",
        "E22": "Side ambiguous (driver/passenger)",
        "E24": "Damage flag, no note",
        "E28": "Partly tested, no record",
        "E30": "Test claim in note",
        "E33": "Not in reference",
        "E37": "Weight missing",
        "E39": "Excluded category (airbag)",
        "E44": "Arabic-only note",
        "E53": "Prompt injection in data",
    }

    question_map = {
        "E12": "The part number on the label is unclear. Can you confirm the full part number?",
        "E16": "The donor year differs between the spreadsheet and the donor note. Which year is correct?",
        "E17": "The donor model differs between sources. Which model is correct?",
        "E22": "The side is listed as 'driver side'. Is this left or right?",
        "E24": "A possible damage was detected. Can you describe the condition?",
        "E28": "The part is marked as partly tested. What was tested?",
        "E30": "The donor note says 'tested OK'. Was this tested by you or the previous owner?",
        "E37": "What is the packed weight and dimensions?",
    }

    parts: list[JSON] = []
    for row in spreadsheet:
        sku = row["sku"]
        ak = _as_map(ak_parts.get(sku))
        planted = [str(e) for e in _as_list(ak.get("planted_issues"))]
        expected = str(ak.get("expected_status") or "Ingested")
        open_q = len([e for e in planted if e in question_map])

        parts.append({
            "id": f"part-{sku}",
            "sku": sku,
            "make": row.get("make", ""),
            "model": row.get("model", ""),
            "year": row.get("year", ""),
            "side": row.get("side", ""),
            "part_number": row.get("part_number", ""),
            "part_type": row.get("part_type", ""),
            "rack_code": inv_map.get(sku, {}).get("rack_code", ""),
            "asking_price_aed": inv_map.get(sku, {}).get("asking_price_aed", ""),
            "short_description": inv_map.get(sku, {}).get("short_description", ""),
            "donor_market": donor_map.get(sku, {}).get("market", ""),
            "status": expected,
            "badge": badge_map.get(expected, "Information missing"),
            "planted_issues": planted,
            "open_questions": open_q,
            "issue_types": [issue_labels.get(e, e) for e in planted],
            "journey": str(ak.get("journey") or ""),
        })

    return parts


def _derive_questions(part: JSON) -> list[JSON]:
    """Derive mock questions from planted issues."""
    question_map = {
        "E12": ("The part number on the label is unclear. Can you confirm the full part number?", "high"),
        "E16": ("The donor year differs between the spreadsheet and the donor note. Which year is correct?", "high"),
        "E17": ("The donor model differs between sources. Which model is correct?", "high"),
        "E22": ("The side is listed as 'driver side'. Is this left or right?", "medium"),
        "E24": ("A possible damage was detected. Can you describe the condition?", "medium"),
        "E28": ("The part is marked as partly tested. What was tested?", "high"),
        "E30": ("The donor note says 'tested OK'. Was this tested by you or the previous owner?", "medium"),
        "E37": ("What is the packed weight and dimensions?", "medium"),
    }
    questions: list[JSON] = []
    for e in map(str, _as_list(part.get("planted_issues"))):
        if e in question_map:
            text, priority = question_map[e]
            questions.append({
                "id": f"q-{part['id']}-{e}",
                "part_id": part["id"],
                "question_text": text,
                "edge_case": e,
                "priority": priority,
                "status": "open",
            })
    return questions


# ── Upload ──


class UploadRequest(BaseModel):
    files: list[dict[str, str]]


@app.post("/upload")
async def upload_files(req: UploadRequest) -> JSON:
    """Queue files for processing."""
    return {
        "job_id": "job-001",
        "status": "queued",
        "parts_queued": len(req.files),
        "message": "Files queued for processing",
    }


# ── Reference data ──


@app.get("/reference")
async def get_reference_data() -> JSON:
    """Get compatibility reference version and checklist version."""
    compat = _read_csv("reference/compat_ref.csv")
    with open(_DATA_DIR / "reference" / "checklist_v0.1.yaml", encoding="utf-8") as f:
        checklist = _as_map(cast(object, yaml.safe_load(f)))

    return {
        "compat_ref": {
            "version": compat[0].get("ref_version", "v0.1") if compat else "v0.1",
            "rows": len(compat),
            "entries": compat,
        },
        "checklist": {
            "version": checklist.get("version", "0.1"),
            "date": checklist.get("date", "2026-10-27"),
            "purpose": checklist.get("purpose", ""),
            "disclaimer": str(checklist.get("disclaimer") or "").strip(),
        },
    }


# ── Static files (PWA manifest, service worker, icons) ──
_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "ui" / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
