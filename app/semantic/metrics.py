"""Metric definitions — one source of truth (TRD 4).

Definitions (label, description, formula, unit) live in
data/reference/metric_definitions.yaml under `semantic_metrics`. Each name
has exactly one implementation here, computed over the logged-in seller's
records through the semantic layer. The chat, dashboard and metrics panel
all call `compute_metrics`, so the same number appears everywhere.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.domain.enums import AttributeStatus
from app.semantic.entity_model import SemanticLayer

DEFINITIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "reference" / "metric_definitions.yaml"
)


class MetricDefinition(BaseModel):
    name: str
    label_en: str
    label_ar: str
    description: str
    formula: str
    unit: str


class MetricValue(BaseModel):
    name: str
    value: float | int | None
    unit: str
    label_en: str
    label_ar: str
    description: str


# ── Implementations ─────────────────────────────────────────


def _ready_count(layer: SemanticLayer) -> int:
    latest: dict[str | None, bool] = {}
    for result in layer.checklist_history():  # ordered by created_at
        latest[result.sku] = result.passed
    return sum(1 for passed in latest.values() if passed)


def _open_questions(layer: SemanticLayer) -> int:
    return len(layer.questions(status="open"))


def _conflict_rate(layer: SemanticLayer) -> float:
    attrs = layer.attributes()
    if not attrs:
        return 0.0
    conflicting = sum(1 for a in attrs if a.status == AttributeStatus.CONFLICTING)
    return round(conflicting / len(attrs), 4)


def _time_to_ready(layer: SemanticLayer) -> float | None:
    created = {p.sku: p.created_at for p in layer.parts()}
    first_ready: dict[str | None, str] = {}
    for result in layer.checklist_history():
        if result.passed and result.sku not in first_ready:
            first_ready[result.sku] = result.evaluated_at
    hours = [
        _hours_between(created[sku], ready_at)
        for sku, ready_at in first_ready.items()
        if sku in created
    ]
    return round(statistics.median(hours), 2) if hours else None


def _unsupported_claims(layer: SemanticLayer) -> int:
    return sum(1 for c in layer.claims() if not c.supported)


_IMPLEMENTATIONS: dict[str, Callable[[SemanticLayer], float | int | None]] = {
    "ready_count": _ready_count,
    "open_questions": _open_questions,
    "conflict_rate": _conflict_rate,
    "time_to_ready": _time_to_ready,
    "unsupported_claims": _unsupported_claims,
}


# ── Public API ──────────────────────────────────────────────


@lru_cache(maxsize=1)
def load_definitions() -> dict[str, MetricDefinition]:
    """Load definitions and check they match the implementations one-to-one."""
    data = yaml.safe_load(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    defs = {d["name"]: MetricDefinition(**d) for d in data.get("semantic_metrics", [])}
    if set(defs) != set(_IMPLEMENTATIONS):
        raise RuntimeError(
            "metric_definitions.yaml and app/semantic/metrics.py disagree: "
            f"defined={sorted(defs)}, implemented={sorted(_IMPLEMENTATIONS)}"
        )
    return defs


def compute_metric(layer: SemanticLayer, name: str) -> MetricValue:
    d = load_definitions()[name]
    return MetricValue(
        name=name,
        value=_IMPLEMENTATIONS[name](layer),
        unit=d.unit,
        label_en=d.label_en,
        label_ar=d.label_ar,
        description=d.description,
    )


def compute_metrics(layer: SemanticLayer) -> dict[str, MetricValue]:
    return {name: compute_metric(layer, name) for name in load_definitions()}


def _hours_between(start: str, end: str) -> float:
    """Hours between two timestamps. Naive timestamps (SQLite datetime('now')) are UTC."""
    def parse(ts: str) -> datetime:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    return (parse(end) - parse(start)).total_seconds() / 3600
