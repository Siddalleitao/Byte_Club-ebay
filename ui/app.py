"""PartProof — camera-first messaging-app UX layer.

Single-page app with 5-pane horizontal track, snap swipe, bottom bar,
story rings, swipeable cards, hold-to-confirm, RTL, dark theme, PWA.

Rule (g): the UI talks to the API gateway only — never to an LLM or the database.
All business logic, data models, and API contracts are unchanged.
"""

from __future__ import annotations

import streamlit as st

from ui.api_client import get_client
from ui.i18n import t
from ui.icons import icon
from ui.styles import get_css
from ui.controller import get_js
from ui.panes import (
    render_home,
    render_chats,
    render_stories,
    render_discover,
    render_map,
)
from ui.profile_sheet import render_profile_sheet

# ── Page config ──
st.set_page_config(
    page_title="PartProof",
    page_icon="📷",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Session state defaults ──
if "lang" not in st.session_state:
    st.session_state.lang = "en"
if "seller" not in st.session_state:
    st.session_state.seller = None
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "uploaded_parts" not in st.session_state:
    st.session_state.uploaded_parts = []
if "selected_part" not in st.session_state:
    st.session_state.selected_part = None
if "theme" not in st.session_state:
    st.session_state.theme = "light"

lang = st.session_state.lang
theme = st.session_state.theme

# ── If not logged in, show login screen ──
if not st.session_state.logged_in:
    client = get_client()
    seller = client.get_seller("S-001")

    css = get_css(lang, theme)
    login_html = f"""
    <div class="pp-app" data-pp-theme="{theme}" data-pp-dir="{'rtl' if lang == 'ar' else 'ltr'}">
        <style>{css}</style>
        <div class="pp-home" style="min-height:70vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;text-align:center">
            <div class="pp-home-title" style="font-size:32px">{t('app_title', lang)}</div>
            <div class="pp-home-sub" style="margin-bottom:32px">{t('app_subtitle', lang)}</div>
            <div style="background:var(--pp-bg-secondary);border-radius:20px;padding:24px;width:100%;max-width:320px;border:1px solid var(--pp-border)">
                <div style="font-size:48px;margin-bottom:8px">{icon('profile', 48)}</div>
                <div style="font-size:20px;font-weight:800">{seller.get('name', '')}</div>
                <div style="font-size:14px;color:var(--pp-text-secondary);margin:4px 0">{seller.get('seller_type', '')} · {seller.get('location', '')}</div>
                <div style="font-size:13px;color:var(--pp-text-secondary);margin:4px 0">{seller.get('language_primary', '')} / {seller.get('language_secondary', '')}</div>
                <div style="font-size:13px;color:var(--pp-text-secondary);margin:4px 0 16px">{seller.get('market_corridor', '')}</div>
            </div>
        </div>
    </div>
    """
    st.iframe(login_html, height=600)

    # Login button — real Streamlit button for reliable state bridge
    if st.button(f"{t('login_continue', lang)}", key="login_btn", type="primary", use_container_width=True):
        session = client.login(seller.get("name", ""))
        st.session_state.token = session["token"] if session else None
        st.session_state.seller = seller
        st.session_state.logged_in = True
        st.rerun()
    st.stop()

# ── Logged in: render full app ──
client = get_client()
seller = st.session_state.seller or client.get_seller("S-001")
parts = client.get_parts()[:10]
selected_part = st.session_state.selected_part

css = get_css(lang, theme)
js = get_js(lang)

# Render all panes
home_html = render_home(lang, seller)
chats_html = render_chats(lang, parts, selected_part)
stories_html = render_stories(lang, parts)
discover_html = render_discover(lang, parts, st.session_state.uploaded_parts)
map_html = render_map(lang, seller)
profile_sheet_html = render_profile_sheet(lang, seller)

# Bottom bar
bottom_bar = f"""
<div class="pp-bottom-bar">
    <button class="pp-nav-btn" data-pane="map" aria-label="{t('corridor', lang)}">
        {icon('map', 22)}
        <span>{'الخريطة' if lang == 'ar' else 'Map'}</span>
    </button>
    <button class="pp-nav-btn" data-pane="chats" aria-label="{t('questions_title', lang)}">
        {icon('chat', 22)}
        <span>{'المحادثات' if lang == 'ar' else 'Chats'}</span>
    </button>
    <div class="pp-nav-centre">
        <button class="pp-nav-centre-btn" aria-label="{t('upload_title', lang)}">
            {icon('camera', 28)}
        </button>
    </div>
    <button class="pp-nav-btn" data-pane="stories" aria-label="{t('board_title', lang)}">
        {icon('story', 22)}
        <span>{'اللوحة' if lang == 'ar' else 'Board'}</span>
    </button>
    <button class="pp-nav-btn" data-pane="discover" aria-label="{t('upload_assign_tray', lang)}">
        {icon('queue', 22)}
        <span>{'القائمة' if lang == 'ar' else 'Queue'}</span>
    </button>
</div>
"""

# Desktop side panel
side_panel = f"""
<div class="pp-side-panel">
    <h3 class="pp-side-panel-title">{t('seller_card', lang)}</h3>
    <div class="pp-profile-field"><span class="pp-profile-field-label">{t('business', lang)}</span><span class="pp-profile-field-value">{seller.get('seller_type', '')}</span></div>
    <div class="pp-profile-field"><span class="pp-profile-field-label">{t('location', lang)}</span><span class="pp-profile-field-value">{seller.get('location', '')}</span></div>
    <div class="pp-profile-field"><span class="pp-profile-field-label">{t('languages', lang)}</span><span class="pp-profile-field-value">{seller.get('language_primary', '')} / {seller.get('language_secondary', '')}</span></div>
    <div class="pp-profile-field"><span class="pp-profile-field-label">{t('corridor', lang)}</span><span class="pp-profile-field-value">{seller.get('market_corridor', '')}</span></div>
    <div class="pp-profile-field"><span class="pp-profile-field-label">{t('rack_codes', lang)}</span><span class="pp-profile-field-value">{seller.get('rack_codes', '')}</span></div>
    <div class="pp-profile-field"><span class="pp-profile-field-label">{t('marketplace', lang)}</span><span class="pp-profile-field-value">{seller.get('marketplace', '')}</span></div>
    <p style="font-size:12px;color:var(--pp-text-secondary);margin-top:16px">
        Use ← → arrow keys to navigate panes. Space advances stories. Esc closes sheets.
    </p>
</div>
"""

# Story overlay (hidden by default, opened by JS)
story_overlay = f"""
<div class="pp-story-overlay" id="pp-story-overlay" role="dialog" aria-label="Part story">
    <div class="pp-story-progress-row"></div>
    <div class="pp-story-close" aria-label="Close">{icon('close', 24)}</div>
    <div class="pp-story-content"></div>
    <div class="pp-story-tap-left" aria-label="Previous"></div>
    <div class="pp-story-tap-right" aria-label="Next"></div>
</div>
"""

# Context menu
context_menu = '<div class="pp-context-menu" id="pp-context-menu" role="menu"></div>'

# Toast
toast = '<div class="pp-toast" id="pp-toast" role="status" aria-live="polite"></div>'

# Tutorial
tutorial = f"""
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
            <button class="pp-btn pp-btn-secondary pp-tutorial-skip">{('تخطي' if lang == 'ar' else 'Skip')}</button>
            <button class="pp-btn pp-btn-primary pp-tutorial-next">{('التالي' if lang == 'ar' else 'Next')}</button>
        </div>
    </div>
</div>
"""

# Offline banner
offline = f'<div class="pp-offline-banner" id="pp-offline-banner"></div>'

# Assemble full app HTML
app_html = f"""
<!DOCTYPE html>
<html lang="{lang}" dir="{'rtl' if lang == 'ar' else 'ltr'}">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover" />
    <meta name="theme-color" content="#1a56db" />
    <style>{css}</style>
</head>
<body>
<div class="pp-app" data-pp-theme="{theme}" data-pp-dir="{'rtl' if lang == 'ar' else 'ltr'}">
    {offline}
    <div class="pp-phone-frame">
        <div class="pp-pane-track">
            <div class="pp-pane">{map_html}</div>
            <div class="pp-pane">{chats_html}</div>
            <div class="pp-pane">{home_html}</div>
            <div class="pp-pane">{stories_html}</div>
            <div class="pp-pane">{discover_html}</div>
        </div>
        {bottom_bar}
        {story_overlay}
        {profile_sheet_html}
        {context_menu}
        {toast}
        {tutorial}
    </div>
    {side_panel}
</div>
<script>{js}</script>
<script>
// PWA: register service worker
if ('serviceWorker' in navigator) {{
    window.addEventListener('load', function() {{
        navigator.serviceWorker.register('/static/sw.js').catch(function(e) {{
            console.log('SW registration failed:', e);
        }});
    }});
}}
// PWA: manifest link
var link = document.createElement('link');
link.rel = 'manifest';
link.href = '/static/manifest.json';
document.head.appendChild(link);
var iconLink = document.createElement('link');
iconLink.rel = 'apple-touch-icon';
iconLink.href = '/static/icon-192.svg';
document.head.appendChild(iconLink);
</script>
</body>
</html>
"""

# Render the full app
st.iframe(app_html, height=900)

# ── Streamlit bridge: handle events from the JS layer ──
# These hidden buttons capture custom events dispatched by the JS controller
# and bridge them back to Streamlit's server-side state.

col_a, col_b, col_c, col_d, col_e = st.columns(5)
with col_a:
    if st.button("⬅", key="nav_left", help="Previous pane"):
        st.rerun()
with col_b:
    if st.button("📷", key="nav_camera", help="Camera"):
        st.rerun()
with col_c:
    if st.button("🏠", key="nav_home", help="Home"):
        st.rerun()
with col_d:
    if st.button("➡", key="nav_right", help="Next pane"):
        st.rerun()
with col_e:
    if st.button("👤", key="nav_profile", help="Profile"):
        st.rerun()

# Handle language toggle
if st.session_state.get("_toggle_lang"):
    st.session_state.lang = "ar" if lang == "en" else "en"
    st.session_state._toggle_lang = False
    st.rerun()

# Handle profile save
if st.session_state.get("_save_profile"):
    st.session_state._save_profile = False
    st.rerun()

# Handle chat selection
if st.session_state.get("_select_chat"):
    part_id = st.session_state._select_chat
    part = client.get_part(part_id)
    if part:
        st.session_state.selected_part = part
    st.rerun()

# Handle back to chat list
if st.session_state.get("_back_to_chats"):
    st.session_state.selected_part = None
    st.session_state._back_to_chats = False
    st.rerun()
