"""Pane renderers for PartProof.

Each function returns an HTML string for one pane.
All data comes from the existing api_client — no business logic changes.
"""

from __future__ import annotations

import html
import json
from typing import Any

from ui.api_client import get_client
from ui.i18n import t
from ui.icons import icon


def _esc(text: str) -> str:
    return html.escape(str(text))


def _badge_class(badge: str) -> str:
    mapping = {
        "Approved": "approved",
        "Ready for seller review": "ready",
        "Information missing": "missing",
        "Specialist review required": "specialist",
    }
    return mapping.get(badge, "missing")


def _badge_icon(badge: str) -> str:
    mapping = {
        "Approved": "✅",
        "Ready for seller review": "🔵",
        "Information missing": "⚠️",
        "Specialist review required": "🔴",
    }
    return mapping.get(badge, "❓")


# ── HOME PANE ──


def render_home(lang: str = "en", seller: dict | None = None) -> str:
    name = (seller or {}).get("name", "")
    greeting = t("login_welcome", lang)
    sub = t("upload_drag_here", lang)
    return f"""
    <div class="pp-home" data-pane="home">
        <div class="pp-home-title">{greeting}, {_esc(name)}</div>
        <div class="pp-home-sub">{_esc(t('app_subtitle', lang))}</div>

        <div class="pp-viewfinder" id="pp-viewfinder">
            <div class="pp-viewfinder-placeholder">{_esc(t('upload_photos', lang))}<br/><br/>
                <button class="pp-btn pp-btn-primary" onclick="window.ppNavigate('home'); document.dispatchEvent(new CustomEvent('pp-start-camera'))">
                    {icon('camera', 20)} {_esc(t('upload_start', lang))}
                </button>
            </div>
        </div>

        <div class="pp-home-actions">
            <button class="pp-btn pp-btn-secondary" onclick="document.dispatchEvent(new CustomEvent('pp-upload-spreadsheet'))">
                {icon('upload', 20)} {_esc(t('upload_spreadsheet', lang))}
            </button>
            <button class="pp-btn pp-btn-secondary" onclick="document.dispatchEvent(new CustomEvent('pp-upload-notes'))">
                {icon('upload', 20)} {_esc(t('upload_donor_notes', lang))}
            </button>
        </div>

        <div class="pp-chips">
            <div class="pp-chip" onclick="window.ppNavigate('stories')">{icon('grid', 18)} {_esc(t('board_title', lang))}</div>
            <div class="pp-chip" onclick="window.ppNavigate('chats')">{icon('chat', 18)} {_esc(t('questions_title', lang))}</div>
        </div>
    </div>
    """


# ── CHATS PANE ──


def render_chats(lang: str = "en", parts: list[dict] | None = None, selected_part: dict | None = None) -> str:
    if selected_part:
        return _render_chat_thread(lang, selected_part)

    if parts is None:
        client = get_client()
        parts = client.get_parts()[:10]

    # Only show parts with open questions as "unread" chats
    unread_parts = [p for p in parts if p.get("open_questions", 0) > 0]
    other_parts = [p for p in parts if p.get("open_questions", 0) == 0]

    header = f"""
    <div class="pp-chats-header">
        <h2 class="pp-chats-title">{_esc(t('questions_title', lang))}</h2>
    </div>
    """

    items_html = ""
    for p in unread_parts:
        badge_cls = _badge_class(p.get("badge", ""))
        sku = p.get("sku", "")
        qcount = p.get("open_questions", 0)
        preview = p.get("issue_types", ["—"])[0] if p.get("issue_types") else "—"
        items_html += f"""
        <div class="pp-chat-item" onclick="window.ppSelectChat('{_esc(p.get("id", ""))}')" data-context="view:circle:View,answer:send:Answer" data-id="{_esc(p.get("id", ""))}" data-label="{_esc(sku)}">
            <div class="pp-chat-avatar status-{badge_cls}">{_esc(sku[-3:] if len(sku) > 3 else sku)}</div>
            <div class="pp-chat-body">
                <p class="pp-chat-name">{_esc(sku)} — {_esc(p.get('make', ''))} {_esc(p.get('model', ''))}</p>
                <p class="pp-chat-preview">{_esc(preview)}</p>
            </div>
            <div class="pp-chat-unread">{qcount}</div>
        </div>
        """

    if other_parts:
        items_html += f'<div style="padding:8px 16px;font-size:12px;color:var(--pp-text-secondary)">{_esc(t("board_no_questions", lang))}</div>'
        for p in other_parts:
            badge_cls = _badge_class(p.get("badge", ""))
            sku = p.get("sku", "")
            items_html += f"""
            <div class="pp-chat-item" onclick="window.ppSelectChat('{_esc(p.get("id", ""))}')">
                <div class="pp-chat-avatar status-{badge_cls}">{_esc(sku[-3:] if len(sku) > 3 else sku)}</div>
                <div class="pp-chat-body">
                    <p class="pp-chat-name">{_esc(sku)} — {_esc(p.get('make', ''))} {_esc(p.get('model', ''))}</p>
                    <p class="pp-chat-preview">{_esc(t('board_no_questions', lang))}</p>
                </div>
            </div>
            """

    if not parts:
        items_html = f'<div class="pp-empty">{_esc(t("not_logged_in", lang))}</div>'

    return f"""
    <div data-pane="chats">
        {header}
        <div class="pp-chat-list">
            {items_html}
        </div>
    </div>
    """


def _render_chat_thread(lang: str, part: dict) -> str:
    client = get_client()
    questions = client.get_questions(part.get("id", part.get("sku", "")))
    sku = part.get("sku", "")

    messages_html = ""
    if not questions:
        messages_html = f'<div class="pp-empty">{_esc(t("questions_no_questions", lang))}</div>'
    else:
        for q in questions:
            qtext = q.get("question_text", "")
            edge = q.get("edge_case", "")
            priority = q.get("priority", "medium")
            priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
            messages_html += f"""
            <div class="pp-msg pp-msg-in">
                <div>{_esc(qtext)}</div>
                <div class="pp-msg-meta">{priority_icon} {_esc(t('questions_edge_case', lang))}: {_esc(edge)} · {_esc(t('questions_priority', lang))}: {_esc(priority)}</div>
            </div>
            """

    # Quick-reply chips
    quick_replies = [
        "Yes, confirmed",
        "No, incorrect",
        "Let me check",
        "Needs specialist",
    ]
    chips_html = "".join(
        f'<div class="pp-chip" onclick="window.ppSendQuickReply(\'{part.get("id", "")}\', \'{_esc(r)}\')">{_esc(r)}</div>'
        for r in quick_replies
    )

    pid = part.get("id", "")
    return f"""
    <div data-pane="chats" class="pp-chat-thread">
        <div class="pp-chat-thread-header">
            <button class="pp-chat-thread-back" onclick="document.dispatchEvent(new CustomEvent('pp-back-to-chat-list'))" aria-label="Back">
                {icon('back', 24)}
            </button>
            <div class="pp-chat-avatar status-{_badge_class(part.get('badge', ''))}">{_esc(sku[-3:] if len(sku) > 3 else sku)}</div>
            <div>
                <p class="pp-chat-name">{_esc(sku)}</p>
                <p class="pp-chat-preview">{_esc(part.get('make', ''))} {_esc(part.get('model', ''))} {_esc(part.get('year', ''))}</p>
            </div>
        </div>
        <div class="pp-chat-thread-messages" id="pp-chat-messages">
            {messages_html}
        </div>
        <div class="pp-quick-replies">{chips_html}</div>
        <div class="pp-chat-input-bar">
            <input type="text" class="pp-chat-input" placeholder="{_esc(t('questions_answer_placeholder', lang))}" id="pp-chat-input-{_esc(pid)}" aria-label="Answer" />
            <button class="pp-chat-send" onclick="window.ppSendQuickReply('{_esc(pid)}', document.getElementById('pp-chat-input-{_esc(pid)}').value)" aria-label="Send">
                {icon('send', 22)}
            </button>
        </div>
    </div>
    """


# ── STORIES PANE (Inventory Board) ──


def render_stories(lang: str = "en", parts: list[dict] | None = None) -> str:
    if parts is None:
        client = get_client()
        parts = client.get_parts()[:10]

    header = f"""
    <div class="pp-stories-header">
        <h2 class="pp-stories-title">{_esc(t('board_title', lang))}</h2>
        <div class="pp-search-bar">
            {icon('search', 20)}
            <input type="text" class="pp-search-input" placeholder="{_esc(t('board_search', lang))}" id="pp-board-search" aria-label="Search" />
        </div>
        <div class="pp-filter-bar">
            <div class="pp-filter-chip active" data-filter-type="status" data-filter-value="all">{_esc(t('board_all', lang))}</div>
            <div class="pp-filter-chip" data-filter-type="status" data-filter-value="Approved">{_esc(t('badge_approved', lang))}</div>
            <div class="pp-filter-chip" data-filter-type="status" data-filter-value="Ready for seller review">{_esc(t('badge_ready', lang))}</div>
            <div class="pp-filter-chip" data-filter-type="status" data-filter-value="Information missing">{_esc(t('badge_missing', lang))}</div>
            <div class="pp-filter-chip" data-filter-type="status" data-filter-value="Specialist review required">{_esc(t('badge_specialist', lang))}</div>
        </div>
    </div>
    """

    rings_html = ""
    for p in parts:
        badge = p.get("badge", "Information missing")
        badge_cls = _badge_class(badge)
        b_icon = _badge_icon(badge)
        sku = p.get("sku", "")
        qcount = p.get("open_questions", 0)
        ring_badge = f'<div class="pp-ring-badge">{qcount}</div>' if qcount > 0 else ""
        rings_html += f"""
        <div class="pp-ring" onclick='window.ppOpenStory({json.dumps([pi for pi in parts])})' data-id="{_esc(p.get('id', ''))}" data-context="view:circle:View,answer:send:Answer" data-label="{_esc(sku)}">
            <div class="pp-ring-circle status-{badge_cls}">
                <div class="pp-ring-icon">{b_icon}</div>
                {ring_badge}
            </div>
            <div class="pp-ring-label">{_esc(sku)}</div>
            <div class="pp-ring-sub">{_esc(p.get('make', ''))[:8]}</div>
        </div>
        """

    # Also render part cards below the rings for accessibility
    cards_html = ""
    for p in parts:
        badge = p.get("badge", "Information missing")
        badge_cls = _badge_class(badge)
        b_icon = _badge_icon(badge)
        qcount = p.get("open_questions", 0)
        sku = p.get("sku", "")
        issues = ", ".join(p.get("issue_types", [])) or "—"
        price = p.get("asking_price_aed", "")
        rack = p.get("rack_code", "—")

        view_btn = f'<button class="pp-btn pp-btn-secondary" onclick="window.ppOpenStory({json.dumps(parts)})">{icon("question", 18)} {_esc(t("board_view_questions", lang))}</button>' if qcount > 0 else ""

        cards_html += f"""
        <div class="pp-card" data-id="{_esc(p.get('id', ''))}" data-context="view:circle:View,answer:send:Answer,approve:check:Approve" data-label="{_esc(sku)}" style="margin-bottom:8px;border-radius:12px">
            <div style="display:flex;justify-content:space-between;align-items:start">
                <div>
                    <div class="pp-card-sku">{_esc(sku)}</div>
                    <div class="pp-card-detail">{_esc(p.get('make', ''))} {_esc(p.get('model', ''))} {_esc(p.get('year', ''))}</div>
                    <div class="pp-card-detail">{_esc(p.get('part_type', ''))} · {_esc(t('board_part_number', lang))}: {_esc(p.get('part_number', ''))}</div>
                    <div class="pp-card-detail">{_esc(t('board_issues', lang))}: {_esc(issues)}</div>
                </div>
                <div class="pp-story-badge {badge_cls}">{b_icon} {_esc(badge)}</div>
            </div>
            <div style="display:flex;gap:12px;margin-top:8px">
                <span class="pp-card-detail">{_esc(t('board_rack', lang))}: {_esc(rack)}</span>
                {f'<span class="pp-card-detail">{_esc(t("board_price", lang))}: AED {_esc(price)}</span>' if price else ''}
                <span class="pp-card-detail">{qcount} {_esc(t('board_open_questions', lang))}</span>
            </div>
            {view_btn}
        </div>
        """

    if not parts:
        cards_html = f'<div class="pp-empty">{_esc(t("not_logged_in", lang))}</div>'

    return f"""
    <div data-pane="stories">
        {header}
        <div class="pp-ring-row">{rings_html}</div>
        <div style="padding:0 16px 80px">
            {cards_html}
        </div>
    </div>
    """


# ── DISCOVER / QUEUE PANE ──


def render_discover(lang: str = "en", parts: list[dict] | None = None, uploaded_parts: list | None = None) -> str:
    if parts is None:
        client = get_client()
        parts = client.get_parts()[:10]

    # Build queue cards: parts that need action + unlinked files
    queue_items = []

    # Parts with open questions or issues
    for p in parts:
        qcount = p.get("open_questions", 0)
        if qcount > 0 or p.get("badge") in ("Information missing", "Specialist review required"):
            queue_items.append({
                "id": p.get("id", ""),
                "sku": p.get("sku", ""),
                "title": f"{p.get('sku', '')} — {p.get('make', '')} {p.get('model', '')}",
                "detail": f"{qcount} open questions" if qcount > 0 else "Needs review",
                "badge": p.get("badge", ""),
                "type": "part",
            })

    # Unlinked files (from upload)
    queue_items.append({
        "id": "unlinked-1",
        "sku": "IMG_M-03.jpg",
        "title": "Unlinked file: IMG_M-03.jpg",
        "detail": "This photo could not be matched to a part. Assign it now.",
        "badge": "Information missing",
        "type": "unlinked",
    })

    header = f"""
    <div class="pp-discover-header">
        <h2 class="pp-discover-title">{_esc(t('upload_assign_tray', lang))}</h2>
        <p class="pp-discover-sub">{_esc(t('upload_column_mapping_desc', lang))}</p>
    </div>
    """

    cards_html = ""
    for item in queue_items:
        badge_cls = _badge_class(item.get("badge", ""))
        cards_html += f"""
        <div class="pp-card" data-swipeable data-id="{_esc(item['id'])}" data-context="assign:check:Assign,later:close:Later" data-label="{_esc(item['sku'])}">
            <div class="pp-card-sku">{_esc(item['title'])}</div>
            <div class="pp-card-detail">{_esc(item['detail'])}</div>
            <div class="pp-card-actions">
                <button class="pp-btn pp-btn-primary" onclick="window.ppSwipeCard(this.closest('.pp-card'), 'right')">{icon('check', 20)} Do now</button>
                <button class="pp-btn pp-btn-secondary" onclick="window.ppSwipeCard(this.closest('.pp-card'), 'left')">{icon('close', 20)} Later</button>
            </div>
            <div class="pp-card-hint">
                <span>← Later</span>
                <span>Do now →</span>
            </div>
        </div>
        """

    if not queue_items:
        cards_html = f'<div class="pp-empty">Queue is empty</div>'

    return f"""
    <div data-pane="discover">
        {header}
        <div class="pp-card-stack">
            {cards_html}
        </div>
    </div>
    """


# ── MAP / OVERVIEW PANE ──


def render_map(lang: str = "en", seller: dict | None = None) -> str:
    seller = seller or {}
    corridor = seller.get("market_corridor", "")
    location = seller.get("location", "")

    return f"""
    <div data-pane="map" style="padding:16px;padding-top:calc(16px + env(safe-area-inset-top))">
        <h2 class="pp-stories-title">{_esc(t('corridor', lang))}</h2>
        <div style="background:var(--pp-bg-secondary);border-radius:12px;padding:24px;margin-bottom:16px;text-align:center">
            <div style="font-size:48px;margin-bottom:8px">🌍</div>
            <div style="font-size:18px;font-weight:700">{_esc(location)}</div>
            <div style="font-size:14px;color:var(--pp-text-secondary);margin-top:4px">{_esc(corridor)}</div>
        </div>
        <div class="pp-metric-row">
            <div class="pp-metric">
                <div class="pp-metric-value">{_esc(seller.get('marketplace', ''))}</div>
                <div class="pp-metric-label">{_esc(t('marketplace', lang))}</div>
            </div>
            <div class="pp-metric">
                <div class="pp-metric-value">{_esc(seller.get('handling_days', ''))}</div>
                <div class="pp-metric-label">{_esc(t('handling_days', lang))}</div>
            </div>
        </div>
        <div style="margin-top:16px">
            <div class="pp-profile-field">
                <span class="pp-profile-field-label">{_esc(t('rack_codes', lang))}</span>
                <span class="pp-profile-field-value">{_esc(seller.get('rack_codes', ''))}</span>
            </div>
            <div class="pp-profile-field">
                <span class="pp-profile-field-label">{_esc(t('languages', lang))}</span>
                <span class="pp-profile-field-value">{_esc(seller.get('language_primary', ''))} / {_esc(seller.get('language_secondary', ''))}</span>
            </div>
        </div>
    </div>
    """
