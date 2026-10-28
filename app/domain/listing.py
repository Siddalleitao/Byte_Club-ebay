"""Listing service — draft claims, CP5, description, staleness (FR-10 to FR-12, TRD 3).

After any re-render of a draft's templated outputs, `refresh_draft`:

  1. rebuilds the claim set (one claim per description sentence) with stable
     ids keyed by `claim_key`, so the summary's citations survive re-renders;
  2. runs CP5 (`guard.run_guard`), which removes and logs failing claims;
  3. assembles the description from the kept claims plus the summary paragraph;
  4. records `built_from_versions` (attribute versions, profile version,
     reference version) so a later mismatch marks the draft stale (E49);
  5. marks the LLM summary stale when any claim it cites changed, was retired
     or was removed. The old summary stays visible (an "updating" badge)
     until the background job regenerates it.

Per Rule (g): no LLM. The summary paragraph itself is written by the Listing
agent in orchestration, from the read-only claim set this module produces.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field

from app.data.reference_data import get_compat_reference
from app.domain.claims import SECTION_TITLES, SECTIONS, ClaimSpec, build_claims
from app.domain.compatibility import CompatResult
from app.domain.glossary import get_glossary
from app.domain.guard import GuardResult, run_guard
from app.domain.ledger import EvidenceLedger
from app.domain.listing_profile import ProfileSections, load_seller_profile
from app.domain.part_facts import PartFacts

SUMMARY_OK = ("critic_passed", "template")

# Item specifics that come from the ledger; the seller changes them through a correction, not the editor.
PROTECTED_SPECIFICS = frozenset({"Type", "Condition", "Placement on Vehicle", "Manufacturer Part Number",
                                 "Compatibility"})


def seller_edits(draft: dict) -> dict:
    return json.loads(draft.get("seller_edits_json") or "{}")


def _note_claims(edits: dict) -> list[ClaimSpec]:
    """The seller's own sentences: claims like any other, backed by the editor evidence row."""
    return [ClaimSpec(f"seller.note.{i}", "part_details", "fact", note["text"], None, [note["evidence_id"]])
            for i, note in enumerate(edits.get("notes", []))]


@dataclass
class DraftRefresh:
    changed_claims: set[str] = field(default_factory=set)
    retired_claims: set[str] = field(default_factory=set)
    guard: GuardResult | None = None
    summary_invalidated: bool = False


# ── Claims ──────────────────────────────────────────────────


def sync_claims(conn: sqlite3.Connection, draft_id: str, part_id: str, specs: list[ClaimSpec]) -> tuple[set, set]:
    """Upsert templated claims by key. Returns (changed claim ids, retired claim ids)."""
    existing = {r["claim_key"]: dict(r) for r in conn.execute(
        "SELECT * FROM claims WHERE draft_id = ? AND section != 'summary'", (draft_id,)
    ).fetchall()}
    changed: set[str] = set()
    for position, spec in enumerate(specs):
        evidence_json = json.dumps(spec.evidence_ids)
        sources_json = json.dumps(spec.sources)
        first = spec.evidence_ids[0] if spec.evidence_ids else None
        row = existing.pop(spec.key, None)
        if row is None:
            claim_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO claims (id, part_id, attribute_name, claim_type, claim_value, evidence_id, draft_id,
                       claim_key, section, position, evidence_ids_json, sources_json, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'kept', datetime('now'))""",
                (claim_id, part_id, spec.field, spec.claim_type, spec.text, first, draft_id, spec.key,
                 spec.section, position, evidence_json, sources_json),
            )
            changed.add(claim_id)
            continue
        same = (row["claim_value"], row["claim_type"], row["evidence_ids_json"], row["sources_json"]) == (
            spec.text, spec.claim_type, evidence_json, sources_json)
        if not same:
            changed.add(row["id"])
        conn.execute(
            """UPDATE claims SET attribute_name = ?, claim_type = ?, claim_value = ?, evidence_id = ?, section = ?,
                   position = ?, evidence_ids_json = ?, sources_json = ?, status = 'kept', removed_reason = NULL,
                   version = version + ?, updated_at = datetime('now')
               WHERE id = ?""",
            (spec.field, spec.claim_type, spec.text, first, spec.section, position, evidence_json, sources_json,
             0 if same else 1, row["id"]),
        )
    retired = {row["id"] for row in existing.values() if row["status"] != "retired"}
    for claim_id in retired:
        conn.execute("UPDATE claims SET status = 'retired', updated_at = datetime('now') WHERE id = ?", (claim_id,))
    return changed, retired


def kept_claims(conn: sqlite3.Connection, draft_id: str, *, section: str | None = None) -> list[dict]:
    sql = "SELECT * FROM claims WHERE draft_id = ? AND status = 'kept'"
    params: list = [draft_id]
    if section:
        sql += " AND section = ?"
        params.append(section)
    return [dict(r) for r in conn.execute(sql + " ORDER BY position", params).fetchall()]


def summary_text(conn: sqlite3.Connection, draft_id: str) -> str | None:
    sentences = [c["claim_value"] for c in kept_claims(conn, draft_id, section="summary")]
    return " ".join(sentences) if sentences else None


def assemble_description(conn: sqlite3.Connection, draft_id: str, summary: str | None) -> str:
    """The description: the summary paragraph, then each templated section's kept sentences."""
    claims = kept_claims(conn, draft_id)
    blocks = [summary] if summary else []
    for section in SECTIONS[1:]:
        sentences = [c["claim_value"] for c in claims if c["section"] == section]
        if sentences:
            blocks.append(f"{SECTION_TITLES[section]}: " + " ".join(sentences))
    return "\n\n".join(blocks)


# ── Versions (E49) ──────────────────────────────────────────


def build_snapshot(conn: sqlite3.Connection, part_id: str, profile_version: int | None, ref_version: str) -> dict:
    return {
        "attributes": EvidenceLedger(conn, part_id).get_version_snapshot(),
        "profile_version": profile_version,
        "ref_version": ref_version,
    }


def draft_staleness(conn: sqlite3.Connection, draft: dict) -> list[str]:
    """Why a draft no longer matches the ledger, the seller profile or the reference ([] = fresh)."""
    if not draft.get("built_from_versions"):
        return ["no version snapshot"]
    built = json.loads(draft["built_from_versions"])
    reasons = []
    attributes = built.get("attributes", built)  # older drafts stored the attribute map flat
    current = EvidenceLedger(conn, draft["part_id"]).get_version_snapshot()
    changed = sorted(n for n in set(attributes) | set(current) if attributes.get(n) != current.get(n))
    if changed:
        reasons.append(f"attributes changed: {', '.join(changed)}")
    part = conn.execute("SELECT seller_id FROM parts WHERE id = ?", (draft["part_id"],)).fetchone()
    profile = load_seller_profile(conn, part["seller_id"])
    if "profile_version" in built and profile and built["profile_version"] != profile.version:
        reasons.append("seller profile changed")
    if "ref_version" in built and built["ref_version"] != get_compat_reference().version:
        reasons.append("compatibility reference changed")
    return reasons


# ── Refresh ─────────────────────────────────────────────────


def refresh_draft(
    conn: sqlite3.Connection,
    draft: dict,
    facts: PartFacts,
    compat: CompatResult,
    sections: ProfileSections | None,
    *,
    force_summary_stale: bool = False,
) -> DraftRefresh:
    """Claims → CP5 → description → snapshot → summary staleness, for one draft. Does not commit."""
    draft_id = draft["id"]
    edits = seller_edits(draft)
    specs = build_claims(conn, facts, compat, sections) + _note_claims(edits)
    changed, retired = sync_claims(conn, draft_id, facts.part_id, specs)
    guard = run_guard(conn, draft_id)
    removed = {f.claim_id for f in guard.removed if f.claim_id}

    cites = set(json.loads(draft.get("summary_cites_json") or "[]"))
    invalidated = bool(cites & (changed | retired | removed)) or force_summary_stale
    current_summary = summary_text(conn, draft_id) or draft.get("summary")
    status = draft.get("summary_status")
    if invalidated or not current_summary or status not in SUMMARY_OK:
        summary_stale, status = 1, "pending"
    else:
        summary_stale = 0

    claims = kept_claims(conn, draft_id)
    disclosure = " ".join(c["claim_value"] for c in claims if c["section"] == "condition")
    donor = " ".join(c["claim_value"] for c in claims if c["section"] == "donor")
    g = get_glossary()
    part_type = g.normalise("part_type", facts.part_type).value
    category = edits.get("category") or g.vocabularies["part_type"].meta.get(part_type or "", {}).get(
        "category_suggestion")
    snapshot = build_snapshot(conn, facts.part_id, sections.profile_version if sections else None, compat.ref_version)
    conn.execute(
        """UPDATE listing_drafts SET description = ?, disclosure_block = ?, donor_block = ?, category = ?,
               built_from_versions = ?, stale = 0, summary = ?, summary_stale = ?, summary_status = ?,
               updated_at = datetime('now')
           WHERE id = ?""",
        (assemble_description(conn, draft_id, current_summary), disclosure, donor, category,
         json.dumps(snapshot), current_summary, summary_stale, status, draft_id),
    )
    return DraftRefresh(changed, retired, guard, invalidated)
