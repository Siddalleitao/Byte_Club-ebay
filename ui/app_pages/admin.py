"""Admin page — editable seller details + reference-data viewer.

The seller-details form edits the profile behind the seller context card
(TRD 4) through PUT /me/profile. Saving invalidates the cached prompt prefix
and re-renders drafts that use a profile field such as handling time.
The reference-data viewer shows the compatibility reference version
and the checklist version with date and its TEST label.
"""

from __future__ import annotations

import streamlit as st

from ui.api_client import get_client
from ui.i18n import t

lang = st.session_state.get("lang", "en")
client = get_client()

if not st.session_state.get("logged_in"):
    st.warning(t("not_logged_in", lang))
    if st.button(t("login_continue", lang)):
        st.switch_page("app_pages/login.py")
    st.stop()

st.title(t("admin_title", lang))

# ── Edit seller details ──
st.subheader(t("admin_edit_profile", lang))
st.caption(t("admin_edit_profile_desc", lang))

token = st.session_state.get("token")
loaded = client.get_profile(token)

if loaded is None:
    st.info("Seller details can be edited when the API gateway is running and you are logged in.")
else:
    profile = loaded["profile"]
    with st.form("seller_profile_form"):
        col1, col2 = st.columns(2)

        with col1:
            display_name = st.text_input("Name", value=profile["display_name"])
            business_name = st.text_input(t("business", lang), value=profile["business_name"])
            city = st.text_input(t("location", lang), value=profile["city"])
            languages = st.multiselect(t("languages", lang), ["en", "ar"], default=profile["languages"])
            reply_language = st.selectbox(
                "Reply language",
                ["match_user", "en", "ar"],
                index=["match_user", "en", "ar"].index(profile["reply_language"]),
            )
            corridor = st.text_input(t("corridor", lang), value=profile["corridor"])

        with col2:
            handling_days = st.number_input(
                t("handling_days", lang), min_value=0, max_value=30, step=1, value=profile["handling_days"]
            )
            return_policy_ref = st.text_input("Returns policy", value=profile["return_policy_ref"] or "")
            inventory_currency = st.text_input("Inventory currency", value=profile["inventory_currency"])
            listing_currency = st.text_input("Listing currency", value=profile["listing_currency"])
            fx_rate_source = st.text_input("FX rate source", value=profile["fx_rate_source"] or "")
            rack_codes = st.text_input(t("rack_codes", lang), value=", ".join(profile["rack_codes"]))

        st.caption(
            f"Seller ID {profile['seller_id']} · product family {profile['product_family']} · "
            f"checklist {profile['checklist_version']} · profile v{profile['version']}"
        )
        submitted = st.form_submit_button(t("admin_save", lang), type="primary")

    if submitted:
        ok, result = client.update_profile(token, {
            "display_name": display_name,
            "business_name": business_name,
            "city": city,
            "languages": languages,
            "reply_language": reply_language,
            "corridor": corridor,
            "handling_days": int(handling_days),
            "return_policy_ref": return_policy_ref or None,
            "inventory_currency": inventory_currency.strip().upper(),
            "listing_currency": listing_currency.strip().upper(),
            "fx_rate_source": fx_rate_source or None,
            "rack_codes": [c.strip() for c in rack_codes.split(",") if c.strip()],
        })
        if not ok:
            st.error(f"Not saved: {result.get('detail')}")
        elif not result["changed_fields"]:
            st.info("No changes to save.")
        else:
            st.session_state.seller = client.get_seller(result["profile"]["seller_id"], token)
            st.success(f"✅ {t('admin_saved', lang)}")
            st.caption(
                f"Changed: {', '.join(result['changed_fields'])} · "
                f"drafts re-rendered: {len(result['rerendered_drafts'])}"
            )

# ── Reference data viewer ──
st.divider()
st.subheader(t("admin_reference_data", lang))

ref = client.get_reference_data()

# Compatibility reference
compat = ref.get("compat_ref", {})
col_c1, col_c2 = st.columns(2)
with col_c1:
    st.metric(t("admin_compat_version", lang), compat.get("version", "—"))
with col_c2:
    st.metric(t("admin_compat_rows", lang), compat.get("rows", 0))

with st.expander(f"{t('admin_compat_version', lang)} — detail"):
    entries = compat.get("entries", [])
    if entries:
        st.dataframe(entries, use_container_width=True, hide_index=True)
    else:
        st.caption("No entries")

# Checklist version with TEST label
st.divider()
checklist = ref.get("checklist", {})

col_v1, col_v2, col_v3 = st.columns(3)
with col_v1:
    st.metric(t("admin_checklist_version", lang), checklist.get("version", "—"))
with col_v2:
    st.metric(t("admin_checklist_date", lang), checklist.get("date", "—"))
with col_v3:
    st.metric(t("admin_checklist_purpose", lang), checklist.get("purpose", "—"))

# TEST label banner
st.warning(
    f"⚠️ **{t('admin_test_label', lang)}** — {t('admin_test_label_desc', lang)}"
)

with st.expander(f"{t('admin_checklist_disclaimer', lang)}"):
    st.write(checklist.get("disclaimer", ""))
