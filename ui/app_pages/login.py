"""Login page — pick a seller name from the profile list.

The picked name is sent to the gateway, which resolves it to seller_id from
the profile table and returns a session token (TRD 4). After login the
seller card appears in the sidebar (handled by app.py).
"""

from __future__ import annotations

import streamlit as st

from ui.api_client import get_client
from ui.i18n import t

lang = st.session_state.get("lang", "en")
client = get_client()

# ── Header ──
st.title(t("app_title", lang))
st.caption(t("app_subtitle", lang))
st.divider()

st.subheader(t("login_select_profile", lang))

profiles = client.list_profiles()

# Render profile cards
cols = st.columns(len(profiles))
for col, p in zip(cols, profiles):
    with col:
        with st.container(border=True):
            st.markdown(f"### {p['display_name']}")
            if st.button(t("login_continue", lang), key=f"login_{p['seller_id']}", type="primary", use_container_width=True):
                session = client.login(p["display_name"])
                st.session_state.token = session["token"] if session else None
                st.session_state.seller = client.get_seller(p["seller_id"], st.session_state.token)
                st.session_state.logged_in = True
                st.switch_page("app_pages/board.py")

st.divider()
st.caption(t("not_logged_in", lang))
