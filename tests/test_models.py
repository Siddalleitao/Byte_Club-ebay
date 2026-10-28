"""Tests for Pydantic v2 domain models."""

import uuid

from app.domain import (
    Attribute,
    AttributeStatus,
    AuditEvent,
    Claim,
    ClaimType,
    CompatRef,
    DonorVehicle,
    Evidence,
    EvidenceKind,
    ListingDraft,
    Part,
    PartStatus,
    Question,
    Seller,
)


def _pid() -> str:
    return str(uuid.uuid4())


def test_seller_defaults():
    s = Seller(id=_pid(), name="Ahmed")
    assert s.name == "Ahmed"
    assert s.phone is None
    assert s.created_at is not None


def test_part_defaults():
    p = Part(id=_pid(), seller_id=_pid(), part_type="side-view mirror")
    assert p.status == PartStatus.INGESTED
    assert p.part_number is None


def test_attribute_defaults():
    a = Attribute(id=_pid(), part_id=_pid(), name="functionality")
    assert a.status == AttributeStatus.MISSING
    assert a.value is None


def test_evidence_kind_enum():
    e = Evidence(
        id=_pid(), part_id=_pid(), kind=EvidenceKind.SELLER_ANSWER,
        content="Yes, it powers on."
    )
    assert e.kind == EvidenceKind.SELLER_ANSWER
    assert e.confidence is None


def test_donor_vehicle_is_metadata():
    dv = DonorVehicle(id=_pid(), part_id=_pid(), make="Toyota", model="Camry", year=2018)
    assert dv.make == "Toyota"
    # DonorVehicle has no compatibility fields — it's descriptive only (Rule c)


def test_compat_ref_exact_lookup_fields():
    cr = CompatRef(
        id=_pid(), part_number="87940-06A10", make="Toyota", model="Camry",
        year_start=2015, year_end=2018, region="UK",
    )
    assert cr.part_number == "87940-06A10"


def test_claim_types():
    c = Claim(id=_pid(), part_id=_pid(), claim_type=ClaimType.FACT, claim_value="left side")
    assert c.claim_type == ClaimType.FACT


def test_listing_draft_defaults():
    ld = ListingDraft(id=_pid(), part_id=_pid())
    assert ld.currency == "GBP"
    assert ld.marketplace == "eBay UK"
    assert ld.compatibility == []
    assert ld.photos == []


def test_audit_event_immutable_fields():
    ae = AuditEvent(part_id=_pid(), event_type="ingest", actor="api")
    assert ae.event_type == "ingest"
    assert ae.detail == {}
