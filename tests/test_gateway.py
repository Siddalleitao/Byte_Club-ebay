"""Tests for the LLM gateway — fixture mode, caching, JSON repair."""

import json
from pathlib import Path

import pytest

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
from app.models.gateway import _input_hash, _repair_json


@pytest.fixture(autouse=True)
def _clean():
    clear_cache()
    clear_call_logs()
    yield
    clear_cache()
    clear_call_logs()


def test_input_hash_deterministic():
    messages = [{"role": "user", "content": "hello"}]
    h1 = _input_hash("test-model", messages, "system")
    h2 = _input_hash("test-model", messages, "system")
    assert h1 == h2


def test_input_hash_changes_with_different_input():
    m1 = [{"role": "user", "content": "hello"}]
    m2 = [{"role": "user", "content": "world"}]
    assert _input_hash("test-model", m1, None) != _input_hash("test-model", m2, None)


def test_repair_json_strips_fences():
    raw = '```json\n{"key": "value"}\n```'
    assert _repair_json(raw) == '{"key": "value"}'


def test_repair_json_removes_trailing_commas():
    raw = '{"a": 1, "b": [1, 2,],}'
    repaired = _repair_json(raw)
    assert json.loads(repaired) == {"a": 1, "b": [1, 2]}


def test_fixture_mode_replays_recorded_response(tmp_path):
    messages = [{"role": "user", "content": "extract attributes"}]
    config = GatewayConfig(fixture_mode=True, fixture_root=tmp_path)

    record_fixture(
        tier=ModelTier.FAST,
        messages=messages,
        system="You are a parser.",
        content='{"side": "left"}',
        input_tokens=100,
        output_tokens=20,
        config=config,
    )

    result = call(
        tier=ModelTier.FAST,
        messages=messages,
        system="You are a parser.",
        config=config,
    )
    assert result["content"] == '{"side": "left"}'
    assert result["cached"] is True


def test_call_json_parses_and_repairs(tmp_path):
    messages = [{"role": "user", "content": "parse this"}]
    config = GatewayConfig(fixture_mode=True, fixture_root=tmp_path)

    # Record a fixture with markdown-fenced JSON
    record_fixture(
        tier=ModelTier.FAST,
        messages=messages,
        system=None,
        content='```json\n{"part_number": "ABC123", "side": "left",}\n```',
        input_tokens=50,
        output_tokens=30,
        config=config,
    )

    result = call_json(
        tier=ModelTier.FAST,
        messages=messages,
        system=None,
        config=config,
    )
    assert result["parsed"]["part_number"] == "ABC123"
    assert result["parsed"]["side"] == "left"


def test_call_json_raises_on_unparseable(tmp_path):
    messages = [{"role": "user", "content": "bad data"}]
    config = GatewayConfig(fixture_mode=True, fixture_root=tmp_path)

    record_fixture(
        tier=ModelTier.FAST,
        messages=messages,
        system=None,
        content="This is not JSON at all.",
        config=config,
    )

    with pytest.raises(ValueError, match="unparseable JSON"):
        call_json(
            tier=ModelTier.FAST,
            messages=messages,
            system=None,
            config=config,
        )


def test_call_logs_recorded(tmp_path):
    messages = [{"role": "user", "content": "hello"}]
    config = GatewayConfig(fixture_mode=True, fixture_root=tmp_path)

    record_fixture(
        tier=ModelTier.MAIN,
        messages=messages,
        system=None,
        content="summary text",
        input_tokens=200,
        output_tokens=50,
        config=config,
    )

    call(tier=ModelTier.MAIN, messages=messages, system=None, part_id="p1", config=config)

    logs = get_call_logs(part_id="p1")
    assert len(logs) == 1
    assert logs[0].model_tier == "MAIN"
    assert logs[0].input_tokens == 200
    assert logs[0].output_tokens == 50
    assert logs[0].total_cost > 0


def test_cache_hit_does_not_replay_fixture_file(tmp_path):
    messages = [{"role": "user", "content": "cache me"}]
    config = GatewayConfig(fixture_mode=True, fixture_root=tmp_path)

    record_fixture(
        tier=ModelTier.FAST,
        messages=messages,
        system=None,
        content='{"ok": true}',
        input_tokens=10,
        output_tokens=5,
        config=config,
    )

    r1 = call(tier=ModelTier.FAST, messages=messages, system=None, config=config)
    r2 = call(tier=ModelTier.FAST, messages=messages, system=None, config=config)
    assert r1["content"] == r2["content"]
    assert r2["cached"] is True


def test_fixture_missing_raises():
    config = GatewayConfig(fixture_mode=True, fixture_root=Path("data/fixtures"))
    messages = [{"role": "user", "content": "nonexistent fixture call"}]
    with pytest.raises(FileNotFoundError, match="FIXTURE_MODE"):
        call(tier=ModelTier.FAST, messages=messages, config=config)
