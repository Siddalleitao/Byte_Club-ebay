"""Log in by picking a seller profile (identity comes from the session, never from files or chat)."""

from __future__ import annotations

import requests
import streamlit as st

from ui.workspace_client import GatewayError
from ui.workspace_ui import client

st.title("PartProof workspace")
st.caption("Evidence-first listings for used auto parts. Synthetic data only.")

try:
    names = [p["display_name"] for p in client().profiles()]
except (GatewayError, requests.RequestException):
    st.error("The API gateway is not reachable. Start it with `python scripts/run_api.py`.", icon=":material/cloud_off:")
    st.stop()

with st.form("login"):
    name = st.selectbox("Seller profile", names)
    if st.form_submit_button("Log in", type="primary", icon=":material/login:"):
        data = client().login(name)
        st.session_state.ws_profile = data["profile"]
        langs = data["profile"].get("languages") or ["en"]
        st.session_state.ws_lang = "ar" if langs[0] == "ar" else "en"
        st.rerun()
