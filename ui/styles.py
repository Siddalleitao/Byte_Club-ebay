"""PartProof CSS — camera-first messaging-app UX layer.

5-pane horizontal track, snap scrolling, bottom bar, story rings,
swipeable cards, hold-to-confirm, RTL mirror, dark theme, desktop phone frame.
"""

from ui.tokens import TOKENS_CSS


def get_css(lang: str = "en", theme: str = "light") -> str:
    rtl = lang == "ar"
    direction = "rtl" if rtl else "ltr"
    return TOKENS_CSS + f"""

/* ═══════════════════════════════════════════════
   RESET & BASE
   ═══════════════════════════════════════════════ */
[data-pp-app] {{
    all: unset;
    --pp-dir: {direction};
    direction: {direction};
}}

.pp-app {{
    font-family: var(--pp-font);
    color: var(--pp-text);
    background: var(--pp-bg);
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    direction: {direction};
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    overflow: hidden;
    height: 100vh;
    height: 100dvh;
    display: flex;
    flex-direction: column;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    z-index: 9999;
}}

.pp-app[data-pp-theme="dark"] {{
    --pp-bg: #0f172a;
    --pp-bg-secondary: #1e293b;
    --pp-bg-tertiary: #334155;
    --pp-text: #f1f5f9;
    --pp-text-secondary: #94a3b8;
    --pp-border: #334155;
}}

.pp-app * {{
    box-sizing: border-box;
}}

/* Hide Streamlit chrome behind our app */
.pp-app {{
    /* Full-bleed */
}}

/* ═══════════════════════════════════════════════
   PANE TRACK — horizontal scroll with snap
   ═══════════════════════════════════════════════ */
.pp-pane-track {{
    display: flex;
    flex-direction: row;
    overflow-x: auto;
    overflow-y: hidden;
    scroll-snap-type: x mandatory;
    scroll-behavior: smooth;
    flex: 1;
    width: 100%;
    height: 100%;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
    position: relative;
    padding-bottom: var(--pp-bottom-bar-h);
}}

.pp-pane-track::-webkit-scrollbar {{
    display: none;
}}

.pp-app[data-pp-dir="rtl"] .pp-pane-track {{
    flex-direction: row-reverse;
}}

.pp-pane {{
    flex: 0 0 100%;
    width: 100vw;
    height: 100%;
    scroll-snap-align: center;
    scroll-snap-stop: always;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 0;
    position: relative;
    -webkit-overflow-scrolling: touch;
}}

.pp-pane::-webkit-scrollbar {{
    width: 0;
}}

/* ═══════════════════════════════════════════════
   BOTTOM BAR — 5 icons + round centre button
   ═══════════════════════════════════════════════ */
.pp-bottom-bar {{
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: calc(var(--pp-bottom-bar-h) + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    background: var(--pp-bg);
    border-top: 1px solid var(--pp-border);
    display: flex;
    align-items: center;
    justify-content: space-around;
    z-index: 100;
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
}}

.pp-app[data-pp-dir="rtl"] .pp-bottom-bar {{
    direction: rtl;
}}

.pp-nav-btn {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    min-width: var(--pp-tap-target);
    min-height: var(--pp-tap-target);
    background: none;
    border: none;
    color: var(--pp-text-secondary);
    cursor: pointer;
    font-size: 10px;
    font-weight: 600;
    transition: color 150ms ease;
    padding: 4px 8px;
    border-radius: var(--pp-radius-sm);
    -webkit-tap-highlight-color: transparent;
}}

.pp-nav-btn:hover {{
    color: var(--pp-primary);
}}

.pp-nav-btn.active {{
    color: var(--pp-primary);
}}

.pp-nav-btn.active svg {{
    fill: currentColor;
}}

.pp-nav-btn svg {{
    width: 22px;
    height: 22px;
    stroke: currentColor;
    fill: none;
}}

.pp-nav-btn.active svg {{
    fill: var(--pp-primary);
    fill-opacity: 0.15;
}}

/* Round centre button */
.pp-nav-centre {{
    position: relative;
    min-width: 56px;
    min-height: 56px;
    margin-top: -20px;
}}

.pp-nav-centre-btn {{
    width: 56px;
    height: 56px;
    border-radius: 50%;
    background: var(--pp-primary);
    color: white;
    border: 4px solid var(--pp-bg);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(26, 86, 219, 0.4);
    transition: transform 150ms ease;
    -webkit-tap-highlight-color: transparent;
}}

.pp-nav-centre-btn:active {{
    transform: scale(0.92);
}}

.pp-nav-centre-btn svg {{
    width: 28px;
    height: 28px;
    stroke: white;
    fill: none;
}}

/* ═══════════════════════════════════════════════
   HOME PANE
   ═══════════════════════════════════════════════ */
.pp-home {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 24px;
    text-align: center;
    background: linear-gradient(135deg, var(--pp-bg-secondary) 0%, var(--pp-bg) 100%);
}}

.pp-home-title {{
    font-size: 28px;
    font-weight: 800;
    margin: 0 0 8px;
    color: var(--pp-text);
}}

.pp-home-sub {{
    font-size: 14px;
    color: var(--pp-text-secondary);
    margin: 0 0 32px;
}}

.pp-home-actions {{
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    max-width: 320px;
}}

/* Viewfinder */
.pp-viewfinder {{
    width: 100%;
    max-width: 360px;
    aspect-ratio: 3/4;
    border-radius: var(--pp-radius-lg);
    background: #000;
    overflow: hidden;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    border: 2px solid var(--pp-border);
}}

.pp-viewfinder video {{
    width: 100%;
    height: 100%;
    object-fit: cover;
}}

.pp-viewfinder-placeholder {{
    color: var(--pp-text-secondary);
    font-size: 14px;
    text-align: center;
    padding: 24px;
}}

.pp-shutter {{
    width: 64px;
    height: 64px;
    border-radius: 50%;
    border: 4px solid var(--pp-bg);
    background: var(--pp-primary);
    cursor: pointer;
    margin: 16px auto;
    transition: transform 100ms ease;
    -webkit-tap-highlight-color: transparent;
}}

.pp-shutter:active {{
    transform: scale(0.88);
}}

/* Action chips */
.pp-chips {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: center;
    margin-top: 16px;
}}

.pp-chip {{
    padding: 10px 16px;
    border-radius: 20px;
    background: var(--pp-bg-tertiary);
    color: var(--pp-text);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--pp-border);
    min-height: var(--pp-tap-target);
    display: flex;
    align-items: center;
    gap: 6px;
    -webkit-tap-highlight-color: transparent;
    transition: background 150ms ease;
}}

.pp-chip:active {{
    background: var(--pp-primary);
    color: white;
}}

.pp-chip svg {{
    width: 18px;
    height: 18px;
}}

/* Primary action button */
.pp-btn {{
    min-height: var(--pp-tap-target);
    padding: 14px 24px;
    border-radius: var(--pp-radius);
    border: none;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    transition: transform 100ms ease, background 150ms ease;
    -webkit-tap-highlight-color: transparent;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
}}

.pp-btn:active {{
    transform: scale(0.97);
}}

.pp-btn-primary {{
    background: var(--pp-primary);
    color: white;
}}

.pp-btn-secondary {{
    background: var(--pp-bg-tertiary);
    color: var(--pp-text);
    border: 1px solid var(--pp-border);
}}

.pp-btn-danger {{
    background: var(--pp-status-specialist);
    color: white;
}}

.pp-btn svg {{
    width: 20px;
    height: 20px;
}}

/* ═══════════════════════════════════════════════
   STORIES PANE — rings + full-screen cards
   ═══════════════════════════════════════════════ */
.pp-stories-header {{
    padding: 16px;
    padding-top: calc(16px + env(safe-area-inset-top));
    background: var(--pp-bg);
    border-bottom: 1px solid var(--pp-border);
    position: sticky;
    top: 0;
    z-index: 10;
}}

.pp-stories-title {{
    font-size: 20px;
    font-weight: 800;
    margin: 0 0 12px;
}}

.pp-ring-row {{
    display: flex;
    gap: 12px;
    overflow-x: auto;
    padding: 4px 0;
    scrollbar-width: none;
}}

.pp-ring-row::-webkit-scrollbar {{ display: none; }}

.pp-ring {{
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    cursor: pointer;
    min-width: 72px;
    -webkit-tap-highlight-color: transparent;
}}

.pp-ring-circle {{
    width: 60px;
    height: 60px;
    border-radius: 50%;
    border: 3px solid var(--pp-border);
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
    transition: border-color 200ms ease;
}}

/* Ring colours by status — always paired with icon */
.pp-ring-circle.status-approved {{ border-color: var(--pp-status-approved); }}
.pp-ring-circle.status-ready {{ border-color: var(--pp-status-ready); }}
.pp-ring-circle.status-missing {{ border-color: var(--pp-status-missing); }}
.pp-ring-circle.status-specialist {{ border-color: var(--pp-status-specialist); }}

/* Ring icon inside */
.pp-ring-circle .pp-ring-icon {{
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
}}

.pp-ring-label {{
    font-size: 11px;
    font-weight: 600;
    color: var(--pp-text);
    text-align: center;
    line-height: 1.2;
}}

.pp-ring-sub {{
    font-size: 9px;
    color: var(--pp-text-secondary);
}}

/* Question badge on ring */
.pp-ring-badge {{
    position: absolute;
    top: -2px;
    right: -2px;
    background: var(--pp-status-specialist);
    color: white;
    font-size: 10px;
    font-weight: 700;
    border-radius: 10px;
    min-width: 18px;
    height: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 4px;
    border: 2px solid var(--pp-bg);
}}

/* Full-screen story overlay */
.pp-story-overlay {{
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: #000;
    z-index: 200;
    display: none;
    flex-direction: column;
    overflow: hidden;
}}

.pp-story-overlay.open {{
    display: flex;
}}

.pp-story-progress-row {{
    display: flex;
    gap: 4px;
    padding: 8px 12px;
    padding-top: calc(8px + env(safe-area-inset-top));
}}

.pp-story-progress {{
    flex: 1;
    height: 3px;
    background: rgba(255,255,255,0.3);
    border-radius: 2px;
    overflow: hidden;
}}

.pp-story-progress-fill {{
    height: 100%;
    background: white;
    width: 0%;
    transition: width linear;
}}

.pp-story-progress-fill.done {{
    width: 100%;
}}

.pp-story-progress-fill.active {{
    animation: pp-story-timer 5s linear forwards;
}}

@keyframes pp-story-timer {{
    from {{ width: 0%; }}
    to {{ width: 100%; }}
}}

.pp-story-content {{
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
    color: white;
    position: relative;
}}

.pp-story-card {{
    width: 100%;
    max-width: 400px;
    background: var(--pp-bg-secondary);
    border-radius: var(--pp-radius-lg);
    padding: 24px;
    color: var(--pp-text);
    text-align: center;
}}

.pp-story-sku {{
    font-size: 24px;
    font-weight: 800;
    margin: 0 0 4px;
}}

.pp-story-detail {{
    font-size: 14px;
    color: var(--pp-text-secondary);
    margin: 4px 0;
}}

.pp-story-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 700;
    margin: 12px 0;
}}

.pp-story-badge.approved {{ background: rgba(22,163,74,0.15); color: var(--pp-status-approved); }}
.pp-story-badge.ready {{ background: rgba(37,99,235,0.15); color: var(--pp-status-ready); }}
.pp-story-badge.missing {{ background: rgba(234,88,12,0.15); color: var(--pp-status-missing); }}
.pp-story-badge.specialist {{ background: rgba(220,38,38,0.15); color: var(--pp-status-specialist); }}

/* Story poll (one-tap question) */
.pp-story-poll {{
    margin-top: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}}

.pp-story-poll-q {{
    font-size: 14px;
    color: var(--pp-text-secondary);
    margin-bottom: 4px;
}}

.pp-story-poll-opt {{
    padding: 14px;
    border-radius: var(--pp-radius);
    background: var(--pp-bg-tertiary);
    color: var(--pp-text);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    border: 2px solid transparent;
    min-height: var(--pp-tap-target);
    display: flex;
    align-items: center;
    justify-content: center;
    -webkit-tap-highlight-color: transparent;
}}

.pp-story-poll-opt:active {{
    border-color: var(--pp-primary);
}}

.pp-story-poll-opt.selected {{
    background: var(--pp-primary);
    color: white;
}}

/* Story tap zones (left = prev, right = next) */
.pp-story-tap-left {{
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 40%;
    cursor: pointer;
}}

.pp-story-tap-right {{
    position: absolute;
    right: 0; top: 0; bottom: 0;
    width: 40%;
    cursor: pointer;
}}

.pp-story-close {{
    position: absolute;
    top: calc(12px + env(safe-area-inset-top));
    right: 12px;
    color: white;
    cursor: pointer;
    z-index: 10;
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
}}

.pp-story-close svg {{
    width: 24px;
    height: 24px;
}}

/* ═══════════════════════════════════════════════
   CHATS PANE — threads + quick-reply
   ═══════════════════════════════════════════════ */
.pp-chats-header {{
    padding: 16px;
    padding-top: calc(16px + env(safe-area-inset-top));
    background: var(--pp-bg);
    border-bottom: 1px solid var(--pp-border);
    position: sticky;
    top: 0;
    z-index: 10;
}}

.pp-chats-title {{
    font-size: 20px;
    font-weight: 800;
    margin: 0;
}}

.pp-chat-list {{
    padding: 8px 0;
}}

.pp-chat-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    cursor: pointer;
    border-bottom: 1px solid var(--pp-border);
    min-height: var(--pp-tap-target);
    -webkit-tap-highlight-color: transparent;
}}

.pp-chat-item:active {{
    background: var(--pp-bg-tertiary);
}}

.pp-chat-avatar {{
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: var(--pp-bg-tertiary);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 14px;
    color: var(--pp-primary);
    flex-shrink: 0;
    border: 2px solid var(--pp-border);
}}

.pp-chat-avatar.status-approved {{ border-color: var(--pp-status-approved); }}
.pp-chat-avatar.status-ready {{ border-color: var(--pp-status-ready); }}
.pp-chat-avatar.status-missing {{ border-color: var(--pp-status-missing); }}
.pp-chat-avatar.status-specialist {{ border-color: var(--pp-status-specialist); }}

.pp-chat-body {{
    flex: 1;
    min-width: 0;
}}

.pp-chat-name {{
    font-size: 15px;
    font-weight: 700;
    margin: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}

.pp-chat-preview {{
    font-size: 13px;
    color: var(--pp-text-secondary);
    margin: 2px 0 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}

.pp-chat-unread {{
    background: var(--pp-status-specialist);
    color: white;
    font-size: 11px;
    font-weight: 700;
    border-radius: 10px;
    min-width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 6px;
    flex-shrink: 0;
}}

/* Chat thread view */
.pp-chat-thread {{
    display: flex;
    flex-direction: column;
    height: 100%;
}}

.pp-chat-thread-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    padding-top: calc(12px + env(safe-area-inset-top));
    background: var(--pp-bg);
    border-bottom: 1px solid var(--pp-border);
}}

.pp-chat-thread-back {{
    min-width: var(--pp-tap-target);
    min-height: var(--pp-tap-target);
    display: flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    color: var(--pp-text);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
}}

.pp-chat-thread-messages {{
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}}

.pp-msg {{
    max-width: 75%;
    padding: 10px 14px;
    border-radius: 16px;
    font-size: 14px;
    line-height: 1.4;
    word-wrap: break-word;
}}

.pp-msg-in {{
    align-self: flex-start;
    background: var(--pp-bg-tertiary);
    color: var(--pp-text);
    border-bottom-left-radius: 4px;
}}

.pp-msg-out {{
    align-self: flex-end;
    background: var(--pp-primary);
    color: white;
    border-bottom-right-radius: 4px;
}}

.pp-msg-meta {{
    font-size: 11px;
    color: var(--pp-text-secondary);
    margin-top: 2px;
}}

.pp-msg-out.pp-msg-pending {{
    opacity: 0.6;
}}

.pp-msg-out.pp-msg-failed {{
    background: var(--pp-status-specialist);
}}

.pp-msg-retry {{
    font-size: 12px;
    color: var(--pp-status-specialist);
    cursor: pointer;
    text-decoration: underline;
}}

/* Quick-reply chips */
.pp-quick-replies {{
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding: 8px 16px;
    scrollbar-width: none;
}}

.pp-quick-replies::-webkit-scrollbar {{ display: none; }}

/* Input bar */
.pp-chat-input-bar {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    padding-bottom: calc(8px + env(safe-area-inset-bottom) + var(--pp-bottom-bar-h));
    background: var(--pp-bg);
    border-top: 1px solid var(--pp-border);
}}

.pp-chat-input {{
    flex: 1;
    min-height: var(--pp-tap-target);
    border-radius: 24px;
    border: 1px solid var(--pp-border);
    background: var(--pp-bg-secondary);
    color: var(--pp-text);
    padding: 12px 16px;
    font-size: 15px;
    font-family: var(--pp-font);
    outline: none;
}}

.pp-chat-input:focus {{
    border-color: var(--pp-primary);
}}

.pp-chat-send {{
    min-width: var(--pp-tap-target);
    min-height: var(--pp-tap-target);
    border-radius: 50%;
    background: var(--pp-primary);
    color: white;
    border: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    -webkit-tap-highlight-color: transparent;
}}

.pp-chat-send svg {{
    width: 22px;
    height: 22px;
}}

/* ═══════════════════════════════════════════════
   DISCOVER / QUEUE — swipeable cards
   ═══════════════════════════════════════════════ */
.pp-discover-header {{
    padding: 16px;
    padding-top: calc(16px + env(safe-area-inset-top));
    background: var(--pp-bg);
    border-bottom: 1px solid var(--pp-border);
    position: sticky;
    top: 0;
    z-index: 10;
}}

.pp-discover-title {{
    font-size: 20px;
    font-weight: 800;
    margin: 0 0 4px;
}}

.pp-discover-sub {{
    font-size: 13px;
    color: var(--pp-text-secondary);
    margin: 0;
}}

.pp-card-stack {{
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
}}

.pp-card {{
    background: var(--pp-bg-secondary);
    border-radius: var(--pp-radius-lg);
    padding: 20px;
    border: 1px solid var(--pp-border);
    position: relative;
    overflow: hidden;
    transition: transform var(--pp-spring-duration) var(--pp-spring-easing), opacity 200ms ease;
    touch-action: pan-y;
}}

.pp-card.swiping {{
    transition: none;
}}

.pp-card.swipe-right {{
    transform: translateX(150%);
    opacity: 0;
}}

.pp-card.swipe-left {{
    transform: translateX(-150%);
    opacity: 0;
}}

.pp-card-sku {{
    font-size: 18px;
    font-weight: 800;
    margin: 0 0 4px;
}}

.pp-card-detail {{
    font-size: 14px;
    color: var(--pp-text-secondary);
    margin: 4px 0;
}}

.pp-card-actions {{
    display: flex;
    gap: 12px;
    margin-top: 16px;
}}

.pp-card-actions .pp-btn {{
    flex: 1;
}}

/* Swipe hint */
.pp-card-hint {{
    display: flex;
    justify-content: space-between;
    padding: 0 4px;
    margin-top: 12px;
    font-size: 12px;
    color: var(--pp-text-secondary);
}}

/* ═══════════════════════════════════════════════
   PROFILE SHEET — swipe down from top
   ═══════════════════════════════════════════════ */
.pp-profile-sheet {{
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5);
    z-index: 300;
    display: none;
    align-items: flex-start;
    justify-content: center;
}}

.pp-profile-sheet.open {{
    display: flex;
}}

.pp-profile-content {{
    width: 100%;
    max-width: 500px;
    background: var(--pp-bg);
    border-radius: 0 0 var(--pp-radius-lg) var(--pp-radius-lg);
    max-height: 85vh;
    overflow-y: auto;
    padding: 16px;
    padding-top: 8px;
    padding-bottom: calc(16px + env(safe-area-inset-bottom));
    animation: pp-slide-down 300ms var(--pp-spring-easing);
}}

@keyframes pp-slide-down {{
    from {{ transform: translateY(-100%); }}
    to {{ transform: translateY(0); }}
}}

.pp-profile-handle {{
    width: 40px;
    height: 4px;
    background: var(--pp-border);
    border-radius: 2px;
    margin: 0 auto 16px;
}}

.pp-profile-section {{
    margin-bottom: 24px;
}}

.pp-profile-section-title {{
    font-size: 16px;
    font-weight: 800;
    margin: 0 0 12px;
}}

.pp-profile-field {{
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid var(--pp-border);
    font-size: 14px;
}}

.pp-profile-field-label {{
    color: var(--pp-text-secondary);
}}

.pp-profile-field-value {{
    font-weight: 600;
}}

/* Form inputs in profile */
.pp-input {{
    width: 100%;
    min-height: var(--pp-tap-target);
    padding: 12px 16px;
    border-radius: var(--pp-radius-sm);
    border: 1px solid var(--pp-border);
    background: var(--pp-bg-secondary);
    color: var(--pp-text);
    font-size: 15px;
    font-family: var(--pp-font);
    outline: none;
    margin-bottom: 8px;
}}

.pp-input:focus {{
    border-color: var(--pp-primary);
}}

/* ═══════════════════════════════════════════════
   HOLD-TO-CONFIRM
   ═══════════════════════════════════════════════ */
.pp-hold-btn {{
    position: relative;
    min-height: var(--pp-tap-target);
    padding: 14px 24px;
    border-radius: var(--pp-radius);
    border: none;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    overflow: hidden;
    background: var(--pp-status-specialist);
    color: white;
    -webkit-tap-highlight-color: transparent;
    user-select: none;
    -webkit-user-select: none;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
}}

.pp-hold-btn-ring {{
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    width: 0%;
    background: rgba(255,255,255,0.2);
    transition: none;
}}

.pp-hold-btn.holding .pp-hold-btn-ring {{
    width: 100%;
    transition: width var(--pp-hold-duration) linear;
}}

.pp-hold-btn-label {{
    position: relative;
    z-index: 1;
}}

.pp-hold-btn.holding .pp-hold-btn-label::after {{
    content: '...';
}}

/* ═══════════════════════════════════════════════
   CONTEXT MENU (long-press)
   ═══════════════════════════════════════════════ */
.pp-context-menu {{
    position: fixed;
    background: var(--pp-bg-secondary);
    border-radius: var(--pp-radius);
    box-shadow: 0 8px 24px rgba(0,0,0,0.2);
    padding: 4px;
    z-index: 400;
    display: none;
    min-width: 160px;
}}

.pp-context-menu.open {{
    display: block;
}}

.pp-context-item {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    font-size: 14px;
    cursor: pointer;
    border-radius: var(--pp-radius-sm);
    min-height: var(--pp-tap-target);
    color: var(--pp-text);
    -webkit-tap-highlight-color: transparent;
}}

.pp-context-item:active {{
    background: var(--pp-bg-tertiary);
}}

.pp-context-item svg {{
    width: 18px;
    height: 18px;
}}

/* ═══════════════════════════════════════════════
   SKELETON STATES
   ═══════════════════════════════════════════════ */
.pp-skeleton {{
    background: linear-gradient(90deg, var(--pp-bg-tertiary) 25%, var(--pp-bg-secondary) 50%, var(--pp-bg-tertiary) 75%);
    background-size: 200% 100%;
    animation: pp-skeleton-shimmer 1.5s infinite;
    border-radius: var(--pp-radius-sm);
}}

@keyframes pp-skeleton-shimmer {{
    0% {{ background-position: 200% 0; }}
    100% {{ background-position: -200% 0; }}
}}

.pp-skeleton-ring {{
    width: 60px;
    height: 60px;
    border-radius: 50%;
}}

.pp-skeleton-card {{
    height: 120px;
    border-radius: var(--pp-radius-lg);
}}

.pp-skeleton-line {{
    height: 16px;
    margin-bottom: 8px;
}}

/* ═══════════════════════════════════════════════
   TOAST
   ═══════════════════════════════════════════════ */
.pp-toast {{
    position: fixed;
    bottom: calc(var(--pp-bottom-bar-h) + 16px + env(safe-area-inset-bottom));
    left: 50%;
    transform: translateX(-50%);
    background: var(--pp-text);
    color: var(--pp-bg);
    padding: 12px 20px;
    border-radius: 24px;
    font-size: 14px;
    font-weight: 600;
    z-index: 500;
    opacity: 0;
    transition: opacity 200ms ease, transform 200ms ease;
    pointer-events: none;
    max-width: 90%;
    text-align: center;
}}

.pp-toast.show {{
    opacity: 1;
    transform: translateX(-50%) translateY(-8px);
}}

/* ═══════════════════════════════════════════════
   TUTORIAL
   ═══════════════════════════════════════════════ */
.pp-tutorial {{
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.7);
    z-index: 600;
    display: none;
    align-items: center;
    justify-content: center;
}}

.pp-tutorial.open {{
    display: flex;
}}

.pp-tutorial-card {{
    width: 90%;
    max-width: 340px;
    background: var(--pp-bg);
    border-radius: var(--pp-radius-lg);
    padding: 32px 24px;
    text-align: center;
}}

.pp-tutorial-icon {{
    width: 64px;
    height: 64px;
    margin: 0 auto 16px;
    color: var(--pp-primary);
}}

.pp-tutorial-title {{
    font-size: 20px;
    font-weight: 800;
    margin: 0 0 8px;
}}

.pp-tutorial-text {{
    font-size: 14px;
    color: var(--pp-text-secondary);
    margin: 0 0 24px;
    line-height: 1.5;
}}

.pp-tutorial-dots {{
    display: flex;
    gap: 8px;
    justify-content: center;
    margin-bottom: 16px;
}}

.pp-tutorial-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--pp-border);
}}

.pp-tutorial-dot.active {{
    background: var(--pp-primary);
}}

/* ═══════════════════════════════════════════════
   OFFLINE BANNER
   ═══════════════════════════════════════════════ */
.pp-offline-banner {{
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    background: var(--pp-status-missing);
    color: white;
    text-align: center;
    padding: 8px 16px;
    padding-top: calc(8px + env(safe-area-inset-top));
    font-size: 13px;
    font-weight: 600;
    z-index: 700;
    display: none;
}}

.pp-offline-banner.show {{
    display: block;
}}

/* ═══════════════════════════════════════════════
   PROGRESS BAR (upload)
   ═══════════════════════════════════════════════ */
.pp-progress-list {{
    width: 100%;
    max-width: 360px;
    margin: 0 auto;
}}

.pp-progress-item {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 0;
    font-size: 13px;
}}

.pp-progress-bar {{
    flex: 1;
    height: 6px;
    background: var(--pp-bg-tertiary);
    border-radius: 3px;
    overflow: hidden;
}}

.pp-progress-bar-fill {{
    height: 100%;
    background: var(--pp-primary);
    width: 0%;
    transition: width 300ms ease;
}}

.pp-progress-bar-fill.done {{
    background: var(--pp-status-approved);
    width: 100%;
}}

/* ═══════════════════════════════════════════════
   DESKTOP: phone frame + side panel
   ═══════════════════════════════════════════════ */
@media (min-width: 1024px) {{
    .pp-app {{
        position: relative;
        height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--pp-bg-tertiary);
    }}

    .pp-phone-frame {{
        width: 390px;
        height: 844px;
        max-height: 90vh;
        border-radius: 40px;
        overflow: hidden;
        border: 8px solid #1a1a1a;
        box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        position: relative;
        flex-shrink: 0;
    }}

    .pp-phone-frame .pp-pane-track {{
        height: 100%;
    }}

    .pp-side-panel {{
        width: 320px;
        max-height: 844px;
        overflow-y: auto;
        padding: 24px;
        background: var(--pp-bg);
        border-radius: var(--pp-radius);
        margin-left: 24px;
    }}

    .pp-app[data-pp-dir="rtl"] .pp-side-panel {{
        margin-left: 0;
        margin-right: 24px;
    }}

    .pp-side-panel-title {{
        font-size: 18px;
        font-weight: 800;
        margin: 0 0 16px;
    }}
}}

/* When not desktop, no phone frame */
@media (max-width: 1023px) {{
    .pp-phone-frame {{
        width: 100%;
        height: 100%;
        border: none;
        border-radius: 0;
    }}

    .pp-side-panel {{
        display: none;
    }}
}}

/* ═══════════════════════════════════════════════
   REDUCED MOTION
   ═══════════════════════════════════════════════ */
@media (prefers-reduced-motion: reduce) {{
    .pp-pane-track {{
        scroll-behavior: auto;
    }}

    * {{
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }}

    .pp-story-progress-fill.active {{
        animation: none;
        transition: width 5s linear;
    }}

    .pp-profile-content {{
        animation: pp-fade-in 200ms ease;
    }}

    @keyframes pp-fade-in {{
        from {{ opacity: 0; }}
        to {{ opacity: 1; }}
    }}
}}

/* ═══════════════════════════════════════════════
   UTILITY
   ═══════════════════════════════════════════════ */
.pp-hidden {{ display: none !important; }}
.pp-sr-only {{
    position: absolute;
    width: 1px; height: 1px;
    padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0,0,0,0);
    white-space: nowrap; border: 0;
}}

.pp-filter-bar {{
    display: flex;
    gap: 8px;
    padding: 8px 16px;
    overflow-x: auto;
    scrollbar-width: none;
}}
.pp-filter-bar::-webkit-scrollbar {{ display: none; }}

.pp-filter-chip {{
    padding: 8px 14px;
    border-radius: 20px;
    background: var(--pp-bg-tertiary);
    font-size: 12px;
    font-weight: 600;
    color: var(--pp-text-secondary);
    border: 1px solid var(--pp-border);
    cursor: pointer;
    white-space: nowrap;
    min-height: 36px;
    display: flex;
    align-items: center;
    -webkit-tap-highlight-color: transparent;
}}

.pp-filter-chip.active {{
    background: var(--pp-primary);
    color: white;
    border-color: var(--pp-primary);
}}

.pp-search-bar {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
}}

.pp-search-input {{
    flex: 1;
    min-height: 40px;
    border-radius: 20px;
    border: 1px solid var(--pp-border);
    background: var(--pp-bg-secondary);
    color: var(--pp-text);
    padding: 8px 16px;
    font-size: 14px;
    font-family: var(--pp-font);
    outline: none;
}}

.pp-search-input:focus {{
    border-color: var(--pp-primary);
}}

.pp-empty {{
    text-align: center;
    padding: 48px 24px;
    color: var(--pp-text-secondary);
    font-size: 14px;
}}

.pp-error {{
    text-align: center;
    padding: 24px;
    color: var(--pp-status-specialist);
    font-size: 14px;
}}

.pp-test-banner {{
    background: rgba(234,88,12,0.1);
    border: 1px solid var(--pp-status-missing);
    border-radius: var(--pp-radius-sm);
    padding: 12px 16px;
    font-size: 13px;
    color: var(--pp-status-missing);
    margin: 16px 0;
    display: flex;
    align-items: flex-start;
    gap: 8px;
}}

.pp-test-banner svg {{
    width: 20px;
    height: 20px;
    flex-shrink: 0;
    margin-top: 1px;
}}

.pp-metric-row {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
}}

.pp-metric {{
    flex: 1;
    min-width: 100px;
    background: var(--pp-bg-tertiary);
    border-radius: var(--pp-radius-sm);
    padding: 12px;
    text-align: center;
}}

.pp-metric-value {{
    font-size: 22px;
    font-weight: 800;
    color: var(--pp-text);
}}

.pp-metric-label {{
    font-size: 11px;
    color: var(--pp-text-secondary);
    margin-top: 4px;
}}

.pp-data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}

.pp-data-table th, .pp-data-table td {{
    padding: 8px;
    text-align: left;
    border-bottom: 1px solid var(--pp-border);
}}

.pp-data-table th {{
    font-weight: 700;
    color: var(--pp-text-secondary);
}}

.pp-expander {{
    border: 1px solid var(--pp-border);
    border-radius: var(--pp-radius-sm);
    margin-bottom: 8px;
    overflow: hidden;
}}

.pp-expander-header {{
    padding: 12px 16px;
    font-weight: 700;
    font-size: 14px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--pp-bg-tertiary);
    min-height: var(--pp-tap-target);
}}

.pp-expander-body {{
    padding: 12px 16px;
    display: none;
}}

.pp-expander.open .pp-expander-body {{
    display: block;
}}
"""
