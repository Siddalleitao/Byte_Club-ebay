"""PartProof — camera-first messaging-app UX layer.

Single-page app with 5-pane horizontal track, snap swipe, bottom bar,
story rings, swipeable cards, hold-to-confirm, RTL, dark theme.

Rule (g): the UI talks to the API gateway only — never to an LLM or the database.
Streamlit handles login and hands the session token to the page; the page's
controller (ui/controller.py) then reads and writes through the gateway's
/me/* endpoints, so every button acts on the seller's real data.
"""

from __future__ import annotations

import json

import streamlit as st

from ui.api_client import get_client
from ui.controller import get_js
from ui.i18n import STRINGS, t
from ui.icons import ICONS, icon
from ui.styles import get_css

# ── Page config ──
st.set_page_config(
    page_title="PartProof",
    page_icon="📷",
    layout="centered",
    initial_sidebar_state="collapsed",
)

for key, default in (("lang", "en"), ("theme", "light"), ("token", None), ("display_name", None)):
    if key not in st.session_state:
        st.session_state[key] = default

lang = st.session_state.lang
theme = st.session_state.theme
client = get_client()


def _api_unreachable() -> None:
    st.error(t("api_unreachable", lang).format(url=client.base_url))
    if st.button(t("retry", lang), type="primary"):
        st.rerun()
    st.stop()


def _session_valid(token: str | None) -> bool:
    return bool(token) and client.get_profile(token) is not None


# ── Keep the session alive across API restarts (sessions live in the API's memory) ──
if st.session_state.token and not _session_valid(st.session_state.token):
    session = client.login(st.session_state.display_name or "")
    st.session_state.token = session["token"] if session else None

# ── Login ──
if not st.session_state.token:
    if client.health().get("status") != "ok":
        _api_unreachable()
    profiles = client.list_profiles()
    names = [p["display_name"] for p in profiles]

    css = get_css(lang, theme)
    st.iframe(f"""
    <div class="pp-app" data-pp-theme="{theme}" data-pp-dir="{'rtl' if lang == 'ar' else 'ltr'}" style="position:static;height:auto">
        <style>{css}</style>
        <div class="pp-home" style="min-height:360px;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;text-align:center">
            <div style="color:var(--pp-primary);margin-bottom:8px">{icon('camera', 48)}</div>
            <div class="pp-home-title" style="font-size:32px">{t('app_title', lang)}</div>
            <div class="pp-home-sub">{t('app_subtitle', lang)}</div>
        </div>
    </div>
    """, height=380)

    name = st.selectbox(t("login_select_profile", lang), names) if len(names) > 1 else (names[0] if names else None)
    if name is None:
        st.warning(t("not_logged_in", lang))
        st.stop()
    if st.button(f"{t('login_continue', lang)} — {name}", key="login_btn", type="primary", use_container_width=True):
        session = client.login(name)
        if not session:
            _api_unreachable()
        st.session_state.token = session["token"]
        st.session_state.display_name = name
        st.rerun()
    st.stop()

# ── Logged in: the page shell. The controller fills every pane from the gateway. ──
config = {
    "api": client.base_url,
    "token": st.session_state.token,
    "lang": lang,
    "displayName": st.session_state.display_name,
    "strings": STRINGS,
    "icons": ICONS,
}

shell = f"""
<!DOCTYPE html>
<html lang="{lang}" dir="{'rtl' if lang == 'ar' else 'ltr'}">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="theme-color" content="#1a56db" />
    <style>{get_css(lang, theme)}</style>
</head>
<body style="margin:0">
<div class="pp-app" data-pp-theme="{theme}" data-pp-dir="{'rtl' if lang == 'ar' else 'ltr'}">
    <div class="pp-offline-banner" id="pp-offline-banner" role="status"></div>
    <div class="pp-session-banner" id="pp-session-banner" role="alert"></div>
    <div class="pp-phone-frame">
        <button class="pp-profile-fab" id="pp-profile-fab" data-act="open-profile" aria-label="{t('nav_profile', lang)}">{icon('profile', 22)}</button>
        <div class="pp-pane-track"></div>
        <nav class="pp-bottom-bar"></nav>
        <div class="pp-story-overlay" id="pp-story-overlay" role="dialog" aria-label="Part story">
            <div class="pp-story-progress-row"></div>
            <button class="pp-story-close" aria-label="Close">{icon('close', 24)}</button>
            <div class="pp-story-tap-left" aria-hidden="true"></div>
            <div class="pp-story-tap-right" aria-hidden="true"></div>
            <div class="pp-story-content"></div>
        </div>
        <div class="pp-profile-sheet" id="pp-profile-sheet" role="dialog" aria-label="{t('nav_profile', lang)}">
            <div class="pp-profile-content"></div>
        </div>
        <div class="pp-context-menu" id="pp-context-menu" role="menu"></div>
        <div class="pp-toast" id="pp-toast" role="status" aria-live="polite"></div>
        <div class="pp-tutorial" id="pp-tutorial" role="dialog" aria-label="Tutorial">
            <div class="pp-tutorial-card">
                <div class="pp-tutorial-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></svg></div>
                <h3 class="pp-tutorial-title"></h3>
                <p class="pp-tutorial-text"></p>
                <div class="pp-tutorial-dots">
                    <div class="pp-tutorial-dot active"></div>
                    <div class="pp-tutorial-dot"></div>
                    <div class="pp-tutorial-dot"></div>
                </div>
                <div style="display:flex;gap:8px;justify-content:center">
                    <button class="pp-btn pp-btn-secondary pp-tutorial-skip"></button>
                    <button class="pp-btn pp-btn-primary pp-tutorial-next"></button>
                </div>
            </div>
        </div>
    </div>
</div>
<script>window.PP_CONFIG = {json.dumps(config, ensure_ascii=False).replace("</", "<\\/")};</script>
<script>{get_js()}</script>
</body>
</html>
"""

st.iframe(shell, height=900)

if st.button(t("logout", lang), key="logout_btn"):
    client.logout(st.session_state.token)
    st.session_state.token = None
    st.session_state.display_name = None
    st.rerun()
