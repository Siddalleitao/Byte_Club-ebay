"""Templated listing outputs — the parts of a draft that need no LLM (TRD 3).

Each renderer is a pure function of the part's facts, the CP4 result and the
seller profile. Only values that may be stated as plain fact (TRD 3 status
rules) are stated; everything else is omitted or labelled "not confirmed".
The donor vehicle is always its own block and never a fitment (Rule c);
fitments appear only from a verified reference lookup.

The description's sentences (and the donor and disclosure blocks built from
them) are claims, rendered in claims.py and guarded by CP5 (listing.py). The
LLM-written summary paragraph is written by the Listing agent in orchestration.
"""

from __future__ import annotations

import json
from app.domain.compatibility import CompatResult, CompatStatus
from app.domain.glossary import get_glossary
from app.domain.listing_profile import ProfileSections
from app.domain.part_facts import PartFacts, price_amount, price_currency
from app.domain.propagation import (
    COMPAT_LOOKUP,
    ITEM_SPECIFICS,
    PRICE_FIELD,
    SHIPPING_FIELDS,
    TITLE,
)

TITLE_MAX = 80  # eBay UK title limit

def _fact(facts: PartFacts, canonical: str) -> str | None:
    attr = facts.get(canonical)
    return attr.value if attr and attr.is_fact and attr.value and "?" not in attr.value else None


def _primary_term(facts: PartFacts) -> str:
    """The main UK buyer term for the part type ("Wing Mirror")."""
    g = get_glossary()
    part_type = g.normalise("part_type", facts.part_type).value
    terms = g.uk_search_terms(part_type) if part_type else []
    return terms[0].title() if terms else (facts.part_type or "Part").title()


def _part_words(facts: PartFacts) -> str:
    g = get_glossary()
    part_type = g.normalise("part_type", facts.part_type).value
    terms = g.uk_search_terms(part_type) if part_type else []
    return " ".join(t.title() for t in terms) if terms else (facts.part_type or "Part").title()


def render_title(facts: PartFacts, compat: CompatResult) -> str:
    side = _fact(facts, "position")
    pn = _fact(facts, "part_number")
    words: list[str] = []
    if compat.status == CompatStatus.VERIFIED and compat.fitments:
        f = compat.fitments[0]
        years = f"{f.year_from}-{f.year_to}" if f.year_from and f.year_to else ""
        words += [f.make, f.model, years]
    words += [side.title() if side else "", _part_words(facts), pn or ""]
    title = " ".join(w for w in words if w)
    donor = compat.donor
    if compat.status != CompatStatus.VERIFIED and donor and (donor.make or donor.model):
        title += f" - from {donor.text().split(',')[0]} donor"
    return _truncate(title)


def _truncate(text: str) -> str:
    if len(text) <= TITLE_MAX:
        return text
    cut = text[:TITLE_MAX].rsplit(" ", 1)[0]
    return cut.rstrip(" -")


def render_item_specifics(facts: PartFacts, compat: CompatResult) -> dict[str, str]:
    specifics: dict[str, str] = {"Type": _primary_term(facts), "Condition": "Used"}
    side = _fact(facts, "position")
    if side:
        specifics["Placement on Vehicle"] = side.title()
    pn = _fact(facts, "part_number")
    if pn:
        specifics["Manufacturer Part Number"] = pn
    brand = facts.attrs.get("brand")
    if brand and brand.value:
        specifics["Brand"] = brand.value
    specifics["Compatibility"] = compat.label
    return specifics


def render_compat_block(compat: CompatResult) -> list[dict]:
    """Stored as compatibility_json: verified fitments, or the not-verified label."""
    if compat.status == CompatStatus.VERIFIED:
        return [{"fitment": f.label(), "ref_version": compat.ref_version, "claim_type": compat.claim_type}
                for f in compat.fitments]
    return [{"label": compat.label, "reason": compat.reason, "ref_version": compat.ref_version}]


def render_shipping(facts: PartFacts, profile: ProfileSections | None) -> dict:
    weight, dims = facts.attrs.get("weight"), facts.attrs.get("dimensions")
    return {
        "packed_weight": weight.value if weight else None,
        "packed_dimensions": dims.value if dims else None,
        "shipping_block": profile.shipping_block if profile else None,
        "handling_days": profile.handling_days if profile else None,
    }


def render_price(facts: PartFacts, profile: ProfileSections | None) -> dict:
    """The price as recorded, in its labelled currency (conversion to the listing
    currency with the dated test rate happens at export, not here)."""
    price = facts.attrs.get("price")
    value = price.value if price and price.value else None
    return {
        "price": price_amount(value),
        "currency": price_currency(facts, value, price.source) if value else None,
        "listing_currency": profile.listing_currency if profile else facts.listing_currency,
    }


# Which draft columns each output writes.
OUTPUT_COLUMNS: dict[str, tuple[str, ...]] = {
    COMPAT_LOOKUP: ("compatibility_json", "ref_version"),
    ITEM_SPECIFICS: ("item_specifics_json",),
    TITLE: ("title",),
    SHIPPING_FIELDS: ("shipping_json", "handling_days", "shipping_block"),
    PRICE_FIELD: ("price", "currency"),
}


def render_output(
    output: str, facts: PartFacts, compat: CompatResult, profile: ProfileSections | None
) -> dict[str, object]:
    """Column values for one templated output."""
    if output == COMPAT_LOOKUP:
        return {"compatibility_json": json.dumps(render_compat_block(compat)), "ref_version": compat.ref_version}
    if output == ITEM_SPECIFICS:
        return {"item_specifics_json": json.dumps(render_item_specifics(facts, compat), ensure_ascii=False)}
    if output == TITLE:
        return {"title": render_title(facts, compat)}
    if output == SHIPPING_FIELDS:
        shipping = render_shipping(facts, profile)
        return {
            "shipping_json": json.dumps(shipping, ensure_ascii=False),
            "handling_days": shipping["handling_days"],
            "shipping_block": shipping["shipping_block"],
        }
    if output == PRICE_FIELD:
        price = render_price(facts, profile)
        return {"price": price["price"], "currency": price["currency"]}
    raise KeyError(output)
