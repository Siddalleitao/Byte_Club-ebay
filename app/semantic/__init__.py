"""Semantic package — the governed layer between the chat/agents and the data.

These functions never mutate part status, compatibility, checklist data, or
exports (Rule b).

Intake agent (TRD 5):
- intake_tools: read_image, ocr, parse_sheet, translate_note (the only 4 allowed tools)
- intake_prompt: cached system prompt, <data> block evidence wrapping, seller card
- intake_agent: orchestrates tool calls + CP1 validation, runs in parallel with asyncio

Semantic layer and seller context (TRD 4):
- entity_model: seller-scoped, read-only typed views over Part, Attribute, Evidence,
  Donor, Claim, Checklist and Question (no raw SQL exposed, no text-to-SQL)
- seller_context: profile resolved at login, SellerSession, profile edits
- context_card: the ~300-token seller context card
- prompt_prefix: cached prefix = system prompt + guardrail policy + glossary + card
- glossary: bilingual glossary, controlled vocabularies, Arabic-Indic digits
- metrics: ready_count, open_questions, conflict_rate, time_to_ready, unsupported_claims
- access_policy: what the chat may read and write, enforced by ChatTools
- intents: chat intent → semantic query map with typed results
"""

from app.semantic.intake_agent import (
    IntakePartInput,
    IntakeResult,
    ingest_part,
    ingest_parts_parallel,
    save_intake_to_db,
)
from app.semantic.intake_prompt import (
    SellerCard,
    build_evidence_block,
    build_request_message,
    get_system_prompt,
)
from app.semantic.intake_tools import (
    OCRResult,
    clear_tool_cache,
    ocr,
    parse_sheet,
    read_image,
    translate_note,
)
from app.semantic.access_policy import AccessDenied, ChatAccessPolicy, ChatTools
from app.semantic.entity_model import SemanticLayer
from app.semantic.glossary import Glossary, get_glossary
from app.semantic.intents import IntentRequest, IntentResult, execute_intent
from app.semantic.metrics import compute_metrics
from app.semantic.prompt_prefix import get_prompt_prefix, invalidate_prompt_prefix
from app.semantic.seller_context import (
    SellerSession,
    UnknownSeller,
    list_profiles,
    load_profile,
    login,
    resolve_seller_id,
    update_profile,
)

__all__ = [
    "IntakePartInput",
    "IntakeResult",
    "ingest_part",
    "ingest_parts_parallel",
    "save_intake_to_db",
    "SellerCard",
    "build_evidence_block",
    "build_request_message",
    "get_system_prompt",
    "OCRResult",
    "clear_tool_cache",
    "ocr",
    "parse_sheet",
    "read_image",
    "translate_note",
    "AccessDenied",
    "ChatAccessPolicy",
    "ChatTools",
    "SemanticLayer",
    "Glossary",
    "get_glossary",
    "IntentRequest",
    "IntentResult",
    "execute_intent",
    "compute_metrics",
    "get_prompt_prefix",
    "invalidate_prompt_prefix",
    "SellerSession",
    "UnknownSeller",
    "list_profiles",
    "load_profile",
    "login",
    "resolve_seller_id",
    "update_profile",
]
