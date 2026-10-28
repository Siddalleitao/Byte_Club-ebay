"""Open-questions dialog page for a single part.

Shows the list of open questions with an answer input for each.
"""

from __future__ import annotations

import streamlit as st

from ui.api_client import get_client
from ui.i18n import t

lang = st.session_state.get("lang", "en")
client = get_client()

if not st.session_state.get("logged_in"):
    st.switch_page("app_pages/login.py")

part = st.session_state.get("selected_part")
if not part:
    st.switch_page("app_pages/board.py")

st.title(f"{t('questions_title', lang)} — {part.get('sku', '')}")
st.caption(f"{part.get('make', '')} {part.get('model', '')} {part.get('year', '')}")

questions = client.get_questions(part.get("id", part.get("sku", "")))

if not questions:
    st.info(t("questions_no_questions", lang))
    if st.button(f"← {t('board_title', lang)}"):
        st.switch_page("app_pages/board.py")
    st.stop()

priority_colors = {"high": "🔴", "medium": "🟡", "low": "🟢"}

for q in questions:
    with st.container(border=True):
        col_q, col_meta = st.columns([4, 1])
        with col_q:
            st.markdown(q.get("question_text", ""))
        with col_meta:
            st.caption(f"{t('questions_edge_case', lang)}: {q.get('edge_case', '')}")
            priority = q.get("priority", "medium")
            st.caption(f"{priority_colors.get(priority, '⚪')} {t('questions_priority', lang)}: {priority}")

        answer = st.text_input(
            t("questions_answer_placeholder", lang),
            key=f"answer_{q.get('id')}",
            label_visibility="collapsed",
            placeholder=t("questions_answer_placeholder", lang),
        )
        if answer:
            if st.button(t("questions_submit", lang), key=f"submit_{q.get('id')}"):
                st.success(f"✅ Answer recorded for {q.get('edge_case', '')}")

st.divider()
if st.button(f"← {t('board_title', lang)}"):
    st.switch_page("app_pages/board.py")
