"""PartProof domain enums.

All enums used across the application.  See docs/RULES.md for the
standing rules that govern how these values flow through the system.
"""

from __future__ import annotations

from enum import Enum


class AttributeStatus(str, Enum):
    """Status of an attribute value, ordered by precedence (highest first)."""

    SELLER_CONFIRMED = "Seller-confirmed"
    VERIFIED = "Verified"
    EXTRACTED = "Extracted"
    CONFLICTING = "Conflicting"
    MISSING = "Missing"
    UNVERIFIED = "Unverified"

    @classmethod
    def precedence(cls) -> list["AttributeStatus"]:
        """Return statuses from highest to lowest precedence."""
        return [
            cls.SELLER_CONFIRMED,
            cls.VERIFIED,
            cls.EXTRACTED,
            cls.CONFLICTING,
            cls.MISSING,
            cls.UNVERIFIED,
        ]

    @classmethod
    def resolve(cls, statuses: list["AttributeStatus"]) -> "AttributeStatus":
        """Pick the highest-precedence status from a list of candidates."""
        order = {s: i for i, s in enumerate(cls.precedence())}
        if not statuses:
            return cls.MISSING
        return min(statuses, key=lambda s: order[s])


class EvidenceKind(str, Enum):
    """What kind of evidence is being recorded."""

    PHOTO_REGION = "photo_region"
    OCR_TEXT = "ocr_text"
    SPREADSHEET_CELL = "spreadsheet_cell"
    DONOR_NOTE = "donor_note"
    ARABIC_NOTE = "arabic_note"
    SELLER_ANSWER = "seller_answer"
    REFERENCE_LOOKUP = "reference_lookup"
    TEST_RECORD = "test_record"


class ClaimType(str, Enum):
    """Type of claim an evidence item makes about an attribute."""

    FACT = "fact"
    DISCLOSURE = "disclosure"
    DONOR_INFO = "donor_info"
    VERIFIED_COMPAT = "verified_compat"


class TrustTier(str, Enum):
    """Trust tiers (TRD 6). Lower is more trusted. See app/guardrails/trust.py."""

    T0 = "T0"  # system configuration: prompts, guardrail policy, checklist, compatibility reference
    T1 = "T1"  # the authenticated seller's chat and editor actions: allowed intents only
    T2 = "T2"  # all ingested content (sheets, notes, OCR, file names, metadata): data, never instructions
    T3 = "T3"  # model output: untrusted until validated and parsed into a schema


class PartStatus(str, Enum):
    """Lifecycle states of a part through the pipeline."""

    INGESTED = "Ingested"
    EXTRACTED = "Extracted"
    RECONCILED = "Reconciled"
    AWAITING_SELLER = "Awaiting seller"
    DRAFTED = "Drafted"
    GUARDED = "Guarded"
    EVALUATED = "Evaluated"
    AWAITING_APPROVAL = "Awaiting approval"
    APPROVED = "Approved"
    EXPORTED = "Exported"

    @classmethod
    def ordered(cls) -> list["PartStatus"]:
        """Return states in pipeline order."""
        return [
            cls.INGESTED,
            cls.EXTRACTED,
            cls.RECONCILED,
            cls.AWAITING_SELLER,
            cls.DRAFTED,
            cls.GUARDED,
            cls.EVALUATED,
            cls.AWAITING_APPROVAL,
            cls.APPROVED,
            cls.EXPORTED,
        ]
