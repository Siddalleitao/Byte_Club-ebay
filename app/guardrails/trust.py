"""Trust tiers and data tagging (TRD 6, defence layers 2 and 3).

    Tier  Sources                                         Treated as               Can it give instructions?
    T0    system prompt, guardrail policy, checklist,      trusted configuration    yes, developers only
          compatibility reference
    T1    the authenticated seller's chat and editor       requests within allowed  allowed intents only;
          actions                                          intents                  never policy changes
    T2    ingested content: sheet cells, donor notes,      data                     never
          Arabic notes, OCR text, file names, metadata
    T3    anything a model returns                         untrusted until          never; parsed into a schema
                                                           validated

Every T2 snippet that reaches a model is wrapped in a data tag carrying a
fresh random id and its source, and the tag cannot be closed from inside.
"""

from __future__ import annotations

import secrets

from app.domain.enums import TrustTier

TIER_RULES: dict[TrustTier, str] = {
    TrustTier.T0: "trusted configuration; developers only",
    TrustTier.T1: "allowed intents only; never policy changes",
    TrustTier.T2: "data; never an instruction",
    TrustTier.T3: "untrusted until validated and parsed into a schema",
}

# Where content comes from → its tier.
SOURCE_TIERS: dict[str, TrustTier] = {
    "system_prompt": TrustTier.T0,
    "guardrail_policy": TrustTier.T0,
    "checklist": TrustTier.T0,
    "compat_reference": TrustTier.T0,
    "seller_profile": TrustTier.T0,  # seller-edited, but only on the seller-details screen
    "chat": TrustTier.T1,
    "editor": TrustTier.T1,
    "spreadsheet_cell": TrustTier.T2,
    "donor_note": TrustTier.T2,
    "arabic_note": TrustTier.T2,
    "ocr_text": TrustTier.T2,
    "photo_region": TrustTier.T2,
    "file_name": TrustTier.T2,
    "metadata": TrustTier.T2,
    "seller_answer": TrustTier.T2,  # stored chat text is evidence, i.e. data, once stored
    "model_output": TrustTier.T3,
}


def tier_of(source_kind: str) -> TrustTier:
    """The trust tier of a source. Unknown sources are treated as ingested data (T2)."""
    return SOURCE_TIERS.get(source_kind, TrustTier.T2)


def new_block_id() -> str:
    """Fresh random id per request (the gateway normalises ev_xxxxxxxx for fixture replay)."""
    return f"ev_{secrets.token_hex(4)}"


def escape_data(content: str) -> str:
    """Stop data from closing or opening a tag: '<' becomes '‹' (a look-alike that is not markup)."""
    return (content or "").replace("<", "‹").replace(">", "›")


def wrap_data(content: str, source: str, block_id: str | None = None) -> str:
    """Wrap a T2 snippet for a prompt: <data id=… source=…>…</data>, escaped so it cannot break out."""
    return f"<data id={block_id or new_block_id()} source={escape_data(source)}>\n{escape_data(content)}\n</data>"
