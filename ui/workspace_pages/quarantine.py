"""Quarantine: text from files, notes, OCR or chat that looked like an instruction (J3).

It is kept as data. It never changes a status, a fitment or a checklist item.
"""

from __future__ import annotations

import streamlit as st

from ui.workspace_client import GatewayError
from ui.workspace_ui import client, open_part

st.title("Quarantine")
st.caption("Text that looked like an instruction is kept as data and shown here with its source and reason. "
           "It has no effect on any status.")

try:
    records = client().quarantine()
except GatewayError as exc:
    st.error(f"Could not load quarantine: {exc.detail}")
    st.stop()

if not records:
    st.success("Nothing quarantined.", icon=":material/verified_user:")
    st.stop()

skus = sorted({r["sku"] for r in records if r.get("sku")})
pick = st.pills("Part", ["All", *skus, "Chat"], default="All", key="ws_quarantine_filter")


def matches(record: dict) -> bool:
    if pick in (None, "All"):
        return True
    return record["channel"] == "chat" if pick == "Chat" else record.get("sku") == pick


for record in filter(matches, records):
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.badge(record.get("sku") or "chat", color="red", icon=":material/gpp_maybe:")
            st.badge(f"{record['channel']} · {record['trust_tier']}", color="gray")
            if record.get("classifier_verdict"):
                st.badge(f"classifier: {record['classifier_verdict']}", color="violet")
            st.caption(record["created_at"])
        st.markdown(f"**Source:** `{record['source']}`")
        st.markdown(f"**Reason:** {record['reason']}")
        if record["pattern_ids"] or record["flags"]:
            st.caption("Matched: " + ", ".join(record["pattern_ids"] + record["flags"]))
        st.code(record["text"], language=None, wrap_lines=True)
        if record.get("sku") and st.button("Open part", key=f"ws_q_open_{record['id']}", icon=":material/open_in_new:"):
            open_part(record["sku"])
