"""Model calls with timeouts and one retry, then a deterministic fallback (TRD 5, E46).

Only orchestration calls the model layer (Rule g). Every call goes through
`call_with_policy`: a per-call timeout, one retry, and then the caller's
template (or Missing). A model that never answers can delay a reply by at
most `timeout * 2`; it can never block the workflow.

`ModelClient` is the seam: production uses the gateway (fixture replay or a
real provider); tests inject fakes to exercise slow and failing models.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from app.models.gateway import ModelTier, call as gateway_call

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Chat budget: first token under 1.5 s, so a single attempt gets less than that.
CHAT_TIMEOUT_S = float(os.getenv("CHAT_LLM_TIMEOUT_SECONDS", "1.2"))
BACKGROUND_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm")


class ModelClient(Protocol):
    def complete(self, *, tier: ModelTier, system: str, messages: list[dict[str, str]]) -> str: ...


class GatewayModel:
    """The model layer via the gateway (fixture replay in FIXTURE_MODE)."""

    def complete(self, *, tier: ModelTier, system: str, messages: list[dict[str, str]]) -> str:
        return gateway_call(tier=tier, system=system, messages=messages)["content"]


@dataclass
class Outcome:
    ok: bool
    value: Any = None
    attempts: int = 0
    error: str | None = None
    elapsed_ms: float = 0.0
    fallback_used: bool = False


def call_with_policy(
    fn: Callable[[], T],
    *,
    timeout_s: float,
    retries: int = 1,
    validate: Callable[[T], Any] | None = None,
) -> Outcome:
    """Run `fn` with a timeout; retry once on timeout, error or invalid output (E46).

    `validate` may transform the raw value (e.g. parse JSON) and raises on
    invalid output, which counts as a failed attempt.
    """
    start = time.perf_counter()
    error = None
    for attempt in range(1, retries + 2):
        future = _EXECUTOR.submit(fn)
        try:
            raw = future.result(timeout=timeout_s)
            value = validate(raw) if validate else raw
            return Outcome(True, value, attempt, None, _ms(start))
        except FutureTimeout:
            error = f"timeout after {timeout_s}s"
        except Exception as exc:  # model, fixture or validation failure
            error = f"{type(exc).__name__}: {exc}"
        logger.info("model attempt %d failed: %s", attempt, error)
    return Outcome(False, None, retries + 1, error, _ms(start), fallback_used=True)


def parse_json_object(raw: str) -> dict:
    """Parse a model's JSON reply (one fence-stripping repair), or raise."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
