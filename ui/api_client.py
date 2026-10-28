"""API client for the PartProof UI.

Rule (g): the UI talks to the API gateway only — never to an LLM or the database.

This client tries the real FastAPI gateway first. If endpoints are not yet
implemented (stub responses), it falls back to mock data derived from the
seed pack so the UI is fully functional during development.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import requests

API_URL = os.environ.get("PARTPROOF_API_URL", "http://localhost:8000")
_TIMEOUT = 5
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Fallback ports to try if the default isn't responding
_FALLBACK_URLS = [
    API_URL,
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
]


# ── Mock data loaders ───────────────────────────────────────


def _read_json(rel_path: str) -> Any:
    with open(_DATA_DIR / rel_path, encoding="utf-8") as f:
        return json.load(f)


def _read_csv(rel_path: str) -> list[dict[str, str]]:
    with open(_DATA_DIR / rel_path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _mock_seller() -> dict[str, Any]:
    ctx = _read_json("reference/seller_context.json")
    return {
        "id": ctx.get("seller_id", "S-001"),
        "name": ctx.get("name", "Noor"),
        "role": ctx.get("role", "used-auto-parts seller"),
        "phone": ctx.get("whatsapp", ""),
        "whatsapp": ctx.get("whatsapp", ""),
        "location": ctx.get("location", "Dubai, UAE"),
        "seller_type": ctx.get("seller_type", "dealer"),
        "language_primary": ctx.get("language_primary", "Arabic"),
        "language_secondary": ctx.get("language_secondary", "English"),
        "market_corridor": ctx.get("market_corridor", "UAE → UK"),
        "marketplace": ctx.get("marketplace", "eBay UK"),
        "currency_export": ctx.get("currency_export", "GBP"),
        "currency_local": ctx.get("currency_local", "AED"),
        "handling_days": ctx.get("handling_days", "Mon–Sat"),
        "rack_codes": ctx.get("rack_codes", "A1–A6, B1–B6, C1–C4"),
        "notes": ctx.get("notes", ""),
    }


def _mock_parts() -> list[dict[str, Any]]:
    """Build mock parts from spreadsheet + inventory + answer key."""
    spreadsheet = _read_csv("seed/spreadsheet.csv")
    inventory = _read_csv("seed/inventory.csv")
    donor = _read_csv("seed/donor_vehicle.csv")
    answer_key = _read_json("answer_key/answer_key.json")

    inv_map = {r["sku"]: r for r in inventory}
    donor_map = {r["sku"]: r for r in donor}
    ak_parts = answer_key.get("parts", {})

    # Map expected_status to UI badge
    badge_map = {
        "Approved": "Approved",
        "Awaiting seller": "Ready for seller review",
        "Guarded": "Specialist review required",
        "Ingested": "Information missing",
        "Reconciled": "Ready for seller review",
        "Drafted": "Ready for seller review",
        "Evaluated": "Specialist review required",
        "Awaiting approval": "Specialist review required",
        "Exported": "Approved",
    }

    parts = []
    for row in spreadsheet:
        sku = row["sku"]
        ak = ak_parts.get(sku, {})
        planted = ak.get("planted_issues", [])
        expected = ak.get("expected_status", "Ingested")

        parts.append({
            "id": f"part-{sku}",
            "sku": sku,
            "make": row.get("make", ""),
            "model": row.get("model", ""),
            "year": row.get("year", ""),
            "side": row.get("side", ""),
            "part_number": row.get("part_number", ""),
            "part_type": row.get("part_type", ""),
            "notes": row.get("notes", ""),
            "rack_code": inv_map.get(sku, {}).get("rack_code", ""),
            "asking_price_aed": inv_map.get(sku, {}).get("asking_price_aed", ""),
            "short_description": inv_map.get(sku, {}).get("short_description", ""),
            "donor_market": donor_map.get(sku, {}).get("market", ""),
            "status": expected,
            "badge": badge_map.get(expected, "Information missing"),
            "planted_issues": planted,
            "open_questions": _count_open_questions(planted),
            "issue_types": _issue_labels(planted),
            "journey": ak.get("journey", ""),
        })

    return parts


def _count_open_questions(planted: list[str]) -> int:
    """Estimate open question count from planted issues."""
    if not planted:
        return 0
    # Each planted issue typically generates one question
    # E39 (airbag exclusion) doesn't generate a question — it's excluded
    return len([e for e in planted if e != "E39"])


def _issue_labels(planted: list[str]) -> list[str]:
    """Map E-IDs to human-readable labels."""
    labels = {
        "E10": "Arabic digits in part number",
        "E11": "Side missing",
        "E12": "Unreadable part number",
        "E16": "Donor year conflict",
        "E17": "Donor model conflict",
        "E22": "Side ambiguous (driver/passenger)",
        "E24": "Damage flag, no note",
        "E28": "Partly tested, no record",
        "E30": "Test claim in note",
        "E33": "Not in reference",
        "E37": "Weight missing",
        "E39": "Excluded category (airbag)",
        "E44": "Arabic-only note",
        "E53": "Prompt injection in data",
    }
    return [labels.get(e, e) for e in planted]


def _mock_reference_data() -> dict[str, Any]:
    """Compatibility reference version and checklist version."""
    compat = _read_csv("reference/compat_ref.csv")
    return {
        "compat_ref": {
            "version": compat[0].get("ref_version", "v0.1") if compat else "v0.1",
            "rows": len(compat),
            "entries": compat,
        },
        "checklist": {
            "version": "0.1",
            "date": "2026-10-27",
            "purpose": "TEST checklist, not customs or legal guidance",
            "disclaimer": "This checklist is part of a hackathon prototype using synthetic data only. It must not be used for real customs declarations, legal compliance, or commercial trade decisions.",
        },
    }


def _auth(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


# ── API client ──────────────────────────────────────────────


class APIClient:
    """Thin client that calls the FastAPI gateway, falls back to mock data."""

    def __init__(self, base_url: str | None = None) -> None:
        self._available: bool | None = None
        # If a specific URL is given, use it directly (tests pass invalid URLs).
        # If no URL given (default), probe fallback ports to find the backend.
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        else:
            self.base_url = API_URL
            for url in _FALLBACK_URLS:
                try:
                    resp = requests.get(f"{url}/health", timeout=2)
                    if resp.status_code == 200 and resp.json().get("status") == "ok":
                        self.base_url = url
                        self._available = True
                        break
                except Exception:
                    continue

    def _get(self, path: str, token: str | None = None) -> dict | None:
        try:
            resp = requests.get(f"{self.base_url}{path}", headers=_auth(token), timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def _post(self, path: str, data: dict, token: str | None = None) -> dict | None:
        try:
            resp = requests.post(f"{self.base_url}{path}", json=data, headers=_auth(token), timeout=_TIMEOUT)
            if resp.status_code in (200, 201, 202):
                return resp.json()
        except Exception:
            pass
        return None

    def health(self) -> dict[str, Any]:
        data = self._get("/health")
        if data:
            return data
        return {"status": "offline", "service": "partproof-api", "version": "0.1.0"}

    # ── Login and seller profile (TRD 4) ──

    def list_profiles(self) -> list[dict[str, str]]:
        """Names for the login picker."""
        data = self._get("/profiles")
        if data and data.get("profiles"):
            return data["profiles"]
        mock = _mock_seller()
        return [{"seller_id": mock["id"], "display_name": mock["name"]}]

    def login(self, display_name: str) -> dict[str, Any] | None:
        """Start a session. Returns {token, seller_id, profile, context_card}, or None if offline."""
        return self._post("/sessions", {"display_name": display_name})

    def get_profile(self, token: str | None) -> dict[str, Any] | None:
        """The seller-details screen: {profile, editable_fields}, or None if offline / not logged in."""
        return self._get("/me/profile", token) if token else None

    def update_profile(self, token: str, fields: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Save seller details. Returns (ok, body); on error the body holds the API's detail."""
        try:
            resp = requests.put(
                f"{self.base_url}/me/profile", json=fields, headers=_auth(token), timeout=_TIMEOUT
            )
            body = resp.json()
            return resp.status_code == 200, body
        except Exception as exc:
            return False, {"detail": f"API unavailable: {exc}"}

    def get_seller(self, seller_id: str = "S-001", token: str | None = None) -> dict[str, Any]:
        data = self._get(f"/sellers/{seller_id}", token)
        if data and data.get("seller"):
            return data["seller"]
        return _mock_seller()

    def update_seller(self, seller_id: str, fields: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        data = self._post(f"/sellers/{seller_id}", fields, token)
        if data and data.get("seller"):
            return data["seller"]
        # Mock: merge fields into mock data
        mock = _mock_seller()
        mock.update(fields)
        mock["_cache_invalidated"] = True
        return mock

    def get_parts(self) -> list[dict[str, Any]]:
        data = self._get("/parts")
        if data and data.get("parts") and len(data["parts"]) > 0:
            return data["parts"]
        return _mock_parts()

    def get_part(self, part_id: str) -> dict[str, Any] | None:
        data = self._get(f"/parts/{part_id}")
        if data and data.get("part"):
            return data["part"]
        # Mock lookup
        for p in _mock_parts():
            if p["id"] == part_id or p["sku"] == part_id:
                return p
        return None

    def upload_files(self, files: list[dict[str, str]]) -> dict[str, Any]:
        """Upload files to the gateway. Returns job status."""
        data = self._post("/upload", {"files": files})
        if data:
            return data
        # Mock: simulate a successful upload with per-part progress
        return {
            "job_id": "mock-job-001",
            "status": "queued",
            "parts_queued": 10,
            "message": "Files queued for processing",
        }

    def get_reference_data(self) -> dict[str, Any]:
        data = self._get("/reference")
        if data and data.get("compat_ref"):
            return data
        return _mock_reference_data()

    def get_questions(self, part_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/parts/{part_id}/questions")
        if data and data.get("questions"):
            return data["questions"]
        # Mock: derive from planted issues
        part = self.get_part(part_id)
        if not part:
            return []
        issues = part.get("planted_issues", [])
        question_map = {
            "E12": "The part number on the label is unclear. Can you confirm the full part number?",
            "E16": "The donor year differs between the spreadsheet and the donor note. Which year is correct?",
            "E17": "The donor model differs between sources. Which model is correct?",
            "E22": "The side is listed as 'driver side'. Is this left or right?",
            "E24": "A possible damage was detected. Can you describe the condition?",
            "E28": "The part is marked as partly tested. What was tested?",
            "E30": "The donor note says 'tested OK'. Was this tested by you or the previous owner?",
            "E37": "What is the packed weight and dimensions?",
        }
        questions = []
        for e in issues:
            if e in question_map:
                questions.append({
                    "id": f"q-{part_id}-{e}",
                    "part_id": part_id,
                    "question_text": question_map[e],
                    "edge_case": e,
                    "priority": "high" if e in ("E12", "E16", "E17") else "medium",
                    "status": "open",
                })
        return questions


# Singleton client
_client: APIClient | None = None


def get_client() -> APIClient:
    global _client
    if _client is None:
        _client = APIClient()
    return _client
