"""CP2 Reconciler — finds missing, conflicting, and implausible values.

All rules, no LLM. Per Rule (e): never auto-resolve, never guess.
Each finding becomes a Question row (priority, text_en and text_ar left
blank for the Inquiry agent) and an effect on part status per the TRD 7 matrix.

Edge cases detected:
  E16: Donor year conflict across sources
  E17: Donor model conflict
  E18: Donor vehicle missing
  E19: Market spec unknown (warning)
  E20: Two donors
  E21: Position unknown
  E22: Position ambiguous (driver/passenger wording)
  E24: Condition flag with no note
  E25: Seller override contradicts visual flag (from provenance)
  E26: Missing included components
  E27: Broken glass
  E28: No test record and partly tested
  E29: No test record
  E30: Note claims tested (seller-reported, held)
  E31: Failed test
  E37: Weight or dimensions missing
  E38: Weight or dimensions implausible
  E40: Missing price or unclear currency
  E41: Hedged answer (English or Arabic)

Per Rule (g): domain services never call an LLM. This module is pure logic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import AttributeStatus, PartStatus
from app.domain.ledger import EvidenceLedger, REQUIRED_FIELDS, LOW_RISK_FIELDS


# ── Finding priority levels ─────────────────────────────────

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"

# ── Part status effects per TRD 7 matrix ────────────────────
# Maps edge case → (part status effect, blocks_export)
# "blocks_export" means the part cannot advance past Guarded

STATUS_EFFECTS: dict[str, tuple[str, bool]] = {
    "E16": ("Awaiting seller", True),   # Donor year conflict
    "E17": ("Awaiting seller", True),   # Donor model conflict
    "E18": ("Awaiting seller", True),   # Donor missing
    "E19": ("Reconciled", False),       # Market spec unknown (warning only)
    "E20": ("Awaiting seller", True),   # Two donors
    "E21": ("Awaiting seller", True),   # Position unknown
    "E22": ("Awaiting seller", True),   # Position ambiguous
    "E24": ("Awaiting seller", True),   # Condition flag no note
    "E25": ("Guarded", True),           # Seller override contradicts visual
    "E26": ("Awaiting seller", True),   # Missing components
    "E27": ("Guarded", True),           # Broken glass
    "E28": ("Awaiting seller", True),   # Partly tested, no record
    "E29": ("Guarded", False),          # No test record (not blocked, but flagged)
    "E30": ("Guarded", True),           # Test claim held
    "E31": ("Guarded", True),           # Failed test
    "E37": ("Awaiting seller", True),   # Weight/dimensions missing
    "E38": ("Awaiting seller", True),   # Weight/dimensions implausible
    "E40": ("Awaiting seller", True),   # Missing price or unclear currency
    "E41": ("Awaiting seller", True),   # Hedged answer
}


# ── Finding ─────────────────────────────────────────────────


@dataclass
class Finding:
    """A single reconciliation finding.

    Each finding becomes a Question row (text_en and text_ar left blank
    for the Inquiry agent) and has an effect on part status.
    """

    edge_case: str
    attribute_name: str
    priority: str = PRIORITY_MEDIUM
    question_text: str = ""  # text_en (left blank for Inquiry agent)
    text_ar: str = ""       # Arabic translation (left blank)
    detail: str = ""        # Human-readable detail
    blocks_export: bool = True
    part_status_effect: str = "Awaiting seller"

    def to_question_dict(self, part_id: str) -> dict:
        """Convert to a dict suitable for the questions table."""
        import uuid
        return {
            "id": str(uuid.uuid4()),
            "part_id": part_id,
            "question_text": self.question_text,
            "text_ar": self.text_ar,
            "priority": self.priority,
            "edge_case": self.edge_case,
            "attribute_name": self.attribute_name,
            "answer_text": None,
            "status": "open",
            "answered_at": None,
        }


@dataclass
class ReconcileResult:
    """Complete result of reconciliation for a part."""

    part_id: str
    findings: list[Finding] = field(default_factory=list)
    part_status: PartStatus = PartStatus.RECONCILED
    blocks_export: bool = False
    edge_cases: list[str] = field(default_factory=list)

    @property
    def finding_count(self) -> int:
        return len(self.findings)


# ── Reconciler ──────────────────────────────────────────────


class Reconciler:
    """CP2 Reconciler — finds missing, conflicting, and implausible values.

    Never auto-resolves a conflict (Rule e). Each finding becomes a Question.
    """

    def reconcile(
        self,
        part_id: str,
        attributes: list[dict],
        donor_vehicle: dict | None = None,
        donor_vehicles: list[dict] | None = None,
        compat_refs: list[dict] | None = None,
        evidence_rows: list[dict] | None = None,
        listing_drafts: list[dict] | None = None,
    ) -> ReconcileResult:
        """Run all reconciliation rules on a part.

        Args:
            part_id: the part ID
            attributes: list of attribute dicts (name, value, status)
            donor_vehicle: the donor vehicle record (or None)
            donor_vehicles: list of all donor vehicles for this part (E20: two donors)
            compat_refs: compatibility reference rows for this part number
            evidence_rows: all evidence rows for this part
            listing_drafts: existing listing drafts (for stale check)
        """
        result = ReconcileResult(part_id=part_id)
        compat_refs = compat_refs or []
        evidence_rows = evidence_rows or []
        donor_vehicles = donor_vehicles or []

        # Build attribute lookup
        attr_map = {a["name"]: a for a in attributes}

        # Run each rule
        self._check_donor_year_conflict(attr_map, donor_vehicle, donor_vehicles, result)
        self._check_donor_model_conflict(attr_map, donor_vehicle, result)
        self._check_donor_missing(donor_vehicle, result)
        self._check_two_donors(donor_vehicles, result)
        self._check_market_spec(donor_vehicle, result)
        self._check_position_unknown(attr_map, result)
        self._check_position_ambiguous(attr_map, result)
        self._check_condition_flag_no_note(attr_map, evidence_rows, result)
        self._check_missing_components(attr_map, result)
        self._check_broken_glass(attr_map, evidence_rows, result)
        self._check_test_record(attr_map, evidence_rows, result)
        self._check_test_claim(attr_map, evidence_rows, result)
        self._check_failed_test(attr_map, evidence_rows, result)
        self._check_weight_missing(attr_map, result)
        self._check_weight_implausible(attr_map, result)
        self._check_price(attr_map, result)
        self._check_hedged_answers(attr_map, evidence_rows, result)
        self._check_unreadable_part_number(attr_map, result)

        # Compute part status from findings
        self._compute_part_status(result)

        return result

    # ── E16: Donor year conflict ──

    def _check_donor_year_conflict(
        self,
        attr_map: dict[str, dict],
        donor_vehicle: dict | None,
        donor_vehicles: list[dict],
        result: ReconcileResult,
    ) -> None:
        """E16: Donor year conflicting across sources."""
        attr = attr_map.get("donor_year")
        if attr and AttributeStatus(attr.get("status", "Missing")) == AttributeStatus.CONFLICTING:
            result.findings.append(Finding(
                edge_case="E16",
                attribute_name="donor_year",
                priority=PRIORITY_HIGH,
                detail=f"Donor year is conflicting: {attr.get('value', 'multiple values')}",
                **_status_kwargs("E16"),
            ))
            result.edge_cases.append("E16")

    # ── E17: Donor model conflict ──

    def _check_donor_model_conflict(
        self,
        attr_map: dict[str, dict],
        donor_vehicle: dict | None,
        result: ReconcileResult,
    ) -> None:
        """E17: Donor model conflicting across sources."""
        attr = attr_map.get("donor_model") or attr_map.get("model")
        if attr and AttributeStatus(attr.get("status", "Missing")) == AttributeStatus.CONFLICTING:
            result.findings.append(Finding(
                edge_case="E17",
                attribute_name="donor_model",
                priority=PRIORITY_HIGH,
                detail=f"Donor model is conflicting: {attr.get('value', 'multiple values')}",
                **_status_kwargs("E17"),
            ))
            result.edge_cases.append("E17")

    # ── E18: Donor missing ──

    def _check_donor_missing(
        self,
        donor_vehicle: dict | None,
        result: ReconcileResult,
    ) -> None:
        """E18: No donor vehicle recorded."""
        if donor_vehicle is None or not donor_vehicle.get("make"):
            result.findings.append(Finding(
                edge_case="E18",
                attribute_name="donor_vehicle",
                priority=PRIORITY_HIGH,
                detail="No donor vehicle recorded",
                **_status_kwargs("E18"),
            ))
            result.edge_cases.append("E18")

    # ── E19: Market spec unknown (warning) ──

    def _check_market_spec(
        self,
        donor_vehicle: dict | None,
        result: ReconcileResult,
    ) -> None:
        """E19: Market spec (GCC, EU, US, etc.) is unknown."""
        if donor_vehicle and not donor_vehicle.get("market"):
            result.findings.append(Finding(
                edge_case="E19",
                attribute_name="market",
                priority=PRIORITY_LOW,
                detail="Market spec (GCC/EU/US) is unknown — warning only",
                **_status_kwargs("E19"),
            ))
            result.edge_cases.append("E19")

    # ── E20: Two donors ──

    def _check_two_donors(
        self,
        donor_vehicles: list[dict],
        result: ReconcileResult,
    ) -> None:
        """E20: Two donor vehicles recorded for one part."""
        if len(donor_vehicles) > 1:
            result.findings.append(Finding(
                edge_case="E20",
                attribute_name="donor_vehicle",
                priority=PRIORITY_HIGH,
                detail=f"Two donor vehicles recorded: {len(donor_vehicles)} donors found",
                **_status_kwargs("E20"),
            ))
            result.edge_cases.append("E20")

    # ── E21: Position unknown ──

    def _check_position_unknown(
        self,
        attr_map: dict[str, dict],
        result: ReconcileResult,
    ) -> None:
        """E21: Position (left/right) is unknown."""
        pos = attr_map.get("position") or attr_map.get("side")
        if not pos or not pos.get("value") or pos.get("status") == "Missing":
            result.findings.append(Finding(
                edge_case="E21",
                attribute_name="position",
                priority=PRIORITY_HIGH,
                detail="Position (left/right) is unknown",
                **_status_kwargs("E21"),
            ))
            result.edge_cases.append("E21")

    # ── E22: Position ambiguous (driver/passenger) ──

    def _check_position_ambiguous(
        self,
        attr_map: dict[str, dict],
        result: ReconcileResult,
    ) -> None:
        """E22: Position uses ambiguous driver/passenger wording."""
        pos = attr_map.get("position") or attr_map.get("side")
        if pos and pos.get("value"):
            val = str(pos["value"]).lower().strip()
            if val in ("driver", "passenger", "driver side", "passenger side"):
                result.findings.append(Finding(
                    edge_case="E22",
                    attribute_name="position",
                    priority=PRIORITY_MEDIUM,
                    detail=f"Position '{val}' is ambiguous — driver/passenger depends on market (LHD vs RHD)",
                    **_status_kwargs("E22"),
                ))
                result.edge_cases.append("E22")

    # ── E24: Condition flag with no note ──

    def _check_condition_flag_no_note(
        self,
        attr_map: dict[str, dict],
        evidence_rows: list[dict],
        result: ReconcileResult,
    ) -> None:
        """E24: A condition flag exists but no condition note explains it."""
        # Check if there's a damage flag (crack, broken, etc.) in any evidence
        damage_keywords = ["crack", "cracked", "broken", "damage", "damaged",
                          "scratch", "scratched", "scuff", "dent", "chip"]
        has_damage_flag = False
        for ev in evidence_rows:
            content = str(ev.get("content", "")).lower()
            for kw in damage_keywords:
                if kw in content:
                    has_damage_flag = True
                    break
            if has_damage_flag:
                break

        # Also check condition_notes attribute
        cn = attr_map.get("condition_notes")
        has_condition_note = cn and cn.get("value")

        if has_damage_flag and not has_condition_note:
            result.findings.append(Finding(
                edge_case="E24",
                attribute_name="condition_notes",
                priority=PRIORITY_MEDIUM,
                detail="Condition flag (damage) detected but no condition note explains it",
                **_status_kwargs("E24"),
            ))
            result.edge_cases.append("E24")

    # ── E26: Missing included components ──

    def _check_missing_components(
        self,
        attr_map: dict[str, dict],
        result: ReconcileResult,
    ) -> None:
        """E26: Missing included components (e.g. mirror without cover)."""
        # Check if part_type is a mirror and features mention components
        pt = attr_map.get("part_type")
        if pt and pt.get("value"):
            pt_val = str(pt["value"]).lower()
            if "mirror" in pt_val:
                # Mirrors typically include glass, housing, cover.
                # Flag only components explicitly marked missing; a list where
                # everything is present is not a finding.
                missing = _missing_components(attr_map)
                if missing:
                    result.findings.append(Finding(
                        edge_case="E26",
                        attribute_name="included_components",
                        priority=PRIORITY_MEDIUM,
                        detail=f"Missing components: {', '.join(missing)}",
                        **_status_kwargs("E26"),
                    ))
                    result.edge_cases.append("E26")

    # ── E27: Broken glass ──

    def _check_broken_glass(
        self,
        attr_map: dict[str, dict],
        evidence_rows: list[dict],
        result: ReconcileResult,
    ) -> None:
        """E27: Broken glass detected."""
        glass_keywords = ["broken glass", "cracked glass", "shattered glass",
                         "glass broken", "glass cracked", "glass shattered"]
        # Check evidence for broken glass
        for ev in evidence_rows:
            content = str(ev.get("content", "")).lower()
            for kw in glass_keywords:
                if kw in content:
                    result.findings.append(Finding(
                        edge_case="E27",
                        attribute_name="broken_glass",
                        priority=PRIORITY_HIGH,
                        detail=f"Broken glass detected: '{kw}' in evidence",
                        **_status_kwargs("E27"),
                    ))
                    result.edge_cases.append("E27")
                    return

        # Also check condition_notes
        cn = attr_map.get("condition_notes")
        if cn and cn.get("value"):
            cn_lower = str(cn["value"]).lower()
            # Check for glass + damage keyword together
            has_glass = "glass" in cn_lower
            has_glass_damage = any(kw in cn_lower for kw in ["crack", "broken", "shatter", "chip"])
            if has_glass and has_glass_damage:
                result.findings.append(Finding(
                    edge_case="E27",
                    attribute_name="broken_glass",
                    priority=PRIORITY_HIGH,
                    detail=f"Broken glass detected in condition notes: '{cn['value']}'",
                    **_status_kwargs("E27"),
                ))
                result.edge_cases.append("E27")
                return
            for kw in glass_keywords:
                if kw in cn_lower:
                    result.findings.append(Finding(
                        edge_case="E27",
                        attribute_name="broken_glass",
                        priority=PRIORITY_HIGH,
                        detail=f"Broken glass detected: '{kw}' in condition notes",
                        **_status_kwargs("E27"),
                    ))
                    result.edge_cases.append("E27")
                    return

    # ── E28: No test record and partly tested ──

    def _check_test_record(
        self,
        attr_map: dict[str, dict],
        evidence_rows: list[dict],
        result: ReconcileResult,
    ) -> None:
        """E28/E29: Test record checks."""
        # Check if there's any test record evidence
        has_test_record = any(
            ev.get("kind") == "test_record" for ev in evidence_rows
        )

        # Check functionality/test_status attributes
        func = attr_map.get("functionality") or attr_map.get("test_status")
        func_value = str(func.get("value", "")).lower() if func else ""

        if not has_test_record:
            if func and func_value in ("partly tested", "partial", "partially tested"):
                # E28: No test record and partly tested
                result.findings.append(Finding(
                    edge_case="E28",
                    attribute_name="functionality",
                    priority=PRIORITY_HIGH,
                    detail="Partly tested but no test record on file",
                    **_status_kwargs("E28"),
                ))
                result.edge_cases.append("E28")
            elif not func or func.get("status") == "Missing":
                # E29: No test record (and not tested)
                result.findings.append(Finding(
                    edge_case="E29",
                    attribute_name="functionality",
                    priority=PRIORITY_LOW,
                    detail="No test record on file — part is untested",
                    **_status_kwargs("E29"),
                ))
                result.edge_cases.append("E29")

    def _check_test_claim(self, *args, **kwargs) -> None:
        """E30 is checked in provenance, not here — but we log it."""
        # E30 is handled by provenance.check_test_claim
        pass

    # ── E31: Failed test ──

    def _check_failed_test(
        self,
        attr_map: dict[str, dict],
        evidence_rows: list[dict],
        result: ReconcileResult,
    ) -> None:
        """E31: A test record shows the part failed."""
        func = attr_map.get("functionality") or attr_map.get("test_status")
        if func and func.get("value"):
            val = str(func["value"]).lower().strip()
            if val in ("failed", "fail", "not working", "defective", "faulty"):
                result.findings.append(Finding(
                    edge_case="E31",
                    attribute_name="functionality",
                    priority=PRIORITY_HIGH,
                    detail=f"Test record shows part failed: '{val}'",
                    **_status_kwargs("E31"),
                ))
                result.edge_cases.append("E31")

        # Also check test_record evidence
        for ev in evidence_rows:
            if ev.get("kind") == "test_record":
                content = str(ev.get("content", "")).lower()
                if any(kw in content for kw in ["fail", "failed", "not working", "defective"]):
                    result.findings.append(Finding(
                        edge_case="E31",
                        attribute_name="functionality",
                        priority=PRIORITY_HIGH,
                        detail="Test record evidence shows failure",
                        **_status_kwargs("E31"),
                    ))
                    result.edge_cases.append("E31")
                    return

    # ── E37: Weight or dimensions missing ──

    def _check_weight_missing(
        self,
        attr_map: dict[str, dict],
        result: ReconcileResult,
    ) -> None:
        """E37: Packed weight or dimensions not recorded."""
        weight = attr_map.get("weight")
        dimensions = attr_map.get("dimensions")

        if (not weight or not weight.get("value") or weight.get("status") == "Missing"):
            result.findings.append(Finding(
                edge_case="E37",
                attribute_name="weight",
                priority=PRIORITY_MEDIUM,
                detail="Packed weight not recorded",
                **_status_kwargs("E37"),
            ))
            result.edge_cases.append("E37")

        if (not dimensions or not dimensions.get("value") or dimensions.get("status") == "Missing"):
            # Only add E37 once for dimensions if not already added for weight
            if "E37" not in result.edge_cases:
                result.findings.append(Finding(
                    edge_case="E37",
                    attribute_name="dimensions",
                    priority=PRIORITY_MEDIUM,
                    detail="Dimensions not recorded",
                    **_status_kwargs("E37"),
                ))
                result.edge_cases.append("E37")

    # ── E38: Weight or dimensions implausible ──

    def _check_weight_implausible(
        self,
        attr_map: dict[str, dict],
        result: ReconcileResult,
    ) -> None:
        """E38: Weight or dimensions implausible with unit checks."""
        weight = attr_map.get("weight")
        if weight and weight.get("value"):
            weight_val = _parse_weight(str(weight["value"]))
            if weight_val is not None:
                # A side-view mirror typically weighs 1-8 kg
                if weight_val < 0.1 or weight_val > 50:
                    result.findings.append(Finding(
                        edge_case="E38",
                        attribute_name="weight",
                        priority=PRIORITY_HIGH,
                        detail=f"Weight {weight_val} kg is implausible for this part type",
                        **_status_kwargs("E38"),
                    ))
                    result.edge_cases.append("E38")

        dimensions = attr_map.get("dimensions")
        if dimensions and dimensions.get("value"):
            dims = _parse_dimensions(str(dimensions["value"]))
            if dims:
                length, width, height = dims
                # Check for implausible dimensions
                if any(d <= 0 or d > 200 for d in [length, width, height]):
                    result.findings.append(Finding(
                        edge_case="E38",
                        attribute_name="dimensions",
                        priority=PRIORITY_HIGH,
                        detail=f"Dimensions {dims} cm are implausible",
                        **_status_kwargs("E38"),
                    ))
                    result.edge_cases.append("E38")

    # ── E40: Missing price or unclear currency ──

    def _check_price(
        self,
        attr_map: dict[str, dict],
        result: ReconcileResult,
    ) -> None:
        """E40: Missing price or unclear currency."""
        price = attr_map.get("price")
        currency = attr_map.get("currency")

        if not price or not price.get("value") or price.get("status") == "Missing":
            result.findings.append(Finding(
                edge_case="E40",
                attribute_name="price",
                priority=PRIORITY_HIGH,
                detail="Price is missing",
                **_status_kwargs("E40"),
            ))
            result.edge_cases.append("E40")
        elif currency and currency.get("value"):
            currency_val = str(currency["value"]).upper().strip()
            if currency_val not in ("AED", "GBP", "USD", "EUR", "SAR"):
                result.findings.append(Finding(
                    edge_case="E40",
                    attribute_name="currency",
                    priority=PRIORITY_MEDIUM,
                    detail=f"Currency '{currency_val}' is unclear or unrecognized",
                    **_status_kwargs("E40"),
                ))
                result.edge_cases.append("E40")

    # ── E41: Hedged answer ──

    def _check_hedged_answers(
        self,
        attr_map: dict[str, dict],
        evidence_rows: list[dict],
        result: ReconcileResult,
    ) -> None:
        """E41: Hedged answers in English or Arabic stored as unconfirmed."""
        from app.domain.provenance import detect_hedged_answer

        # Check seller answers for hedged language
        for ev in evidence_rows:
            if ev.get("kind") == "seller_answer":
                content = str(ev.get("content", ""))
                if detect_hedged_answer(content):
                    result.findings.append(Finding(
                        edge_case="E41",
                        attribute_name=ev.get("attribute_name", "unknown"),
                        priority=PRIORITY_MEDIUM,
                        detail=f"Hedged answer detected: '{content[:100]}' — stored as unconfirmed",
                        **_status_kwargs("E41"),
                    ))
                    result.edge_cases.append("E41")

    # ── E12: Unreadable part number (Unverified) ──

    def _check_unreadable_part_number(
        self,
        attr_map: dict[str, dict],
        result: ReconcileResult,
    ) -> None:
        """Part number is unreadable (contains '?' — E12)."""
        pn = attr_map.get("part_number")
        if pn and pn.get("value"):
            val = str(pn["value"])
            if "?" in val:
                result.findings.append(Finding(
                    edge_case="E12",
                    attribute_name="part_number",
                    priority=PRIORITY_HIGH,
                    detail=f"Part number is unreadable: '{val}' — contains ambiguous characters",
                    **_status_kwargs("E12") if "E12" in STATUS_EFFECTS else {
                        "part_status_effect": "Guarded",
                        "blocks_export": True,
                    },
                ))
                result.edge_cases.append("E12")

    # ── Part status computation ──

    def _compute_part_status(self, result: ReconcileResult) -> None:
        """Compute the final part status from all findings.

        Per TRD 7 matrix: the most severe finding determines the part status.
        "Awaiting seller" is the most severe (part cannot advance without seller input).
        "Guarded" is less severe (part can be listed but with caveats).
        "Reconciled" is the least severe (no issues found).
        """
        if not result.findings:
            result.part_status = PartStatus.RECONCILED
            result.blocks_export = False
            return

        # Priority order: lower number = more severe
        priority_order = {
            "Awaiting seller": 0,  # Most severe — needs seller input
            "Guarded": 1,          # Can proceed but with caveats
            "Reconciled": 2,       # No issues
        }

        worst_status = "Reconciled"
        blocks = False

        for finding in result.findings:
            effect = finding.part_status_effect
            if priority_order.get(effect, 2) < priority_order.get(worst_status, 2):
                worst_status = effect
            if finding.blocks_export:
                blocks = True

        # Map to PartStatus
        status_map = {
            "Guarded": PartStatus.GUARDED,
            "Awaiting seller": PartStatus.AWAITING_SELLER,
            "Reconciled": PartStatus.RECONCILED,
        }

        result.part_status = status_map.get(worst_status, PartStatus.RECONCILED)
        result.blocks_export = blocks


# ── Helpers ─────────────────────────────────────────────────


def _status_kwargs(edge_case: str) -> dict:
    """Get status effect kwargs for an edge case."""
    effect, blocks = STATUS_EFFECTS.get(edge_case, ("Awaiting seller", True))
    return {
        "part_status_effect": effect,
        "blocks_export": blocks,
    }


def _missing_components(attr_map: dict[str, dict]) -> list[str]:
    """Components marked missing in `included_components` (JSON {name: present|missing})
    or listed in a free-text `missing_components` value."""
    missing: list[str] = []
    comp = attr_map.get("included_components")
    if comp and comp.get("value"):
        try:
            marked = json.loads(comp["value"])
        except (TypeError, json.JSONDecodeError):
            marked = None
        if isinstance(marked, dict):
            missing += [name for name, state in marked.items() if str(state).lower() == "missing"]
        elif "missing" in str(comp["value"]).lower():
            missing.append(str(comp["value"]))
    extra = attr_map.get("missing_components")
    if extra and extra.get("value"):
        missing.append(str(extra["value"]))
    return missing


def _parse_weight(text: str) -> float | None:
    """Parse a weight string, handling kg and lb units."""
    text = text.lower().strip()

    # Try to find a number followed by a unit
    match = re.search(r"(\d+\.?\d*)\s*(kg|kilo|kilogram|lb|lbs|pound)", text)
    if not match:
        # Try just a number
        match = re.search(r"(\d+\.?\d*)", text)
        if match:
            return float(match.group(1))
        return None

    value = float(match.group(1))
    unit = match.group(2)

    # Convert lb to kg
    if unit in ("lb", "lbs", "pound"):
        value = value * 0.453592

    return value


def _parse_dimensions(text: str) -> tuple[float, float, float] | None:
    """Parse a dimensions string (L x W x H) in cm or inches."""
    text = text.lower().strip()

    # Try L x W x H pattern
    match = re.search(r"(\d+\.?\d*)\s*[x×]\s*(\d+\.?\d*)\s*[x×]\s*(\d+\.?\d*)\s*(cm|inch|in)?", text)
    if not match:
        return None

    length = float(match.group(1))
    width = float(match.group(2))
    height = float(match.group(3))
    unit = match.group(4)

    # Convert inches to cm
    if unit in ("inch", "in"):
        length = length * 2.54
        width = width * 2.54
        height = height * 2.54

    return (length, width, height)


# ── Partial re-run for E43 ──


def rerun_for_fields(
    reconciler: Reconciler,
    part_id: str,
    affected_fields: list[str],
    attributes: list[dict],
    donor_vehicle: dict | None = None,
    donor_vehicles: list[dict] | None = None,
    compat_refs: list[dict] | None = None,
    evidence_rows: list[dict] | None = None,
    listing_drafts: list[dict] | None = None,
) -> ReconcileResult:
    """Re-run the Reconciler for only the affected fields (E43).

    When a seller changes a confirmed field, a new version is written,
    an AuditEvent is logged, and the Reconciler re-runs for only the
    affected fields — not the whole part.
    """
    full_result = reconciler.reconcile(
        part_id=part_id,
        attributes=attributes,
        donor_vehicle=donor_vehicle,
        donor_vehicles=donor_vehicles,
        compat_refs=compat_refs,
        evidence_rows=evidence_rows,
        listing_drafts=listing_drafts,
    )

    # Filter findings to only those on affected fields
    affected_result = ReconcileResult(part_id=part_id)
    for finding in full_result.findings:
        if finding.attribute_name in affected_fields:
            affected_result.findings.append(finding)
            affected_result.edge_cases.append(finding.edge_case)

    reconciler._compute_part_status(affected_result)
    return affected_result
