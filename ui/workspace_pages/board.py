"""Inventory board: every part's status, readiness, waiting questions and quarantine flags."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui.workspace_client import GatewayError
from ui.workspace_ui import client, open_part

st.title("Inventory board")

top = st.container(horizontal=True, vertical_alignment="bottom")
with top:
    if st.button("Run pipeline", icon=":material/play_arrow:", type="primary"):
        with st.spinner("Extracting, reconciling and drafting every part"):
            try:
                client().run_pipeline(wait=True)
            except GatewayError as exc:
                st.error(f"The pipeline did not start: {exc.detail}")
    st.caption("Parts stop at the seller's questions (CP3) and at approval (CP7). Nothing is approved for you.")

try:
    parts = client().board()
except GatewayError as exc:
    st.error(f"Could not load the board: {exc.detail}")
    st.stop()

counts = {r: sum(p["readiness"] == r for p in parts) for r in
          ("Ready for seller review", "Information missing", "Specialist review required")}
with st.container(horizontal=True):
    st.metric("Parts", len(parts), border=True)
    st.metric("Ready for seller review", counts["Ready for seller review"], border=True)
    st.metric("Information missing", counts["Information missing"], border=True)
    st.metric("Specialist review", counts["Specialist review required"], border=True)
    st.metric("Quarantined items", sum(p["quarantined"] for p in parts), border=True)

readiness = st.pills("Readiness", ["All", *counts], default="All", key="ws_board_filter")
shown = [p for p in parts if readiness in (None, "All") or p["readiness"] == readiness]

table = pd.DataFrame([{
    "SKU": p["sku"],
    "Title": p["title"],
    "Status": p["status"],
    "Readiness": p["readiness"] or "Not evaluated",
    "Questions": p["questions_waiting"],
    "Quarantine": f"⚠ {p['quarantined']}" if p["quarantined"] else "",
    "Compatibility": (p["compatibility"] or "").replace("_", " "),
} for p in shown])

event = st.dataframe(
    table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="ws_board_table",
    column_config={
        "SKU": st.column_config.TextColumn(width="small", pinned=True),
        "Questions": st.column_config.NumberColumn("Questions", width="small",
                                                   help="Questions waiting in the latest round"),
        "Quarantine": st.column_config.TextColumn(width="small", help="Text in this part's files that was quarantined as an "
                                                       "injection attempt. It never changes a status."),
    },
)
if event.selection.rows:
    open_part(table.iloc[event.selection.rows[0]]["SKU"])

with st.container(horizontal=True, vertical_alignment="bottom"):
    sku = st.selectbox("Part", [p["sku"] for p in parts], key="ws_board_pick")
    if st.button("Open part", icon=":material/open_in_new:"):
        open_part(sku)
