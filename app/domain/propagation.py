"""Propagation map — a dependency graph held as data (TRD 3).

Nodes are ledger fields, derived inputs and draft outputs. An edge A → B
means "when A changes, B must be recomputed". The propagation engine
(propagation_engine.py) walks this graph so that one correction recomputes
only the affected outputs, in OUTPUT_ORDER, in a single step.

    Field changed              Re-computed                                   LLM call?
    part number                compat lookup, item specifics, title,         description only
                               description, checklist
    position (left or right)   item specifics, title, compat lookup,         no, templated
                               checklist
    donor year, model or make  donor block, title, checklist                 no, templated
    condition or defect        disclosure block, description, checklist      description only
    test status                disclosure block, checklist                   no, templated
    weight or dimensions       shipping fields, checklist                    no
    price                      price field only                              no

"description" is the LLM-written summary paragraph; the templated sections of
the description are the blocks above and never need the LLM.

Per Rule (g): domain services never call an LLM. This module is data plus lookups.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Outputs ─────────────────────────────────────────────────

COMPAT_LOOKUP = "compat_lookup"
ITEM_SPECIFICS = "item_specifics"
TITLE = "title"
DESCRIPTION = "description"  # the LLM summary paragraph
DONOR_BLOCK = "donor_block"
DISCLOSURE_BLOCK = "disclosure_block"
SHIPPING_FIELDS = "shipping_fields"
PRICE_FIELD = "price_field"
CHECKLIST = "checklist"

# Recompute order: the compat lookup first (title and item specifics read it),
# rendered sections next, the checklist last (it reads everything).
OUTPUT_ORDER: tuple[str, ...] = (
    COMPAT_LOOKUP,
    ITEM_SPECIFICS,
    TITLE,
    DONOR_BLOCK,
    DISCLOSURE_BLOCK,
    SHIPPING_FIELDS,
    PRICE_FIELD,
    DESCRIPTION,
    CHECKLIST,
)

# Outputs that need the LLM (the summary paragraph is regenerated in the background).
LLM_OUTPUTS: frozenset[str] = frozenset({DESCRIPTION})

# ── The graph ───────────────────────────────────────────────

DEPENDENCY_GRAPH: dict[str, tuple[str, ...]] = {
    "part_number": (COMPAT_LOOKUP, ITEM_SPECIFICS, TITLE, DESCRIPTION, CHECKLIST),
    "position": (ITEM_SPECIFICS, TITLE, COMPAT_LOOKUP, CHECKLIST),
    "donor_year": (DONOR_BLOCK, TITLE, CHECKLIST),
    "donor_model": (DONOR_BLOCK, TITLE, CHECKLIST),
    "donor_make": (DONOR_BLOCK, TITLE, CHECKLIST),
    "condition": (DISCLOSURE_BLOCK, DESCRIPTION, CHECKLIST),
    "test_status": (DISCLOSURE_BLOCK, CHECKLIST),
    "weight": (SHIPPING_FIELDS, CHECKLIST),
    "dimensions": (SHIPPING_FIELDS, CHECKLIST),
    "price": (PRICE_FIELD,),
    # A new compatibility reference version (E35) re-runs the lookup and
    # re-evaluates everything that shows compatibility.
    "compat_reference": (COMPAT_LOOKUP, ITEM_SPECIFICS, TITLE, CHECKLIST),
}

# Attribute names used by intake, the reconciler and the chat → graph node.
FIELD_ALIASES: dict[str, str] = {
    "side": "position",
    "year": "donor_year",
    "model": "donor_model",
    "make": "donor_make",
    "condition_notes": "condition",
    "defect": "condition",
    "defects": "condition",
    "functionality": "test_status",
    "packed_weight": "weight",
}


def node_for_field(field: str) -> str:
    return FIELD_ALIASES.get(field, field)


def outputs_for(field: str) -> tuple[str, ...]:
    """Every output affected by a change to `field`, transitively, in OUTPUT_ORDER."""
    seen: set[str] = set()
    stack = [node_for_field(field)]
    while stack:
        for target in DEPENDENCY_GRAPH.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return tuple(o for o in OUTPUT_ORDER if o in seen)


def needs_llm(outputs: tuple[str, ...] | list[str]) -> list[str]:
    """Which of the outputs need the LLM summary regenerated."""
    return [o for o in outputs if o in LLM_OUTPUTS]


@dataclass(frozen=True)
class PropagationRule:
    """Outputs re-computed when a field changes (the graph's direct edges, in TRD order)."""

    recompute: tuple[str, ...]

    @property
    def needs_llm(self) -> bool:
        return bool(needs_llm(self.recompute))


def rule_for_field(field: str) -> PropagationRule | None:
    """Return the propagation rule for a field, or None if nothing downstream depends on it."""
    edges = DEPENDENCY_GRAPH.get(node_for_field(field))
    return PropagationRule(edges) if edges else None


# ── Seller-profile fields (TRD 4) ───────────────────────────

# Seller-profile fields used by drafts, and the draft sections they feed.
# Editing one of these re-renders those sections in every open draft.
PROFILE_DRAFT_FIELDS: dict[str, tuple[str, ...]] = {
    "handling_days": ("shipping_block",),
    "city": ("shipping_block",),
    "return_policy_ref": ("shipping_block",),
    "listing_currency": ("price_field",),
}

# Fields a correction may never touch: these are computed or reference data.
NOT_CORRECTABLE: frozenset[str] = frozenset({
    "status",
    "part_status",
    "compatibility",
    "fitment",
    "checklist",
    "checklist_version",
    "export",
})


def profile_fields_used_by_drafts(changed: set[str] | frozenset[str]) -> set[str]:
    """Return which of the changed profile fields feed draft sections."""
    return {f for f in changed if f in PROFILE_DRAFT_FIELDS}
