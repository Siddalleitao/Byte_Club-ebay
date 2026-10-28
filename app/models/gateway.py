"""LLM Gateway — two-tier model access with caching, fixture replay, and cost logging.

Architecture (Rule g): only orchestration calls this layer.

Tiers:
  MAIN_MODEL  — claude-sonnet-5-5 (default) — vision, summary
  FAST_MODEL  — claude-haiku-5-5 (default) — routing, guard, parsing

Features:
  - Per-call timeout (env LLM_TIMEOUT_SECONDS, default 30)
  - One retry with JSON repair on parse failure
  - Token + cost logging per part
  - Response cache keyed by SHA-256 of serialized input
  - FIXTURE_MODE=true replays recorded JSON from data/fixtures — no API key needed
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.domain.models import LLMCallLog

# ── Configuration ─────────────────────────────────────────────

MAIN_MODEL = os.getenv("MAIN_MODEL", "claude-sonnet-5-5")
FAST_MODEL = os.getenv("FAST_MODEL", "claude-haiku-5-5")

FIXTURE_MODE = os.getenv("FIXTURE_MODE", "true").lower() in ("true", "1", "yes")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

# Per-1K-token cost rates (env-configurable, defaults are mock estimates)
MAIN_INPUT_COST = float(os.getenv("MAIN_MODEL_INPUT_COST", "0.003"))
MAIN_OUTPUT_COST = float(os.getenv("MAIN_MODEL_OUTPUT_COST", "0.015"))
FAST_INPUT_COST = float(os.getenv("FAST_MODEL_INPUT_COST", "0.0008"))
FAST_OUTPUT_COST = float(os.getenv("FAST_MODEL_OUTPUT_COST", "0.004"))

# Fixture root
FIXTURE_ROOT = Path(os.getenv("FIXTURE_ROOT", "data/fixtures"))

# In-memory cache (process lifetime)
_CACHE: dict[str, dict[str, Any]] = {}

# Per-part call logs (accumulated in memory; orchestration flushes them to the llm_calls table)
_CALL_LOGS: list[LLMCallLog] = []

# The part a call is for, when the caller did not pass part_id (set by orchestration per job).
_CURRENT_PART: ContextVar[str | None] = ContextVar("partproof_llm_part", default=None)


@contextmanager
def attributed_to(part_id: str | None) -> Iterator[None]:
    """Attribute every model call made inside the block to `part_id` (token log, cost per listing)."""
    token = _CURRENT_PART.set(part_id)
    try:
        yield
    finally:
        _CURRENT_PART.reset(token)


class ModelTier(str, Enum):
    MAIN = "MAIN"
    FAST = "FAST"


@dataclass
class GatewayConfig:
    main_model: str = field(default_factory=lambda: MAIN_MODEL)
    fast_model: str = field(default_factory=lambda: FAST_MODEL)
    fixture_mode: bool = field(default_factory=lambda: FIXTURE_MODE)
    timeout_seconds: int = field(default_factory=lambda: LLM_TIMEOUT_SECONDS)
    fixture_root: Path = field(default_factory=lambda: FIXTURE_ROOT)

    def model_name(self, tier: ModelTier) -> str:
        return self.main_model if tier is ModelTier.MAIN else self.fast_model

    def cost_rates(self, tier: ModelTier) -> tuple[float, float]:
        if tier is ModelTier.MAIN:
            return MAIN_INPUT_COST, MAIN_OUTPUT_COST
        return FAST_INPUT_COST, FAST_OUTPUT_COST


# ── Helpers ───────────────────────────────────────────────────


def _input_hash(model_name: str, messages: list[dict], system: str | None) -> str:
    """SHA-256 of the fully serialized call input.

    Random evidence block IDs (ev_XXXX) are normalised to 'ev_XXXX' so
    that the same evidence content produces the same hash regardless of
    the random ID generated per request. This ensures fixture replay
    works correctly.
    """
    import re

    payload = json.dumps(
        {"model": model_name, "system": system, "messages": messages},
        sort_keys=True,
        ensure_ascii=False,
    )
    # Normalise random evidence IDs so the hash is deterministic
    payload = re.sub(r"ev_[a-f0-9]{8}", "ev_XXXXXXXX", payload)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _repair_json(text: str) -> str:
    """Attempt to fix common LLM JSON issues: markdown fences, trailing commas."""
    cleaned = text.strip()
    # Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first fence line
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove trailing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    # Remove trailing commas before } or ]
    import re

    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def _fixture_path(config: GatewayConfig, tier: ModelTier, key: str) -> Path:
    tier_dir = config.fixture_root / tier.value.lower()
    return tier_dir / f"{key}.json"


def _load_fixture(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Public API ────────────────────────────────────────────────


def call(
    *,
    tier: ModelTier,
    messages: list[dict[str, str]],
    system: str | None = None,
    part_id: str | None = None,
    config: GatewayConfig | None = None,
    response_format: str | None = None,  # "json" to request JSON output
) -> dict[str, Any]:
    """Call the LLM (or replay a fixture).

    Returns a dict with keys:
        content    — the raw text response
        parsed     — parsed JSON dict (only if response_format == "json")
        usage      — {"input_tokens", "output_tokens"}
        cached     — bool
        model      — model name used
        tier       — "MAIN" | "FAST"
    """
    config = config or GatewayConfig()
    model_name = config.model_name(tier)
    key = _input_hash(model_name, messages, system)
    part_id = part_id or _CURRENT_PART.get()

    start = time.monotonic()

    # ── Cache hit ──
    if key in _CACHE:
        cached_result = _CACHE[key]
        duration_ms = int((time.monotonic() - start) * 1000)
        log = LLMCallLog(
            model_tier=tier.value,
            model_name=model_name,
            input_tokens=cached_result.get("usage", {}).get("input_tokens", 0),
            output_tokens=cached_result.get("usage", {}).get("output_tokens", 0),
            cached=True,
            duration_ms=duration_ms,
            part_id=part_id,
        )
        _log_call(log)
        return {**cached_result, "cached": True}

    # ── Fixture mode ──
    if config.fixture_mode:
        fixture = _load_fixture(_fixture_path(config, tier, key))
        if fixture is None:
            raise FileNotFoundError(
                f"FIXTURE_MODE is on but no fixture found for {tier.value} "
                f"hash {key[:12]}… at {_fixture_path(config, tier, key)}"
            )
        # Store in cache for subsequent identical calls
        _CACHE[key] = fixture
        duration_ms = int((time.monotonic() - start) * 1000)
        log = LLMCallLog(
            model_tier=tier.value,
            model_name=model_name,
            input_tokens=fixture.get("usage", {}).get("input_tokens", 0),
            output_tokens=fixture.get("usage", {}).get("output_tokens", 0),
            cached=True,
            fixture=True,
            duration_ms=duration_ms,
            part_id=part_id,
        )
        _log_call(log)
        return {**fixture, "cached": True}

    # ── Real API call (not implemented in skeleton) ──
    # When FIXTURE_MODE=false, this is where the actual SDK call would go.
    # For the hackathon skeleton we raise so it's clear the path is unimplemented.
    raise NotImplementedError(
        "Real LLM API calls are not wired in this skeleton. "
        "Set FIXTURE_MODE=true (default) and provide fixtures in data/fixtures/, "
        "or implement the provider call in app/models/provider.py."
    )


def call_json(
    *,
    tier: ModelTier,
    messages: list[dict[str, str]],
    system: str | None = None,
    part_id: str | None = None,
    config: GatewayConfig | None = None,
) -> dict[str, Any]:
    """Call the LLM expecting a JSON response.

    Implements the one-retry-with-repair policy:
    1. Parse the response as JSON.
    2. If it fails, run _repair_json and retry the parse once.
    3. If still failing, raise ValueError with the raw text for debugging.
    """
    result = call(
        tier=tier,
        messages=messages,
        system=system,
        part_id=part_id,
        config=config,
        response_format="json",
    )
    raw = result.get("content", "")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # One retry with repair
        repaired = _repair_json(raw)
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM returned unparseable JSON after repair. Error: {exc}. Raw:\n{raw[:500]}"
            ) from exc

    result["parsed"] = parsed
    return result


# ── Cost / token logging ──────────────────────────────────────


def _log_call(log: LLMCallLog) -> None:
    """Compute costs and append to the in-memory log."""
    in_rate, out_rate = (
        GatewayConfig().cost_rates(ModelTier(log.model_tier))
    )
    if log.cached and not log.fixture:  # an in-process cache hit sends nothing to the API
        log.input_cost = log.output_cost = log.total_cost = 0.0
    else:
        log.input_cost = (log.input_tokens / 1000) * in_rate
        log.output_cost = (log.output_tokens / 1000) * out_rate
        log.total_cost = log.input_cost + log.output_cost
    _CALL_LOGS.append(log)


def get_call_logs(part_id: str | None = None) -> list[LLMCallLog]:
    """Retrieve call logs, optionally filtered by part_id."""
    if part_id is None:
        return list(_CALL_LOGS)
    return [l for l in _CALL_LOGS if l.part_id == part_id]


def clear_call_logs() -> None:
    _CALL_LOGS.clear()


def clear_cache() -> None:
    _CACHE.clear()


# ── Fixture recorder (for building fixtures during development) ──


def record_fixture(
    *,
    tier: ModelTier,
    messages: list[dict[str, str]],
    system: str | None,
    content: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    config: GatewayConfig | None = None,
) -> Path:
    """Write a fixture JSON file so FIXTURE_MODE can replay it later.

    Use this during development to capture real API responses, then
    run the app with FIXTURE_MODE=true for demos.
    """
    config = config or GatewayConfig()
    model_name = config.model_name(tier)
    key = _input_hash(model_name, messages, system)
    path = _fixture_path(config, tier, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "content": content,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model_name,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
