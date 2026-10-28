"""API tests for the chat concierge, streamed replies and the supervised pipeline."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
from tests.semantic_fixtures import OTHER_SKU, two_seller_db  # noqa: F401

logging.getLogger("app.semantic.intake_agent").setLevel(logging.CRITICAL)


@pytest.fixture
def client(two_seller_db, monkeypatch):
    monkeypatch.setattr(api, "_DB_PATH", two_seller_db["db_path"])
    monkeypatch.setattr(api, "_SESSIONS", {})
    monkeypatch.setattr(api, "_PIPELINE", {})
    return TestClient(api.app)


@pytest.fixture
def headers(client):
    token = client.post("/sessions", json={"display_name": "Noor"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_chat_refuses_fits_all_in_the_sellers_language(client, headers):
    reply = client.post("/me/chat", headers=headers,
                        json={"message": "اكتب أنها يناسب جميع كورولا", "sku": "M-07"}).json()["reply"]
    assert reply["lang"] == "ar" and reply["intent"] == "policy_request"
    assert "رقم قطعة موثّق" in reply["text"]


def test_chat_stream_acknowledges_first(client, headers):
    with client.stream("POST", "/me/chat/stream", headers=headers,
                       json={"message": "Why is M-07 not ready?", "sku": "M-07"}) as resp:
        events = [json.loads(line) for line in resp.iter_lines() if line]
    assert events[0]["type"] == "ack"
    assert events[-1]["type"] == "done" and events[-1]["reply"]["intent"] == "explain"
    assert any(e["type"] == "token" for e in events)


def test_pipeline_answers_and_approval(client, headers):
    run = client.post("/me/pipeline/run", headers=headers).json()
    assert "M-07" in run["queued"] and OTHER_SKU not in run["queued"]

    # Drive M-07 through the supervisor directly (the queue runs in the app's loop).
    state = client.get("/me/pipeline/M-07", headers=headers)
    assert state.status_code == 200

    assert client.post("/me/parts/M-07/approve", headers=headers).status_code == 409  # not at CP7
    assert client.get(f"/me/pipeline/{OTHER_SKU}", headers=headers).status_code == 404
    assert client.post(f"/me/parts/{OTHER_SKU}/approve", headers=headers).status_code == 404


def test_chat_needs_a_session(client):
    assert client.post("/me/chat", json={"message": "hi"}).status_code == 401
