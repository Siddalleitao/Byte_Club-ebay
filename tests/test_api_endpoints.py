"""Tests for the FastAPI gateway endpoints.

Verifies that the API gateway serves the data the UI needs:
  - /health
  - /sellers/{id} (see test_semantic_api.py)
  - /parts
  - /parts/{id}
  - /parts/{id}/questions
  - /reference
  - /upload

Exit test: M-01 and M-07 appear in /parts response.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "partproof-api"


# Seller profile and session endpoints are covered in tests/test_semantic_api.py.


class TestParts:
    def test_list_parts_returns_all(self, client):
        resp = client.get("/parts")
        assert resp.status_code == 200
        parts = resp.json()["parts"]
        assert len(parts) >= 10

    def test_list_parts_includes_m01(self, client):
        """Exit test: M-01 appears in /parts."""
        resp = client.get("/parts")
        parts = resp.json()["parts"]
        skus = {p["sku"] for p in parts}
        assert "M-01" in skus

    def test_list_parts_includes_m07(self, client):
        """Exit test: M-07 appears in /parts."""
        resp = client.get("/parts")
        parts = resp.json()["parts"]
        skus = {p["sku"] for p in parts}
        assert "M-07" in skus

    def test_get_part_by_sku(self, client):
        resp = client.get("/parts/M-07")
        assert resp.status_code == 200
        part = resp.json()["part"]
        assert part["sku"] == "M-07"
        assert part["make"] == "Toyota"

    def test_get_part_not_found(self, client):
        resp = client.get("/parts/nonexistent")
        assert resp.status_code == 404

    def test_m01_has_approved_badge(self, client):
        resp = client.get("/parts")
        parts = resp.json()["parts"]
        m01 = [p for p in parts if p["sku"] == "M-01"][0]
        assert m01["badge"] == "Approved"

    def test_m07_has_specialist_badge(self, client):
        resp = client.get("/parts")
        parts = resp.json()["parts"]
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        assert m07["badge"] == "Specialist review required"


class TestQuestions:
    def test_m07_has_questions(self, client):
        resp = client.get("/parts/M-07/questions")
        assert resp.status_code == 200
        questions = resp.json()["questions"]
        assert len(questions) > 0
        edge_cases = {q["edge_case"] for q in questions}
        assert "E12" in edge_cases
        assert "E16" in edge_cases

    def test_m01_has_no_questions(self, client):
        resp = client.get("/parts/M-01/questions")
        assert resp.status_code == 200
        questions = resp.json()["questions"]
        assert len(questions) == 0


class TestReference:
    def test_reference_compat(self, client):
        resp = client.get("/reference")
        assert resp.status_code == 200
        data = resp.json()
        assert data["compat_ref"]["version"] == "v0.1"
        assert data["compat_ref"]["rows"] > 0

    def test_reference_checklist(self, client):
        resp = client.get("/reference")
        data = resp.json()
        assert data["checklist"]["version"] == "0.1"
        assert data["checklist"]["date"] == "2026-10-27"
        assert "TEST" in data["checklist"]["purpose"]


class TestUpload:
    def test_upload_returns_job(self, client):
        resp = client.post("/upload", json={
            "files": [{"name": "test.jpg", "type": "photo"}]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"


class TestExitM01M07API:
    """Exit test: M-01 and M-07 appear via the API."""

    def test_m01_and_m07_in_parts_list(self, client):
        """Both M-01 and M-07 must appear in /parts."""
        resp = client.get("/parts")
        parts = resp.json()["parts"]
        skus = {p["sku"] for p in parts}
        assert "M-01" in skus, "M-01 must appear in /parts"
        assert "M-07" in skus, "M-07 must appear in /parts"

    def test_m01_clean_no_questions(self, client):
        """M-01 has no open questions."""
        resp = client.get("/parts/M-01/questions")
        assert resp.json()["questions"] == []

    def test_m07_has_open_questions_with_issues(self, client):
        """M-07 has open questions for E12 and E16."""
        resp = client.get("/parts/M-07/questions")
        questions = resp.json()["questions"]
        edge_cases = {q["edge_case"] for q in questions}
        assert "E12" in edge_cases, "M-07 must have E12 question"
        assert "E16" in edge_cases, "M-07 must have E16 question"
