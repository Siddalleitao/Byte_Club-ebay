"""Seller context — profile at login, the context card, and profile edits (TRD 4).

How the profile loads:
  1. The seller picks their name on the login screen. The name resolves to
     seller_id through the `seller_profiles` table only — never from text in
     uploaded files or chat messages.
  2. The profile renders as a ~300-token context card that sits in the cached
     prompt prefix (see prompt_prefix.py).
  3. A profile edit bumps the profile version, invalidates the cached prefix
     and re-renders the drafts that use a profile field (e.g. handling time).

The SellerSession created at login is the only source of seller_id for the
semantic layer.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domain.listing_profile import load_seller_profile, rerender_profile_drafts
from app.domain.models import EDITABLE_PROFILE_FIELDS, SellerProfile
from app.semantic import prompt_prefix

_JSON_COLUMNS = {"languages": "languages_json", "rack_codes": "rack_codes_json"}


class UnknownSeller(LookupError):
    """The selected name or seller_id has no profile."""


# ── Profile table access ────────────────────────────────────


def list_profiles(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Names for the login picker. Only identity fields, no profile detail."""
    rows = conn.execute(
        "SELECT seller_id, display_name FROM seller_profiles ORDER BY display_name"
    ).fetchall()
    return [dict(r) for r in rows]


def resolve_seller_id(conn: sqlite3.Connection, display_name: str) -> str:
    """Resolve the name picked at login to seller_id, from the profile table only."""
    row = conn.execute(
        "SELECT seller_id FROM seller_profiles WHERE display_name = ?",
        (display_name,),
    ).fetchone()
    if row is None:
        raise UnknownSeller(f"No seller profile named {display_name!r}")
    return row["seller_id"]


def load_profile(conn: sqlite3.Connection, seller_id: str) -> SellerProfile:
    profile = load_seller_profile(conn, seller_id)
    if profile is None:
        raise UnknownSeller(f"No seller profile for {seller_id!r}")
    return profile


def profile_version(conn: sqlite3.Connection, seller_id: str) -> int | None:
    row = conn.execute(
        "SELECT version FROM seller_profiles WHERE seller_id = ?", (seller_id,)
    ).fetchone()
    return row["version"] if row else None


def upsert_profile(conn: sqlite3.Connection, profile: SellerProfile) -> None:
    """Insert or replace a profile row as-is (used by seeding). Does not commit."""
    data = profile.model_dump(exclude={"updated_at"})
    for key, column in _JSON_COLUMNS.items():
        data[column] = json.dumps(data.pop(key), ensure_ascii=False)
    columns = list(data)
    conn.execute(
        f"""INSERT INTO seller_profiles ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})
            ON CONFLICT(seller_id) DO UPDATE SET
            {", ".join(f"{c} = excluded.{c}" for c in columns if c != "seller_id")},
            updated_at = datetime('now')""",
        [data[c] for c in columns],
    )


# ── Session ─────────────────────────────────────────────────


@dataclass
class SellerSession:
    """An authenticated seller session. seller_id is fixed at login."""

    seller_id: str
    profile: SellerProfile
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def current_profile(self, conn: sqlite3.Connection) -> SellerProfile:
        """Return the profile, reloading it if it was edited since it was cached."""
        if profile_version(conn, self.seller_id) != self.profile.version:
            self.profile = load_profile(conn, self.seller_id)
        return self.profile


def login(conn: sqlite3.Connection, display_name: str) -> SellerSession:
    """Start a session for the seller whose name was picked on the login screen."""
    seller_id = resolve_seller_id(conn, display_name)
    profile = load_profile(conn, seller_id)
    prompt_prefix.get_prompt_prefix(profile)  # warm the cached prefix for this seller
    return SellerSession(seller_id=seller_id, profile=profile)


# ── Profile edits ───────────────────────────────────────────


@dataclass
class ProfileUpdateResult:
    profile: SellerProfile
    changed_fields: list[str]
    rerendered_drafts: list[str]
    prompt_cache_invalidated: bool


def update_profile(
    conn: sqlite3.Connection,
    seller_id: str,
    changes: dict[str, Any],
    *,
    actor: str = "seller",
) -> ProfileUpdateResult:
    """Apply an edit from the seller-details screen.

    Only EDITABLE_PROFILE_FIELDS may change. A real change bumps the version,
    writes an AuditEvent, invalidates the cached prompt prefix and re-renders
    the drafts that use a changed profile field. Commits.
    """
    not_editable = set(changes) - EDITABLE_PROFILE_FIELDS
    if not_editable:
        raise ValueError(f"Profile fields not editable: {sorted(not_editable)}")

    current = load_profile(conn, seller_id)
    merged = current.model_dump()
    merged.update(changes)
    candidate = SellerProfile(**merged)  # validates
    changed = sorted(
        f for f in changes if getattr(candidate, f) != getattr(current, f)
    )
    if not changed:
        return ProfileUpdateResult(current, [], [], False)

    updated = candidate.model_copy(update={"version": current.version + 1})
    assignments = []
    values: list[Any] = []
    for f in changed:
        column = _JSON_COLUMNS.get(f, f)
        value = getattr(updated, f)
        assignments.append(f"{column} = ?")
        values.append(json.dumps(value, ensure_ascii=False) if f in _JSON_COLUMNS else value)
    conn.execute(
        f"""UPDATE seller_profiles SET {", ".join(assignments)},
            version = ?, updated_at = datetime('now') WHERE seller_id = ?""",
        (*values, updated.version, seller_id),
    )
    conn.execute(
        """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
           VALUES (NULL, 'profile_edit', ?, ?)""",
        (
            actor,
            json.dumps({
                "seller_id": seller_id,
                "changed_fields": changed,
                "before": {f: getattr(current, f) for f in changed},
                "after": {f: getattr(updated, f) for f in changed},
                "version": updated.version,
            }, ensure_ascii=False),
        ),
    )
    updated = load_profile(conn, seller_id)
    rerendered = rerender_profile_drafts(conn, updated, set(changed))
    conn.commit()

    prompt_prefix.invalidate_prompt_prefix(seller_id)
    return ProfileUpdateResult(updated, changed, rerendered, True)
