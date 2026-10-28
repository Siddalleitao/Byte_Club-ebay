"""Shared pieces for the workspace pages: the client, status chips, RTL text, photo regions."""

from __future__ import annotations

import html
import io
import json
import re

import streamlit as st
from PIL import Image, ImageDraw

from ui.workspace_client import GatewayError, WorkspaceClient

# ── Session ─────────────────────────────────────────────────


def client() -> WorkspaceClient:
    if "ws_client" not in st.session_state:
        st.session_state.ws_client = WorkspaceClient()
    return st.session_state.ws_client


def logged_in() -> bool:
    return bool(st.session_state.get("ws_client") and st.session_state.ws_client.token)


def lang() -> str:
    return st.session_state.get("ws_lang") or "en"


def open_part(sku: str) -> None:
    st.session_state.ws_sku = sku
    st.switch_page("workspace_pages/part.py")


def gateway_error(exc: GatewayError) -> None:
    st.error(f"The gateway said no ({exc.status}): {exc.detail}", icon=":material/error:")


# ── Status chips ────────────────────────────────────────────

_ATTRIBUTE_COLOURS = {"Seller-confirmed": "green", "Verified": "green", "Extracted": "blue",
                      "Conflicting": "red", "Missing": "gray", "Unverified": "orange"}
_READINESS = {"Ready for seller review": ("green", ":material/task_alt:"),
              "Information missing": ("orange", ":material/help:"),
              "Specialist review required": ("red", ":material/engineering:")}
_STATUS = {"Awaiting seller": "orange", "Awaiting approval": "blue", "Approved": "green", "Exported": "green"}
_COMPAT = {"verified": ("green", "Compatibility verified"), "not_verified": ("gray", "Compatibility not verified"),
           "conflicting": ("red", "Compatibility conflicting"), "side_mismatch": ("orange", "Side differs from reference")}


def attribute_chip(status: str) -> None:
    st.badge(status, color=_ATTRIBUTE_COLOURS.get(status, "gray"))


def readiness_chip(readiness: str | None) -> None:
    if readiness:
        colour, icon = _READINESS.get(readiness, ("gray", None))
        st.badge(readiness, color=colour, icon=icon)


def status_chip(status: str) -> None:
    st.badge(status, color=_STATUS.get(status, "gray"), icon=":material/conversion_path:")


def compat_chip(compat: str | None) -> None:
    if compat:
        colour, label = _COMPAT.get(compat, ("gray", compat))
        st.badge(label, color=colour, icon=":material/directions_car:")


def item_result(item: dict) -> tuple[str, str]:
    """C01–C15 shown as pass, fail or warning (a non-blocking failure or a pending item is a warning)."""
    if item["result"] == "pass":
        return "pass", "green"
    if item["result"] == "fail" and item["blocking"] and item.get("blocks") != "approval":
        return "fail", "red"
    return ("pending" if item["result"] == "pending" else "warning"), "orange"


# ── Text ────────────────────────────────────────────────────

_ARABIC = re.compile(r"[؀-ۿ]")


def is_arabic(text: str | None) -> bool:
    return bool(text and _ARABIC.search(text))


def rtl_html(text: str) -> str:
    """Seller or model text, escaped (it is data), laid out right to left when it is Arabic."""
    body = html.escape(text or "").replace("\n", "<br>")
    if is_arabic(text):
        return f'<div dir="rtl" style="text-align:right">{body}</div>'
    return f"<div>{body}</div>"


def show_text(text: str) -> None:
    st.markdown(rtl_html(text), unsafe_allow_html=True)


def readable_value(value: str | None) -> str:
    """Ledger values for people: a JSON map such as the component list becomes "glass: present, …"."""
    if value in (None, ""):
        return "—"
    if value.startswith("{"):
        try:
            return ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in json.loads(value).items())
        except (ValueError, AttributeError):
            pass
    return value


def source_label(source: dict) -> str:
    """A short label for an evidence pointer: file, cell or photo region."""
    pointer = (source.get("pointer") or "").replace("\\", "/")
    for prefix, label in (("chat", "seller answer (chat)"), ("editor:", "listing editor"), ("ocr:", "label OCR"),
                          ("compat_ref:", "compatibility reference")):
        if pointer.startswith(prefix):
            return label
    if pointer.startswith("photo:"):
        return f"{pointer.removeprefix('photo:')} (photo reading)"
    if pointer.startswith("sheet:"):
        return f"spreadsheet row {pointer.removeprefix('sheet:')}"
    if pointer.startswith("note:"):
        return f"{pointer.removeprefix('note:')} (donor note)"
    if ".csv:" in pointer:
        file, *cell = pointer.split(":")
        return f"{file.rsplit('/', 1)[-1]} › {' › '.join(cell)}"
    return pointer.rsplit("/", 1)[-1] or source.get("kind", "source")


# ── Photos ──────────────────────────────────────────────────


@st.cache_data(show_spinner=False, max_entries=64)
def _photo_bytes(token: str, sku: str, evidence_id: str) -> bytes:
    return WorkspaceClient(token).photo(sku, evidence_id)


def photo_bytes(sku: str, evidence_id: str) -> bytes:
    return _photo_bytes(client().token, sku, evidence_id)


def draw_regions(content: bytes, regions: list[dict]) -> Image.Image:
    """The photo with its regions outlined: flagged in red, others in blue."""
    image = Image.open(io.BytesIO(content)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for region in regions:
        colour = (220, 38, 38) if region.get("flagged") else (37, 99, 235)
        draw.rectangle(region["box"], outline=colour, width=4)
        x0, y0 = region["box"][0], max(region["box"][1] - 16, 0)
        draw.text((x0 + 2, y0), region["label"], fill=colour)
    return image


def crop(content: bytes, box: list[int], pad: int = 30) -> Image.Image:
    image = Image.open(io.BytesIO(content)).convert("RGB")
    x0, y0, x1, y1 = box
    return image.crop((max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, image.width), min(y1 + pad, image.height)))
