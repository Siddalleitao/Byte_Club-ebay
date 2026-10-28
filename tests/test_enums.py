"""Tests for domain enums and their precedence logic."""

from app.domain.enums import AttributeStatus, PartStatus, TrustTier


def test_attribute_status_precedence_order():
    expected = [
        AttributeStatus.SELLER_CONFIRMED,
        AttributeStatus.VERIFIED,
        AttributeStatus.EXTRACTED,
        AttributeStatus.CONFLICTING,
        AttributeStatus.MISSING,
        AttributeStatus.UNVERIFIED,
    ]
    assert AttributeStatus.precedence() == expected


def test_resolve_picks_highest_precedence():
    statuses = [
        AttributeStatus.MISSING,
        AttributeStatus.EXTRACTED,
        AttributeStatus.UNVERIFIED,
    ]
    assert AttributeStatus.resolve(statuses) == AttributeStatus.EXTRACTED


def test_resolve_seller_confirmed_beats_all():
    statuses = [
        AttributeStatus.MISSING,
        AttributeStatus.SELLER_CONFIRMED,
        AttributeStatus.EXTRACTED,
    ]
    assert AttributeStatus.resolve(statuses) == AttributeStatus.SELLER_CONFIRMED


def test_resolve_empty_returns_missing():
    assert AttributeStatus.resolve([]) == AttributeStatus.MISSING


def test_part_status_ordered_complete():
    ordered = PartStatus.ordered()
    assert ordered[0] == PartStatus.INGESTED
    assert ordered[-1] == PartStatus.EXPORTED
    assert len(ordered) == 10


def test_trust_tier_values():
    assert TrustTier.T0.value == "T0"
    assert TrustTier.T3.value == "T3"
