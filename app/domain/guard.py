"""CP5 draft guard — claim-evidence binding and the forbidden-claim lexicon (TRD 5, TRD 6).

Rules only, under 100 ms. For every kept claim of a draft (templated sentences
and the LLM summary's sentences):

  1. Evidence of the right type. verified_compat needs a reference_lookup;
     donor_info needs a donor note, sheet cell or seller answer; test status,
     function and authenticity need a seller answer or test record (Rule d);
     fitment needs a reference lookup (Rule c). A sentence may instead rest on
     a seller-profile field or a ledger status ("not yet stated").
  2. Fitment language ("fits", "compatible with", …) only in verified_compat
     claims (E36, Rule c).
  3. The forbidden-claim lexicon in English and Arabic: guaranteed fit, fits
     all, fully working, genuine OEM, brand new, no defects. A hit removes the
     claim unless matching evidence exists (a test record that says so);
     "guaranteed fit", "fits all" and "brand new" can never be supported (E54).

A failing claim is removed (status 'removed') and logged with the text that
triggered it. The title and item specifics get the same lexicon check.

Per Rule (g): no LLM.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any
from dataclasses import asdict, dataclass, field

from app.domain.enums import EvidenceKind

ALL_KINDS = frozenset(k.value for k in EvidenceKind)
ALLOWED_KINDS: dict[str, frozenset[str]] = {
    "fact": ALL_KINDS,
    "disclosure": ALL_KINDS,
    "donor_info": frozenset({"donor_note", "arabic_note", "spreadsheet_cell", "seller_answer"}),
    "verified_compat": frozenset({"reference_lookup"}),
}
SELLER_OR_TEST = frozenset({"seller_answer", "test_record"})
FIELD_KINDS: dict[str, frozenset[str]] = {
    "test_status": SELLER_OR_TEST,
    "functionality": SELLER_OR_TEST,
    "feature_tests": SELLER_OR_TEST,
    "authenticity": SELLER_OR_TEST,
}

# phrase → (evidence kind, raw_data key, value) that supports it; None = never supportable.
FORBIDDEN: dict[str, tuple[str, str, str] | None] = {
    "guaranteed fit": None,
    "fits all": None,
    "brand new": None,
    "fully working": ("test_record", "result", "pass"),
    "genuine oem": ("test_record", "authenticity", "verified"),
    "no defects": ("test_record", "inspection", "no_defects"),
    "مضمون": None,
    "يناسب جميع": None,
    "جديد": None,
    "يعمل بالكامل": ("test_record", "result", "pass"),
    "أصلي": ("test_record", "authenticity", "verified"),
    "بدون عيوب": ("test_record", "inspection", "no_defects"),
}
FITMENT_LANGUAGE = re.compile(
    r"\bfits?\b|\bwill fit\b|\bcompatible with\b|\bsuitable for\b|\bfor use on\b|يناسب|متوافق مع|يركب على", re.I
)


@dataclass
class GuardFinding:
    target: str  # claim | title | item_specifics
    reason: str
    text: str
    claim_id: str | None = None
    claim_key: str | None = None
    phrase: str | None = None


@dataclass
class GuardResult:
    draft_id: str
    kept: int
    removed: list[GuardFinding] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"draft_id": self.draft_id, "kept": self.kept, "elapsed_ms": self.elapsed_ms,
                "removed": [asdict(f) for f in self.removed]}


def forbidden_phrases(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [p for p in FORBIDDEN if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", lowered)]


def _supported(phrase: str, evidence: list[dict]) -> bool:
    rule = FORBIDDEN[phrase]
    if rule is None:
        return False
    kind, key, value = rule
    for ev in evidence:
        if ev["kind"] != kind:
            continue
        try:
            raw = json.loads(ev["raw_data"] or "{}")
        except json.JSONDecodeError:
            continue
        if str(raw.get(key, "")).lower() == value:
            return True
    return False


def check_claim(claim: dict, evidence_by_id: dict[str, dict]) -> tuple[str, str | None] | None:
    """Why a claim must be removed (reason, phrase), or None if it may stay."""
    text = claim["claim_value"] or ""
    ids = json.loads(claim["evidence_ids_json"] or "[]") or ([claim["evidence_id"]] if claim["evidence_id"] else [])
    sources = json.loads(claim["sources_json"] or "[]")
    evidence = [evidence_by_id[i] for i in ids if i in evidence_by_id]

    claim_type = claim["claim_type"]
    allowed = ALLOWED_KINDS.get(claim_type, frozenset())
    if claim["attribute_name"] in FIELD_KINDS:
        allowed = allowed & FIELD_KINDS[claim["attribute_name"]]
    if claim_type == "verified_compat":
        if not any(e["kind"] == "reference_lookup" for e in evidence):
            return "verified_compat needs a reference lookup (Rule c)", None
    elif not any(e["kind"] in allowed for e in evidence) and not sources:
        return f"no evidence of the right type for {claim_type}", None

    if claim_type != "verified_compat" and FITMENT_LANGUAGE.search(text):
        return "fitment claim without a verified lookup (Rule c, E36)", FITMENT_LANGUAGE.search(text).group(0)
    for phrase in forbidden_phrases(text):
        if not _supported(phrase, evidence):
            return "forbidden claim without matching evidence (E54)", phrase
    return None


def run_guard(conn: sqlite3.Connection, draft_id: str) -> GuardResult:
    """Run CP5 on a draft: remove and log failing claims, clean the title and item specifics.

    Does not commit; the caller owns the transaction.
    """
    start = time.perf_counter()
    draft = dict(conn.execute("SELECT * FROM listing_drafts WHERE id = ?", (draft_id,)).fetchone())
    part_id = draft["part_id"]
    evidence_by_id = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, kind, raw_data FROM evidence WHERE part_id = ?", (part_id,)
    ).fetchall()}
    claims = [dict(r) for r in conn.execute(
        "SELECT * FROM claims WHERE draft_id = ? AND status = 'kept' ORDER BY position", (draft_id,)
    ).fetchall()]

    result = GuardResult(draft_id=draft_id, kept=0)
    for claim in claims:
        failure = check_claim(claim, evidence_by_id)
        if failure is None:
            result.kept += 1
            continue
        reason, phrase = failure
        conn.execute(
            "UPDATE claims SET status = 'removed', removed_reason = ?, updated_at = datetime('now') WHERE id = ?",
            (reason, claim["id"]),
        )
        result.removed.append(GuardFinding("claim", reason, claim["claim_value"] or "", claim["id"],
                                           claim["claim_key"], phrase))

    title = draft["title"] or ""
    for phrase in forbidden_phrases(title) + ([FITMENT_LANGUAGE.search(title).group(0)]
                                              if FITMENT_LANGUAGE.search(title) else []):
        cleaned = re.sub(rf"\s*{re.escape(phrase)}\s*", " ", title, flags=re.I).strip()
        result.removed.append(GuardFinding("title", "forbidden or fitment wording in the title", title, phrase=phrase))
        title = cleaned
    specifics = json.loads(draft["item_specifics_json"] or "{}")
    for key, value in list(specifics.items()):
        hits = forbidden_phrases(f"{key} {value}")
        if hits or (key.lower().startswith("compatible") and "verified" not in str(value).lower()):
            result.removed.append(GuardFinding("item_specifics", "forbidden wording or unverified fitment",
                                               f"{key}: {value}", phrase=(hits or [key])[0]))
            del specifics[key]

    result.elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    conn.execute(
        "UPDATE listing_drafts SET title = ?, item_specifics_json = ?, guard_json = ? WHERE id = ?",
        (title, json.dumps(specifics, ensure_ascii=False), json.dumps(result.to_dict(), ensure_ascii=False), draft_id),
    )
    for finding in result.removed:
        conn.execute(
            """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
               VALUES (?, 'guard_removed', 'cp5', ?)""",
            (part_id, json.dumps(asdict(finding), ensure_ascii=False)),
        )
    return result
