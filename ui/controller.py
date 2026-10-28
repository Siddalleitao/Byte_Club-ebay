"""PartProof JavaScript controller.

Gesture engine: pane swipe with snap, story progress bars, hold-to-confirm,
haptics, hash routing, pull-to-refresh, RTL mirroring, keyboard nav.

This is injected as a single script tag. All communication with Streamlit
happens via Streamlit's setComponentValue mechanism (st.components.v1.html).
"""


def get_js(lang: str = "en") -> str:
    rtl = "true" if lang == "ar" else "false"
    return f"""
(function() {{
    'use strict';

    var RTL = {rtl};
    var PANES = ['map', 'chats', 'home', 'stories', 'discover'];
    var PANE_LABELS = {{
        map: RTL ? 'الخريطة' : 'Map',
        chats: RTL ? 'المحادثات' : 'Chats',
        home: RTL ? 'الرئيسية' : 'Home',
        stories: RTL ? 'اللوحة' : 'Board',
        discover: RTL ? 'القائمة' : 'Queue'
    }};
    var currentPane = 'home';
    var track, bottomBar, profileSheet, storyOverlay, contextMenu, toastEl;
    var tutorialEl, offlineBanner;
    var touchStartX = 0, touchStartY = 0, touchStartTime = 0;
    var isSwiping = false;
    var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── Init ──
    function init() {{
        track = document.querySelector('.pp-pane-track');
        bottomBar = document.querySelector('.pp-bottom-bar');
        profileSheet = document.getElementById('pp-profile-sheet');
        storyOverlay = document.getElementById('pp-story-overlay');
        contextMenu = document.getElementById('pp-context-menu');
        toastEl = document.getElementById('pp-toast');
        tutorialEl = document.getElementById('pp-tutorial');
        offlineBanner = document.getElementById('pp-offline-banner');

        if (!track) return;

        setupPaneNavigation();
        setupSwipe();
        setupBottomBar();
        setupProfileSheet();
        setupStoryOverlay();
        setupHoldToConfirm();
        setupContextMenu();
        setupPullToRefresh();
        setupKeyboard();
        setupHashRouting();
        setupHaptics();
        setupTutorial();
        setupOnlineDetection();

        // Restore hash route
        var hash = window.location.hash.replace('#/', '').replace('#', '');
        if (hash && PANES.indexOf(hash) >= 0) {{
            navigateTo(hash);
        }} else {{
            navigateTo('home');
        }}

        // Show tutorial on first launch
        if (!localStorage.getItem('pp-tutorial-seen')) {{
            setTimeout(function() {{ openTutorial(); }}, 500);
        }}
    }}

    // ── Pane Navigation ──
    function navigateTo(pane) {{
        if (PANES.indexOf(pane) < 0) return;
        currentPane = pane;
        var idx = PANES.indexOf(pane);
        if (RTL) {{
            // RTL: panes are reversed, so scroll from right
            idx = PANES.length - 1 - idx;
        }}
        var targetX = track.offsetWidth * idx;
        if (reducedMotion) {{
            track.scrollLeft = targetX;
        }} else {{
            track.scrollTo({{ left: targetX, behavior: 'smooth' }});
        }}
        updateBottomBar();
        updateHash(pane);
        haptic(10);
    }}

    function updateHash(pane) {{
        if (window.location.hash !== '#/' + pane) {{
            history.replaceState(null, '', '#/' + pane);
        }}
    }}

    function setupHashRouting() {{
        window.addEventListener('hashchange', function() {{
            var hash = window.location.hash.replace('#/', '').replace('#', '');
            if (hash && PANES.indexOf(hash) >= 0 && hash !== currentPane) {{
                navigateTo(hash);
            }}
        }});

        // Android/browser back button
        window.addEventListener('popstate', function(e) {{
            var hash = window.location.hash.replace('#/', '').replace('#', '');
            if (hash && PANES.indexOf(hash) >= 0) {{
                navigateTo(hash);
            }} else if (storyOverlay && storyOverlay.classList.contains('open')) {{
                closeStory();
            }} else if (profileSheet && profileSheet.classList.contains('open')) {{
                closeProfileSheet();
            }}
        }});
    }}

    function setupPaneNavigation() {{
        // Snap on scroll end
        var scrollTimeout;
        track.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(function() {{
                snapToNearest();
            }}, 150);
        }});
    }}

    function snapToNearest() {{
        if (isSwiping) return;
        var paneWidth = track.offsetWidth;
        var scrollLeft = track.scrollLeft;
        var idx = Math.round(scrollLeft / paneWidth);
        idx = Math.max(0, Math.min(PANES.length - 1, idx));
        if (RTL) {{
            idx = PANES.length - 1 - idx;
        }}
        var pane = PANES[idx];
        if (pane !== currentPane) {{
            currentPane = pane;
            updateBottomBar();
            updateHash(pane);
        }}
        // Snap
        var targetX = RTL ? (PANES.length - 1 - idx) * paneWidth : idx * paneWidth;
        if (Math.abs(scrollLeft - targetX) > 5) {{
            track.scrollTo({{ left: targetX, behavior: reducedMotion ? 'auto' : 'smooth' }});
        }}
    }}

    // ── Swipe Gestures ──
    function setupSwipe() {{
        var startY = 0, startX = 0;
        var isVerticalSwipe = false;
        var isHorizontalSwipe = false;

        track.addEventListener('touchstart', function(e) {{
            if (e.touches.length > 1) return;
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            touchStartX = startX;
            touchStartY = startY;
            touchStartTime = Date.now();
            isSwiping = true;
            isVerticalSwipe = false;
            isHorizontalSwipe = false;
        }}, {{ passive: true }});

        track.addEventListener('touchmove', function(e) {{
            if (e.touches.length > 1) return;
            var dx = e.touches[0].clientX - startX;
            var dy = e.touches[0].clientY - startY;
            if (!isVerticalSwipe && !isHorizontalSwipe) {{
                if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > 10) {{
                    isVerticalSwipe = true;
                }} else if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 10) {{
                    isHorizontalSwipe = true;
                }}
            }}
            if (isVerticalSwipe) {{
                // Let the pane scroll vertically
                return;
            }}
        }}, {{ passive: true }});

        track.addEventListener('touchend', function(e) {{
            isSwiping = false;
            if (isVerticalSwipe) {{
                // Check for swipe down from top (profile sheet)
                var dy = e.changedTouches[0].clientY - startY;
                var paneRect = track.getBoundingClientRect();
                if (dy > 80 && startY < paneRect.top + 60) {{
                    openProfileSheet();
                }}
                // Check for swipe up from home (full grid)
                if (dy < -80 && currentPane === 'home') {{
                    navigateTo('stories');
                }}
                return;
            }}
            if (!isHorizontalSwipe) return;
            var dx = e.changedTouches[0].clientX - startX;
            var velocity = Math.abs(dx) / (Date.now() - touchStartTime);
            var threshold = velocity > 0.5 ? 40 : 80;
            if (Math.abs(dx) > threshold) {{
                if (RTL) dx = -dx; // Mirror in RTL
                if (dx < 0) {{
                    // Swipe left → next pane (right)
                    var idx = PANES.indexOf(currentPane);
                    if (idx < PANES.length - 1) {{
                        navigateTo(PANES[idx + 1]);
                    }}
                }} else {{
                    // Swipe right → previous pane (left)
                    var idx = PANES.indexOf(currentPane);
                    if (idx > 0) {{
                        navigateTo(PANES[idx - 1]);
                    }}
                }}
            }} else {{
                snapToNearest();
            }}
        }}, {{ passive: true }});
    }}

    // ── Bottom Bar ──
    function setupBottomBar() {{
        var btns = bottomBar.querySelectorAll('.pp-nav-btn');
        btns.forEach(function(btn) {{
            btn.addEventListener('click', function() {{
                var pane = btn.getAttribute('data-pane');
                if (pane) navigateTo(pane);
            }});
        }});
        var centreBtn = bottomBar.querySelector('.pp-nav-centre-btn');
        if (centreBtn) {{
            centreBtn.addEventListener('click', function() {{
                navigateTo('home');
                // Trigger camera action
                triggerCameraAction();
            }});
        }}
    }}

    function updateBottomBar() {{
        var btns = bottomBar.querySelectorAll('.pp-nav-btn');
        btns.forEach(function(btn) {{
            var pane = btn.getAttribute('data-pane');
            if (pane === currentPane) {{
                btn.classList.add('active');
            }} else {{
                btn.classList.remove('active');
            }}
        }});
    }}

    // ── Camera Action ──
    function triggerCameraAction() {{
        var homePane = document.querySelector('.pp-pane[data-pane="home"]');
        if (!homePane) return;
        var viewfinder = homePane.querySelector('.pp-viewfinder');
        if (viewfinder && !viewfinder.querySelector('video')) {{
            startCamera(viewfinder);
        }}
    }}

    function startCamera(viewfinder) {{
        var placeholder = viewfinder.querySelector('.pp-viewfinder-placeholder');
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {{
            navigator.mediaDevices.getUserMedia({{
                video: {{ facingMode: {{ ideal: 'environment' }} }},
                audio: false
            }}).then(function(stream) {{
                var video = document.createElement('video');
                video.srcObject = stream;
                video.setAttribute('playsinline', '');
                video.autoplay = true;
                viewfinder.innerHTML = '';
                viewfinder.appendChild(video);
                video.play();
                haptic(10);
            }}).catch(function(err) {{
                if (placeholder) {{
                    placeholder.textContent = 'Camera unavailable. Use the file upload button below.';
                }}
                showFileInputFallback(viewfinder);
            }});
        }} else {{
            if (placeholder) {{
                placeholder.textContent = 'Camera not supported. Use the file upload button below.';
            }}
            showFileInputFallback(viewfinder);
        }}
    }}

    function showFileInputFallback(container) {{
        var existing = container.querySelector('.pp-file-fallback');
        if (existing) return;
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.capture = 'environment';
        input.multiple = true;
        input.className = 'pp-file-fallback';
        input.style.cssText = 'display:block;margin:12px auto;padding:8px;';
        input.setAttribute('aria-label', 'Upload photos');
        container.appendChild(input);
    }}

    // ── Profile Sheet ──
    function setupProfileSheet() {{
        var backdrop = profileSheet;
        if (!backdrop) return;
        backdrop.addEventListener('click', function(e) {{
            if (e.target === backdrop) closeProfileSheet();
        }});
        var closeBtn = profileSheet.querySelector('.pp-profile-close');
        if (closeBtn) {{
            closeBtn.addEventListener('click', closeProfileSheet);
        }}
    }}

    function openProfileSheet() {{
        if (profileSheet) {{
            profileSheet.classList.add('open');
            history.pushState({{ sheet: 'profile' }}, '');
            haptic(10);
        }}
    }}

    function closeProfileSheet() {{
        if (profileSheet) {{
            profileSheet.classList.remove('open');
        }}
    }}

    // ── Story Overlay ──
    var storyIndex = 0;
    var storyData = [];
    var storyTimer = null;

    function setupStoryOverlay() {{
        if (!storyOverlay) return;
        var closeBtn = storyOverlay.querySelector('.pp-story-close');
        if (closeBtn) {{
            closeBtn.addEventListener('click', closeStory);
        }}
        var tapLeft = storyOverlay.querySelector('.pp-story-tap-left');
        var tapRight = storyOverlay.querySelector('.pp-story-tap-right');
        if (tapLeft) tapLeft.addEventListener('click', function() {{ prevStory(); }});
        if (tapRight) tapRight.addEventListener('click', function() {{ nextStory(); }});

        // Swipe down to close
        var startY = 0;
        storyOverlay.addEventListener('touchstart', function(e) {{
            startY = e.touches[0].clientY;
        }}, {{ passive: true }});
        storyOverlay.addEventListener('touchend', function(e) {{
            var dy = e.changedTouches[0].clientY - startY;
            if (dy > 80) closeStory();
        }}, {{ passive: true }});
    }}

    function openStory(data) {{
        storyData = data;
        storyIndex = 0;
        storyOverlay.classList.add('open');
        history.pushState({{ story: true }}, '');
        renderStory();
        haptic(10);
    }}

    function renderStory() {{
        if (!storyData.length) {{ closeStory(); return; }}
        var part = storyData[storyIndex];
        var content = storyOverlay.querySelector('.pp-story-content');
        if (!content) return;

        // Progress bars
        var progressRow = storyOverlay.querySelector('.pp-story-progress-row');
        progressRow.innerHTML = '';
        for (var i = 0; i < storyData.length; i++) {{
            var bar = document.createElement('div');
            bar.className = 'pp-story-progress';
            var fill = document.createElement('div');
            fill.className = 'pp-story-progress-fill';
            if (i < storyIndex) fill.classList.add('done');
            else if (i === storyIndex) fill.classList.add('active');
            bar.appendChild(fill);
            progressRow.appendChild(bar);
        }}

        // Build story card
        var badgeClass = part.badge || 'missing';
        var badgeLabel = part.badge_label || part.badge || '';
        var html = '<div class="pp-story-card">';
        html += '<div class="pp-story-sku">' + (part.sku || '') + '</div>';
        html += '<div class="pp-story-detail">' + (part.make || '') + ' ' + (part.model || '') + ' ' + (part.year || '') + '</div>';
        html += '<div class="pp-story-detail">' + (part.part_type || '') + '</div>';
        html += '<div class="pp-story-badge ' + badgeClass.toLowerCase().replace(/\\s+/g, '') + '">' + badgeLabel + '</div>';
        if (part.rack_code) html += '<div class="pp-story-detail">Rack: ' + part.rack_code + '</div>';
        if (part.asking_price_aed) html += '<div class="pp-story-detail">Price: AED ' + part.asking_price_aed + '</div>';
        if (part.open_questions > 0) {{
            html += '<div class="pp-story-poll">';
            html += '<div class="pp-story-poll-q">' + part.open_questions + ' open questions — tap to answer</div>';
            html += '<div class="pp-story-poll-opt" onclick="window.ppOpenQuestions(\\'' + part.id + '\\')">View Questions</div>';
            html += '</div>';
        }}
        if (part.issue_types && part.issue_types.length) {{
            html += '<div style="margin-top:12px;font-size:13px;color:var(--pp-text-secondary)">Issues: ' + part.issue_types.join(', ') + '</div>';
        }}
        html += '</div>';
        content.innerHTML = html;

        // Auto-advance after 5s
        clearTimeout(storyTimer);
        if (!reducedMotion) {{
            storyTimer = setTimeout(function() {{ nextStory(); }}, 6000);
        }}
    }}

    function nextStory() {{
        clearTimeout(storyTimer);
        storyIndex++;
        if (storyIndex >= storyData.length) {{
            closeStory();
        }} else {{
            renderStory();
            haptic(10);
        }}
    }}

    function prevStory() {{
        clearTimeout(storyTimer);
        storyIndex--;
        if (storyIndex < 0) storyIndex = 0;
        renderStory();
        haptic(10);
    }}

    function closeStory() {{
        clearTimeout(storyTimer);
        storyOverlay.classList.remove('open');
    }}

    // Expose for inline onclick
    window.ppOpenQuestions = function(partId) {{
        closeStory();
        navigateTo('chats');
        window.ppSelectChat(partId);
    }};

    // ── Chats ──
    window.ppSelectChat = function(partId) {{
        // Dispatch event for Streamlit to pick up
        var event = new CustomEvent('pp-select-chat', {{ detail: {{ partId: partId }} }});
        document.dispatchEvent(event);
        haptic(10);
    }};

    window.ppSendQuickReply = function(partId, text) {{
        var event = new CustomEvent('pp-quick-reply', {{ detail: {{ partId: partId, text: text }} }});
        document.dispatchEvent(event);
        haptic(15);
    }};

    window.ppRetryMessage = function(msgId) {{
        var event = new CustomEvent('pp-retry-message', {{ detail: {{ msgId: msgId }} }});
        document.dispatchEvent(event);
    }};

    // ── Hold to Confirm ──
    function setupHoldToConfirm() {{
        var holdTimer = null;
        document.addEventListener('pointerdown', function(e) {{
            var btn = e.target.closest('.pp-hold-btn');
            if (!btn) return;
            btn.classList.add('holding');
            holdTimer = setTimeout(function() {{
                var action = btn.getAttribute('data-action');
                btn.classList.remove('holding');
                haptic(30);
                var event = new CustomEvent('pp-hold-confirm', {{ detail: {{ action: action }} }});
                document.dispatchEvent(event);
            }}, 1200);
        }});

        document.addEventListener('pointerup', function(e) {{
            var btn = e.target.closest('.pp-hold-btn');
            if (btn) {{
                btn.classList.remove('holding');
                clearTimeout(holdTimer);
            }}
        }});

        document.addEventListener('pointerleave', function(e) {{
            var btn = e.target.closest('.pp-hold-btn');
            if (btn) {{
                btn.classList.remove('holding');
                clearTimeout(holdTimer);
            }}
        }});
    }}

    // ── Context Menu (long-press) ──
    function setupContextMenu() {{
        if (!contextMenu) return;
        var longPressTimer = null;
        var longPressTarget = null;

        document.addEventListener('touchstart', function(e) {{
            var item = e.target.closest('[data-context]');
            if (!item) return;
            longPressTarget = item;
            longPressTimer = setTimeout(function() {{
                showContextMenu(item, e.touches[0].clientX, e.touches[0].clientY);
                haptic(20);
            }}, 500);
        }}, {{ passive: true }});

        document.addEventListener('touchend', function() {{
            clearTimeout(longPressTimer);
        }}, {{ passive: true }});

        document.addEventListener('touchmove', function() {{
            clearTimeout(longPressTimer);
        }}, {{ passive: true }});

        // Also right-click for desktop
        document.addEventListener('contextmenu', function(e) {{
            var item = e.target.closest('[data-context]');
            if (item) {{
                e.preventDefault();
                showContextMenu(item, e.clientX, e.clientY);
            }}
        }});

        // Close on click outside
        document.addEventListener('click', function(e) {{
            if (!e.target.closest('.pp-context-menu')) {{
                contextMenu.classList.remove('open');
            }}
        }});
    }}

    function showContextMenu(item, x, y) {{
        var actions = item.getAttribute('data-context');
        var label = item.getAttribute('data-label') || '';
        var id = item.getAttribute('data-id') || '';
        contextMenu.innerHTML = '';
        var actList = actions.split(',');
        actList.forEach(function(act) {{
            var parts = act.split(':');
            var action = parts[0];
            var icon = parts[1] || '';
            var label2 = parts[2] || action;
            var el = document.createElement('div');
            el.className = 'pp-context-item';
            el.innerHTML = (icon ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + icon + '</svg>' : '') + '<span>' + label2 + '</span>';
            el.addEventListener('click', function() {{
                contextMenu.classList.remove('open');
                var event = new CustomEvent('pp-context-action', {{ detail: {{ action: action, id: id }} }});
                document.dispatchEvent(event);
                haptic(10);
            }});
            contextMenu.appendChild(el);
        }});
        contextMenu.style.left = Math.min(x, window.innerWidth - 180) + 'px';
        contextMenu.style.top = Math.min(y, window.innerHeight - 200) + 'px';
        contextMenu.classList.add('open');
    }}

    // ── Pull to Refresh ──
    function setupPullToRefresh() {{
        var startY = 0;
        var pulling = false;
        var pullDistance = 0;
        var indicator = document.createElement('div');
        indicator.className = 'pp-sr-only';
        indicator.setAttribute('aria-live', 'polite');

        document.addEventListener('touchstart', function(e) {{
            var pane = e.target.closest('.pp-pane');
            if (!pane) return;
            if (pane.scrollTop === 0) {{
                startY = e.touches[0].clientY;
                pulling = true;
                pullDistance = 0;
            }}
        }}, {{ passive: true }});

        document.addEventListener('touchmove', function(e) {{
            if (!pulling) return;
            pullDistance = e.touches[0].clientY - startY;
        }}, {{ passive: true }});

        document.addEventListener('touchend', function() {{
            if (pulling && pullDistance > 100) {{
                haptic(20);
                showToast(RTL ? 'جارٍ التحديث...' : 'Refreshing...');
                var event = new CustomEvent('pp-pull-refresh');
                document.dispatchEvent(event);
            }}
            pulling = false;
        }}, {{ passive: true }});
    }}

    // ── Keyboard Navigation (desktop) ──
    function setupKeyboard() {{
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') {{
                if (storyOverlay && storyOverlay.classList.contains('open')) {{
                    closeStory();
                }} else if (profileSheet && profileSheet.classList.contains('open')) {{
                    closeProfileSheet();
                }} else if (contextMenu && contextMenu.classList.contains('open')) {{
                    contextMenu.classList.remove('open');
                }}
            }}
            if (e.key === 'ArrowLeft') {{
                var idx = PANES.indexOf(currentPane);
                if (RTL) {{
                    if (idx < PANES.length - 1) navigateTo(PANES[idx + 1]);
                }} else {{
                    if (idx > 0) navigateTo(PANES[idx - 1]);
                }}
            }}
            if (e.key === 'ArrowRight') {{
                var idx = PANES.indexOf(currentPane);
                if (RTL) {{
                    if (idx > 0) navigateTo(PANES[idx - 1]);
                }} else {{
                    if (idx < PANES.length - 1) navigateTo(PANES[idx + 1]);
                }}
            }}
            if (e.key === ' ' && storyOverlay && storyOverlay.classList.contains('open')) {{
                e.preventDefault();
                nextStory();
            }}
        }});
    }}

    // ── Haptics ──
    function setupHaptics() {{
        // navigator.vibrate may not exist on desktop
    }}

    function haptic(ms) {{
        if (navigator.vibrate) {{
            try {{ navigator.vibrate(ms); }} catch(e) {{}}
        }}
    }}

    // ── Toast ──
    function showToast(msg) {{
        if (!toastEl) return;
        toastEl.textContent = msg;
        toastEl.classList.add('show');
        setTimeout(function() {{
            toastEl.classList.remove('show');
        }}, 2500);
    }}
    window.ppToast = showToast;

    // ── Tutorial ──
    var tutorialStep = 0;
    var tutorialCards = RTL ? [
        {{ icon: '<path d="M3 12h4l3-9 4 18 3-9h4"/>', title: 'اسحب للتنقل', text: 'اسحب يميناً ويساراً للتنقل بين الصفحات الخمس' }},
        {{ icon: '<circle cx="12" cy="13" r="4"/><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>', title: 'اضغط للالتقاط', text: 'زر الكاميرا في المنتصف يلتقط صور القطع' }},
        {{ icon: '<circle cx="12" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>', title: 'اسحب للأسفل للملف', text: 'اسحب من أعلى الشاشة لعرض ملفك الشخصي والإعدادات' }}
    ] : [
        {{ icon: '<path d="M3 12h4l3-9 4 18 3-9h4"/>', title: 'Swipe to navigate', text: 'Swipe left and right to move between the five panes' }},
        {{ icon: '<circle cx="12" cy="13" r="4"/><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>', title: 'Tap to capture', text: 'The centre camera button captures part photos' }},
        {{ icon: '<circle cx="12" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>', title: 'Swipe down for profile', text: 'Swipe down from the top to see your profile and settings' }}
    ];

    function setupTutorial() {{
        if (!tutorialEl) return;
        var skipBtn = tutorialEl.querySelector('.pp-tutorial-skip');
        var nextBtn = tutorialEl.querySelector('.pp-tutorial-next');
        if (skipBtn) skipBtn.addEventListener('click', closeTutorial);
        if (nextBtn) nextBtn.addEventListener('click', function() {{
            tutorialStep++;
            if (tutorialStep >= tutorialCards.length) {{
                closeTutorial();
            }} else {{
                renderTutorial();
            }}
        }});
    }}

    function openTutorial() {{
        tutorialStep = 0;
        tutorialEl.classList.add('open');
        renderTutorial();
    }}

    function renderTutorial() {{
        if (!tutorialEl || tutorialStep >= tutorialCards.length) return;
        var card = tutorialCards[tutorialStep];
        var iconEl = tutorialEl.querySelector('.pp-tutorial-icon svg');
        iconEl.innerHTML = card.icon;
        tutorialEl.querySelector('.pp-tutorial-title').textContent = card.title;
        tutorialEl.querySelector('.pp-tutorial-text').textContent = card.text;
        var dots = tutorialEl.querySelectorAll('.pp-tutorial-dot');
        dots.forEach(function(d, i) {{
            d.classList.toggle('active', i === tutorialStep);
        }});
        var nextBtn = tutorialEl.querySelector('.pp-tutorial-next');
        nextBtn.textContent = tutorialStep === tutorialCards.length - 1 ? (RTL ? 'ابدأ' : 'Start') : (RTL ? 'التالي' : 'Next');
    }}

    function closeTutorial() {{
        tutorialEl.classList.remove('open');
        localStorage.setItem('pp-tutorial-seen', '1');
    }}

    // ── Online/Offline Detection ──
    function setupOnlineDetection() {{
        if (!offlineBanner) return;
        function updateOnline() {{
            if (navigator.onLine) {{
                offlineBanner.classList.remove('show');
            }} else {{
                offlineBanner.classList.add('show');
                offlineBanner.textContent = RTL ? 'أنت غير متصل — البيانات محفوظة محلياً' : 'You are offline — data is cached locally';
            }}
        }}
        window.addEventListener('online', updateOnline);
        window.addEventListener('offline', updateOnline);
        updateOnline();
    }}

    // ── Swipeable Cards (Discover) ──
    window.ppSwipeCard = function(cardEl, direction) {{
        cardEl.classList.add('swipe-' + direction);
        setTimeout(function() {{
            cardEl.style.display = 'none';
            var event = new CustomEvent('pp-card-swipe', {{ detail: {{ direction: direction, id: cardEl.getAttribute('data-id') }} }});
            document.dispatchEvent(event);
        }}, 300);
    }};

    // Card touch swipe
    document.addEventListener('touchstart', function(e) {{
        var card = e.target.closest('.pp-card[data-swipeable]');
        if (!card) return;
        card._startX = e.touches[0].clientX;
        card._startY = e.touches[0].clientY;
        card._swiping = true;
        card.classList.add('swiping');
    }}, {{ passive: true }});

    document.addEventListener('touchmove', function(e) {{
        var card = e.target.closest('.pp-card[data-swipeable]');
        if (!card || !card._swiping) return;
        var dx = e.touches[0].clientX - card._startX;
        var dy = e.touches[0].clientY - card._startY;
        if (Math.abs(dx) > Math.abs(dy)) {{
            card.style.transform = 'translateX(' + dx + 'px)';
        }}
    }}, {{ passive: true }});

    document.addEventListener('touchend', function(e) {{
        var card = e.target.closest('.pp-card[data-swipeable]');
        if (!card || !card._swiping) return;
        card._swiping = false;
        card.classList.remove('swiping');
        var dx = e.changedTouches[0].clientX - card._startX;
        card.style.transform = '';
        if (RTL) dx = -dx;
        if (dx > 100) {{
            window.ppSwipeCard(card, 'right');
            haptic(20);
        }} else if (dx < -100) {{
            window.ppSwipeCard(card, 'left');
            haptic(20);
        }}
    }}, {{ passive: true }});

    // ── Expander ──
    document.addEventListener('click', function(e) {{
        var header = e.target.closest('.pp-expander-header');
        if (!header) return;
        var expander = header.closest('.pp-expander');
        expander.classList.toggle('open');
    }});

    // ── Window expose ──
    window.ppNavigate = navigateTo;
    window.ppOpenStory = openStory;
    window.ppOpenProfile = openProfileSheet;
    window.ppCloseProfile = closeProfileSheet;

    // ── Init on DOM ready ──
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', init);
    }} else {{
        init();
    }}
}})();
"""
