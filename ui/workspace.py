"""PartProof workspace: the inventory board, part detail, quarantine and seller details (PRD 3, J1 to J5).

Run: python scripts/run_workspace.py   (or: streamlit run ui/workspace.py --server.port 8502)
The camera-first shell (ui/app.py) is unchanged; this is the desk view of the same gateway.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from ui.workspace_client import GatewayError  # noqa: E402
from ui.workspace_ui import client, logged_in  # noqa: E402

st.set_page_config(page_title="PartProof workspace", page_icon=":material/inventory_2:", layout="wide")

login = st.Page("workspace_pages/login.py", title="Log in", icon=":material/login:")
board = st.Page("workspace_pages/board.py", title="Inventory board", icon=":material/view_list:", default=True)
part = st.Page("workspace_pages/part.py", title="Part detail", icon=":material/frame_inspect:")
quarantine = st.Page("workspace_pages/quarantine.py", title="Quarantine", icon=":material/gpp_maybe:")
seller = st.Page("workspace_pages/seller.py", title="Seller details", icon=":material/storefront:")

if not logged_in():
    page = st.navigation([login], position="hidden")
else:
    page = st.navigation({"Inventory": [board, part, quarantine], "Account": [seller]})
    with st.sidebar:
        profile = st.session_state.get("ws_profile", {})
        with st.container(border=True):
            st.markdown(f"**{profile.get('business_name', '')}**")
            st.caption(f"{profile.get('display_name', '')} · {profile.get('city', '')} · {profile.get('corridor', '')}")
        st.session_state.setdefault("ws_lang", "en")
        st.segmented_control("Chat language", ["en", "ar"], key="ws_lang",
                             format_func=lambda c: {"en": "English", "ar": "العربية"}[c])
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            try:
                client().logout()
            except GatewayError:
                pass
            for key in [k for k in st.session_state if k.startswith("ws_")]:
                del st.session_state[key]
            st.rerun()

page.run()
