"""Inventory board — 10 parts with status badges, question counts, filters.

Exit criteria: M-01 and M-07 appear on the board after an upload.
"""

from __future__ import annotations

import streamlit as st

from ui.api_client import get_client
from ui.i18n import t

lang = st.session_state.get("lang", "en")
client = get_client()

# ── Guard: must be logged in ──
if not st.session_state.get("logged_in"):
    st.warning(t("not_logged_in", lang))
    if st.button(t("login_continue", lang)):
        st.switch_page("app_pages/login.py")
    st.stop()

st.title(t("board_title", lang))

# ── Fetch parts ──
parts = client.get_parts()

# Show the first 10 (PRD: "10 parts")
board_parts = parts[:10]

# ── Filters ──
all_statuses = sorted({p.get("badge", "") for p in board_parts})
all_issues = sorted({label for p in board_parts for label in p.get("issue_types", [])})
all_racks = sorted({p.get("rack_code", "") for p in board_parts if p.get("rack_code")})

col_search, col_status, col_issue, col_rack = st.columns([2, 1, 1, 1])
with col_search:
    search = st.text_input(t("board_search", lang), placeholder="M-01, Toyota, 88210...", key="board_search")
with col_status:
    status_options = [t("board_all", lang)] + all_statuses
    status_sel = st.selectbox(t("board_filter_status", lang), status_options, key="board_status_filter")
with col_issue:
    issue_options = [t("board_all", lang)] + all_issues
    issue_sel = st.selectbox(t("board_filter_issue", lang), issue_options, key="board_issue_filter")
with col_rack:
    rack_options = [t("board_all", lang)] + all_racks
    rack_sel = st.selectbox(t("board_filter_rack", lang), rack_options, key="board_rack_filter")

# ── Apply filters ──
filtered = board_parts
if search:
    s = search.lower()
    filtered = [p for p in filtered if s in p.get("sku", "").lower() or s in p.get("make", "").lower() or s in p.get("part_number", "").lower()]
if status_sel and status_sel != t("board_all", lang):
    filtered = [p for p in filtered if p.get("badge") == status_sel]
if issue_sel and issue_sel != t("board_all", lang):
    filtered = [p for p in filtered if issue_sel in p.get("issue_types", [])]
if rack_sel and rack_sel != t("board_all", lang):
    filtered = [p for p in filtered if p.get("rack_code") == rack_sel]

st.caption(f"{len(filtered)} / {len(board_parts)} parts")

# ── Badge colours ──
badge_colors = {
    "Approved": ("green", "✅"),
    "Ready for seller review": ("blue", "🔵"),
    "Information missing": ("orange", "⚠️"),
    "Specialist review required": ("red", "🔴"),
}

# ── Part cards ──
for p in filtered:
    badge = p.get("badge", "Information missing")
    color, icon = badge_colors.get(badge, ("gray", "❓"))
    qcount = p.get("open_questions", 0)

    with st.container(border=True):
        col_sku, col_donor, col_rack2, col_price, col_q, col_action = st.columns([1.5, 2, 1, 1, 1, 1])

        with col_sku:
            st.markdown(f"**{p.get('sku', '')}**")
            st.caption(f"{p.get('make', '')} {p.get('model', '')} {p.get('year', '')}")
            st.caption(f"{p.get('part_type', '')} — {t('board_part_number', lang)}: {p.get('part_number', '')}")

        with col_donor:
            st.caption(f"{t('board_donor', lang)}: {p.get('donor_market', '')}")
            st.caption(f"{t('board_issues', lang)}: {', '.join(p.get('issue_types', [])) or '—'}")

        with col_rack2:
            st.caption(f"{t('board_rack', lang)}: {p.get('rack_code', '—')}")

        with col_price:
            price = p.get("asking_price_aed", "")
            if price:
                st.caption(f"{t('board_price', lang)}: AED {price}")
            else:
                st.caption(f"{t('board_price', lang)}: —")

        with col_q:
            if qcount > 0:
                st.metric(t("board_open_questions", lang), qcount)
            else:
                st.metric(t("board_open_questions", lang), 0)

        with col_action:
            # Status badge
            badge_key_map = {
                "Approved": "badge_approved",
                "Ready for seller review": "badge_ready",
                "Information missing": "badge_missing",
                "Specialist review required": "badge_specialist",
            }
            badge_label = t(badge_key_map.get(badge, "badge_missing"), lang)
            st.markdown(f":{color}-badge[{icon} {badge_label}]")
            # Question dialog
            if qcount > 0:
                if st.button(t("board_view_questions", lang), key=f"qbtn_{p.get('id')}", use_container_width=True):
                    st.session_state["selected_part"] = p
                    st.switch_page("app_pages/questions.py")

st.divider()
if not filtered:
    st.info("No parts match the current filters.")
