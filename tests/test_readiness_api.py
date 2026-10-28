"""API tests for CP4, CP6, draft rendering, startup reference checks and chat propagation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
from app.data.database import get_connection
from tests.semantic_fixtures import OTHER_SKU, two_seller_db  # noqa: F401


@pytest.fixture
def client(two_seller_db, monkeypatch):
    monkeypatch.setattr(api, "_DB_PATH", two_seller_db["db_path"])
    monkeypatch.setattr(api, "_SESSIONS", {})
    return TestClient(api.app)


@pytest.fixture
def headers(client):
    token = client.post("/sessions", json={"display_name": "Noor"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_startup_verifies_and_records_reference_versions(two_seller_db, monkeypatch):
    monkeypatch.setattr(api, "_DB_PATH", two_seller_db["db_path"])
    with TestClient(api.app) as c:
        refs = c.app.state.references
    assert refs["compat_ref"]["version"] == "v0.1" and refs["compat_ref"]["rows"] == 16
    assert refs["checklist"]["version"] == "0.1"
    conn = get_connection(two_seller_db["db_path"])
    names = [r["name"] for r in conn.execute("SELECT name FROM reference_versions ORDER BY id")]
    conn.close()
    assert names == ["compat_ref", "checklist"]


def test_compatibility_endpoint_returns_donor_as_separate_fact(client, headers):
    data = client.get("/me/parts/M-01/compatibility", headers=headers).json()["compatibility"]
    assert data["status"] == "verified" and data["claim_type"] == "verified_compat"
    assert data["fitments"][0]["model"] == "Corolla" and data["ref_version"] == "v0.1"
    assert data["donor"]["label"].startswith("Donor vehicle (descriptive only")


def test_part_not_in_reference(client, headers):
    data = client.get("/me/parts/M-15/compatibility", headers=headers).json()["compatibility"]
    assert data["status"] == "not_verified" and data["label"] == "compatibility not verified"
    assert data["edge_case"] == "E33"


def test_readiness_endpoint(client, headers):
    data = client.get("/me/parts/M-17/readiness", headers=headers).json()
    assert data["status"] == "Specialist review required"
    data = client.get("/me/parts/M-01/readiness", headers=headers).json()
    assert data["status"] == "Information missing"
    assert len(data["top_actions"]) <= 3 and data["top_actions"] == data["actions"][:3]


def test_other_sellers_part_is_404(client, headers):
    for path in (f"/me/parts/{OTHER_SKU}/readiness", f"/me/parts/{OTHER_SKU}/compatibility"):
        assert client.get(path, headers=headers).status_code == 404
    assert client.post(f"/me/parts/{OTHER_SKU}/draft", headers=headers).status_code == 404


def test_dashboard_lists_every_part(client, headers):
    parts = client.get("/me/readiness", headers=headers).json()["parts"]
    assert len(parts) == 20 and OTHER_SKU not in {p["sku"] for p in parts}


def test_chat_correction_propagates_in_one_step(client, headers):
    assert client.post("/me/parts/M-04/draft", headers=headers).status_code == 200
    resp = client.post("/me/chat/intent", headers=headers, json={
        "intent": "correction", "sku": "M-04", "field": "side",
        "text": "Change M-04 to right side", "value": "right",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["accepted"] is True
    propagation = body["propagation"]
    assert propagation["recomputed"] == ["compat_lookup", "item_specifics", "title", "checklist"]
    assert "Right" in propagation["changes"]["title"]["after"]["title"]

    explain = client.post("/me/chat/intent", headers=headers, json={"intent": "explain", "sku": "M-04"}).json()
    assert explain["result"]["readiness_status"] == "Information missing"
    # No intake ran for M-04 here, so its part number is not in the ledger yet (C03,
    # a photo, ranks first); the side/reference disagreement is the next identity action.
    keys = [a["key"] for a in explain["result"]["next_actions"]]
    assert "check_side_vs_reference" in keys
