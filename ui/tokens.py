"""Design tokens for PartProof — light and dark themes from the same tokens.

Original colours (not copied from any brand). Used by CSS and JS.
"""

TOKENS = {
    # Brand
    "primary": "#1a56db",
    "primary_dark": "#1340b5",
    "accent": "#0ea5e9",
    # Status colours (paired with icons — never colour-only)
    "status_approved": "#16a34a",
    "status_ready": "#2563eb",
    "status_missing": "#ea580c",
    "status_specialist": "#dc2626",
    # Neutrals — light
    "bg_light": "#ffffff",
    "bg_secondary_light": "#f8fafc",
    "bg_tertiary_light": "#f1f5f9",
    "text_light": "#1e293b",
    "text_secondary_light": "#64748b",
    "border_light": "#e2e8f0",
    # Neutrals — dark
    "bg_dark": "#0f172a",
    "bg_secondary_dark": "#1e293b",
    "bg_tertiary_dark": "#334155",
    "text_dark": "#f1f5f9",
    "text_secondary_dark": "#94a3b8",
    "border_dark": "#334155",
    # Layout
    "bottom_bar_height": "64px",
    "safe_area_bottom": "env(safe-area-inset-bottom)",
    "tap_target_min": "48px",
    "spring_duration": "220ms",
    "spring_easing": "cubic-bezier(0.34, 1.56, 0.64, 1)",
    "hold_duration": "1200ms",
    # Fonts
    "font_family": "system-ui, -apple-system, 'Segoe UI', sans-serif",
    "heading_font": "system-ui, -apple-system, 'Segoe UI', sans-serif",
}

# CSS custom properties string for injection
TOKENS_CSS = """
:root {
  --pp-primary: #1a56db;
  --pp-primary-dark: #1340b5;
  --pp-accent: #0ea5e9;
  --pp-status-approved: #16a34a;
  --pp-status-ready: #2563eb;
  --pp-status-missing: #ea580c;
  --pp-status-specialist: #dc2626;
  --pp-bg: #ffffff;
  --pp-bg-secondary: #f8fafc;
  --pp-bg-tertiary: #f1f5f9;
  --pp-text: #1e293b;
  --pp-text-secondary: #64748b;
  --pp-border: #e2e8f0;
  --pp-bottom-bar-h: 64px;
  --pp-tap-target: 48px;
  --pp-spring-duration: 220ms;
  --pp-spring-easing: cubic-bezier(0.34, 1.56, 0.64, 1);
  --pp-hold-duration: 1200ms;
  --pp-font: system-ui, -apple-system, 'Segoe UI', sans-serif;
  --pp-radius: 12px;
  --pp-radius-sm: 8px;
  --pp-radius-lg: 20px;
}

[data-pp-theme="dark"] {
  --pp-bg: #0f172a;
  --pp-bg-secondary: #1e293b;
  --pp-bg-tertiary: #334155;
  --pp-text: #f1f5f9;
  --pp-text-secondary: #94a3b8;
  --pp-border: #334155;
}

/* RTL */
[data-pp-dir="rtl"] {
  direction: rtl;
}
[data-pp-dir="rtl"] .pp-pane-track {
    flex-direction: row-reverse;
}
"""
