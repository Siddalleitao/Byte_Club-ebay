"""LLM model layer package.

Rule (g): only orchestration calls this layer.
"""

from app.models.gateway import (
    GatewayConfig,
    ModelTier,
    call,
    call_json,
    clear_cache,
    clear_call_logs,
    get_call_logs,
    record_fixture,
)

__all__ = [
    "GatewayConfig",
    "ModelTier",
    "call",
    "call_json",
    "clear_cache",
    "clear_call_logs",
    "get_call_logs",
    "record_fixture",
]
