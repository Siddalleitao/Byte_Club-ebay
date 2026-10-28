"""Read views for the part detail screen (PRD 3, J1 to J5), plus the chat photo upload.

Everything here reads the ledger, evidence, quarantine and audit tables for one
seller's part; nothing sets a status, a compatibility result or a checklist item.
The photo upload stores evidence and suggests a part number from OCR, but the
part number changes only when the seller confirms it (Rule e).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from app.domain.enums import TrustTier
from app.guardrails.quarantine import list_quarantine, record_quarantine
from app.guardrails.screen import screen_text
from app.orchestration.cp0_pipeline import process_uploads, save_evidence_to_db
from app.orchestration.part_pipeline import PartNotFound, _part_id
from app.semantic.intake_tools import ocr
from app.semantic.seller_context import SellerSession

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

# Ledger field -> spreadsheet column, for showing the cell behind a value.
_SHEET_COLUMN = {"donor_year": "year", "position": "side"}


def _latest_attributes(conn: sqlite3.Connection, part_id: str) -> dict[str, dict[str, Any]]:
    return {r["name"]: dict(r) for r in conn.execute("SELECT * FROM attributes WHERE part_id = ?", (part_id,))}


def _resolve_under_data(pointer: str | None) -> Path | None:
    """A stored file path, only if it points at a file inside data/ or the upload folder (never elsewhere)."""
    if not pointer or pointer.startswith(("photo:", "note:", "sheet:", "ocr:")):
        return None
    path = Path(pointer.replace("\\", "/"))
    path = (path if path.is_absolute() else DATA_DIR.parent / path).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in (DATA_DIR, UPLOAD_DIR)):
        return None
    return path if path.is_file() else None


# ── Photos with flagged regions ─────────────────────────────


def photos(conn: sqlite3.Connection, session: SellerSession, sku: str) -> list[dict[str, Any]]:
    """The part's photos (seeded and uploaded) with their regions; flagged regions say why."""
    part_id = _part_id(conn, session, sku)
    attrs = _latest_attributes(conn, part_id)
    part_number = (attrs.get("part_number") or {}).get("value") or ""
    label_unreadable = "?" in part_number or (attrs.get("label_readable") or {}).get("value") == "False"
    out = []
    rows = conn.execute(
        "SELECT * FROM evidence WHERE part_id = ? AND kind = 'photo_region' ORDER BY created_at, rowid", (part_id,))
    for row in rows:
        path = _resolve_under_data(row["source_ref"])
        if path is None:
            continue
        raw = json.loads(row["raw_data"] or "{}")
        regions = []
        for region in raw.get("regions", []):
            flag = None
            if region["kind"] == "defect":
                flag = f"possible damage: {region['label']}"
            elif region["kind"] == "label" and label_unreadable:
                flag = f"label unreadable: the part number reads {part_number or 'nothing'} (E12)"
            regions.append({**region, "flagged": flag is not None, "reason": flag})
        out.append({
            "evidence_id": row["id"],
            "name": path.name,
            "pointer": row["source_ref"],
            "views": raw.get("views", []),
            "regions": regions,
            "uploaded": path.is_relative_to(UPLOAD_DIR.resolve()),
            "created_at": row["created_at"],
        })
    return out


def photo_file(conn: sqlite3.Connection, session: SellerSession, sku: str, evidence_id: str) -> Path:
    """The file behind one of this seller's photo evidence rows."""
    part_id = _part_id(conn, session, sku)
    row = conn.execute("SELECT source_ref FROM evidence WHERE id = ? AND part_id = ? AND kind = 'photo_region'",
                       (evidence_id, part_id)).fetchone()
    path = _resolve_under_data(row["source_ref"]) if row else None
    if path is None:
        raise PartNotFound(evidence_id)
    return path


# ── Attributes with their sources ───────────────────────────


def attribute_table(conn: sqlite3.Connection, session: SellerSession, sku: str) -> list[dict[str, Any]]:
    """Every ledger field with its status and the evidence it came from."""
    part_id = _part_id(conn, session, sku)
    sources: dict[str, list[dict]] = {}

    def add(name: str, r) -> None:
        entry = {"evidence_id": r["id"], "kind": r["kind"], "pointer": r["source_ref"], "text": (r["content"] or "")[:200]}
        if entry not in sources.setdefault(name, []):
            sources[name].append(entry)

    for r in conn.execute(
        """SELECT a.name, e.id, e.kind, e.source_ref, e.content, e.raw_data FROM attribute_evidence ae
           JOIN attributes a ON a.id = ae.attribute_id JOIN evidence e ON e.id = ae.evidence_id
           WHERE a.part_id = ? ORDER BY ae.created_at, ae.rowid""", (part_id,)):
        raw = json.loads(r["raw_data"] or "{}") if (r["raw_data"] or "").startswith("{") else {}
        if raw.get("conflict") and raw.get("field") != r["name"]:
            continue  # a conflict record about another field
        add(r["name"], r)
    rows = conn.execute("SELECT * FROM attributes WHERE part_id = ? ORDER BY rowid", (part_id,)).fetchall()
    for a in rows:  # the spreadsheet cell behind a value, when the cell says the same thing
        cell = conn.execute("SELECT id, kind, source_ref, content FROM evidence WHERE part_id = ? AND source_ref LIKE ?",
                            (part_id, f"%spreadsheet.csv:{sku}:{_SHEET_COLUMN.get(a['name'], a['name'])}")).fetchone()
        if cell and a["value"] and (cell["content"] or "").strip().lower() == str(a["value"]).strip().lower():
            add(a["name"], cell)
    return [{"field": r["name"], "value": r["value"], "status": r["status"], "sources": sources.get(r["name"], [])}
            for r in rows]


# ── Conflicts: both sources side by side ────────────────────


def _excerpt(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle and needle in line:
            return line.strip()
    return text.strip().splitlines()[0] if text.strip() else ""


def _conflict_side(conn: sqlite3.Connection, part_id: str, sku: str, field: str, value, source: str,
                   photo_list: list[dict]) -> dict:
    """One side of a conflict, with the cell, note line or photo region it came from."""
    value_text = "" if value is None else str(value)
    side = {"value": value_text, "source": source, "pointer": None, "excerpt": None, "photo": None}
    if source.startswith("spreadsheet"):
        column = _SHEET_COLUMN.get(field, field)
        cell = conn.execute("SELECT source_ref, content FROM evidence WHERE part_id = ? AND source_ref LIKE ?",
                            (part_id, f"%spreadsheet.csv:{sku}:{column}")).fetchone()
        if cell:
            side.update(pointer=cell["source_ref"], excerpt=cell["content"])
    elif source.startswith(("donor_note", "note")):
        note = conn.execute(
            "SELECT source_ref, content FROM evidence WHERE part_id = ? AND kind IN ('donor_note', 'arabic_note') "
            "ORDER BY rowid LIMIT 1", (part_id,)).fetchone()
        if note:
            side.update(pointer=note["source_ref"], excerpt=_excerpt(note["content"] or "", value_text))
    elif source.startswith(("photo", "ocr")):
        for photo in photo_list:
            label = next((r for r in photo["regions"] if r["kind"] == "label"), None)
            if label:
                side.update(pointer=photo["pointer"], photo={"evidence_id": photo["evidence_id"], "box": label["box"]})
                break
    return side


def conflicts(conn: sqlite3.Connection, session: SellerSession, sku: str) -> list[dict[str, Any]]:
    """CP1 conflicts that are still open, each with both sources and their excerpts or crops."""
    part_id = _part_id(conn, session, sku)
    attrs = _latest_attributes(conn, part_id)
    photo_list = photos(conn, session, sku)
    grouped: dict[str, list[dict]] = {}
    for row in conn.execute("SELECT raw_data FROM evidence WHERE part_id = ? AND raw_data LIKE '%\"conflict\": true%' "
                            "ORDER BY rowid", (part_id,)):
        raw = json.loads(row["raw_data"])
        grouped.setdefault(raw["field"], []).append(raw)
    out = []
    for field, sides in grouped.items():
        status = (attrs.get(field) or {}).get("status")
        out.append({
            "field": field,
            "status": status,
            "open": status == "Conflicting",
            "resolved_value": None if status == "Conflicting" else (attrs.get(field) or {}).get("value"),
            "sides": [_conflict_side(conn, part_id, sku, field, s["value"], s["source"], photo_list) for s in sides],
        })
    return out


# ── Change history (AuditEvent) ─────────────────────────────


def _describe(event: str, d: dict) -> tuple[str | None, object, object, str]:
    """(field, before, after, one-line summary) for one audit event."""
    if event == "status_change":
        return "status", d.get("before"), d.get("after"), d.get("reason") or ""
    if event == "seller_change":
        review = " (marked for review)" if d.get("marks_for_review") else ""
        return d.get("attribute"), d.get("old_value"), d.get("new_value"), f"seller changed {d.get('attribute')}{review}"
    if event == "propagate":
        if d.get("trigger"):
            return None, None, None, f"draft {d['trigger']} (v{d.get('draft_version')})"
        outputs = ", ".join(d.get("recomputed", []))
        return d.get("field"), d.get("old_value"), d.get("new_value"), f"updated {outputs or 'nothing else'}"
    if event == "compat_lookup":
        return "compatibility", d.get("previous_status"), d.get("status"), f"lookup {d.get('part_number')}: {d.get('reason')}"
    if event == "seller_edit":
        def view(side: dict) -> dict:
            notes = json.loads(side.get("seller_edits_json") or "{}").get("notes", [])
            return {"title": side.get("title"), "category": side.get("category"),
                    "item specifics": side.get("item_specifics_json"), "notes": [n["text"] for n in notes]}

        before, after = view(d.get("before", {})), view(d.get("after", {}))
        changed = [k for k in after if before[k] != after[k]]
        removed = f"; the guard removed {len(d['guard_removed'])}" if d.get("guard_removed") else ""
        return ", ".join(changed) or "listing", {k: before[k] for k in changed}, {k: after[k] for k in changed}, \
            f"seller edited the listing{removed}"
    if event == "guard_removed":
        return d.get("target"), d.get("text"), None, f"guard removed: {d.get('reason')}"
    if event == "summary":
        return "summary", None, d.get("status"), "summary paragraph " + str(d.get("status", "written"))
    if event == "inquiry":
        return None, None, None, f"round {d.get('round')} questions: {', '.join(d.get('asked', []))}"
    if event == "reconcile":
        return None, None, d.get("part_status"), f"reconciled; edge cases {', '.join(d.get('edge_cases', []))}"
    if event == "injection_quarantine":
        return None, None, None, f"quarantined {d.get('source')}: {d.get('reason')}"
    if event == "refusal":
        return None, None, None, f"declined: {d.get('reason') or d.get('intent') or ''}".strip()
    if event == "photo_upload":
        return None, None, d.get("file"), f"photo uploaded: {d.get('original_name')}"
    return None, None, None, event.replace("_", " ")


def history(conn: sqlite3.Connection, session: SellerSession, sku: str, limit: int = 200) -> list[dict[str, Any]]:
    """The part's audit trail, newest first, with before and after values."""
    part_id = _part_id(conn, session, sku)
    rows = conn.execute("SELECT * FROM audit_events WHERE part_id = ? ORDER BY timestamp DESC, rowid DESC LIMIT ?",
                        (part_id, limit)).fetchall()
    out = []
    for r in rows:
        detail = json.loads(r["detail_json"] or "{}")
        field, before, after, summary = _describe(r["event_type"], detail)
        out.append({"timestamp": r["timestamp"], "event": r["event_type"], "actor": r["actor"], "field": field,
                    "before": before, "after": after, "summary": summary})
    return out


# ── Review summary (CP7) ────────────────────────────────────


def review_summary(conn: sqlite3.Connection, session: SellerSession, sku: str) -> dict[str, Any]:
    """What the seller is approving: their overrides, warnings, specialist reasons and guard removals."""
    part_id = _part_id(conn, session, sku)
    overrides = []
    for r in conn.execute("SELECT timestamp, detail_json FROM audit_events WHERE part_id = ? AND event_type = "
                          "'seller_change' ORDER BY rowid", (part_id,)):
        d = json.loads(r["detail_json"])
        if d.get("old_value") not in (None, "") and str(d.get("old_value")) != str(d.get("new_value")):
            overrides.append({"field": d["attribute"], "before": d["old_value"], "after": d["new_value"],
                              "marked_for_review": bool(d.get("marks_for_review")), "timestamp": r["timestamp"]})
    edits = conn.execute("SELECT COUNT(*) c FROM audit_events WHERE part_id = ? AND event_type = 'seller_edit'",
                         (part_id,)).fetchone()["c"]
    latest = conn.execute("SELECT checklist_json FROM checklist_results WHERE part_id = ? "
                          "ORDER BY created_at DESC, rowid DESC LIMIT 1", (part_id,)).fetchone()
    checklist = json.loads(latest["checklist_json"]) if latest else {}
    draft = conn.execute("SELECT id FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1",
                         (part_id,)).fetchone()
    removed = [] if draft is None else [
        {"text": r["claim_value"], "reason": r["removed_reason"]}
        for r in conn.execute("SELECT claim_value, removed_reason FROM claims WHERE draft_id = ? AND status = 'removed'",
                              (draft["id"],))]
    return {
        "overrides": overrides,
        "marked_for_review": [o for o in overrides if o["marked_for_review"]],
        "editor_changes": edits,
        "warnings": checklist.get("warnings", []),
        "specialist_reasons": checklist.get("specialist_reasons", []),
        "guard_removed": removed,
    }


# ── Inventory board ─────────────────────────────────────────


def board(conn: sqlite3.Connection, session: SellerSession) -> list[dict[str, Any]]:
    """One row per part: status, readiness, questions waiting, quarantine flags, compatibility."""
    quarantined: dict[str, int] = {}
    for record in list_quarantine(conn, session.seller_id):
        if record.get("sku"):
            quarantined[record["sku"]] = quarantined.get(record["sku"], 0) + 1
    rows = []
    for p in conn.execute("SELECT * FROM parts WHERE seller_id = ? ORDER BY sku", (session.seller_id,)).fetchall():
        latest = conn.execute("SELECT checklist_json FROM checklist_results WHERE part_id = ? "
                              "ORDER BY created_at DESC, rowid DESC LIMIT 1", (p["id"],)).fetchone()
        compat = conn.execute("SELECT detail_json FROM audit_events WHERE part_id = ? AND event_type = 'compat_lookup' "
                              "ORDER BY rowid DESC LIMIT 1", (p["id"],)).fetchone()
        draft = conn.execute("SELECT title FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1",
                             (p["id"],)).fetchone()
        waiting = conn.execute(  # the latest Inquiry round's open questions (what the chat shows)
            "SELECT COUNT(*) c FROM questions WHERE part_id = ? AND status = 'open' AND round = "
            "(SELECT MAX(round) FROM questions WHERE part_id = ?)", (p["id"], p["id"])).fetchone()["c"]
        rows.append({
            "sku": p["sku"],
            "part_type": p["part_type"],
            "title": draft["title"] if draft else p["title"],
            "status": p["status"],
            "readiness": json.loads(latest["checklist_json"]).get("status") if latest else None,
            "questions_waiting": waiting,
            "quarantined": quarantined.get(p["sku"], 0),
            "compatibility": json.loads(compat["detail_json"])["status"] if compat else None,
        })
    return rows


# ── Photo upload in the chat ────────────────────────────────


def upload_photo(conn: sqlite3.Connection, session: SellerSession, sku: str, filename: str,
                 content: bytes) -> dict[str, Any]:
    """A photo sent in the chat (e.g. a sharper label): CP0 gate, quarantine screen, OCR suggestion.

    The file is stored under a generated name; the original name is screened as data.
    OCR may only suggest a part number. The ledger changes when the seller confirms it.
    """
    part_id = _part_id(conn, session, sku)
    suffix = Path(filename).suffix.lower()
    if suffix not in PHOTO_TYPES:
        return {"accepted": False, "message": f"Only photos can be added here ({', '.join(sorted(PHOTO_TYPES))})."}
    name_screen = screen_text(filename, source=f"file_name:{filename}")
    if name_screen.quarantine:
        record_quarantine(conn, name_screen, channel="ingest", tier=TrustTier.T2, seller_id=session.seller_id,
                          part_id=part_id)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOAD_DIR / f"{sku}_{uuid.uuid4().hex[:8]}{suffix}"
    stored.write_bytes(content)
    result = process_uploads(photo_paths=[str(stored)], photo_output_dir=UPLOAD_DIR / "processed")
    if result.rejected_files:
        stored.unlink(missing_ok=True)
        return {"accepted": False, "message": f"The photo was not accepted: {result.rejected_files[0]['reason']}"}
    save_evidence_to_db(conn, result, {sku: part_id}, seller_id=session.seller_id)
    evidence = conn.execute("SELECT id, source_ref FROM evidence WHERE part_id = ? AND kind = 'photo_region' "
                            "ORDER BY rowid DESC LIMIT 1", (part_id,)).fetchone()
    reading = ocr(stored)
    unclear = re.search(r"[\w-]*\d[\w-]*\?+", reading.raw_text or "")  # E12: kept with its '?', never guessed
    number = reading.oem_number or (unclear.group(0) if unclear else None)
    retake = bool(result.retake_requested_photos)
    if number and "?" not in number:
        message = (f"Photo added. The label reads {number}. If that is right, reply "
                   f"\"Change {sku} part number to {number}\" and I will update the listing.")
    elif number:
        message = (f"Photo added. The label still reads {number}: some characters are unclear, so I have not "
                   f"changed the part number. Type the full number from the label, for example "
                   f"\"Change {sku} part number to …\".")
    else:
        message = "Photo added. I could not read a part number on it; type the number from the label if you can."
    if retake:
        message += " The photo looks blurry; a sharper one in good light would help."
    conn.execute(
        """INSERT INTO audit_events (part_id, event_type, actor, detail_json) VALUES (?, 'photo_upload', 'seller', ?)""",
        (part_id, json.dumps({"original_name": filename, "file": stored.name, "evidence_id": evidence["id"] if evidence
                              else None, "ocr_text": reading.raw_text, "name_quarantined": name_screen.quarantine},
                             ensure_ascii=False)),
    )
    conn.commit()
    return {"accepted": True, "evidence_id": evidence["id"] if evidence else None, "stored_as": stored.name,
            "ocr_text": reading.raw_text, "suggested_part_number": number if number and "?" not in number else None,
            "blurry": retake, "name_quarantined": name_screen.quarantine, "message": message}
