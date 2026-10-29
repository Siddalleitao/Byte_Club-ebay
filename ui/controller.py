"""PartProof JavaScript controller.

Gesture engine (pane swipe with snap, story overlay, hold-to-confirm, context
menu, pull-to-refresh, keyboard nav, RTL mirroring) plus the data layer.

Rule (g): the page talks to the API gateway only. The iframe cannot call back
into Streamlit, so every action (answer a question, run the pipeline, approve,
send a photo, save the profile) is a fetch to the gateway with the seller's
session token, and the panes re-render from the gateway's response.

The script reads its settings from `window.PP_CONFIG`:
{api, token, lang, strings: {en: {...}, ar: {...}}, icons: {name: svg}}.
"""


def get_js() -> str:
    return _JS


_JS = r"""
(function () {
'use strict';

var CFG = window.PP_CONFIG || {};
var PANES = ['map', 'chats', 'home', 'stories', 'discover'];
var PANE_ICON = {map: 'map', chats: 'chat', stories: 'story', discover: 'queue'};
var PANE_LABEL = {map: 'nav_map', chats: 'nav_chats', stories: 'nav_board', discover: 'nav_queue'};
var BADGES = {
    'Approved': {cls: 'approved', icon: '✅', key: 'badge_approved'},
    'Ready for seller review': {cls: 'ready', icon: '🔵', key: 'badge_ready'},
    'Information missing': {cls: 'missing', icon: '⚠️', key: 'badge_missing'},
    'Specialist review required': {cls: 'specialist', icon: '🔴', key: 'badge_specialist'}
};
var FILTERS = ['all', 'Approved', 'Ready for seller review', 'Information missing', 'Specialist review required'];
// Quick replies for the question at the top of a thread, by the field it asks about.
var QUICK = {
    en: {
        test_status: ['untested', 'tested and working', 'partly tested', 'failed'],
        position: ['left', 'right'],
        side: ['left', 'right']
    },
    ar: {
        test_status: ['لم يتم فحصها', 'تم فحصها وتعمل', 'فحص جزئي', 'فشل الفحص'],
        position: ['يسار', 'يمين'],
        side: ['يسار', 'يمين']
    }
};
var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

var lang = store('local', 'get', 'pp-lang') || CFG.lang || 'en';
var S = {
    profile: null, editable: [], board: [], quarantine: [], ref: null,
    details: {}, logs: {}, chatSku: null, filter: 'all', search: '',
    deferred: JSON.parse(store('session', 'get', 'pp-deferred') || '[]'),
    busy: false, loaded: false, error: null, expired: false,
    photo: null, cameraStream: null
};
var currentPane = store('session', 'get', 'pp-pane') || 'home';
var app, track, bottomBar, profileSheet, storyOverlay, contextMenu, toastEl, tutorialEl, offlineBanner;

// ── Helpers ──
function store(kind, op, key, value) {
    // Storage can throw in a sandboxed or private frame; the app works without it.
    try {
        var s = kind === 'local' ? window.localStorage : window.sessionStorage;
        if (op === 'get') return s.getItem(key);
        s.setItem(key, value);
    } catch (e) { return null; }
    return null;
}
function T(key, vars) {
    var table = (CFG.strings && (CFG.strings[lang] || CFG.strings.en)) || {};
    var s = table[key] || (CFG.strings && CFG.strings.en && CFG.strings.en[key]) || key;
    if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
    return s;
}
function esc(v) {
    return String(v === null || v === undefined ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function icon(name, size) {
    size = size || 24;
    var svg = (CFG.icons && (CFG.icons[name] || CFG.icons.question)) || '';
    return '<span class="pp-icon" style="width:' + size + 'px;height:' + size + 'px;display:inline-flex;align-items:center;justify-content:center">' + svg + '</span>';
}
function rtl() { return lang === 'ar'; }
function order() { return rtl() ? PANES.slice().reverse() : PANES.slice(); }
function haptic(ms) { if (navigator.vibrate) { try { navigator.vibrate(ms); } catch (e) {} } }
function badgeOf(p) {
    if (p.status === 'Approved' || p.status === 'Exported') return 'Approved';
    return BADGES[p.readiness] ? p.readiness : 'Information missing';
}
function badgeInfo(p) { return BADGES[badgeOf(p)]; }
function canApprove(p) { return badgeOf(p) === 'Ready for seller review'; }
function partBySku(sku) { return S.board.filter(function (p) { return p.sku === sku; })[0]; }
function shortSku(sku) { return String(sku).split('-').pop().slice(-3); }
function qText(q) { return (rtl() ? (q.text_ar || q.text_en) : (q.text_en || q.text_ar)) || ''; }
function openQuestions(sku) {
    var d = S.details[sku];
    return d ? (d.questions || []).filter(function (q) { return q.status === 'open'; }) : [];
}

// ── API ──
function apiBase() {
    try {
        var u = new URL(CFG.api);
        var host = window.parent.location.hostname;
        if ((u.hostname === 'localhost' || u.hostname === '127.0.0.1') && host && host !== 'localhost' && host !== '127.0.0.1') {
            u.hostname = host;  // opened from another machine: the API runs beside Streamlit
        }
        return u.origin;
    } catch (e) { return CFG.api; }
}
var BASE = apiBase();

function ApiError(status, message) { this.status = status; this.message = message; }

function detailText(data) {
    if (!data) return T('api_error');
    var d = data.detail !== undefined ? data.detail : data;
    if (Array.isArray(d)) {
        return d.map(function (e) {
            var field = e.loc ? e.loc[e.loc.length - 1] + ': ' : '';
            return field + (e.msg || JSON.stringify(e));
        }).join('; ');
    }
    return typeof d === 'object' ? JSON.stringify(d) : String(d);
}

function api(method, path, body, auth) {
    var opts = {method: method, headers: {}};
    if (auth !== false) opts.headers.Authorization = 'Bearer ' + CFG.token;
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    return fetch(BASE + path, opts).then(function (r) {
        return r.text().then(function (txt) {
            var data = null;
            if (txt) { try { data = JSON.parse(txt); } catch (e) { data = txt; } }
            if (r.status === 401) { sessionExpired(); throw new ApiError(401, T('session_expired')); }
            if (!r.ok) throw new ApiError(r.status, detailText(data));
            return data;
        });
    }, function () {
        throw new ApiError(0, T('api_unreachable', {url: BASE}));
    });
}

// ── Data loading ──
function loadBoard() {
    return Promise.all([api('GET', '/me/board'), api('GET', '/me/quarantine')]).then(function (res) {
        S.board = res[0].parts || [];
        S.quarantine = (res[1].quarantine || []).filter(function (q) { return q.status === 'open'; });
        S.loaded = true;
        S.error = null;
        renderData();
    });
}

function loadDetail(sku) {
    return api('GET', '/me/parts/' + encodeURIComponent(sku) + '/detail').then(function (d) {
        S.details[sku] = d;
        return d;
    });
}

function loadProfile() {
    return api('GET', '/me/profile').then(function (d) {
        S.profile = d.profile;
        S.editable = d.editable_fields || [];
        renderProfileSheet();
        renderHome();
    });
}

function boot() {
    renderAll();
    loadProfile()
        .then(loadBoard)
        .then(ensurePipeline)
        .catch(showError);
    api('GET', '/reference', undefined, false).then(function (r) {
        S.ref = r;
        // Do not wipe a form the seller is already editing.
        if (!profileSheet.classList.contains('open')) renderProfileSheet();
    }, function () {});
}

// The pipeline's checkpoint state lives in the API's memory: after an API
// restart it is empty and answers cannot move a part, so run it once.
function ensurePipeline() {
    if (!S.board.length) return null;
    return api('GET', '/me/pipeline/' + encodeURIComponent(S.board[0].sku)).then(function (r) {
        if (!r.pipeline || !r.pipeline.sku) return runPipeline(T('home_preparing'));
        return null;
    });
}

function runPipeline(label) {
    if (S.busy) return null;
    S.busy = label || T('home_running');
    renderHomeSummary();
    return api('POST', '/me/pipeline/run?wait=true')
        .then(function (r) {
            S.details = {};
            return loadBoard().then(function () {
                toast(T('home_pipeline_done', {n: (r.queued || []).length}));
                if (S.chatSku) return openChat(S.chatSku, true);
                return null;
            });
        })
        .catch(showError)
        .then(function () { S.busy = false; renderHomeSummary(); });
}

function refreshAfterChange(sku) {
    var jobs = [loadBoard()];
    if (sku) jobs.push(loadDetail(sku));
    return Promise.all(jobs).then(function () { renderData(); });
}

function showError(e) {
    if (e && e.status === 401) return;
    S.error = (e && e.message) || String(e);
    renderData();
    toast(S.error);
}

function sessionExpired() {
    if (S.expired) return;
    S.expired = true;
    var bar = document.getElementById('pp-session-banner');
    if (bar) {
        bar.innerHTML = esc(T('session_expired')) +
            ' <button class="pp-btn pp-btn-primary" style="min-height:32px;padding:4px 12px;margin-inline-start:8px" data-act="reload">' + esc(T('reload')) + '</button>';
        bar.classList.add('show');
    }
}

// ── Rendering ──
function renderAll() {
    app.setAttribute('data-pp-dir', rtl() ? 'rtl' : 'ltr');
    app.style.direction = rtl() ? 'rtl' : 'ltr';
    document.documentElement.setAttribute('lang', lang);
    document.documentElement.setAttribute('dir', rtl() ? 'rtl' : 'ltr');
    // Panes sit in visual order so scroll offsets match in both directions.
    track.innerHTML = order().map(function (p) {
        return '<div class="pp-pane" data-pane="' + p + '" id="pp-pane-' + p + '"></div>';
    }).join('');
    renderBottomBar();
    renderHome();
    renderData();
    renderProfileSheet();
    renderTutorialButtons();
    navigateTo(currentPane, true);
}

function renderData() {
    renderHomeSummary();
    renderChats();
    renderBoard();
    renderQueue();
    renderMap();
}

function renderBottomBar() {
    var html = '';
    PANES.forEach(function (p) {
        if (p === 'home') {
            html += '<div class="pp-nav-centre"><button class="pp-nav-centre-btn" data-act="camera" aria-label="' + esc(T('camera_start')) + '">' + icon('camera', 28) + '</button></div>';
        } else {
            html += '<button class="pp-nav-btn" data-pane="' + p + '" aria-label="' + esc(T(PANE_LABEL[p])) + '">' + icon(PANE_ICON[p], 22) + '<span>' + esc(T(PANE_LABEL[p])) + '</span></button>';
        }
    });
    bottomBar.innerHTML = html;
    var fab = document.getElementById('pp-profile-fab');
    if (fab) fab.setAttribute('aria-label', T('nav_profile'));
    updateBottomBar();
}

function statusEmpty() {
    if (S.error && !S.loaded) {
        return '<div class="pp-empty">' + esc(S.error) + '<br><br><button class="pp-btn pp-btn-primary" data-act="retry">' + icon('refresh', 18) + ' ' + esc(T('retry')) + '</button></div>';
    }
    return '<div class="pp-skeleton" style="padding:16px"><div class="pp-skeleton-card"></div><div class="pp-skeleton-card"></div><div class="pp-skeleton-card"></div></div>';
}

// HOME — camera and pipeline. The viewfinder renders once so a running camera survives data refreshes.
function renderHome() {
    var el = document.getElementById('pp-pane-home');
    if (!el) return;
    var name = S.profile ? S.profile.display_name : (CFG.displayName || '');
    el.innerHTML =
        '<div class="pp-home">' +
        '<div class="pp-home-title">' + esc(T('login_welcome')) + ', ' + esc(name) + '</div>' +
        '<div class="pp-home-sub">' + esc(T('app_subtitle')) + '</div>' +
        '<div id="pp-home-summary" style="width:100%;max-width:420px"></div>' +
        '<div class="pp-viewfinder" id="pp-viewfinder"></div>' +
        '<div id="pp-capture-bar" class="pp-capture-bar" hidden></div>' +
        '<input type="file" id="pp-photo-input" accept="image/*" capture="environment" hidden>' +
        '<div class="pp-chips">' +
        '<button class="pp-chip" data-nav="stories">' + icon('grid', 18) + ' ' + esc(T('board_title')) + '</button>' +
        '<button class="pp-chip" data-nav="chats">' + icon('chat', 18) + ' ' + esc(T('questions_title')) + '</button>' +
        '</div></div>';
    stopCamera();
    renderViewfinder();
    renderHomeSummary();
}

function renderViewfinder() {
    var vf = document.getElementById('pp-viewfinder');
    if (!vf) return;
    if (S.photo) {
        vf.innerHTML = '<img src="' + S.photo.dataUrl + '" alt="" style="width:100%;height:100%;object-fit:cover">';
    } else {
        vf.innerHTML = '<div class="pp-viewfinder-placeholder">' +
            '<div style="margin-bottom:16px">' + esc(T('upload_photos')) + '</div>' +
            '<div style="display:flex;flex-direction:column;gap:8px;align-items:center">' +
            '<button class="pp-btn pp-btn-primary" data-act="camera">' + icon('camera', 20) + ' ' + esc(T('camera_start')) + '</button>' +
            '<button class="pp-btn pp-btn-secondary" data-act="pick-photo">' + icon('upload', 20) + ' ' + esc(T('camera_choose_file')) + '</button>' +
            '</div></div>';
    }
    renderCaptureBar();
}

function renderCaptureBar() {
    var bar = document.getElementById('pp-capture-bar');
    if (!bar) return;
    if (!S.photo) { bar.hidden = true; bar.innerHTML = ''; return; }
    var selected = S.photo.sku || S.chatSku || (S.board[0] && S.board[0].sku) || '';
    var options = S.board.map(function (p) {
        return '<option value="' + esc(p.sku) + '"' + (p.sku === selected ? ' selected' : '') + '>' + esc(p.sku + ' · ' + (p.title || p.part_type || '')) + '</option>';
    }).join('');
    bar.hidden = false;
    bar.innerHTML =
        '<label class="pp-field-label" for="pp-photo-sku">' + esc(T('camera_attach_to')) + '</label>' +
        '<select class="pp-input" id="pp-photo-sku">' + options + '</select>' +
        '<div style="display:flex;gap:8px">' +
        '<button class="pp-btn pp-btn-secondary" style="flex:1" data-act="retake">' + icon('refresh', 18) + ' ' + esc(T('camera_retake')) + '</button>' +
        '<button class="pp-btn pp-btn-primary" style="flex:1" data-act="send-photo"' + (S.photo.sending ? ' disabled' : '') + '>' + icon('send', 18) + ' ' + esc(S.photo.sending ? T('chat_sending') : T('camera_send')) + '</button>' +
        '</div>';
}

function counts() {
    var c = {};
    Object.keys(BADGES).forEach(function (b) { c[b] = 0; });
    S.board.forEach(function (p) { c[badgeOf(p)] += 1; });
    return c;
}

function metricTiles(c) {
    return '<div class="pp-metric-row">' + Object.keys(BADGES).map(function (b) {
        var info = BADGES[b];
        return '<button class="pp-metric pp-metric-btn" data-filter-jump="' + esc(b) + '">' +
            '<div class="pp-metric-value">' + c[b] + '</div>' +
            '<div class="pp-metric-label">' + info.icon + ' ' + esc(T(info.key)) + '</div></button>';
    }).join('') + '</div>';
}

function renderHomeSummary() {
    var el = document.getElementById('pp-home-summary');
    if (!el) return;
    var body = S.loaded ? metricTiles(counts()) : (S.error ? '' : '<div class="pp-skeleton-line" style="height:64px"></div>');
    var btn = S.busy
        ? '<button class="pp-btn pp-btn-primary" disabled style="width:100%;margin-top:12px"><span class="pp-spinner"></span> ' + esc(S.busy) + '</button>'
        : '<button class="pp-btn pp-btn-primary" data-act="run-pipeline" style="width:100%;margin-top:12px">' + icon('refresh', 20) + ' ' + esc(T('home_run_pipeline')) + '</button>';
    var err = S.error ? '<div class="pp-error" style="margin-top:12px">' + esc(S.error) + '</div>' : '';
    el.innerHTML = '<div class="pp-section-label">' + esc(T('home_summary')) + '</div>' + body + btn + err;
}

// CHATS — question threads per part.
function renderChats() {
    var el = document.getElementById('pp-pane-chats');
    if (!el) return;
    if (S.chatSku) { renderThread(el); return; }
    var head = '<div class="pp-chats-header"><h2 class="pp-chats-title">' + esc(T('questions_title')) + '</h2></div>';
    if (!S.loaded) { el.innerHTML = head + statusEmpty(); return; }
    var waiting = S.board.filter(function (p) { return p.questions_waiting > 0; })
        .sort(function (a, b) { return b.questions_waiting - a.questions_waiting; });
    var rest = S.board.filter(function (p) { return !(p.questions_waiting > 0); });
    var item = function (p) {
        var info = badgeInfo(p);
        var preview = p.questions_waiting > 0
            ? p.questions_waiting + ' ' + T('board_questions_waiting')
            : (canApprove(p) ? T('chat_ready') : T(info.key));
        return '<div class="pp-chat-item" role="button" tabindex="0" data-open-chat="' + esc(p.sku) + '" data-context="1" data-sku="' + esc(p.sku) + '">' +
            '<div class="pp-chat-avatar status-' + info.cls + '">' + esc(shortSku(p.sku)) + '</div>' +
            '<div class="pp-chat-body"><p class="pp-chat-name">' + esc(p.sku) + ' · ' + esc(p.title || p.part_type || '') + '</p>' +
            '<p class="pp-chat-preview">' + info.icon + ' ' + esc(preview) + '</p></div>' +
            (p.questions_waiting > 0 ? '<div class="pp-chat-unread">' + p.questions_waiting + '</div>' : '') +
            '</div>';
    };
    var html = waiting.map(item).join('');
    if (rest.length) {
        html += '<div class="pp-section-label" style="padding:12px 16px 4px">' + esc(T('board_no_questions')) + '</div>' + rest.map(item).join('');
    }
    el.innerHTML = '<div data-view="chat-list">' + head + '<div class="pp-chat-list">' + html + '</div></div>';
}

function renderThread(el) {
    var sku = S.chatSku;
    var p = partBySku(sku) || {sku: sku};
    var info = badgeInfo(p);
    var input = document.getElementById('pp-chat-input');
    var draft = input ? input.value : '';
    var hadFocus = input && document.activeElement === input;
    var d = S.details[sku];
    var log = S.logs[sku] || [];
    var msgs = '';
    log.forEach(function (m, i) {
        if (m.dir === 'out') {
            var cls = m.state === 'pending' ? ' pp-msg-pending' : (m.state === 'failed' ? ' pp-msg-failed' : '');
            msgs += '<div class="pp-msg pp-msg-out' + cls + '" dir="auto">' + esc(m.text) + '</div>';
            if (m.state === 'failed') {
                msgs += '<div class="pp-msg-retry" style="align-self:flex-end" role="button" tabindex="0" data-retry="' + i + '">' + esc(m.error || '') + ' · ' + esc(T('chat_failed')) + '</div>';
            }
        } else {
            msgs += '<div class="pp-msg pp-msg-in" dir="auto">' + esc(m.text) + '</div>';
        }
    });
    var open = openQuestions(sku);
    if (!d) {
        msgs += '<div class="pp-skeleton-line" style="height:48px;width:70%"></div>';
    } else if (!open.length && !log.length) {
        msgs += '<div class="pp-empty">' + esc(T('questions_no_questions')) + '</div>';
    }
    open.forEach(function (q, i) {
        var pr = {high: '🔴', medium: '🟡', low: '🟢'}[q.priority] || '⚪';
        msgs += '<div class="pp-msg pp-msg-in" dir="auto"' + (i === 0 ? ' data-top-question="1"' : '') + '><div>' + esc(qText(q)) + '</div>' +
            '<div class="pp-msg-meta">' + pr + ' ' + esc(T('questions_priority')) + ': ' + esc(q.priority || '') +
            (q.field ? ' · ' + esc(q.field) : '') + '</div></div>';
    });
    if (canApprove(p)) {
        msgs += '<div class="pp-approve-banner"><div>✅ ' + esc(T('chat_ready')) + '</div>' + holdApprove(sku) + '</div>';
    }
    var chips = open.length ? (QUICK[lang][open[0].field] || []) : [];
    el.innerHTML =
        '<div class="pp-chat-thread" data-view="thread">' +
        '<div class="pp-chat-thread-header">' +
        '<button class="pp-chat-thread-back" data-act="back-to-chats" aria-label="Back">' + icon('back', 24) + '</button>' +
        '<div class="pp-chat-avatar status-' + info.cls + '">' + esc(shortSku(sku)) + '</div>' +
        '<div style="min-width:0;flex:1"><p class="pp-chat-name">' + esc(sku) + '</p>' +
        '<p class="pp-chat-preview">' + info.icon + ' ' + esc(T(info.key)) + (p.title ? ' · ' + esc(p.title) : '') + '</p></div>' +
        '<button class="pp-chat-thread-back" data-act="open-story" data-sku="' + esc(sku) + '" aria-label="' + esc(T('chat_open_part')) + '">' + icon('more', 24) + '</button>' +
        '</div>' +
        '<div class="pp-chat-thread-messages" id="pp-chat-messages" aria-live="polite">' + msgs + '</div>' +
        (chips.length ? '<div class="pp-quick-replies">' + chips.map(function (c) {
            return '<button class="pp-chip" data-quick="' + esc(c) + '">' + esc(c) + '</button>';
        }).join('') + '</div>' : '') +
        '<form class="pp-chat-input-bar" id="pp-chat-form">' +
        '<input type="text" class="pp-chat-input" id="pp-chat-input" dir="auto" autocomplete="off" placeholder="' + esc(T('questions_answer_placeholder')) + '" aria-label="' + esc(T('questions_answer_placeholder')) + '">' +
        '<button type="submit" class="pp-chat-send" aria-label="' + esc(T('questions_submit')) + '">' + icon('send', 22) + '</button>' +
        '</form></div>';
    var newInput = document.getElementById('pp-chat-input');
    newInput.value = draft;
    if (hadFocus) newInput.focus();
    var box = document.getElementById('pp-chat-messages');
    box.scrollTop = box.scrollHeight;
}

function openChat(sku, quiet) {
    S.chatSku = sku;
    if (!quiet) navigateTo('chats');
    renderChats();
    return loadDetail(sku).then(renderChats, showError);
}

function sendChat(sku, text) {
    text = (text || '').trim();
    if (!text || !sku) return;
    var log = S.logs[sku] = S.logs[sku] || [];
    var msg = {dir: 'out', text: text, state: 'pending'};
    log.push(msg);
    renderChats();
    haptic(15);
    api('POST', '/me/chat', {message: text, sku: sku}).then(function (r) {
        msg.state = 'sent';
        log.push({dir: 'in', text: (r.reply && r.reply.text) || ''});
        return refreshAfterChange(sku);
    }).catch(function (e) {
        msg.state = 'failed';
        msg.error = e.message;
        renderChats();
    });
}

function holdApprove(sku) {
    return '<button class="pp-hold-btn pp-hold-approve" data-hold="approve" data-sku="' + esc(sku) + '">' +
        '<span class="pp-hold-btn-ring"></span><span class="pp-hold-btn-label">' + icon('check', 20) + ' ' + esc(T('board_hold_approve')) + '</span></button>';
}

function approve(sku) {
    toast(T('chat_sending'));
    return api('POST', '/me/parts/' + encodeURIComponent(sku) + '/approve').then(function () {
        toast(T('board_approved', {sku: sku}));
        haptic(30);
        return refreshAfterChange(sku);
    }).catch(showError);
}

// BOARD — status rings and part cards, with working search and filters.
function boardParts() {
    var q = S.search.toLowerCase();
    return S.board.filter(function (p) {
        if (S.filter !== 'all' && badgeOf(p) !== S.filter) return false;
        if (!q) return true;
        return [p.sku, p.title, p.part_type, p.status, p.readiness].join(' ').toLowerCase().indexOf(q) >= 0;
    });
}

function renderBoard() {
    var el = document.getElementById('pp-pane-stories');
    if (!el) return;
    if (!el.querySelector('#pp-board-search')) {
        el.innerHTML =
            '<div data-pane-body="stories"><div class="pp-stories-header">' +
            '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px">' +
            '<h2 class="pp-stories-title">' + esc(T('board_title')) + '</h2>' +
            '<button class="pp-chat-thread-back" data-act="refresh" aria-label="' + esc(T('refresh')) + '">' + icon('refresh', 22) + '</button></div>' +
            '<div class="pp-search-bar">' + icon('search', 20) +
            '<input type="search" class="pp-search-input" id="pp-board-search" placeholder="' + esc(T('board_search')) + '" aria-label="' + esc(T('board_search')) + '"></div>' +
            '<div class="pp-filter-bar" id="pp-board-filters"></div></div>' +
            '<div class="pp-ring-row" id="pp-board-rings"></div>' +
            '<div style="padding:0 16px 16px" id="pp-board-cards"></div></div>';
    }
    document.getElementById('pp-board-search').value = S.search;
    document.getElementById('pp-board-filters').innerHTML = FILTERS.map(function (f) {
        var label = f === 'all' ? T('board_all') : BADGES[f].icon + ' ' + T(BADGES[f].key);
        return '<button class="pp-filter-chip' + (S.filter === f ? ' active' : '') + '" data-filter="' + esc(f) + '" aria-pressed="' + (S.filter === f) + '">' + esc(label) + '</button>';
    }).join('');
    renderBoardList();
}

function renderBoardList() {
    var rings = document.getElementById('pp-board-rings');
    var cards = document.getElementById('pp-board-cards');
    if (!rings || !cards) return;
    if (!S.loaded) { rings.innerHTML = ''; cards.innerHTML = statusEmpty(); return; }
    var parts = boardParts();
    rings.innerHTML = parts.map(function (p, i) {
        var info = badgeInfo(p);
        return '<button class="pp-ring" data-story="' + i + '" data-context="1" data-sku="' + esc(p.sku) + '" aria-label="' + esc(p.sku + ', ' + T(info.key)) + '">' +
            '<div class="pp-ring-circle status-' + info.cls + '"><div class="pp-ring-icon">' + info.icon + '</div>' +
            (p.questions_waiting > 0 ? '<div class="pp-ring-badge">' + p.questions_waiting + '</div>' : '') + '</div>' +
            '<div class="pp-ring-label">' + esc(p.sku) + '</div></button>';
    }).join('');
    if (!parts.length) { cards.innerHTML = '<div class="pp-empty">' + esc(T('board_no_match')) + '</div>'; return; }
    cards.innerHTML = parts.map(function (p, i) {
        var info = badgeInfo(p);
        var actions = '';
        if (p.questions_waiting > 0) actions += '<button class="pp-btn pp-btn-secondary" data-open-chat="' + esc(p.sku) + '">' + icon('question', 18) + ' ' + esc(T('board_answer')) + '</button>';
        if (canApprove(p)) actions += holdApprove(p.sku);
        return '<div class="pp-card" data-context="1" data-sku="' + esc(p.sku) + '" style="margin-bottom:8px;border-radius:12px">' +
            '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">' +
            '<div style="min-width:0"><div class="pp-card-sku">' + esc(p.sku) + '</div>' +
            '<div class="pp-card-detail">' + esc(p.title || '') + '</div>' +
            '<div class="pp-card-detail">' + esc(p.part_type || '') + ' · ' + esc(T('board_status')) + ': ' + esc(p.status || '') + '</div></div>' +
            '<div class="pp-story-badge ' + info.cls + '">' + info.icon + ' ' + esc(T(info.key)) + '</div></div>' +
            '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px">' +
            '<span class="pp-card-detail">' + (p.questions_waiting || 0) + ' ' + esc(T('board_questions_waiting')) + '</span>' +
            (p.compatibility ? '<span class="pp-card-detail">' + esc(T('board_compat')) + ': ' + esc(p.compatibility) + '</span>' : '') +
            (p.quarantined ? '<span class="pp-card-detail" style="color:var(--pp-status-specialist)">' + icon('shield', 14) + ' ' + esc(T('board_quarantined')) + ': ' + p.quarantined + '</span>' : '') +
            '</div>' +
            (actions ? '<div class="pp-card-actions" style="margin-top:12px">' + actions + '</div>' : '') +
            '<button class="pp-link" data-story="' + i + '">' + esc(T('chat_open_part')) + ' ›</button>' +
            '</div>';
    }).join('');
}

// QUEUE — what needs the seller, swipe right to act, left to defer.
function queueItems() {
    var items = [];
    S.quarantine.forEach(function (q) {
        items.push({id: 'q:' + q.id, sku: q.sku, rank: 0, title: T('queue_blocked', {sku: q.sku || '—'}),
            detail: (q.reason || '') + (q.text ? ' · "' + q.text + '"' : ''), cls: 'specialist'});
    });
    S.board.forEach(function (p) {
        var b = badgeOf(p);
        if (b === 'Approved') return;
        var rank = b === 'Specialist review required' ? 1 : (p.questions_waiting > 0 ? 2 : (canApprove(p) ? 3 : 4));
        if (rank === 4) return;
        var detail = p.questions_waiting > 0 ? p.questions_waiting + ' ' + T('board_questions_waiting') : T(BADGES[b].key);
        items.push({id: 'p:' + p.sku, sku: p.sku, rank: rank, title: p.sku + ' · ' + (p.title || p.part_type || ''),
            detail: detail, cls: BADGES[b].cls, questions: p.questions_waiting || 0});
    });
    items.sort(function (a, b) { return a.rank - b.rank || (b.questions || 0) - (a.questions || 0); });
    return items;
}

function renderQueue() {
    var el = document.getElementById('pp-pane-discover');
    if (!el) return;
    var head = '<div class="pp-discover-header"><h2 class="pp-discover-title">' + esc(T('queue_title')) + '</h2>' +
        '<p class="pp-discover-sub">' + esc(T('queue_sub')) + '</p></div>';
    if (!S.loaded) { el.innerHTML = head + statusEmpty(); return; }
    var items = queueItems();
    var now = items.filter(function (i) { return S.deferred.indexOf(i.id) < 0; });
    var later = items.filter(function (i) { return S.deferred.indexOf(i.id) >= 0; });
    var card = function (it, deferred) {
        return '<div class="pp-card' + (deferred ? ' pp-card-deferred' : '') + '" data-swipeable data-qid="' + esc(it.id) + '" data-sku="' + esc(it.sku || '') + '">' +
            '<div style="display:flex;gap:8px;align-items:center"><span class="pp-dot status-' + it.cls + '"></span><div class="pp-card-sku">' + esc(it.title) + '</div></div>' +
            '<div class="pp-card-detail" dir="auto">' + esc(it.detail) + '</div>' +
            '<div class="pp-card-actions">' +
            '<button class="pp-btn pp-btn-primary" data-swipe="right">' + icon('check', 20) + ' ' + esc(T('queue_do_now')) + '</button>' +
            (deferred ? '' : '<button class="pp-btn pp-btn-secondary" data-swipe="left">' + icon('close', 20) + ' ' + esc(T('queue_later')) + '</button>') +
            '</div>' +
            '<div class="pp-card-hint"><span>← ' + esc(T('queue_later')) + '</span><span>' + esc(T('queue_do_now')) + ' →</span></div></div>';
    };
    var html = now.map(function (i) { return card(i, false); }).join('');
    if (!now.length) html = '<div class="pp-empty">' + esc(T('queue_empty')) + '</div>';
    if (later.length) html += '<div class="pp-section-label" style="padding:16px 0 8px">' + esc(T('queue_deferred')) + '</div>' + later.map(function (i) { return card(i, true); }).join('');
    el.innerHTML = '<div>' + head + '<div class="pp-card-stack">' + html + '</div></div>';
}

function queueAction(cardEl, direction) {
    var id = cardEl.getAttribute('data-qid');
    var sku = cardEl.getAttribute('data-sku');
    cardEl.classList.add('swipe-' + direction);
    haptic(20);
    setTimeout(function () {
        var i = S.deferred.indexOf(id);
        if (direction === 'left') {
            if (i < 0) S.deferred.push(id);
        } else if (i >= 0) {
            S.deferred.splice(i, 1);
        }
        store('session', 'set', 'pp-deferred', JSON.stringify(S.deferred));
        renderQueue();
        if (direction === 'right' && sku) openChat(sku);
    }, reducedMotion ? 0 : 250);
}

// MAP — the seller's corridor and fixed details.
function renderMap() {
    var el = document.getElementById('pp-pane-map');
    if (!el) return;
    var p = S.profile;
    if (!p) { el.innerHTML = '<div style="padding:16px">' + statusEmpty() + '</div>'; return; }
    var field = function (k, v) {
        return '<div class="pp-profile-field"><span class="pp-profile-field-label">' + esc(T(k)) + '</span><span class="pp-profile-field-value">' + esc(v) + '</span></div>';
    };
    el.innerHTML =
        '<div style="padding:16px;padding-top:calc(16px + env(safe-area-inset-top))">' +
        '<h2 class="pp-stories-title">' + esc(T('corridor')) + '</h2>' +
        '<div class="pp-corridor-card"><div style="font-size:40px;margin-bottom:8px">🌍</div>' +
        '<div style="font-size:18px;font-weight:700">' + esc(p.city) + '</div>' +
        '<div style="font-size:14px;color:var(--pp-text-secondary);margin-top:4px">' + esc(p.corridor) + ' · ' + esc(p.inventory_currency) + ' → ' + esc(p.listing_currency) + '</div></div>' +
        (S.loaded ? metricTiles(counts()) : '') +
        '<div style="margin-top:16px">' +
        field('business', p.business_name) +
        field('handling_days', p.handling_days) +
        field('field_return_policy_ref', p.return_policy_ref || '—') +
        field('rack_codes', (p.rack_codes || []).join(', ')) +
        field('languages', (p.languages || []).join(', ')) +
        field('field_fx_rate_source', p.fx_rate_source || '—') +
        '</div></div>';
}

// PROFILE SHEET — the seller-details screen.
var FIELD_ORDER = ['display_name', 'business_name', 'city', 'languages', 'reply_language', 'corridor',
    'inventory_currency', 'listing_currency', 'fx_rate_source', 'handling_days', 'return_policy_ref', 'rack_codes'];

function fieldValue(name) {
    var v = S.profile ? S.profile[name] : '';
    if (Array.isArray(v)) return v.join(', ');
    return v === null || v === undefined ? '' : v;
}

function renderProfileSheet() {
    var content = profileSheet && profileSheet.querySelector('.pp-profile-content');
    if (!content) return;
    var p = S.profile;
    var html = '<div class="pp-profile-handle"></div>' +
        '<button class="pp-chat-thread-back pp-profile-close" data-act="close-profile" aria-label="Close">' + icon('close', 24) + '</button>' +
        '<h2 class="pp-stories-title" style="padding-inline-end:48px">' + esc(T('profile_title')) + '</h2>';
    if (!p) {
        content.innerHTML = html + statusEmpty();
        return;
    }
    var fields = FIELD_ORDER.filter(function (f) { return S.editable.indexOf(f) >= 0; }).map(function (f) {
        var id = 'pp-edit-' + f;
        var label = '<label class="pp-field-label" for="' + id + '">' + esc(T('field_' + f)) + '</label>';
        if (f === 'reply_language') {
            var opts = [['match_user', T('reply_match_user')], ['en', 'English'], ['ar', 'العربية']];
            return label + '<select class="pp-input" id="' + id + '" data-field="' + f + '">' + opts.map(function (o) {
                return '<option value="' + o[0] + '"' + (p.reply_language === o[0] ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
            }).join('') + '</select>';
        }
        var type = f === 'handling_days' ? 'number' : 'text';
        return label + '<input class="pp-input" type="' + type + '" id="' + id + '" data-field="' + f + '" value="' + esc(fieldValue(f)) + '" dir="auto">';
    }).join('');
    var ref = S.ref || {};
    var compat = ref.compat_ref || {};
    var checklist = ref.checklist || {};
    html +=
        '<div class="pp-profile-section"><h3 class="pp-profile-section-title">' + esc(T('seller_card')) + '</h3>' +
        '<div class="pp-profile-field"><span class="pp-profile-field-label">' + esc(T('business')) + '</span><span class="pp-profile-field-value">' + esc(p.business_name) + '</span></div>' +
        '<div class="pp-profile-field"><span class="pp-profile-field-label">' + esc(T('location')) + '</span><span class="pp-profile-field-value">' + esc(p.city) + '</span></div>' +
        '<div class="pp-profile-field"><span class="pp-profile-field-label">' + esc(T('corridor')) + '</span><span class="pp-profile-field-value">' + esc(p.corridor) + '</span></div>' +
        '<div class="pp-profile-field"><span class="pp-profile-field-label">Version</span><span class="pp-profile-field-value">v' + esc(p.version) + '</span></div></div>' +
        '<form class="pp-profile-section" id="pp-profile-form"><h3 class="pp-profile-section-title">' + esc(T('admin_edit_profile')) + '</h3>' +
        '<p style="font-size:13px;color:var(--pp-text-secondary);margin:0 0 12px">' + esc(T('admin_edit_profile_desc')) + '</p>' +
        fields +
        '<button type="button" class="pp-hold-btn pp-hold-save" data-hold="save-profile" style="width:100%;margin-top:8px">' +
        '<span class="pp-hold-btn-ring"></span><span class="pp-hold-btn-label">' + icon('check', 20) + ' ' + esc(T('admin_save')) + '</span></button>' +
        '<p style="font-size:11px;color:var(--pp-text-secondary);margin:4px 0 0;text-align:center">' + esc(T('profile_hold_hint')) + '</p></form>' +
        '<div class="pp-profile-section"><h3 class="pp-profile-section-title">' + esc(T('admin_reference_data')) + '</h3>' +
        '<div class="pp-metric-row" style="margin-bottom:12px">' +
        '<div class="pp-metric"><div class="pp-metric-value">' + esc(compat.version || '—') + '</div><div class="pp-metric-label">' + esc(T('admin_compat_version')) + '</div></div>' +
        '<div class="pp-metric"><div class="pp-metric-value">' + esc(compat.rows || 0) + '</div><div class="pp-metric-label">' + esc(T('admin_compat_rows')) + '</div></div>' +
        '<div class="pp-metric"><div class="pp-metric-value">' + esc(checklist.version || '—') + '</div><div class="pp-metric-label">' + esc(T('admin_checklist_version')) + '</div></div></div>' +
        '<div class="pp-test-banner">' + icon('warning', 20) + '<div><strong>' + esc(T('admin_test_label')) + '</strong>: ' + esc(T('admin_test_label_desc')) + '</div></div>' +
        (checklist.disclaimer ? '<div class="pp-expander"><div class="pp-expander-header">' + esc(T('admin_checklist_disclaimer')) + ' <span>▾</span></div><div class="pp-expander-body" style="font-size:13px;color:var(--pp-text-secondary)">' + esc(checklist.disclaimer) + '</div></div>' : '') +
        '</div>' +
        '<div class="pp-profile-section"><h3 class="pp-profile-section-title">' + esc(T('lang_label')) + '</h3>' +
        '<button class="pp-btn pp-btn-secondary" style="width:100%" data-act="toggle-lang">' + icon('settings', 20) + ' ' + esc(T('lang_toggle')) + '</button>' +
        '<button class="pp-btn pp-btn-secondary" style="width:100%;margin-top:8px" data-act="replay-tutorial">' + icon('question', 20) + ' ' + esc(T('tutorial_replay')) + '</button></div>';
    content.innerHTML = html;
}

function saveProfile() {
    if (!S.profile) return;
    var changes = {};
    profileSheet.querySelectorAll('[data-field]').forEach(function (el) {
        var f = el.getAttribute('data-field');
        var raw = el.value.trim();
        var v;
        if (f === 'languages' || f === 'rack_codes') {
            v = raw ? raw.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : [];
            if (JSON.stringify(v) === JSON.stringify(S.profile[f] || [])) return;
        } else if (f === 'handling_days') {
            v = raw === '' ? null : Number(raw);
            if (v === S.profile[f]) return;
        } else {
            v = raw === '' ? null : raw;
            if (v === (S.profile[f] === undefined ? null : S.profile[f])) return;
        }
        changes[f] = v;
    });
    if (!Object.keys(changes).length) { toast(T('profile_no_changes')); return; }
    api('PUT', '/me/profile', changes).then(function (r) {
        S.profile = r.profile;
        toast(T('profile_saved', {fields: (r.changed_fields || Object.keys(changes)).join(', ')}));
        haptic(30);
        renderProfileSheet();
        renderMap();
        renderHome();
    }).catch(function (e) { toast(T('profile_save_failed') + ': ' + e.message); });
}

// ── Navigation ──
function navigateTo(pane, instant) {
    if (PANES.indexOf(pane) < 0) return;
    currentPane = pane;
    store('session', 'set', 'pp-pane', pane);
    var targetX = track.clientWidth * order().indexOf(pane);
    if (instant || reducedMotion) {
        track.style.scrollBehavior = 'auto';
        track.scrollLeft = targetX;
        track.style.scrollBehavior = '';
    } else {
        track.scrollTo({left: targetX, behavior: 'smooth'});
    }
    updateBottomBar();
    haptic(10);
}

function step(delta) {
    var o = order();
    var i = o.indexOf(currentPane) + delta;
    if (i >= 0 && i < o.length) navigateTo(o[i]);
}

function updateBottomBar() {
    bottomBar.querySelectorAll('.pp-nav-btn').forEach(function (btn) {
        var on = btn.getAttribute('data-pane') === currentPane;
        btn.classList.toggle('active', on);
        if (on) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
    });
}

function setupPaneSnap() {
    var t;
    track.addEventListener('scroll', function () {
        clearTimeout(t);
        t = setTimeout(function () {
            var i = Math.round(track.scrollLeft / track.clientWidth);
            var pane = order()[Math.max(0, Math.min(PANES.length - 1, i))];
            if (pane !== currentPane) {
                currentPane = pane;
                store('session', 'set', 'pp-pane', pane);
                updateBottomBar();
            }
        }, 120);
    });
    window.addEventListener('resize', function () { navigateTo(currentPane, true); });
}

// Vertical gestures: swipe down from the top opens the profile, swipe up on Home opens the board.
function setupVerticalSwipe() {
    var sx = 0, sy = 0;
    track.addEventListener('touchstart', function (e) {
        sx = e.touches[0].clientX; sy = e.touches[0].clientY;
    }, {passive: true});
    track.addEventListener('touchend', function (e) {
        var dx = e.changedTouches[0].clientX - sx;
        var dy = e.changedTouches[0].clientY - sy;
        if (Math.abs(dy) < Math.abs(dx) || Math.abs(dy) < 80) return;
        var pane = document.getElementById('pp-pane-' + currentPane);
        if (dy > 0 && sy < track.getBoundingClientRect().top + 60 && pane && pane.scrollTop === 0) openProfileSheet();
        if (dy < 0 && currentPane === 'home') navigateTo('stories');
    }, {passive: true});
}

// ── Camera and photos ──
function startCamera() {
    navigateTo('home');
    var vf = document.getElementById('pp-viewfinder');
    if (!vf || S.cameraStream) return;
    S.photo = null;
    renderCaptureBar();
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) { cameraUnavailable(); return; }
    navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}}, audio: false}).then(function (stream) {
        S.cameraStream = stream;
        vf.innerHTML = '<video id="pp-video" playsinline autoplay muted></video>' +
            '<button class="pp-shutter" data-act="capture" aria-label="' + esc(T('camera_capture')) + '"></button>';
        var video = document.getElementById('pp-video');
        video.srcObject = stream;
        video.play();
        haptic(10);
    }, cameraUnavailable);
}

function cameraUnavailable() {
    toast(T('camera_unavailable'));
    pickPhoto();
}

function stopCamera() {
    if (S.cameraStream) {
        S.cameraStream.getTracks().forEach(function (t) { t.stop(); });
        S.cameraStream = null;
    }
}

function capture() {
    var video = document.getElementById('pp-video');
    if (!video || !video.videoWidth) return;
    var canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    stopCamera();
    S.photo = {dataUrl: canvas.toDataURL('image/jpeg', 0.9), name: 'camera-' + Date.now() + '.jpg'};
    haptic(20);
    renderViewfinder();
}

function pickPhoto() {
    var input = document.getElementById('pp-photo-input');
    if (input) { input.value = ''; input.click(); }
}

function photoChosen(file) {
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
        stopCamera();
        S.photo = {dataUrl: reader.result, name: file.name};
        navigateTo('home');
        renderViewfinder();
    };
    reader.readAsDataURL(file);
}

function sendPhoto() {
    if (!S.photo || S.photo.sending) return;
    var sel = document.getElementById('pp-photo-sku');
    var sku = sel ? sel.value : '';
    if (!sku) return;
    S.photo.sku = sku;
    S.photo.sending = true;
    renderCaptureBar();
    var b64 = S.photo.dataUrl.split(',')[1] || '';
    api('POST', '/me/parts/' + encodeURIComponent(sku) + '/photos', {filename: S.photo.name, content_b64: b64}).then(function (r) {
        var up = r.upload || {};
        var log = S.logs[sku] = S.logs[sku] || [];
        log.push({dir: 'out', text: '📷 ' + S.photo.name, state: 'sent'});
        if (up.message) log.push({dir: 'in', text: up.message});
        toast(up.accepted === false ? up.message : T('camera_sent', {sku: sku}));
        S.photo = null;
        renderViewfinder();
        return refreshAfterChange(sku);
    }).catch(function (e) {
        if (S.photo) S.photo.sending = false;
        renderCaptureBar();
        toast(e.message);
    });
}

// ── Profile sheet ──
function openProfileSheet() {
    renderProfileSheet();  // a fresh form each time: closing the sheet discards unsaved edits
    profileSheet.classList.add('open');
    haptic(10);
    if (!S.profile) loadProfile().catch(showError);
}
function closeProfileSheet() { profileSheet.classList.remove('open'); }

// ── Story overlay ──
var storyIndex = 0, storyData = [], storyTimer = null;

function openStory(parts, index) {
    if (!parts.length) return;
    storyData = parts;
    storyIndex = Math.max(0, Math.min(index || 0, parts.length - 1));
    storyOverlay.classList.add('open');
    renderStory();
    haptic(10);
}

function renderStory() {
    var p = storyData[storyIndex];
    if (!p) { closeStory(); return; }
    var row = storyOverlay.querySelector('.pp-story-progress-row');
    row.innerHTML = storyData.map(function (_, i) {
        return '<div class="pp-story-progress"><div class="pp-story-progress-fill' + (i < storyIndex ? ' done' : (i === storyIndex ? ' active' : '')) + '"></div></div>';
    }).join('');
    var info = badgeInfo(p);
    var html = '<div class="pp-story-card">' +
        '<div class="pp-story-sku">' + esc(p.sku) + '</div>' +
        '<div class="pp-story-detail">' + esc(p.title || '') + '</div>' +
        '<div class="pp-story-detail">' + esc(p.part_type || '') + ' · ' + esc(T('board_status')) + ': ' + esc(p.status || '') + '</div>' +
        '<div class="pp-story-badge ' + info.cls + '">' + info.icon + ' ' + esc(T(info.key)) + '</div>' +
        (p.compatibility ? '<div class="pp-story-detail">' + esc(T('board_compat')) + ': ' + esc(p.compatibility) + '</div>' : '') +
        (p.quarantined ? '<div class="pp-story-detail" style="color:var(--pp-status-specialist)">' + esc(T('board_quarantined')) + ': ' + p.quarantined + '</div>' : '');
    if (p.questions_waiting > 0) {
        html += '<div class="pp-story-poll"><div class="pp-story-poll-q">' + p.questions_waiting + ' ' + esc(T('board_questions_waiting')) + '</div>' +
            '<button class="pp-story-poll-opt" data-open-chat="' + esc(p.sku) + '">' + esc(T('board_answer')) + '</button></div>';
    }
    if (canApprove(p)) html += '<div style="margin-top:16px">' + holdApprove(p.sku) + '</div>';
    html += '<div class="pp-story-nav">' +
        '<button class="pp-btn pp-btn-secondary" data-act="story-prev"' + (storyIndex === 0 ? ' disabled' : '') + ' aria-label="Previous">' + (rtl() ? '›' : '‹') + '</button>' +
        '<span class="pp-card-detail">' + (storyIndex + 1) + ' / ' + storyData.length + '</span>' +
        '<button class="pp-btn pp-btn-secondary" data-act="story-next" aria-label="Next">' + (rtl() ? '‹' : '›') + '</button></div>';
    storyOverlay.querySelector('.pp-story-content').innerHTML = html + '</div>';
    clearTimeout(storyTimer);
    if (!reducedMotion) storyTimer = setTimeout(nextStory, 6000);
}

function nextStory() {
    clearTimeout(storyTimer);
    if (storyIndex >= storyData.length - 1) { closeStory(); return; }
    storyIndex++;
    renderStory();
}
function prevStory() {
    clearTimeout(storyTimer);
    if (storyIndex > 0) storyIndex--;
    renderStory();
}
function closeStory() {
    clearTimeout(storyTimer);
    storyOverlay.classList.remove('open');
}
function pauseStory() { clearTimeout(storyTimer); }

function setupStoryOverlay() {
    storyOverlay.querySelector('.pp-story-tap-left').addEventListener('click', function () { rtl() ? nextStory() : prevStory(); });
    storyOverlay.querySelector('.pp-story-tap-right').addEventListener('click', function () { rtl() ? prevStory() : nextStory(); });
    var sy = 0;
    storyOverlay.addEventListener('touchstart', function (e) { sy = e.touches[0].clientY; }, {passive: true});
    storyOverlay.addEventListener('touchend', function (e) { if (e.changedTouches[0].clientY - sy > 80) closeStory(); }, {passive: true});
    // Reading or pressing a button inside the card should not auto-advance.
    storyOverlay.addEventListener('pointerdown', function (e) { if (e.target.closest('.pp-story-card')) pauseStory(); });
}

// ── Hold to confirm ──
var HOLD_MS = 1200;
function setupHoldToConfirm() {
    var timer = null, btn = null;
    function cancel() {
        if (btn) btn.classList.remove('holding');
        clearTimeout(timer);
        btn = null;
    }
    document.addEventListener('pointerdown', function (e) {
        var b = e.target.closest('.pp-hold-btn');
        if (!b) return;
        btn = b;
        b._held = false;
        b.classList.add('holding');
        timer = setTimeout(function () {
            b.classList.remove('holding');
            b._held = true;
            haptic(30);
            holdConfirmed(b);
            btn = null;
        }, HOLD_MS);
    });
    document.addEventListener('pointerup', cancel);
    document.addEventListener('pointercancel', cancel);
    document.addEventListener('pointerout', function (e) { if (btn && e.target === btn && !btn.contains(e.relatedTarget)) cancel(); });
}

function holdConfirmed(b) {
    var what = b.getAttribute('data-hold');
    if (what === 'approve') approve(b.getAttribute('data-sku'));
    if (what === 'save-profile') saveProfile();
}

// ── Context menu (long-press, right-click) ──
function setupContextMenu() {
    var timer = null;
    document.addEventListener('touchstart', function (e) {
        var item = e.target.closest('[data-context]');
        if (!item) return;
        var t = e.touches[0];
        timer = setTimeout(function () { showContextMenu(item, t.clientX, t.clientY); haptic(20); }, 500);
    }, {passive: true});
    ['touchend', 'touchmove'].forEach(function (ev) {
        document.addEventListener(ev, function () { clearTimeout(timer); }, {passive: true});
    });
    document.addEventListener('contextmenu', function (e) {
        var item = e.target.closest('[data-context]');
        if (!item) return;
        e.preventDefault();
        showContextMenu(item, e.clientX, e.clientY);
    });
}

function showContextMenu(item, x, y) {
    var sku = item.getAttribute('data-sku');
    var p = partBySku(sku);
    if (!p) return;
    var acts = [['open-chat', 'chat', T('board_answer')], ['open-story', 'circle', T('chat_open_part')]];
    contextMenu.innerHTML = acts.map(function (a) {
        return '<button class="pp-context-item" data-ctx="' + a[0] + '" data-sku="' + esc(sku) + '">' + icon(a[1] === 'circle' ? 'story' : a[1], 18) + '<span>' + esc(a[2]) + '</span></button>';
    }).join('');
    contextMenu.style.left = Math.min(x, window.innerWidth - 200) + 'px';
    contextMenu.style.top = Math.min(y, window.innerHeight - 120) + 'px';
    contextMenu.classList.add('open');
}

// ── Pull to refresh ──
function setupPullToRefresh() {
    var sy = 0, pulling = false, dist = 0;
    document.addEventListener('touchstart', function (e) {
        var pane = e.target.closest('.pp-pane');
        pulling = !!pane && pane.scrollTop === 0;
        sy = e.touches[0].clientY;
        dist = 0;
    }, {passive: true});
    document.addEventListener('touchmove', function (e) { if (pulling) dist = e.touches[0].clientY - sy; }, {passive: true});
    document.addEventListener('touchend', function () {
        if (pulling && dist > 100 && !profileSheet.classList.contains('open')) refresh();
        pulling = false;
    }, {passive: true});
}

function refresh() {
    toast(T('refresh') + '…');
    var jobs = [loadBoard()];
    if (S.chatSku) jobs.push(loadDetail(S.chatSku));
    Promise.all(jobs).then(renderData, showError);
}

// ── Queue card swipe ──
function setupCardSwipe() {
    document.addEventListener('touchstart', function (e) {
        var card = e.target.closest('.pp-card[data-swipeable]');
        if (!card) return;
        card._sx = e.touches[0].clientX;
        card._sy = e.touches[0].clientY;
        card._swiping = true;
    }, {passive: true});
    document.addEventListener('touchmove', function (e) {
        var card = e.target.closest('.pp-card[data-swipeable]');
        if (!card || !card._swiping) return;
        var dx = e.touches[0].clientX - card._sx;
        if (Math.abs(dx) > Math.abs(e.touches[0].clientY - card._sy)) {
            card.classList.add('swiping');
            card.style.transform = 'translateX(' + dx + 'px)';
        }
    }, {passive: true});
    document.addEventListener('touchend', function (e) {
        var card = e.target.closest('.pp-card[data-swipeable]');
        if (!card || !card._swiping) return;
        card._swiping = false;
        card.classList.remove('swiping');
        card.style.transform = '';
        var dx = e.changedTouches[0].clientX - card._sx;
        if (rtl()) dx = -dx;
        if (dx > 100) queueAction(card, 'right');
        else if (dx < -100) queueAction(card, 'left');
    }, {passive: true});
}

// ── Keyboard (desktop) ──
function setupKeyboard() {
    document.addEventListener('keydown', function (e) {
        var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
        if (e.key === 'Escape') {
            if (contextMenu.classList.contains('open')) contextMenu.classList.remove('open');
            else if (storyOverlay.classList.contains('open')) closeStory();
            else if (profileSheet.classList.contains('open')) closeProfileSheet();
            else if (tutorialEl.classList.contains('open')) closeTutorial();
            return;
        }
        if (typing) return;
        if (storyOverlay.classList.contains('open')) {
            if (e.key === ' ' || e.key === (rtl() ? 'ArrowLeft' : 'ArrowRight')) { e.preventDefault(); nextStory(); }
            if (e.key === (rtl() ? 'ArrowRight' : 'ArrowLeft')) { e.preventDefault(); prevStory(); }
            return;
        }
        if (profileSheet.classList.contains('open')) return;
        if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
        if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
        if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[role="button"]')) { e.preventDefault(); e.target.click(); }
    });
}

// ── Toast ──
var toastTimer = null;
function toast(msg) {
    if (!toastEl || !msg) return;
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 3500);
}

// ── Tutorial ──
var tutorialStep = 0;
var TUTORIAL = {
    en: [
        {icon: '<path d="M3 12h4l3-9 4 18 3-9h4"/>', title: 'Swipe to navigate', text: 'Swipe left and right, use the arrow keys, or tap the bottom bar to move between the five panes.'},
        {icon: '<circle cx="12" cy="13" r="4"/><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>', title: 'Tap to capture', text: 'The centre camera button takes a part photo and sends it to the part you choose.'},
        {icon: '<circle cx="12" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>', title: 'Your profile', text: 'Swipe down from the top, or tap the profile button, to see and edit your seller details.'}
    ],
    ar: [
        {icon: '<path d="M3 12h4l3-9 4 18 3-9h4"/>', title: 'اسحب للتنقل', text: 'اسحب يميناً ويساراً، أو استخدم الأسهم، أو اضغط الشريط السفلي للتنقل بين الصفحات الخمس.'},
        {icon: '<circle cx="12" cy="13" r="4"/><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>', title: 'اضغط للالتقاط', text: 'زر الكاميرا في المنتصف يلتقط صورة القطعة ويرسلها إلى القطعة التي تختارها.'},
        {icon: '<circle cx="12" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>', title: 'ملفك الشخصي', text: 'اسحب من الأعلى أو اضغط زر الملف الشخصي لعرض بياناتك وتعديلها.'}
    ]
};
function renderTutorialButtons() {
    tutorialEl.querySelector('.pp-tutorial-skip').textContent = rtl() ? 'تخطي' : 'Skip';
    renderTutorial();
}
function openTutorial() {
    tutorialStep = 0;
    tutorialEl.classList.add('open');
    renderTutorial();
}
function renderTutorial() {
    var cards = TUTORIAL[lang] || TUTORIAL.en;
    var card = cards[tutorialStep];
    if (!card) return;
    tutorialEl.querySelector('.pp-tutorial-icon svg').innerHTML = card.icon;
    tutorialEl.querySelector('.pp-tutorial-title').textContent = card.title;
    tutorialEl.querySelector('.pp-tutorial-text').textContent = card.text;
    tutorialEl.querySelectorAll('.pp-tutorial-dot').forEach(function (d, i) { d.classList.toggle('active', i === tutorialStep); });
    tutorialEl.querySelector('.pp-tutorial-next').textContent = tutorialStep === cards.length - 1 ? (rtl() ? 'ابدأ' : 'Start') : (rtl() ? 'التالي' : 'Next');
}
function closeTutorial() {
    tutorialEl.classList.remove('open');
    store('local', 'set', 'pp-tutorial-seen', '1');
}
function setupTutorial() {
    tutorialEl.querySelector('.pp-tutorial-skip').addEventListener('click', closeTutorial);
    tutorialEl.querySelector('.pp-tutorial-next').addEventListener('click', function () {
        tutorialStep++;
        if (tutorialStep >= (TUTORIAL[lang] || TUTORIAL.en).length) closeTutorial(); else renderTutorial();
    });
    if (!store('local', 'get', 'pp-tutorial-seen')) setTimeout(openTutorial, 500);
}

// ── Online / offline ──
function setupOnlineDetection() {
    function update() {
        offlineBanner.classList.toggle('show', !navigator.onLine);
        offlineBanner.textContent = rtl() ? 'أنت غير متصل بالإنترنت' : 'You are offline';
    }
    window.addEventListener('online', function () { update(); refresh(); });
    window.addEventListener('offline', update);
    update();
}

// ── One click handler for every control ──
function setupClicks() {
    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t.closest('.pp-context-menu')) contextMenu.classList.remove('open');
        if (t === profileSheet) { closeProfileSheet(); return; }

        var hold = t.closest('.pp-hold-btn');
        if (hold) {
            // Save also works with a single tap; approve needs the full hold.
            if (!hold._held && hold.getAttribute('data-hold') === 'save-profile') saveProfile();
            hold._held = false;
            return;
        }
        var el;
        if ((el = t.closest('[data-ctx]'))) {
            contextMenu.classList.remove('open');
            var sku = el.getAttribute('data-sku');
            if (el.getAttribute('data-ctx') === 'open-chat') openChat(sku);
            else openStoryFor(sku);
            return;
        }
        if ((el = t.closest('.pp-nav-btn[data-pane]'))) { navigateTo(el.getAttribute('data-pane')); return; }
        if ((el = t.closest('[data-nav]'))) { navigateTo(el.getAttribute('data-nav')); return; }
        if ((el = t.closest('[data-open-chat]'))) { closeStory(); openChat(el.getAttribute('data-open-chat')); return; }
        if ((el = t.closest('[data-story]'))) { openStory(boardParts(), Number(el.getAttribute('data-story'))); return; }
        if ((el = t.closest('[data-filter]'))) { S.filter = el.getAttribute('data-filter'); renderBoard(); return; }
        if ((el = t.closest('[data-filter-jump]'))) { S.filter = el.getAttribute('data-filter-jump'); S.search = ''; renderBoard(); navigateTo('stories'); return; }
        if ((el = t.closest('[data-quick]'))) { sendChat(S.chatSku, el.getAttribute('data-quick')); return; }
        if ((el = t.closest('[data-retry]'))) {
            var log = S.logs[S.chatSku] || [];
            var m = log.splice(Number(el.getAttribute('data-retry')), 1)[0];
            if (m) sendChat(S.chatSku, m.text);
            return;
        }
        if ((el = t.closest('[data-swipe]'))) { queueAction(el.closest('.pp-card'), el.getAttribute('data-swipe')); return; }
        if ((el = t.closest('.pp-expander-header'))) { el.closest('.pp-expander').classList.toggle('open'); return; }
        if ((el = t.closest('[data-act]'))) { action(el.getAttribute('data-act'), el); }
    });

    document.addEventListener('submit', function (e) {
        e.preventDefault();
        if (e.target.id === 'pp-chat-form') {
            var input = document.getElementById('pp-chat-input');
            var text = input.value;
            input.value = '';
            sendChat(S.chatSku, text);
        }
    });
    document.addEventListener('input', function (e) {
        if (e.target.id === 'pp-board-search') { S.search = e.target.value; renderBoardList(); }
    });
    document.addEventListener('change', function (e) {
        if (e.target.id === 'pp-photo-input') photoChosen(e.target.files && e.target.files[0]);
        if (e.target.id === 'pp-photo-sku' && S.photo) S.photo.sku = e.target.value;
    });
}

function action(name, el) {
    switch (name) {
        case 'camera': startCamera(); break;
        case 'capture': capture(); break;
        case 'pick-photo': pickPhoto(); break;
        case 'retake': S.photo = null; renderViewfinder(); break;
        case 'send-photo': sendPhoto(); break;
        case 'run-pipeline': runPipeline(); break;
        case 'refresh': refresh(); break;
        case 'retry': S.error = null; renderData(); loadProfile().then(loadBoard).then(ensurePipeline).catch(showError); break;
        case 'back-to-chats': S.chatSku = null; renderChats(); break;
        case 'open-story': openStoryFor(el.getAttribute('data-sku')); break;
        case 'story-next': nextStory(); break;
        case 'story-prev': prevStory(); break;
        case 'open-profile': openProfileSheet(); break;
        case 'close-profile': closeProfileSheet(); break;
        case 'toggle-lang':
            lang = rtl() ? 'en' : 'ar';
            store('local', 'set', 'pp-lang', lang);
            renderAll();
            break;
        case 'replay-tutorial': closeProfileSheet(); openTutorial(); break;
        case 'reload': try { window.parent.location.reload(); } catch (err) { window.location.reload(); } break;
    }
}

function openStoryFor(sku) {
    var parts = boardParts();
    var i = parts.map(function (p) { return p.sku; }).indexOf(sku);
    if (i < 0) { parts = S.board.slice(); i = parts.map(function (p) { return p.sku; }).indexOf(sku); }
    openStory(parts, Math.max(0, i));
}

// ── Init ──
function init() {
    app = document.querySelector('.pp-app');
    track = document.querySelector('.pp-pane-track');
    bottomBar = document.querySelector('.pp-bottom-bar');
    profileSheet = document.getElementById('pp-profile-sheet');
    storyOverlay = document.getElementById('pp-story-overlay');
    contextMenu = document.getElementById('pp-context-menu');
    toastEl = document.getElementById('pp-toast');
    tutorialEl = document.getElementById('pp-tutorial');
    offlineBanner = document.getElementById('pp-offline-banner');
    if (!track) return;
    storyOverlay.querySelector('.pp-story-close').addEventListener('click', closeStory);
    setupClicks();
    setupPaneSnap();
    setupVerticalSwipe();
    setupStoryOverlay();
    setupHoldToConfirm();
    setupContextMenu();
    setupPullToRefresh();
    setupCardSwipe();
    setupKeyboard();
    setupTutorial();
    setupOnlineDetection();
    boot();
}

window.ppNavigate = function (p) { navigateTo(p); };
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
"""
