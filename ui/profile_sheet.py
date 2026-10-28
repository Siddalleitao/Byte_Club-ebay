"""Profile/settings sheet — swipe down from top.

Contains: seller profile card, editable fields, reference-data viewer,
language toggle, column mapping, TEST label, more menu.
"""

from __future__ import annotations

import html

from ui.api_client import get_client
from ui.i18n import t
from ui.icons import icon


def _esc(text: str) -> str:
    return html.escape(str(text))


def render_profile_sheet(lang: str = "en", seller: dict | None = None) -> str:
    seller = seller or {}
    client = get_client()
    ref = client.get_reference_data()
    compat = ref.get("compat_ref", {})
    checklist = ref.get("checklist", {})

    # Seller profile card
    profile_card = f"""
    <div class="pp-profile-section">
        <h3 class="pp-profile-section-title">{_esc(t('seller_card', lang))}</h3>
        <div class="pp-profile-field"><span class="pp-profile-field-label">{_esc(t('business', lang))}</span><span class="pp-profile-field-value">{_esc(seller.get('seller_type', ''))}</span></div>
        <div class="pp-profile-field"><span class="pp-profile-field-label">{_esc(t('location', lang))}</span><span class="pp-profile-field-value">{_esc(seller.get('location', ''))}</span></div>
        <div class="pp-profile-field"><span class="pp-profile-field-label">{_esc(t('languages', lang))}</span><span class="pp-profile-field-value">{_esc(seller.get('language_primary', ''))} / {_esc(seller.get('language_secondary', ''))}</span></div>
        <div class="pp-profile-field"><span class="pp-profile-field-label">{_esc(t('corridor', lang))}</span><span class="pp-profile-field-value">{_esc(seller.get('market_corridor', ''))}</span></div>
        <div class="pp-profile-field"><span class="pp-profile-field-label">{_esc(t('handling_days', lang))}</span><span class="pp-profile-field-value">{_esc(seller.get('handling_days', ''))}</span></div>
        <div class="pp-profile-field"><span class="pp-profile-field-label">{_esc(t('rack_codes', lang))}</span><span class="pp-profile-field-value">{_esc(seller.get('rack_codes', ''))}</span></div>
        <div class="pp-profile-field"><span class="pp-profile-field-label">{_esc(t('marketplace', lang))}</span><span class="pp-profile-field-value">{_esc(seller.get('marketplace', ''))}</span></div>
    </div>
    """

    # Editable fields
    edit_form = f"""
    <div class="pp-profile-section">
        <h3 class="pp-profile-section-title">{_esc(t('admin_edit_profile', lang))}</h3>
        <p style="font-size:13px;color:var(--pp-text-secondary);margin:0 0 12px">{_esc(t('admin_edit_profile_desc', lang))}</p>
        <input class="pp-input" placeholder="Name" value="{_esc(seller.get('name', ''))}" id="pp-edit-name" aria-label="Name" />
        <input class="pp-input" placeholder="Phone" value="{_esc(seller.get('phone', seller.get('whatsapp', '')))}" id="pp-edit-phone" aria-label="Phone" />
        <input class="pp-input" placeholder="{_esc(t('location', lang))}" value="{_esc(seller.get('location', ''))}" id="pp-edit-location" aria-label="Location" />
        <input class="pp-input" placeholder="{_esc(t('corridor', lang))}" value="{_esc(seller.get('market_corridor', ''))}" id="pp-edit-corridor" aria-label="Corridor" />
        <input class="pp-input" placeholder="{_esc(t('handling_days', lang))}" value="{_esc(seller.get('handling_days', ''))}" id="pp-edit-handling" aria-label="Handling days" />
        <input class="pp-input" placeholder="{_esc(t('rack_codes', lang))}" value="{_esc(seller.get('rack_codes', ''))}" id="pp-edit-rack" aria-label="Rack codes" />
        <input class="pp-input" placeholder="{_esc(t('marketplace', lang))}" value="{_esc(seller.get('marketplace', ''))}" id="pp-edit-marketplace" aria-label="Marketplace" />
        <input class="pp-input" placeholder="Currency (local)" value="{_esc(seller.get('currency_local', ''))}" id="pp-edit-currency-local" aria-label="Currency local" />
        <input class="pp-input" placeholder="Currency (export)" value="{_esc(seller.get('currency_export', ''))}" id="pp-edit-currency-export" aria-label="Currency export" />
        <button class="pp-hold-btn" data-action="save-profile" style="width:100%;margin-top:8px">
            <span class="pp-hold-btn-ring"></span>
            <span class="pp-hold-btn-label">{icon('check', 20)} {_esc(t('admin_save', lang))}</span>
        </button>
        <p style="font-size:11px;color:var(--pp-text-secondary);margin:4px 0 0;text-align:center">Hold to confirm — releasing early cancels</p>
    </div>
    """

    # Reference data
    ref_section = f"""
    <div class="pp-profile-section">
        <h3 class="pp-profile-section-title">{_esc(t('admin_reference_data', lang))}</h3>
        <div class="pp-metric-row" style="margin-bottom:12px">
            <div class="pp-metric">
                <div class="pp-metric-value">{_esc(compat.get('version', '—'))}</div>
                <div class="pp-metric-label">{_esc(t('admin_compat_version', lang))}</div>
            </div>
            <div class="pp-metric">
                <div class="pp-metric-value">{_esc(compat.get('rows', 0))}</div>
                <div class="pp-metric-label">{_esc(t('admin_compat_rows', lang))}</div>
            </div>
        </div>
        <div class="pp-expander">
            <div class="pp-expander-header">{_esc(t('admin_compat_version', lang))} — detail <span>▾</span></div>
            <div class="pp-expander-body">{_render_compat_table(compat.get('entries', []))}</div>
        </div>
    </div>
    """

    # Checklist
    checklist_section = f"""
    <div class="pp-profile-section">
        <h3 class="pp-profile-section-title">{_esc(t('admin_checklist_version', lang))}</h3>
        <div class="pp-metric-row" style="margin-bottom:12px">
            <div class="pp-metric">
                <div class="pp-metric-value">{_esc(checklist.get('version', '—'))}</div>
                <div class="pp-metric-label">{_esc(t('admin_checklist_version', lang))}</div>
            </div>
            <div class="pp-metric">
                <div class="pp-metric-value">{_esc(checklist.get('date', '—'))}</div>
                <div class="pp-metric-label">{_esc(t('admin_checklist_date', lang))}</div>
            </div>
        </div>
        <div class="pp-test-banner">
            {icon('warning', 20)}
            <div>
                <strong>{_esc(t('admin_test_label', lang))}</strong> — {_esc(t('admin_test_label_desc', lang))}
            </div>
        </div>
        <div class="pp-expander">
            <div class="pp-expander-header">{_esc(t('admin_checklist_disclaimer', lang))} <span>▾</span></div>
            <div class="pp-expander-body" style="font-size:13px;color:var(--pp-text-secondary)">{_esc(checklist.get('disclaimer', ''))}</div>
        </div>
    </div>
    """

    # Language toggle
    lang_section = f"""
    <div class="pp-profile-section">
        <h3 class="pp-profile-section-title">{_esc(t('lang_label', lang))}</h3>
        <button class="pp-btn pp-btn-secondary" style="width:100%" onclick="document.dispatchEvent(new CustomEvent('pp-toggle-lang'))">
            {icon('settings', 20)} {_esc(t('lang_toggle', lang))}
        </button>
    </div>
    """

    # More menu items
    more_section = f"""
    <div class="pp-profile-section">
        <h3 class="pp-profile-section-title">More</h3>
        <div class="pp-profile-field" style="cursor:pointer" onclick="document.dispatchEvent(new CustomEvent('pp-show-mapping'))">
            <span>{_esc(t('upload_column_mapping', lang))}</span>
            <span>▸</span>
        </div>
        <div class="pp-profile-field" style="cursor:pointer" onclick="document.dispatchEvent(new CustomEvent('pp-replay-tutorial'))">
            <span>Tutorial</span>
            <span>▸</span>
        </div>
    </div>
    """

    return f"""
    <div class="pp-profile-sheet" id="pp-profile-sheet" role="dialog" aria-label="Profile and settings">
        <div class="pp-profile-content">
            <div class="pp-profile-handle"></div>
            <button class="pp-chat-thread-back pp-profile-close" style="position:absolute;top:12px;right:12px" aria-label="Close">
                {icon('close', 24)}
            </button>
            {profile_card}
            {edit_form}
            {ref_section}
            {checklist_section}
            {lang_section}
            {more_section}
        </div>
    </div>
    """


def _render_compat_table(entries: list[dict]) -> str:
    if not entries:
        return '<p style="font-size:13px;color:var(--pp-text-secondary)">No entries</p>'
    # Show first 10 rows
    rows = entries[:10]
    cols = list(rows[0].keys())[:5]
    header = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body = ""
    for r in rows:
        body += "<tr>" + "".join(f"<td>{_esc(r.get(c, ''))}</td>" for c in cols) + "</tr>"
    return f'<table class="pp-data-table"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'
