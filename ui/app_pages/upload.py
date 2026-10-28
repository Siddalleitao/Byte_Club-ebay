"""Upload page — drag in photos, spreadsheet, donor notes.

Features:
  - Per-part progress bar while the background queue runs
  - Manual-assign tray for unlinked files
  - Column-mapping confirmation
"""

from __future__ import annotations

import time

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

st.title(t("upload_title", lang))

# ── File upload zones ──
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(f"#### {t('upload_photos', lang)}")
    photos = st.file_uploader(
        t("upload_drag_here", lang),
        type=["png", "jpg", "jpeg", "heic"],
        accept_multiple_files=True,
        key="photo_uploader",
        label_visibility="collapsed",
    )

with col2:
    st.markdown(f"#### {t('upload_spreadsheet', lang)}")
    spreadsheet = st.file_uploader(
        t("upload_drag_here", lang),
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=False,
        key="spreadsheet_uploader",
        label_visibility="collapsed",
    )

with col3:
    st.markdown(f"#### {t('upload_donor_notes', lang)}")
    donor_notes = st.file_uploader(
        t("upload_drag_here", lang),
        type=["txt", "md", "csv", "json"],
        accept_multiple_files=True,
        key="notes_uploader",
        label_visibility="collapsed",
    )

# ── File summary ──
total_files = len(photos or []) + (1 if spreadsheet else 0) + len(donor_notes or [])
if total_files > 0:
    st.success(f"{total_files} {t('upload_files_queued', lang)}")
else:
    st.info(t("upload_no_files", lang))

# ── Start processing ──
if total_files > 0:
    if st.button(t("upload_start", lang), type="primary", icon=":material/play_arrow:"):
        st.session_state["upload_started"] = True
        st.rerun()

# ── Per-part progress bar ──
if st.session_state.get("upload_started"):
    st.divider()
    st.subheader(t("upload_progress", lang))

    # Build a mock file list
    file_list = []
    for ph in (photos or []):
        file_list.append({"name": ph.name, "type": "photo"})
    if spreadsheet:
        file_list.append({"name": spreadsheet.name, "type": "spreadsheet"})
    for dn in (donor_notes or []):
        file_list.append({"name": dn.name, "type": "donor_notes"})

    # Simulate upload + processing
    result = client.upload_files(file_list)

    # Per-part progress (mock 10 parts)
    progress_parts = ["M-01", "M-02", "M-03", "M-04", "M-05", "M-06", "M-07", "M-08", "M-09", "M-10"]
    progress_bar = st.progress(0, text=t("upload_progress", lang))

    completed = 0
    linked = []
    unlinked = []

    for i, sku in enumerate(progress_parts):
        time.sleep(0.15)
        completed = i + 1
        pct = int(completed / len(progress_parts) * 100)
        progress_bar.progress(pct, text=f"{t('upload_progress', lang)} — {sku} ({completed}/{len(progress_parts)})")
        # Simulate: M-03 photo is unlinked
        if sku == "M-03":
            unlinked.append({"file": f"IMG_{sku}.jpg", "sku": ""})
        else:
            linked.append(sku)

    progress_bar.progress(100, text=t("upload_complete", lang))
    st.success(f"✅ {t('upload_complete', lang)} — {len(linked)} parts linked")

    st.session_state["uploaded_parts"] = linked

    # ── Manual-assign tray ──
    st.divider()
    st.subheader(t("upload_assign_tray", lang))

    if unlinked:
        for item in unlinked:
            col_file, col_assign, col_btn = st.columns([2, 2, 1])
            with col_file:
                st.text(item["file"])
            with col_assign:
                st.selectbox(
                    f"Assign to SKU",
                    options=["M-01", "M-02", "M-03", "M-04", "M-05", "M-06", "M-07", "M-08", "M-09", "M-10"],
                    key=f"assign_{item['file']}",
                )
            with col_btn:
                if st.button("Link", key=f"link_{item['file']}"):
                    st.success(f"Linked {item['file']}")
    else:
        st.info(t("upload_assign_tray_empty", lang))

    # ── Column-mapping confirmation ──
    st.divider()
    st.subheader(t("upload_column_mapping", lang))
    st.caption(t("upload_column_mapping_desc", lang))

    default_mapping = {
        "sku": "sku",
        "make": "make",
        "model": "model",
        "year": "year",
        "side": "side",
        "part_number": "part_number",
        "part_type": "part_type",
        "notes": "notes",
    }

    cols_map = st.columns(4)
    mapping = {}
    for i, (field, source_col) in enumerate(default_mapping.items()):
        with cols_map[i % 4]:
            mapping[field] = st.selectbox(field, options=list(default_mapping.keys()), index=list(default_mapping.keys()).index(source_col), key=f"map_{field}")

    if st.button(t("upload_confirm_mapping", lang), type="primary"):
        st.session_state["column_mapping"] = mapping
        st.success("✅ Mapping confirmed. Parts are now on the board.")
        if st.button(f"→ {t('board_title', lang)}"):
            st.switch_page("app_pages/board.py")
