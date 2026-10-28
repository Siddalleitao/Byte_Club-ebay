"""Profile-driven draft sections — templated, deterministic (TRD 3 and 4).

A listing draft takes some values from the seller profile rather than from
the part's evidence: handling time, ship-from city, returns policy and the
listing currency. These sections are templated, so re-rendering them after
a profile edit needs no LLM call.

Per Rule (g): domain services never call an LLM. This module is pure logic + DB I/O.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.models import SellerProfile
from app.domain.propagation import profile_fields_used_by_drafts

# Drafts in these statuses are re-rendered when the profile changes.
# Exported drafts are a record of what was published and are never rewritten.
_RERENDER_STATUSES = ("draft", "guarded", "approved")


@dataclass(frozen=True)
class ProfileSections:
    """Draft sections rendered from the seller profile."""

    handling_days: int
    shipping_block: str
    listing_currency: str
    return_policy_ref: str | None
    profile_version: int
    city: str = ""


_PROFILE_JSON_COLUMNS = {"languages": "languages_json", "rack_codes": "rack_codes_json"}


def profile_from_row(row: sqlite3.Row | dict) -> SellerProfile:
    """Build a SellerProfile from a seller_profiles row."""
    data = dict(row)
    for key, column in _PROFILE_JSON_COLUMNS.items():
        data[key] = json.loads(data.pop(column) or "[]")
    return SellerProfile(**data)


def load_seller_profile(conn: sqlite3.Connection, seller_id: str) -> SellerProfile | None:
    row = conn.execute("SELECT * FROM seller_profiles WHERE seller_id = ?", (seller_id,)).fetchone()
    return profile_from_row(row) if row else None


def render_profile_sections(profile: SellerProfile) -> ProfileSections:
    """Render the profile-driven draft sections from a seller profile."""
    days = profile.handling_days
    day_word = "working day" if days == 1 else "working days"
    shipping = f"Dispatched from {profile.city} within {days} {day_word} of cleared payment."
    if profile.return_policy_ref:
        shipping += f" Returns: see policy {profile.return_policy_ref}."
    return ProfileSections(
        handling_days=days,
        shipping_block=shipping,
        listing_currency=profile.listing_currency,
        return_policy_ref=profile.return_policy_ref,
        profile_version=profile.version,
        city=profile.city,
    )


def rerender_profile_drafts(
    conn: sqlite3.Connection,
    profile: SellerProfile,
    changed_fields: set[str],
) -> list[str]:
    """Re-render the profile sections of the seller's open drafts.

    Only runs when a changed field feeds a draft section (see
    PROFILE_DRAFT_FIELDS). An approved draft whose content changes goes back
    to `draft`, because the approval covered the old text.

    Returns the IDs of the re-rendered drafts. Does not commit.
    """
    if not profile_fields_used_by_drafts(changed_fields):
        return []

    sections = render_profile_sections(profile)
    placeholders = ",".join("?" for _ in _RERENDER_STATUSES)
    drafts = conn.execute(
        f"""SELECT ld.id, ld.part_id, ld.status FROM listing_drafts ld
            JOIN parts p ON p.id = ld.part_id
            WHERE p.seller_id = ? AND ld.status IN ({placeholders})""",
        (profile.seller_id, *_RERENDER_STATUSES),
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    rerendered: list[str] = []
    for draft in drafts:
        new_status = "draft" if draft["status"] == "approved" else draft["status"]
        conn.execute(
            """UPDATE listing_drafts
               SET handling_days = ?, shipping_block = ?, currency = ?,
                   profile_version = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (
                sections.handling_days,
                sections.shipping_block,
                sections.listing_currency,
                sections.profile_version,
                new_status,
                now,
                draft["id"],
            ),
        )
        conn.execute(
            """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
               VALUES (?, 'draft_rerender', 'listing_profile', ?)""",
            (
                draft["part_id"],
                json.dumps({
                    "draft_id": draft["id"],
                    "trigger": "profile_edit",
                    "changed_fields": sorted(changed_fields),
                    "profile_version": sections.profile_version,
                    "approval_revoked": draft["status"] == "approved",
                }),
            ),
        )
        rerendered.append(draft["id"])
    return rerendered
