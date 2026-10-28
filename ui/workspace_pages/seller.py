"""Seller details: the profile the listings are built from (editing re-renders affected drafts)."""

from __future__ import annotations

import streamlit as st

from ui.workspace_client import GatewayError
from ui.workspace_ui import client

st.title("Seller details")

try:
    data = client().profile()
except GatewayError as exc:
    st.error(f"Could not load the profile: {exc.detail}")
    st.stop()
profile, editable = data["profile"], set(data["editable_fields"])

with st.form("ws_profile_form"):
    business = st.text_input("Business name", profile["business_name"], disabled="business_name" not in editable)
    city = st.text_input("City", profile["city"], disabled="city" not in editable)
    handling = st.number_input("Handling days", min_value=0, max_value=30, value=int(profile["handling_days"]),
                               disabled="handling_days" not in editable)
    returns = st.text_input("Return policy", profile.get("return_policy_ref") or "",
                            disabled="return_policy_ref" not in editable)
    if st.form_submit_button("Save", type="primary", icon=":material/save:"):
        changes = {k: v for k, v in {"business_name": business, "city": city, "handling_days": int(handling),
                                     "return_policy_ref": returns or None}.items()
                   if k in editable and v != profile.get(k)}
        if changes:
            try:
                result = client().update_profile(changes)
            except GatewayError as exc:
                st.error(f"Not saved: {exc.detail}")
            else:
                st.session_state.ws_profile = result["profile"]
                st.success(f"Saved {', '.join(result['changed_fields'])}; "
                           f"{len(result['rerendered_drafts'])} drafts re-rendered.")
        else:
            st.info("Nothing changed.")

st.caption(f"Corridor {profile['corridor']} · listing currency {profile['listing_currency']} · "
           f"checklist {profile['checklist_version']} · profile version {profile['version']}")
