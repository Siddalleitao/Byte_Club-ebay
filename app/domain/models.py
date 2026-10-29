"""PartProof Pydantic v2 domain models.

Every entity mirrors a SQLite table in app/data/schema.sql.
These models are the canonical in-memory representation used by
orchestration, domain services, and the API gateway.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import re

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import (
    AttributeStatus,
    ClaimType,
    EvidenceKind,
    PartStatus,
    TrustTier,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Seller ────────────────────────────────────────────────────
class Seller(BaseModel):
    id: str
    name: str
    phone: str | None = None
    whatsapp: str | None = None
    location: str | None = None
    seller_type: str | None = None  # individual | dealer
    created_at: datetime = Field(default_factory=_now)


# ── Seller profile (TRD 4 seller context card) ────────────────
SUPPORTED_LANGUAGES = ("en", "ar")
REPLY_LANGUAGES = ("match_user", "en", "ar")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_RACK_RANGE_RE = re.compile(r"^[A-Z]\d{1,2}(-[A-Z]\d{1,2})?$")


class SellerProfile(BaseModel):
    """Pre-filled seller context, stored in `seller_profiles`.

    seller_id is the identity key: it comes from the profile table at login,
    never from text in uploaded files or chat (TRD 4).
    """

    seller_id: str
    display_name: str = Field(min_length=1, max_length=80)
    business_name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=80)
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    reply_language: str = "match_user"
    corridor: str = Field(min_length=1, max_length=40)
    inventory_currency: str = "AED"
    listing_currency: str = "GBP"
    fx_rate_source: str | None = Field(default=None, max_length=120)
    fx_rate: float | None = Field(default=None, gt=0)  # dated test rate: 1 inventory_currency = fx_rate listing_currency (E40)
    fx_rate_date: str | None = None
    handling_days: int = Field(ge=0, le=30)
    return_policy_ref: str | None = Field(default=None, max_length=40)
    rack_codes: list[str] = Field(default_factory=list)
    product_family: str
    checklist_version: str
    version: int = 1
    updated_at: str | None = None

    @field_validator("languages")
    @classmethod
    def _known_languages(cls, v: list[str]) -> list[str]:
        unknown = [lang for lang in v if lang not in SUPPORTED_LANGUAGES]
        if unknown:
            raise ValueError(f"unsupported languages: {unknown}")
        return v

    @field_validator("reply_language")
    @classmethod
    def _known_reply_language(cls, v: str) -> str:
        if v not in REPLY_LANGUAGES:
            raise ValueError(f"reply_language must be one of {REPLY_LANGUAGES}")
        return v

    @field_validator("inventory_currency", "listing_currency")
    @classmethod
    def _currency_code(cls, v: str) -> str:
        if not _CURRENCY_RE.match(v):
            raise ValueError("currency must be a 3-letter ISO code, e.g. GBP")
        return v

    @field_validator("rack_codes")
    @classmethod
    def _rack_ranges(cls, v: list[str]) -> list[str]:
        bad = [code for code in v if not _RACK_RANGE_RE.match(code)]
        if bad:
            raise ValueError(f"rack codes must look like A1 or A1-A6: {bad}")
        return v


# Profile fields the seller may edit on the seller-details screen.
# seller_id, product_family and checklist_version are system configuration (T0).
EDITABLE_PROFILE_FIELDS: frozenset[str] = frozenset({
    "display_name",
    "business_name",
    "city",
    "languages",
    "reply_language",
    "corridor",
    "inventory_currency",
    "listing_currency",
    "fx_rate_source",
    "handling_days",
    "return_policy_ref",
    "rack_codes",
})


# ── Part ──────────────────────────────────────────────────────
class Part(BaseModel):
    id: str
    seller_id: str
    title: str | None = None
    part_number: str | None = None
    part_type: str
    side: str | None = None  # left | right
    status: PartStatus = PartStatus.INGESTED
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── Donor Vehicle ─────────────────────────────────────────────
class DonorVehicle(BaseModel):
    """Descriptive metadata only — NEVER used for compatibility (Rule c)."""

    id: str
    part_id: str
    make: str | None = None
    model: str | None = None
    year: int | None = None
    vin: str | None = None
    trim: str | None = None
    market: str | None = None  # GCC, EU, US, etc.
    note: str | None = None  # donor_note — DATA, never instructions (Rule f)
    created_at: datetime = Field(default_factory=_now)


# ── Evidence ──────────────────────────────────────────────────
class Evidence(BaseModel):
    id: str
    part_id: str
    kind: EvidenceKind
    source_ref: str | None = None
    content: str | None = None
    confidence: float | None = None  # 0.0–1.0
    raw_data: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_now)


# ── Attribute ─────────────────────────────────────────────────
class Attribute(BaseModel):
    id: str
    part_id: str
    name: str  # functionality, authenticity, side, colour, part_number, etc.
    value: str | None = None
    status: AttributeStatus = AttributeStatus.MISSING
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── AttributeEvidence (join with claim info) ──────────────────
class AttributeEvidence(BaseModel):
    id: str
    attribute_id: str
    evidence_id: str
    claim_type: ClaimType | None = None
    claim_value: str | None = None
    created_at: datetime = Field(default_factory=_now)


# ── Compatibility Reference ───────────────────────────────────
class CompatRef(BaseModel):
    """Reference-data row from mock OEM catalogue.

    Compatibility is established ONLY by exact part-number match
    against this table (Rule c).
    """

    id: str
    part_number: str
    make: str
    model: str
    year_start: int | None = None
    year_end: int | None = None
    trim: str | None = None
    market: str | None = None
    region: str | None = None  # UK, EU, etc.
    source: str | None = None
    created_at: datetime = Field(default_factory=_now)


# ── Question (seller Q&A) ─────────────────────────────────────
class Question(BaseModel):
    id: str
    part_id: str
    question_text: str
    attribute_name: str | None = None
    answer_text: str | None = None  # NULL until seller answers
    status: str = "open"  # open | answered | skipped
    created_at: datetime = Field(default_factory=_now)
    answered_at: datetime | None = None


# ── Claim ─────────────────────────────────────────────────────
class Claim(BaseModel):
    id: str
    part_id: str
    attribute_name: str | None = None
    claim_type: ClaimType
    claim_value: str | None = None
    evidence_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


# ── Listing Draft ─────────────────────────────────────────────
class ListingDraft(BaseModel):
    id: str
    part_id: str
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    condition_note: str | None = None
    compatibility: list[dict[str, Any]] = Field(default_factory=list)
    checklist: dict[str, Any] | None = None
    photos: list[str] = Field(default_factory=list)
    price: float | None = None
    currency: str = "GBP"
    marketplace: str = "eBay UK"
    status: str = "draft"  # draft | guarded | approved | exported
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── Checklist Result ──────────────────────────────────────────
class ChecklistResult(BaseModel):
    id: str
    part_id: str
    listing_id: str | None = None
    checklist: dict[str, Any]  # full structured checklist
    passed: bool = False
    created_at: datetime = Field(default_factory=_now)


# ── Audit Event ───────────────────────────────────────────────
class AuditEvent(BaseModel):
    """Immutable append-only audit trail entry."""

    id: int | None = None  # auto-increment in SQLite
    part_id: str | None = None
    event_type: str  # ingest, extract, reconcile, draft, guard, evaluate, approve, export, llm_call
    actor: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)


# ── LLM cost / token log (used by the gateway) ────────────────
class LLMCallLog(BaseModel):
    """Per-call token and cost record, logged per part."""

    model_tier: str  # MAIN | FAST
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    cached: bool = False
    duration_ms: int | None = None
    part_id: str | None = None
    timestamp: datetime = Field(default_factory=_now)
