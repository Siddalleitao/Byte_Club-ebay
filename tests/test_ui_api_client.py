"""Tests for the UI API client and mock data.

Exit test: M-01 and M-07 appear on the board after an upload.
The API client must return parts with correct badges and question counts.
"""

from __future__ import annotations

import pytest

from ui.api_client import (
    APIClient,
    _mock_parts,
    _mock_seller,
    _mock_reference_data,
)


class TestMockSeller:
    """Mock seller data — Noor's profile."""

    def test_seller_is_noor(self):
        seller = _mock_seller()
        assert seller["name"] == "Noor"
        assert seller["id"] == "S-001"

    def test_seller_location_dubai(self):
        seller = _mock_seller()
        assert "Dubai" in seller["location"]

    def test_seller_languages(self):
        seller = _mock_seller()
        assert seller["language_primary"] == "Arabic"
        assert seller["language_secondary"] == "English"

    def test_seller_corridor(self):
        seller = _mock_seller()
        assert "UAE" in seller["market_corridor"]
        assert "UK" in seller["market_corridor"]

    def test_seller_marketplace(self):
        seller = _mock_seller()
        assert seller["marketplace"] == "eBay UK"


class TestMockParts:
    """Mock parts data — 20 parts from the seed pack."""

    def test_returns_20_parts(self):
        parts = _mock_parts()
        assert len(parts) == 20

    def test_m01_present(self):
        """Exit test: M-01 appears on the board."""
        parts = _mock_parts()
        m01 = [p for p in parts if p["sku"] == "M-01"]
        assert len(m01) == 1
        assert m01[0]["make"] == "Toyota"
        assert m01[0]["model"] == "Corolla"
        assert m01[0]["part_number"] == "87910-02A10"
        assert m01[0]["rack_code"] == "A1"

    def test_m07_present(self):
        """Exit test: M-07 appears on the board."""
        parts = _mock_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"]
        assert len(m07) == 1
        assert m07[0]["make"] == "Toyota"
        assert m07[0]["model"] == "Corolla"
        assert m07[0]["part_number"] == "87910-0????"
        assert m07[0]["rack_code"] == "B4"

    def test_m01_approved_badge(self):
        """M-01 has Approved badge."""
        parts = _mock_parts()
        m01 = [p for p in parts if p["sku"] == "M-01"][0]
        assert m01["badge"] == "Approved"
        assert m01["open_questions"] == 0

    def test_m07_specialist_badge(self):
        """M-07 has Specialist review required badge."""
        parts = _mock_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        # M-07 expected_status is "Guarded" → "Specialist review required" badge
        assert m07["badge"] == "Specialist review required"
        assert m07["open_questions"] > 0

    def test_m07_has_issue_types(self):
        """M-07 has planted issues listed."""
        parts = _mock_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        assert len(m07["planted_issues"]) > 0
        assert "E12" in m07["planted_issues"]
        assert "E16" in m07["planted_issues"]

    def test_m07_issue_labels_human_readable(self):
        """Issue types are human-readable labels."""
        parts = _mock_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        labels = m07["issue_types"]
        assert any("part number" in l.lower() for l in labels)
        assert any("year" in l.lower() for l in labels)

    def test_all_skus_m01_to_m20(self):
        """All 20 SKUs M-01 through M-20 are present."""
        parts = _mock_parts()
        skus = {p["sku"] for p in parts}
        for i in range(1, 21):
            assert f"M-{i:02d}" in skus

    def test_parts_have_rack_codes(self):
        """Every part has a rack code."""
        parts = _mock_parts()
        for p in parts:
            assert p["rack_code"] != ""

    def test_parts_have_asking_price(self):
        """Every part has an asking price."""
        parts = _mock_parts()
        for p in parts:
            assert p["asking_price_aed"] != ""

    def test_m17_excluded_airbag(self):
        """M-17 is the excluded airbag — Ingested status."""
        parts = _mock_parts()
        m17 = [p for p in parts if p["sku"] == "M-17"][0]
        assert m17["status"] == "Ingested"
        assert "E39" in m17["planted_issues"]


class TestMockReferenceData:
    """Mock reference data — compat ref version and checklist version."""

    def test_compat_ref_version(self):
        ref = _mock_reference_data()
        assert ref["compat_ref"]["version"] == "v0.1"
        assert ref["compat_ref"]["rows"] > 0

    def test_checklist_version(self):
        ref = _mock_reference_data()
        assert ref["checklist"]["version"] == "0.1"
        assert ref["checklist"]["date"] == "2026-10-27"

    def test_checklist_test_label(self):
        """Checklist has the TEST label."""
        ref = _mock_reference_data()
        purpose = ref["checklist"]["purpose"]
        assert "TEST" in purpose
        assert "not customs" in purpose.lower()

    def test_checklist_disclaimer(self):
        ref = _mock_reference_data()
        assert "hackathon" in ref["checklist"]["disclaimer"].lower()
        assert "synthetic" in ref["checklist"]["disclaimer"].lower()


class TestAPIClient:
    """API client — falls back to mock data when gateway is offline."""

    def test_get_seller_returns_noor(self):
        client = APIClient(base_url="http://localhost:99999")
        seller = client.get_seller()
        assert seller["name"] == "Noor"

    def test_get_parts_returns_m01_and_m07(self):
        """Exit test: M-01 and M-07 appear on the board via the API client."""
        client = APIClient(base_url="http://localhost:99999")
        parts = client.get_parts()
        skus = {p["sku"] for p in parts}
        assert "M-01" in skus
        assert "M-07" in skus

    def test_get_part_by_id(self):
        client = APIClient(base_url="http://localhost:99999")
        part = client.get_part("M-07")
        assert part is not None
        assert part["sku"] == "M-07"

    def test_get_questions_for_m07(self):
        """M-07 has open questions."""
        client = APIClient(base_url="http://localhost:99999")
        questions = client.get_questions("M-07")
        assert len(questions) > 0
        # Should have questions for E12 and E16 at minimum
        edge_cases = {q["edge_case"] for q in questions}
        assert "E12" in edge_cases
        assert "E16" in edge_cases

    def test_get_questions_for_m01(self):
        """M-01 has no open questions (clean part)."""
        client = APIClient(base_url="http://localhost:99999")
        questions = client.get_questions("M-01")
        assert len(questions) == 0

    def test_upload_returns_job(self):
        """Upload returns a job status."""
        client = APIClient(base_url="http://localhost:99999")
        result = client.upload_files([{"name": "test.jpg", "type": "photo"}])
        assert "job_id" in result
        assert "status" in result

    def test_update_seller_invalidates_cache(self):
        """Updating seller profile returns the updated data with cache flag."""
        client = APIClient(base_url="http://localhost:99999")
        result = client.update_seller("S-001", {"name": "Noor Updated"})
        assert result["name"] == "Noor Updated"
        assert result.get("_cache_invalidated") is True

    def test_health_offline(self):
        """Health check returns offline when no server."""
        client = APIClient(base_url="http://localhost:99999")
        health = client.health()
        assert health["status"] == "offline"

    def test_get_reference_data(self):
        """Reference data includes compat ref and checklist."""
        client = APIClient(base_url="http://localhost:99999")
        ref = client.get_reference_data()
        assert "compat_ref" in ref
        assert "checklist" in ref


class TestExitM01M07OnBoard:
    """Exit test: M-01 and M-07 appear on the board after an upload.

    This simulates the full flow:
      1. Upload files
      2. Board loads parts
      3. M-01 and M-07 are present with correct badges
    """

    def test_m01_and_m07_appear_after_upload(self):
        """After upload, the board shows M-01 and M-07."""
        client = APIClient(base_url="http://localhost:99999")

        # Simulate upload
        upload_result = client.upload_files([
            {"name": "spreadsheet.csv", "type": "spreadsheet"},
            {"name": "M-01_p1.jpg", "type": "photo"},
            {"name": "M-07_p1.jpg", "type": "photo"},
        ])
        assert upload_result["status"] in ("queued", "complete")

        # Board loads parts
        parts = client.get_parts()
        skus = {p["sku"] for p in parts}

        # Exit test: M-01 and M-07 appear
        assert "M-01" in skus, "M-01 must appear on the board after upload"
        assert "M-07" in skus, "M-07 must appear on the board after upload"

    def test_m01_has_approved_badge(self):
        """M-01 shows Approved badge on the board."""
        client = APIClient(base_url="http://localhost:99999")
        parts = client.get_parts()
        m01 = [p for p in parts if p["sku"] == "M-01"][0]
        assert m01["badge"] == "Approved"

    def test_m07_has_specialist_badge(self):
        """M-07 shows Specialist review required badge on the board."""
        client = APIClient(base_url="http://localhost:99999")
        parts = client.get_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        assert m07["badge"] == "Specialist review required"

    def test_m07_has_open_questions(self):
        """M-07 has open questions on the board."""
        client = APIClient(base_url="http://localhost:99999")
        parts = client.get_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        assert m07["open_questions"] > 0

    def test_m01_has_no_open_questions(self):
        """M-01 has no open questions (clean part)."""
        client = APIClient(base_url="http://localhost:99999")
        parts = client.get_parts()
        m01 = [p for p in parts if p["sku"] == "M-01"][0]
        assert m01["open_questions"] == 0

    def test_m07_shows_donor_year_conflict(self):
        """M-07 shows donor year conflict in its issue types."""
        client = APIClient(base_url="http://localhost:99999")
        parts = client.get_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        assert any("year" in issue.lower() for issue in m07["issue_types"])

    def test_m07_shows_unreadable_part_number(self):
        """M-07 shows unreadable part number in its issue types."""
        client = APIClient(base_url="http://localhost:99999")
        parts = client.get_parts()
        m07 = [p for p in parts if p["sku"] == "M-07"][0]
        assert any("part number" in issue.lower() for issue in m07["issue_types"])
