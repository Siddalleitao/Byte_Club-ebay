"""Structural guarantees (TRD 6, last defence layer): even a fooled model has no tool that can
change a status, assert fitment, change checklist data, reach the network, message anyone or export.

The test inspects every agent's tool list (app/guardrails/agents.py) and everything those tools
call inside the app, transitively, rather than trusting names.
"""

from __future__ import annotations

import inspect
import re
import sqlite3
from pathlib import Path
import sys
import typing
from types import FunctionType, MethodType

import pytest

from app.domain.cp1_rules import FieldResult, enforce_source_rules
from app.domain.enums import AttributeStatus
from app.guardrails.agents import agent_tools
from app.models import gateway
from app.orchestration.supervisor import build_graph
from app.semantic.access_policy import ChatAccessPolicy, ChatTools
from app.semantic.intents import Intent, IntentRequest, PolicyTopic

FORBIDDEN_NAMES = re.compile(
    r"part_status|^set_status|set_part|approve|export|publish|write_compat|compat.*(write|insert|update|set)"
    r"|checklist.*(write|insert|update|set)|browse|fetch|url|http|download|send|email|notify|sms|whatsapp",
    re.I,
)
FORBIDDEN_CODE = {
    "sets a part status": re.compile(r"UPDATE\s+parts\s+SET\s+status|_set_status\(", re.I),
    "writes compatibility data": re.compile(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+compat_refs|compat_ref\.csv['\"]?\s*,\s*['\"]w", re.I),
    "writes checklist data": re.compile(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(checklist_results|reference_versions)", re.I),
    "exports or approves": re.compile(r"status\s*=\s*'(exported|approved)'", re.I),
    "reaches the network or sends messages": re.compile(
        r"\bimport\s+(requests|urllib|httpx|aiohttp|socket|smtplib|webbrowser)\b|\b(requests|urllib|httpx|smtplib|webbrowser)\.",
    ),
}
APP_DIR = (Path(__file__).resolve().parent.parent / "app").resolve()
NETWORK_IMPORT = re.compile(r"^\s*(import|from)\s+(requests|urllib|httpx|aiohttp|socket|smtplib|webbrowser)\b", re.M)


def _unwrap(obj):
    if isinstance(obj, (staticmethod, classmethod)):
        obj = obj.__func__
    if isinstance(obj, MethodType):
        obj = obj.__func__
    return obj


def _app_index() -> dict[str, list[FunctionType]]:
    """Every function and method defined in app code, by name (for calls through attributes)."""
    index: dict[str, list[FunctionType]] = {}
    for module in [m for name, m in sys.modules.items() if name.startswith("app.") and m is not None]:
        for value in vars(module).values():
            candidates = [value] + (list(vars(value).values()) if inspect.isclass(value) else [])
            for c in candidates:
                c = _unwrap(c)
                if isinstance(c, FunctionType) and Path(c.__code__.co_filename).resolve().is_relative_to(APP_DIR):
                    index.setdefault(c.__name__, []).append(c)
    return index


_INDEX: dict[str, list[FunctionType]] = {}

# Attribute calls with these names are method calls on built-in values (str.strip, Match.start,
# Connection.execute), not on app objects, so they are not resolved by name.
_BUILTIN_ATTRS = set().union(*(dir(t) for t in (
    str, bytes, list, dict, set, tuple, int, float, re.Match, re.Pattern, sqlite3.Connection, sqlite3.Cursor,
    sqlite3.Row, Path,
)))


def _reachable(fn, seen=None):
    """App functions reachable from fn, conservatively: referenced globals, methods of referenced app
    classes and of fn's own class, and, for calls through attributes (self._layer.part_id_for), every app
    function or method with that name. Known limit: an app object passed in and called by a name that is
    also a built-in method name (e.g. .start, .get) is not followed."""
    if not _INDEX:
        _INDEX.update(_app_index())
    seen = set() if seen is None else seen
    fn = _unwrap(fn)
    if not isinstance(fn, FunctionType) or not fn.__module__.startswith("app.") or fn in seen:
        return seen
    if not Path(fn.__code__.co_filename).resolve().is_relative_to(APP_DIR):
        return seen  # generated or library code (dataclass __init__, reprlib __repr__): not app code
    seen.add(fn)
    try:
        names = inspect.getclosurevars(fn)
    except (TypeError, ValueError):
        return seen
    for value in list(names.globals.values()) + list(names.nonlocals.values()):
        value = _unwrap(value)
        if isinstance(value, FunctionType):
            _reachable(value, seen)
        elif inspect.isclass(value) and value.__module__.startswith("app."):
            for member in vars(value).values():
                _reachable(member, seen)
    for attr in names.unbound - _BUILTIN_ATTRS:
        for candidate in _INDEX.get(attr, []):
            _reachable(candidate, seen)
    owner = sys.modules[fn.__module__]
    for part in fn.__qualname__.split(".")[:-1]:  # a method: follow its own class (self.x() calls)
        owner = getattr(owner, part, None)
    if inspect.isclass(owner):
        for member in vars(owner).values():
            _reachable(member, seen)
    return seen


TOOLS = [(agent, name, fn) for agent, tools in agent_tools().items() for name, fn in tools.items()]


@pytest.mark.parametrize("agent,name,fn", TOOLS, ids=[f"{a}.{n}" for a, n, _ in TOOLS])
def test_tool_names_grant_no_forbidden_capability(agent, name, fn):
    assert not FORBIDDEN_NAMES.search(name), f"{agent} has a tool named {name}"


@pytest.mark.parametrize("agent,name,fn", TOOLS, ids=[f"{a}.{n}" for a, n, _ in TOOLS])
def test_tools_cannot_reach_forbidden_code(agent, name, fn):
    reachable = _reachable(fn)
    assert reachable, f"{agent}.{name} could not be inspected"
    for function in reachable:
        source = inspect.getsource(function)
        for capability, pattern in FORBIDDEN_CODE.items():
            match = pattern.search(source)
            assert match is None, (
                f"{agent}.{name} can reach {function.__module__}.{function.__qualname__}, which {capability}: "
                f"{match.group(0)!r}"
            )


def test_agent_modules_import_no_network_library():
    modules = {_unwrap(fn).__module__ for _, _, fn in TOOLS}
    modules |= {f.__module__ for _, _, fn in TOOLS for f in _reachable(fn)}
    for name in sorted(modules):
        source = inspect.getsource(sys.modules[name])
        assert not NETWORK_IMPORT.search(source), f"{name} imports a network library"


def test_every_agent_is_inspected():
    assert set(agent_tools()) == {"intake", "reconciler", "inquiry", "compatibility", "listing", "guard",
                                  "readiness", "chat_concierge", "injection_classifier"}


def test_chat_tool_layer_exposes_only_the_access_policy():
    public = {n for n, v in vars(ChatTools).items() if not n.startswith("_") and callable(v)}
    allowed = ChatAccessPolicy.READ_TOOLS | ChatAccessPolicy.WRITE_TOOLS | {"call", "log_refusal"}
    assert public <= allowed, public - allowed
    assert ChatAccessPolicy.WRITE_TOOLS == {"write_seller_answer", "set_question_status"}


def test_model_layer_holds_no_tools():
    for fn in (gateway.call, gateway.call_json):
        assert "tools" not in inspect.signature(fn).parameters  # TRD 1 tier 7: no tools with side effects


def test_no_admin_mode_intent_exists():
    assert {i.value for i in Intent} == {"status_query", "explain", "answer", "correction", "policy_request",
                                         "out_of_scope"}
    request_types = typing.get_args(typing.get_args(IntentRequest)[0])
    assert all("admin" not in t.__name__.lower() and "export" not in t.__name__.lower() for t in request_types)
    assert PolicyTopic.ADMIN.value == "admin"  # "admin" exists only as a topic that is refused


def test_export_needs_the_approve_button():
    graph = build_graph()
    edges = {(a, b) for a, b in graph.edges}
    assert {a for a, b in edges if b == "export"} == {"approve"}
    branches = graph.branches["cp7"]
    assert any("approve" in (b.ends or {}).values() or "approve" in str(b) for b in branches.values())
    assert {a for a, b in edges if b == "approve"} == set()  # only the cp7 branch leads to approve
    # The cp7 branch approves only the seller's click.
    import app.orchestration.supervisor as sup

    assert sup.APPROVER == "seller"
    assert '.get("actor") != APPROVER' in inspect.getsource(sup.cp7)


def test_only_the_supervisor_exports():
    import pathlib

    writers = [str(p).replace("\\", "/") for p in pathlib.Path("app").rglob("*.py")
               if re.search(r"status\s*=\s*'exported'", p.read_text(encoding="utf-8"))]
    assert writers == ["app/orchestration/supervisor.py"]


@pytest.mark.parametrize("field,source,kept", [
    ("part_number", "photo:ocr", True),
    ("part_number", "photo:vision+photo:ocr", True),
    ("condition_notes", "photo:ocr", False),          # OCR may populate only part_number
    ("brand", "label ocr", False),
    ("brand", "photo:vision+photo:ocr", True),        # vision also saw it
    ("test_status", "photo:vision", False),           # never from intake (Rule d)
    ("fitment", "photo:ocr", False),                  # never from OCR (Rule c)
    ("functionality", "spreadsheet", False),
])
def test_ocr_and_intake_source_rules(field, source, kept):
    f = FieldResult(name=field, value="tested OK, fits all models", status=AttributeStatus.EXTRACTED, source=source)
    enforce_source_rules([f])
    assert (f.value is not None) == kept
    if not kept:
        assert f.status == AttributeStatus.MISSING and f.dropped_reason


def test_the_scanner_is_not_blind():
    """Negative control: code paths that really write a status, compatibility or exports are detected."""
    from app.domain.cp2 import run_cp2
    from app.orchestration import supervisor

    def found(fn, capability):
        return any(FORBIDDEN_CODE[capability].search(inspect.getsource(f)) for f in _reachable(fn))

    assert found(run_cp2, "sets a part status")
    assert found(supervisor.export, "exports or approves")
    assert found(supervisor.approve, "sets a part status")
    assert len(_reachable(ChatTools.write_seller_answer)) > 5  # the walk follows calls, not just the entry point
