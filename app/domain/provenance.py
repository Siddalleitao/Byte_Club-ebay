"""Provenance rules — how evidence sources interact (TRD 3, 7).

Enforces:
  E25: Seller override of a visual flag → create close-up photo question,
       mark override for review summary
  E30: "Tested OK" from a note → labelled seller-reported, held (not Verified)
  Fitment exists only as verified_compat or absent (Rule c)
  built_from_versions stored on every draft for stale detection (E49)

Per Rule (g): domain services never call an LLM. This module is pure logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import AttributeStatus, ClaimType, EvidenceKind
from app.domain.ledger import EvidenceLedger, SELLER_OR_TEST_FIELDS


# ── Provenance result ───────────────────────────────────────


@dataclass
class ProvenanceResult:
    """Result of applying provenance rules to a seller change."""

    attribute_name: str
    value: Any
    status: AttributeStatus
    source: str
    reason: str
    creates_question: bool = False
    question_text: str = ""
    question_edge_case: str = ""
    marks_for_review: bool = False
    review_note: str = ""


# ── Visual flags that a seller might contradict ─────────────

VISUAL_DAMAGE_FLAGS: set[str] = {
    "crack", "cracked", "broken", "scratch", "scratched", "scuff",
    "scuffed", "dent", "dented", "chip", "chipped", "damage", "damaged",
    "tab", "mounting", "hole", "shatter", "shattered",
}

NO_DAMAGE_PHRASES: set[str] = {
    "no damage", "no cracks", "no scratches", "no dents", "no chips",
    "mint condition", "perfect condition", "no issues", "undamaged",
    "pristine", "flawless", "like new",
}

# E30: Phrases that claim test status without a test record
TEST_CLAIM_PHRASES: list[str] = [
    "tested ok", "tested and working", "tested good", "fully functional",
    "fully tested", "tested working", "in working order",
    "functions properly", "tested and functional",
]

# Arabic test claim phrases
TEST_CLAIM_PHRASES_AR: list[str] = [
    "تم الاختبار",  # "tested"
    "يعمل بشكل جيد",  # "works well"
    "تم اختباره",  # "was tested"
]


# ── Provenance rules ────────────────────────────────────────


def apply_seller_override(
    attribute_name: str,
    new_value: Any,
    existing_value: Any,
    existing_status: AttributeStatus,
    *,
    is_visual_flag: bool = False,
) -> ProvenanceResult:
    """Apply a seller override to an existing attribute.

    E25: If a seller contradicts a visual flag (e.g. "no damage" vs
    a suspected crack), create a close-up photo question and mark the
    override for the review summary.

    A seller confirmation may override extracted data but is logged.
    """
    result = ProvenanceResult(
        attribute_name=attribute_name,
        value=new_value,
        status=AttributeStatus.SELLER_CONFIRMED,
        source="seller_answer",
        reason=f"Seller confirmed '{new_value}' (was '{existing_value}'/{existing_status.value})",
    )

    # E25: Check if the seller contradicts a visual flag
    if is_visual_flag:
        old_lower = str(existing_value).lower()
        new_lower = str(new_value).lower()

        # Check if existing has a damage flag and seller says no damage
        has_damage_flag = any(kw in old_lower for kw in VISUAL_DAMAGE_FLAGS)
        seller_says_no_damage = any(phrase in new_lower for phrase in NO_DAMAGE_PHRASES)

        if has_damage_flag and seller_says_no_damage:
            result.creates_question = True
            result.question_text = (
                f"A close-up photo is needed for {attribute_name}: "
                f"seller reports no damage but a visual flag was detected ({existing_value})."
            )
            result.question_edge_case = "E25"
            result.marks_for_review = True
            result.review_note = (
                f"E25: Seller override contradicts visual flag on {attribute_name}. "
                f"Visual: '{existing_value}', Seller: '{new_value}'. "
                f"Close-up photo required."
            )

    return result


def check_test_claim(text: str, attribute_name: str) -> ProvenanceResult | None:
    """Check if text contains a test status claim (E30).

    Per Rule (d): test status, functionality, and authenticity come only
    from a seller answer or a test record. A note saying "tested OK"
    is labelled seller-reported and held (not Verified).

    Returns a ProvenanceResult if a test claim is found, None otherwise.
    """
    text_lower = text.lower().strip()

    # Check English phrases
    for phrase in TEST_CLAIM_PHRASES:
        if phrase in text_lower:
            return ProvenanceResult(
                attribute_name=attribute_name,
                value=text.strip(),
                status=AttributeStatus.EXTRACTED,  # Held as Extracted, not Verified
                source="donor_note",
                reason=f"E30: Test claim '{phrase}' found in note — "
                       "labelled as seller-reported, held for confirmation",
                marks_for_review=True,
                review_note=(
                    f"E30: Note contains test claim '{phrase}'. "
                    "This is seller-reported, not verified by a test record. "
                    "Attribute is held as Extracted, not Verified."
                ),
            )

    # Check Arabic phrases
    for phrase in TEST_CLAIM_PHRASES_AR:
        if phrase in text:
            return ProvenanceResult(
                attribute_name=attribute_name,
                value=text.strip(),
                status=AttributeStatus.EXTRACTED,
                source="donor_note",
                reason=f"E30: Arabic test claim found in note — "
                       "labelled as seller-reported, held for confirmation",
                marks_for_review=True,
                review_note=(
                    f"E30: Note contains Arabic test claim. "
                    "This is seller-reported, not verified by a test record."
                ),
            )

    return None


def check_fitment(
    part_number: str | None,
    compat_refs: list[dict],
) -> tuple[AttributeStatus, str]:
    """Check fitment/compatibility status.

    Per Rule (c): fitment exists only as verified_compat or absent.
    The donor vehicle is never fitment.

    Returns (status, source):
      - (Verified, "reference_lookup") if part_number has an exact match
      - (Missing, None) if part_number is None or no match
    """
    if not part_number:
        return AttributeStatus.MISSING, ""

    # Check for unreadable part number (contains ?)
    if "?" in part_number:
        return AttributeStatus.MISSING, "part number not readable"

    # Exact match against compat_refs
    for ref in compat_refs:
        if ref.get("part_number", "").upper() == part_number.upper():
            return AttributeStatus.VERIFIED, "reference_lookup"

    return AttributeStatus.MISSING, "part number not in reference"


def check_stale_draft(
    built_from_versions: dict[str, str],
    current_versions: dict[str, str],
) -> bool:
    """E49: Check if a draft is stale.

    A draft is stale if any attribute version has changed since the draft
    was created.
    """
    for name, version_id in built_from_versions.items():
        if current_versions.get(name) != version_id:
            return True
    return False


def is_visual_flag_field(field_name: str) -> bool:
    """Check if a field is a visual flag that might be contradicted by a seller."""
    return field_name in {
        "condition_notes",
        "broken_glass",
        "cracked_tab",
        "damage",
    }


def detect_hedged_answer(text: str) -> bool:
    """E41: Detect hedged/uncertain answers in English or Arabic.

    Hedged answers are stored as unconfirmed.
    """
    text_lower = text.lower().strip()

    hedged_phrases = [
        "i think", "maybe", "possibly", "might be", "could be",
        "not sure", "i believe", "approximately", "around",
        "roughly", "about", "as far as i know", "to the best of my knowledge",
        "i guess", "probably", "seems like", "appears to be",
    ]

    hedged_arabic = [
        "أعتقد",  # "I think"
        "ربما",  # "maybe"
        "من الممكن",  # "possibly"
        "لست متأكد",  # "I'm not sure"
        "قد يكون",  # "might be"
    ]

    for phrase in hedged_phrases:
        if phrase in text_lower:
            return True

    for phrase in hedged_arabic:
        if phrase in text:
            return True

    return False
