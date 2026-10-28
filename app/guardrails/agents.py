"""Agent tool registry — the only callables each agent may use (TRD 5 roster, TRD 6 layer 5).

The structural guarantee is checked against this registry
(tests/test_structural_guarantees.py): no agent tool, nor anything it calls
inside the app, can set a part status, write compatibility or checklist
data, browse, fetch a URL, send a message or export. Those are code paths
behind the UI (the supervisor and the seller's approve button).
"""

from __future__ import annotations

from collections.abc import Callable

from app.domain import compatibility, guard, readiness
from app.domain.claims import build_claims
from app.domain.glossary import Glossary
from app.orchestration import inquiry_agent, injection_classifier, listing_agent
from app.semantic import intake_tools
from app.semantic.access_policy import ChatAccessPolicy, ChatTools


def agent_tools() -> dict[str, dict[str, Callable]]:
    chat = {name: getattr(ChatTools, name)
            for name in sorted(ChatAccessPolicy.READ_TOOLS | ChatAccessPolicy.WRITE_TOOLS)}
    return {
        # Intake: read photos, sheet and notes; propose attribute candidates as JSON.
        "intake": {
            "read_image": intake_tools.read_image,
            "ocr": intake_tools.ocr,
            "parse_sheet": intake_tools.parse_sheet,
            "translate_note": intake_tools.translate_note,
        },
        # Reconciler: rules engine, no tools.
        "reconciler": {},
        # Inquiry: question templates and the glossary.
        "inquiry": {
            "question_templates": inquiry_agent.template_for,
            "glossary": Glossary.normalise,
        },
        # Compatibility: exact lookup only.
        "compatibility": {"compat_lookup": compatibility.lookup},
        # Listing: templates and the read-only claim set.
        "listing": {
            "templates": build_claims,
            "read_claim_set": listing_agent.claim_set,
        },
        # Guard: claim validator, lexicon, critic.
        "guard": {
            "claim_validator": guard.check_claim,
            "lexicon": guard.forbidden_phrases,
            "critic": listing_agent._critic,
        },
        # Readiness: the checklist engine (pure evaluation); the model only words the actions.
        "readiness": {"checklist_engine": readiness.evaluate},
        # Chat concierge: the semantic layer API only, through the access policy.
        "chat_concierge": chat,
        # Injection classifier: labels flagged text.
        "injection_classifier": {"classify": injection_classifier.classify},
    }
