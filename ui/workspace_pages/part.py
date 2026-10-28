"""Part detail (PRD 3): evidence, listing editor, readiness, history, and the chat beside them.

The UI only calls the gateway. Values change through the chat (answers and corrections)
or the listing editor, and the guard and checklist re-run on every change.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from ui.workspace_client import GatewayError
from ui.workspace_ui import (
    attribute_chip,
    client,
    compat_chip,
    crop,
    draw_regions,
    gateway_error,
    item_result,
    lang,
    photo_bytes,
    readiness_chip,
    readable_value,
    rtl_html,
    show_text,
    source_label,
    status_chip,
)

api = client()

# ── Which part ──────────────────────────────────────────────

try:
    board = {p["sku"]: p for p in api.board()}
except GatewayError as exc:
    gateway_error(exc)
    st.stop()
skus = list(board)
current = st.session_state.get("ws_sku") if st.session_state.get("ws_sku") in board else skus[0]
sku = st.selectbox("Part", skus, index=skus.index(current), key="ws_part_pick", label_visibility="collapsed")
st.session_state.ws_sku = sku
row = board[sku]

st.session_state.setdefault("ws_chat", {})
history_log: list[dict] = st.session_state.ws_chat.setdefault(sku, [])
notice = st.session_state.pop("ws_notice", None)

# ── Quick replies (top three asked questions) ───────────────

OPTIONS = {
    "confirm_test_status": {
        "en": [("Untested", "untested"), ("Tested and working", "tested and working"),
               ("Partly tested", "partly tested"), ("Failed", "failed")],
        "ar": [("لم يتم فحصها", "لم يتم فحصها"), ("تم فحصها وتعمل", "تم فحصها وتعمل"),
               ("فحص جزئي", "فحص جزئي"), ("لا تعمل", "لا تعمل")],
    },
    "confirm_position": {
        "en": [("Left", "left"), ("Right", "right")],
        "ar": [("يسار", "اليسار"), ("يمين", "اليمين")],
    },
}
HINTS = {
    "weigh_and_measure": {"en": 'Type the packed weight and size, e.g. "2.1 kg, 40 x 30 x 25 cm".',
                          "ar": "اكتب الوزن والأبعاد، مثل «١٫٨ كيلو، ٤٥ في ٣٠ في ٢٥ سم»."},
    "photo": {"en": "Attach the photo with the clip in the chat box.",
              "ar": "أرفق الصورة من زر المرفقات في مربع المحادثة."},
}


def quick_replies(question: dict, conflicts: list[dict]) -> list[tuple[str, str]]:
    key, language = question.get("action_key"), lang()
    if key in OPTIONS:
        return OPTIONS[key][language]
    if key == "confirm_donor_year":
        values = [s["value"] for c in conflicts if c["field"] == "donor_year" for s in c["sides"]]
        if language == "ar":
            return [(v, f"السيارة المانحة موديل {v}") for v in values]
        return [(v, f"The donor year is {v}") for v in values]
    return []


# ── Chat handling (before the tabs render, so they show the result) ──


def send(text: str) -> None:
    """Stream one message through the concierge into the chat log."""
    history_log.append({"role": "user", "text": text})
    with chat_box:
        with st.chat_message("user"):
            show_text(text)
        with st.chat_message("assistant"):
            placeholder = st.empty()
            reply, buffer = None, ""
            try:
                for event in api.stream_chat(text, sku):
                    if event["type"] == "ack" and not buffer:
                        placeholder.markdown(rtl_html(event["text"] + " …"), unsafe_allow_html=True)
                    elif event["type"] == "token":
                        buffer += event["text"]
                        placeholder.markdown(rtl_html(buffer), unsafe_allow_html=True)
                    elif event["type"] == "done":
                        reply = event["reply"]
            except GatewayError as exc:
                buffer = f"Not sent: {exc.detail}"
            text_out = (reply or {}).get("text") or buffer
            placeholder.markdown(rtl_html(text_out), unsafe_allow_html=True)
    history_log.append({"role": "assistant", "text": text_out})


def send_photo(name: str, content: bytes) -> None:
    history_log.append({"role": "user", "text": f"📎 {name}"})
    try:
        result = api.upload_photo(sku, name, content)
        message = result["message"]
    except GatewayError as exc:
        message = f"The photo was not added: {exc.detail}"
    history_log.append({"role": "assistant", "text": message})


try:
    detail = api.detail(sku)
except GatewayError as exc:
    gateway_error(exc)
    st.stop()

main, side = st.columns([3, 2], gap="large")

with side:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader("Chat", anchor=False)
    cards = detail["questions"][:3]
    pending: str | None = None
    if cards:
        st.caption("Waiting on you" if lang() == "en" else "بانتظار ردك")
        for q in cards:
            with st.container(border=True):
                text = q["text_ar"] if lang() == "ar" and q.get("text_ar") else q["text_en"]
                show_text(f"{q['rank'] or ''}. {text}")
                guide = q.get("guide_ar") if lang() == "ar" else q.get("guide_en")
                if guide:
                    with st.expander("دليل التصوير" if lang() == "ar" else "Photo guide", icon=":material/photo_camera:"):
                        st.markdown(rtl_html(guide), unsafe_allow_html=True)
                options = quick_replies(q, detail["conflicts"])
                if options:
                    with st.container(horizontal=True):
                        for label, message in options:
                            if st.button(label, key=f"ws_qr_{q['question_id']}_{label}", type="secondary"):
                                pending = message
                hint = HINTS.get(q.get("action_key")) or (HINTS["photo"] if q.get("kind") == "photo" else None)
                if hint:
                    st.caption(hint[lang()])
    chat_box = st.container(height=420, border=True)
    with chat_box:
        if not history_log:
            st.caption("Answer the questions above, correct a value (\"Change M-04 to right side\"), "
                       "or attach a sharper photo.")
        for message in history_log:
            with st.chat_message(message["role"]):
                show_text(message["text"])
    prompt = st.chat_input("اكتب رسالة…" if lang() == "ar" else "Message the assistant…",
                           accept_file=True, file_type=["jpg", "jpeg", "png", "webp"], key="ws_chat_input")
    if prompt:
        for file in prompt.files or []:
            send_photo(file.name, file.getvalue())
        if prompt.text:
            pending = prompt.text
    if pending:
        send(pending)
        st.rerun()
    if prompt and prompt.files:
        st.rerun()

# ── Header ──────────────────────────────────────────────────

draft = api.draft(sku)
with main:
    st.header(sku, anchor=False)
    st.markdown(f"**{(draft or {}).get('title') or row['title']}**")
    with st.container(horizontal=True):
        status_chip(row["status"])
        readiness_chip(row["readiness"])
        compat_chip(row["compatibility"])
        if row["quarantined"]:
            st.badge(f"{row['quarantined']} quarantined", color="red", icon=":material/gpp_maybe:")
    if notice:
        st.success(notice, icon=":material/check_circle:")

    if draft and draft["badge"] == "updating":
        @st.fragment(run_every="2s")
        def summary_watch() -> None:
            latest = api.draft(sku)
            if latest and latest["badge"] != "updating":
                st.rerun()
            st.badge("Updating summary", color="blue", icon=":material/sync:")

        summary_watch()

    evidence_tab, listing_tab, readiness_tab, history_tab = st.tabs(
        [":material/photo_library: Evidence", ":material/edit_note: Listing", ":material/checklist: Readiness",
         ":material/history: History"])

photos = {p["name"]: p for p in detail["photos"]}


def photo_for(pointer: str | None) -> dict | None:
    """The photo behind a pointer such as `photo:M-07_p3.jpg` or a stored path."""
    name = (pointer or "").replace("\\", "/").rsplit("/", 1)[-1].removeprefix("photo:")
    return photos.get(name)


def sources_help(sentence: dict) -> str:
    lines = [f"{source_label(e)}: “{(e['text'] or '')[:140]}”" for e in sentence["evidence"]]
    lines += [f"{s}" if isinstance(s, str) else json.dumps(s, ensure_ascii=False) for s in sentence["sources"]]
    return "\n\n".join(lines) or "Seller profile or rule template"


def show_sources(sentence: dict) -> None:
    for e in sentence["evidence"]:
        st.markdown(f"**{source_label(e)}** · {e['kind'].replace('_', ' ')}")
        st.code(e["text"] or "", language=None, wrap_lines=True)
        photo = photo_for(e["pointer"])
        if photo:
            flagged = [r for r in photo["regions"] if r["flagged"]] or photo["regions"]
            content = photo_bytes(sku, photo["evidence_id"])
            st.image(crop(content, flagged[0]["box"]) if flagged else content,
                     caption=flagged[0]["label"] if flagged else photo["name"])
    for s in sentence["sources"]:
        st.caption(s if isinstance(s, str) else json.dumps(s, ensure_ascii=False))


# ── Evidence tab ────────────────────────────────────────────

with evidence_tab:
    st.subheader("Photos", anchor=False)
    if not detail["photos"]:
        st.caption("No photos.")
    for start in range(0, len(detail["photos"]), 3):
        for col, photo in zip(st.columns(3), detail["photos"][start:start + 3]):
            with col:
                content = photo_bytes(sku, photo["evidence_id"])
                st.image(draw_regions(content, photo["regions"]),
                         caption=f"{photo['name']} · {', '.join(photo['views']) or 'uploaded'}")
                for region in photo["regions"]:
                    if region["flagged"]:
                        st.markdown(f":red[:material/flag: {region['reason']}]")

    st.subheader("Attributes", anchor=False)
    for attr in detail["attributes"]:
        c1, c2, c3, c4 = st.columns([2, 3, 2, 3], vertical_alignment="center")
        c1.markdown(f"**{attr['field'].replace('_', ' ')}**")
        with c2:
            show_text(readable_value(attr["value"]))
        with c3:
            attribute_chip(attr["status"])
        with c4:
            if attr["sources"]:
                latest = attr["sources"][-1]  # the newest source: a seller answer outranks the photo reading
                st.markdown(source_label(latest), help="\n\n".join(
                    f"{source_label(s)}: “{s['text'][:140]}”" for s in reversed(attr["sources"])))
            else:
                st.caption("no source")

    if detail["conflicts"]:
        st.subheader("Conflicts", anchor=False)
    for conflict in detail["conflicts"]:
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{conflict['field'].replace('_', ' ')}**")
                if conflict["open"]:
                    st.badge("Open: the seller decides", color="red", icon=":material/compare_arrows:")
                else:
                    st.badge(f"Resolved: {conflict['resolved_value']}", color="green", icon=":material/check:")
            for col, side_ in zip(st.columns(len(conflict["sides"])), conflict["sides"]):
                with col, st.container(border=True):
                    st.metric(side_["source"].replace("_", " "), side_["value"] or "—")
                    st.caption(source_label(side_) if side_["pointer"] else side_["source"])
                    if side_["photo"]:
                        content = photo_bytes(sku, side_["photo"]["evidence_id"])
                        st.image(crop(content, side_["photo"]["box"]), caption="label crop")
                    elif side_["excerpt"]:
                        st.code(side_["excerpt"], language=None, wrap_lines=True)

    st.subheader("Description claims", anchor=False)
    if draft is None:
        st.info("The description is drafted after the open questions are answered (CP3).", icon=":material/hourglass:")
    else:
        st.caption("Hover a sentence for its source, or open the info button for the full evidence.")
        blocks = [("summary", "Summary", draft["summary"]["sentences"])] + [
            (s["section"], s["title"], s["sentences"]) for s in draft["sections"] if s["section"] != "summary"]
        for key, title, sentences in blocks:
            if not sentences:
                continue
            st.markdown(f"**{title}**")
            for sentence in sentences:
                text_col, info_col = st.columns([12, 1], vertical_alignment="center")
                text_col.markdown(sentence["text"], help=sources_help(sentence))
                with info_col.popover("", icon=":material/info:", help="Sources"):
                    show_sources(sentence)

# ── Listing editor ──────────────────────────────────────────

with listing_tab:
    if draft is None:
        st.info("No draft yet. Answer the questions in the chat; the listing is drafted once nothing is missing.",
                icon=":material/hourglass:")
    else:
        version = draft["version"]
        limit = draft["title_limit"]
        title = st.text_input("Title", draft["title"] or "", max_chars=limit, key=f"ws_title_{sku}_{version}")
        st.caption(f"{len(title)}/{limit} characters")
        for finding in draft["guard_findings"]:
            st.warning(f"The guard removed “{finding['phrase']}” from the {finding['target'].replace('_', ' ')}: "
                       f"{finding['reason']}.", icon=":material/block:")
        c1, c2 = st.columns(2)
        category = c1.text_input("Category", draft["category"] or "", key=f"ws_category_{sku}_{version}")
        price_now = "" if draft["price"] is None else f"{draft['price']:g} {draft['currency'] or ''}".strip()
        price = c2.text_input("Price with currency", price_now, key=f"ws_price_{sku}_{version}",
                              help="Write the currency too, e.g. 430 AED. A price without one is not guessed (E40).")

        st.markdown("**Mandatory disclosure** · locked")
        st.text_area("Mandatory disclosure", draft["disclosure"] or "", disabled=True, label_visibility="collapsed",
                     key=f"ws_disclosure_{sku}_{version}",
                     help="Built from the evidence ledger. Change it by answering or correcting in the chat.")

        st.markdown("**Description sections**")
        for section in draft["sections"]:
            if section["section"] in ("summary", "condition") or not section["sentences"]:
                continue
            with st.expander(section["title"]):
                for sentence in section["sentences"]:
                    st.markdown(sentence["text"], help=sources_help(sentence))

        if draft["removed_by_guard"]:
            st.markdown("**Blocked claims**")
            for removed in draft["removed_by_guard"]:
                st.error(f"“{removed['text']}”: {removed['reason']}", icon=":material/block:")

        notes_now = [n["text"] for n in draft["seller_edits"].get("notes", [])]
        notes = st.text_area("Your notes (one sentence per line; the guard checks each one)", "\n".join(notes_now),
                             key=f"ws_notes_{sku}_{version}")

        st.markdown("**Item specifics**")
        protected = set(draft["protected_specifics"])
        fixed = {k: v for k, v in draft["item_specifics"].items() if k in protected}
        st.dataframe(pd.DataFrame({"Specific": list(fixed), "Value": list(fixed.values())}), hide_index=True,
                     width="stretch")
        extras = draft["seller_edits"].get("item_specifics", {})
        edited = st.data_editor(pd.DataFrame({"Specific": list(extras), "Value": list(extras.values())},
                                             dtype="string"),
                                num_rows="dynamic", hide_index=True, width="stretch", key=f"ws_specifics_{sku}_{version}")

        save, approve = st.columns(2)
        if save.button("Save and re-check", icon=":material/fact_check:", type="primary", width="stretch"):
            new_specifics = {str(r["Specific"]).strip(): str(r["Value"]).strip() for _, r in edited.iterrows()
                             if pd.notna(r["Specific"]) and str(r["Specific"]).strip()}
            new_notes = [n.strip() for n in notes.splitlines() if n.strip()]
            changes = {k: v for k, v in {
                "title": title.strip() if title.strip() != (draft["title"] or "") else None,
                "category": category.strip() if category.strip() != (draft["category"] or "") else None,
                "price": price.strip() if price.strip() and price.strip() != price_now else None,
                "notes": new_notes if new_notes != notes_now else None,
                "item_specifics": new_specifics if new_specifics != extras else None,
            }.items() if v is not None}
            if not changes:
                st.info("Nothing changed.")
            else:
                try:
                    result = api.edit_draft(sku, changes)
                except GatewayError as exc:
                    gateway_error(exc)
                else:
                    edit = result["edit"]
                    removed = len(edit["guard_removed"])
                    st.session_state.ws_notice = (
                        f"Saved. The guard re-ran: {removed} item{'s' if removed != 1 else ''} blocked. "
                        f"Readiness: {edit['readiness']}.")
                    st.rerun()

        approve_help = None if draft["approve_enabled"] else (
            "The summary is updating." if draft["badge"] == "updating" else
            f"Readiness is {draft['readiness']}; approval needs Ready for seller review.")
        if approve.button("Approve and export", icon=":material/verified:", width="stretch",
                          disabled=not draft["approve_enabled"], help=approve_help):
            try:
                snap = api.approve(sku)
            except GatewayError as exc:
                gateway_error(exc)
            else:
                st.session_state.ws_notice = f"{sku} is {snap['status']}. Export: {snap.get('export_path') or 'pending'}"
                st.rerun()

# ── Readiness ───────────────────────────────────────────────

with readiness_tab:
    readiness = api.readiness(sku)
    readiness_chip(readiness["status"])
    st.subheader("Checklist", anchor=False)
    for item in readiness["items"]:
        label, colour = item_result(item)
        c1, c2, c3 = st.columns([1, 4, 5], vertical_alignment="center")
        c1.badge(item["item_id"], color=colour)
        c2.markdown(f"{item['description']} · :{colour}[{label}]")
        c3.caption(item["reason"] or "")
    st.subheader("What to do next", anchor=False)
    if not readiness["actions"]:
        st.caption("Nothing to do.")
    for n, action in enumerate(readiness["actions"], 1):
        text = action["text_ar"] if lang() == "ar" else action["text_en"]
        st.markdown(rtl_html(f"{n}. {text}"), unsafe_allow_html=True)
        st.caption(f"{action['tier']} · {action['effort']} · clears {', '.join(action['clears'])}")

    st.subheader("Review summary", anchor=False)
    review = api.review(sku)
    if review["overrides"]:
        st.markdown("**Your overrides**")
        st.dataframe(pd.DataFrame([{"Field": o["field"], "Before": o["before"], "After": o["after"],
                                    "Marked for review": "yes" if o["marked_for_review"] else "",
                                    "When": o["timestamp"]} for o in review["overrides"]]),
                     hide_index=True, width="stretch")
    else:
        st.caption("No seller overrides.")
    for warning in review["warnings"]:
        st.warning(warning, icon=":material/warning:")
    for reason in review["specialist_reasons"]:
        st.error(f"Specialist review: {reason}", icon=":material/engineering:")
    for removed in review["guard_removed"]:
        st.error(f"Blocked: “{removed['text']}”: {removed['reason']}", icon=":material/block:")
    if review["editor_changes"]:
        st.caption(f"{review['editor_changes']} listing-editor saves (see History).")

# ── History ─────────────────────────────────────────────────


def _cell(value) -> str:
    if value in (None, "", {}, []):
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


with history_tab:
    events = api.history(sku)
    kinds = sorted({e["event"] for e in events})
    chosen = st.multiselect("Events", kinds, key=f"ws_history_filter_{sku}", placeholder="All events")
    st.dataframe(pd.DataFrame([{"When": e["timestamp"], "Event": e["event"].replace("_", " "), "By": e["actor"],
                                "Field": e["field"] or "", "Before": _cell(e["before"]), "After": _cell(e["after"]),
                                "Summary": e["summary"]}
                               for e in events if not chosen or e["event"] in chosen]),
                 hide_index=True, width="stretch")
