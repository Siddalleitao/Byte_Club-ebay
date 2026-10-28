"""Claims — every description sentence, with its type and evidence (FR-11, FR-12, TRD 3).

The Listing agent renders from the ledger, never free-hand: each sentence of
the description is built here from the part's facts, the CP4 result and the
seller profile, and carries

  claim_type   fact | disclosure | donor_info | verified_compat
  evidence_ids the evidence rows behind it (for the source-on-hover UI)
  sources      non-evidence sources: seller-profile fields (T0 configuration)
               or a ledger status ("test status not yet stated")

Sections, in order: part details, condition and disclosure (mandatory),
donor vehicle, compatibility, shipping. Donor vehicle and verified
compatibility are separate sections (Rule c). Only values the ledger allows
as plain fact are stated; anything else is stated as "being confirmed".

Per Rule (g): pure logic + DB reads. No LLM.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from app.domain.compatibility import CompatResult, CompatStatus
from app.domain.enums import AttributeStatus
from app.domain.glossary import get_glossary
from app.domain.listing_profile import ProfileSections
from app.domain.part_facts import ALIASES, PartFacts

SECTIONS = ("summary", "part_details", "condition", "donor", "compatibility", "shipping")
SECTION_TITLES = {
    "part_details": "Part details",
    "condition": "Condition and disclosure",
    "donor": "Donor vehicle",
    "compatibility": "Compatibility",
    "shipping": "Shipping",
}

_TEST_TEXT = {
    "untested": "Test status: untested.",
    "seller_reported_working": "Test status: the seller reports it working; there is no test record.",
    "partly_tested": "Test status: partly tested.",
    "failed": "Test status: failed test; sold for parts or not working.",
}
_TEST_WORDS = {"untested": "untested", "seller_reported_working": "working (seller-reported)",
               "partly_tested": "partly tested", "failed": "failed"}


@dataclass
class ClaimSpec:
    key: str
    section: str
    claim_type: str
    text: str
    field: str | None = None
    evidence_ids: list[str] = dataclass_field(default_factory=list)
    sources: list[str] = dataclass_field(default_factory=list)


# ── Evidence lookups ────────────────────────────────────────


def attribute_evidence(conn: sqlite3.Connection, part_id: str, *names: str) -> list[str]:
    """Evidence ids linked to the current rows of these attributes (aliases included)."""
    expanded = sorted({n for name in names for n in ALIASES.get(name, (name,))} | set(names))
    marks = ",".join("?" for _ in expanded)
    rows = conn.execute(
        f"""SELECT DISTINCT ae.evidence_id FROM attribute_evidence ae
            JOIN attributes a ON a.id = ae.attribute_id
            JOIN evidence e ON e.id = ae.evidence_id AND e.part_id = a.part_id
            WHERE a.part_id = ? AND a.name IN ({marks})
            ORDER BY ae.created_at""",
        (part_id, *expanded),
    ).fetchall()
    return [r["evidence_id"] for r in rows]


def evidence_of_kind(conn: sqlite3.Connection, part_id: str, *kinds: str, source_like: str | None = None,
                     latest: bool = False) -> list[str]:
    marks = ",".join("?" for _ in kinds)
    sql = f"SELECT id FROM evidence WHERE part_id = ? AND kind IN ({marks})"
    params: list = [part_id, *kinds]
    if source_like:
        sql += " AND source_ref LIKE ?"
        params.append(source_like)
    sql += " ORDER BY created_at DESC, rowid DESC" if latest else " ORDER BY created_at, rowid"
    rows = conn.execute(sql, params).fetchall()
    ids = [r["id"] for r in rows]
    return ids[:1] if latest else ids


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


# ── Builders ────────────────────────────────────────────────


def _fact_value(facts: PartFacts, canonical: str) -> str | None:
    attr = facts.get(canonical)
    if attr and attr.is_fact and attr.value and "?" not in attr.value:
        return attr.value
    return None


def _part_words(facts: PartFacts) -> str:
    g = get_glossary()
    part_type = g.normalise("part_type", facts.part_type).value
    terms = g.uk_search_terms(part_type) if part_type else []
    if len(terms) >= 2:
        return f"{terms[0]} ({terms[1]})"
    return terms[0] if terms else (facts.part_type or "part")


def _part_details(conn: sqlite3.Connection, facts: PartFacts) -> list[ClaimSpec]:
    pid = facts.part_id
    type_evidence = attribute_evidence(conn, pid, "part_type") + evidence_of_kind(
        conn, pid, "spreadsheet_cell", source_like="%:part_type")
    side = _fact_value(facts, "position")
    words = _part_words(facts)
    if side:
        part = ClaimSpec("details.part", "part_details", "fact", f"{side.title()} {words}, used.", "position",
                         _dedupe(attribute_evidence(conn, pid, "position") + type_evidence))
    else:
        part = ClaimSpec("details.part", "part_details", "fact", f"{words[0].upper()}{words[1:]}, used; side being confirmed.",
                         "position", _dedupe(type_evidence), ["ledger:position:unconfirmed"])
    specs = [part]

    pn = _fact_value(facts, "part_number")
    if pn:
        specs.append(ClaimSpec("details.part_number", "part_details", "fact", f"Part number on the label: {pn}.",
                               "part_number", attribute_evidence(conn, pid, "part_number")))
    else:
        specs.append(ClaimSpec("details.part_number", "part_details", "fact",
                               "Part number: being confirmed from the label.", "part_number",
                               [], ["ledger:part_number:unconfirmed"]))
    brand = facts.attrs.get("brand")
    if brand and brand.value and brand.status not in (AttributeStatus.MISSING, AttributeStatus.CONFLICTING):
        specs.append(ClaimSpec("details.brand", "part_details", "fact", f"Brand marking on the label: {brand.value}.",
                               "brand", attribute_evidence(conn, pid, "brand")))
    colour = _fact_value(facts, "colour")
    if colour:
        specs.append(ClaimSpec("details.colour", "part_details", "fact", f"Colour: {colour}.", "colour",
                               attribute_evidence(conn, pid, "colour")))
    features = facts.attrs.get("features")
    if features and features.is_fact and features.value:
        try:
            names = json.loads(features.value)
        except json.JSONDecodeError:
            names = [features.value]
        if names:
            specs.append(ClaimSpec("details.features", "part_details", "fact",
                                   f"Features: {', '.join(str(n).replace('_', ' ') for n in names)}.", "features",
                                   attribute_evidence(conn, pid, "features")))
    return specs


def _condition(conn: sqlite3.Connection, facts: PartFacts) -> list[ClaimSpec]:
    pid = facts.part_id
    used_evidence = evidence_of_kind(conn, pid, "donor_note", "arabic_note") + evidence_of_kind(
        conn, pid, "spreadsheet_cell", source_like="%:part_type")
    specs = [ClaimSpec("condition.used", "condition", "disclosure", "Used part, removed from a donor vehicle.",
                       "condition", _dedupe(used_evidence))]

    cond = facts.get("condition")
    cond_evidence = attribute_evidence(conn, pid, "condition_notes", "condition")
    if cond and cond.value and cond.confirmed:
        if cond.value.lower().startswith("no damage"):
            specs.append(ClaimSpec("condition.defect", "condition", "disclosure",
                                   "No damage reported by the seller.", "condition_notes", cond_evidence))
        else:
            value = (cond.value[0].lower() + cond.value[1:]).replace(": ", " ", 1)  # "lower mounting tab cracked"
            specs.append(ClaimSpec("condition.defect", "condition", "disclosure", f"Defect: {value}.",
                                   "condition_notes", cond_evidence))
    elif cond and cond.value:
        specs.append(ClaimSpec("condition.defect", "condition", "disclosure",
                               f"Possible defect being confirmed with the seller: {cond.value}.", "condition_notes",
                               _dedupe(cond_evidence + evidence_of_kind(conn, pid, "photo_region"))))
    else:
        specs.append(ClaimSpec("condition.defect", "condition", "disclosure", "Condition notes: none recorded yet.",
                               "condition_notes", [], ["ledger:condition_notes:missing"]))

    test = facts.get("test_status")
    test_evidence = attribute_evidence(conn, pid, "test_status")
    g = get_glossary()
    value = g.normalise("test_status", test.value).value if test and test.value else None
    value = value or (test.value if test else None)
    per_feature = facts.attrs.get("feature_tests")
    if test and test.confirmed and value == "partly_tested" and per_feature and per_feature.value:
        try:
            results = json.loads(per_feature.value)
        except json.JSONDecodeError:
            results = {}
        detail = "; ".join(f"{k.replace('_', ' ')} {_TEST_WORDS.get(v, v)}" for k, v in results.items())
        specs.append(ClaimSpec("condition.test_status", "condition", "disclosure",
                               f"Test status: partly tested ({detail}); no test record.", "test_status",
                               _dedupe(test_evidence + attribute_evidence(conn, pid, "feature_tests"))))
    elif test and test.confirmed and value in _TEST_TEXT:
        specs.append(ClaimSpec("condition.test_status", "condition", "disclosure", _TEST_TEXT[value],
                               "test_status", test_evidence))
    else:
        specs.append(ClaimSpec("condition.test_status", "condition", "disclosure", "Test status: not yet stated.",
                               "test_status", [], ["ledger:test_status:missing"]))

    comps = facts.attrs.get("included_components")
    marked = {}
    if comps and comps.value and comps.status not in (AttributeStatus.MISSING, AttributeStatus.CONFLICTING):
        try:
            marked = json.loads(comps.value)
        except json.JSONDecodeError:
            marked = {}
    if marked:
        comp_evidence = attribute_evidence(conn, pid, "included_components")
        present = [k.replace("_", " ") for k, v in marked.items() if v == "present"]
        missing = [k.replace("_", " ") for k, v in marked.items() if v == "missing"]
        specs.append(ClaimSpec("condition.components", "condition", "disclosure",
                               f"Included: {', '.join(present) or 'none'}.", "included_components", comp_evidence))
        specs.append(ClaimSpec("condition.components_missing", "condition", "disclosure",
                               f"Missing: {', '.join(missing)}." if missing else "Missing components: none recorded.",
                               "included_components", comp_evidence))
    else:
        specs.append(ClaimSpec("condition.components", "condition", "disclosure",
                               "Included components: not yet recorded.", "included_components",
                               [], ["ledger:included_components:missing"]))
    return specs


def _donor(conn: sqlite3.Connection, facts: PartFacts, compat: CompatResult) -> list[ClaimSpec]:
    pid = facts.part_id
    donor = compat.donor
    evidence = _dedupe(evidence_of_kind(conn, pid, "donor_note", "arabic_note")
                       + attribute_evidence(conn, pid, "donor_year", "donor_model", "donor_make", "year", "make", "model"))
    if donor is None or not (donor.make or donor.model):
        return [ClaimSpec("donor.vehicle", "donor", "donor_info", "Donor vehicle: not recorded.", "donor_year",
                          [], ["ledger:donor:missing"])]
    year_attr = facts.attrs.get("donor_year")
    conflicting = year_attr is not None and year_attr.status == AttributeStatus.CONFLICTING and not donor.year_confirmed
    bits = [None if conflicting else donor.year, donor.make, donor.model]
    car = " ".join(str(b) for b in bits if b)
    spec = f", {donor.market} spec" if donor.market else ""
    tail = " (year being confirmed with the seller)" if conflicting else ""
    return [ClaimSpec("donor.vehicle", "donor", "donor_info",
                      f"Donor vehicle (information only, not a fitment claim): {car}{spec}{tail}.", "donor_year",
                      evidence)]


def _compatibility(conn: sqlite3.Connection, facts: PartFacts, compat: CompatResult) -> list[ClaimSpec]:
    lookup = evidence_of_kind(conn, facts.part_id, "reference_lookup", latest=True)
    if compat.status == CompatStatus.VERIFIED:
        specs = []
        for i, f in enumerate(compat.fitments):
            years = f"{f.year_from}-{f.year_to}" if f.year_from and f.year_to else ""
            where = ", ".join(x for x in (f"{f.market} market" if f.market else "",
                                          f"{f.position} side" if f.position else "") if x)
            specs.append(ClaimSpec(f"compatibility.fit.{i}", "compatibility", "verified_compat",
                                   f"Verified fit: {f.make} {f.model} {years} ({where}), reference {compat.ref_version}.",
                                   "fitment", lookup))
        return specs
    sources = [] if lookup else [f"ledger:compatibility:{compat.reason}"]
    return [ClaimSpec("compatibility.not_verified", "compatibility", "disclosure",
                      "Compatibility not verified: please match your part number before buying.", "fitment",
                      lookup, sources)]


def _shipping(conn: sqlite3.Connection, facts: PartFacts, profile: ProfileSections | None) -> list[ClaimSpec]:
    pid = facts.part_id
    weight, dims = facts.attrs.get("weight"), facts.attrs.get("dimensions")
    if weight and weight.value and dims and dims.value:
        packed = ClaimSpec("shipping.packed", "shipping", "fact",
                           f"Packed weight {weight.value}; packed size {dims.value}.", "weight",
                           attribute_evidence(conn, pid, "weight", "dimensions"))
    else:
        packed = ClaimSpec("shipping.packed", "shipping", "fact", "Packed weight and size: being measured.", "weight",
                           [], ["ledger:weight:missing"])
    specs = [packed]
    if profile:
        days, city = profile.handling_days, profile.city
        specs.append(ClaimSpec("shipping.dispatch", "shipping", "fact",
                               f"Dispatched from {city} within {days} working day{'s' if days != 1 else ''} "
                               f"of cleared payment.", None, [], ["profile:city", "profile:handling_days"]))
        if profile.return_policy_ref:
            specs.append(ClaimSpec("shipping.returns", "shipping", "fact",
                                   f"Returns: see policy {profile.return_policy_ref}.", None, [],
                                   ["profile:return_policy_ref"]))
    return specs


def build_claims(
    conn: sqlite3.Connection, facts: PartFacts, compat: CompatResult, profile: ProfileSections | None
) -> list[ClaimSpec]:
    """Every templated description sentence for one part, in section order."""
    return (_part_details(conn, facts) + _condition(conn, facts) + _donor(conn, facts, compat)
            + _compatibility(conn, facts, compat) + _shipping(conn, facts, profile))
