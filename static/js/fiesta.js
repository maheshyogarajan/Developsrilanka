/* ============================================================================
   FIESTA shell — F-Platform-1 (X9 MS1 Stage B).

   ONE JS file (Design Lock 1 constraint). No framework, vanilla only.

   Responsibilities:
     1. Wire the topbar mobile toggle + user-menu open/close.
     2. On DOMContentLoaded, fetch /api/fiesta/savings-projection (auth
        required; gracefully skip on 401/404) and update
        #fiesta-savings-counter.
     3. Cache the projection in localStorage with TTL per `cached_until`.
     4. Listen for the contract-locked custom events from Design Lock 1
        and refetch when any fires:
           fiesta:remittance-added
           fiesta:deduction-toggled
           fiesta:sp-added
           fiesta:property-added
           fiesta:income-source-added
           fiesta:savings-counter-refresh
     5. Expose window.fiesta with refreshSavings() + dispatchUpdate(eventName)
        so inline page scripts can opt in without a build step.

   Subagent constraint: F-Platform-4 / F-Platform-5 / Wave 7 dispatchers
   that fire these events MUST use the names above verbatim. Do NOT
   rename them.

   Authored 2026-05-25.
   ============================================================================ */
(function () {
  'use strict';

  // ---------- Configuration ----------
  var SAVINGS_API_URL = '/api/fiesta/savings-projection';
  var CACHE_STORAGE_KEY = 'fiesta:savings-projection:v1';
  var COUNTER_EL_ID = 'fiesta-savings-counter';
  var EVENT_NAMES = [
    'fiesta:remittance-added',
    'fiesta:deduction-toggled',
    'fiesta:sp-added',
    'fiesta:property-added',
    'fiesta:income-source-added',
    'fiesta:savings-counter-refresh'
  ];

  // ---------- Helpers ----------
  function formatLkr(amount) {
    var n = Number(amount);
    if (!isFinite(n)) return 'Rs --';
    try {
      return 'Rs ' + n.toLocaleString('en-LK', { maximumFractionDigits: 0 });
    } catch (e) {
      return 'Rs ' + Math.round(n);
    }
  }

  function readCache() {
    try {
      var raw = localStorage.getItem(CACHE_STORAGE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || !parsed.cached_until) return null;
      if (new Date(parsed.cached_until).getTime() <= Date.now()) {
        // Stale — drop it so the next read is a miss.
        localStorage.removeItem(CACHE_STORAGE_KEY);
        return null;
      }
      return parsed;
    } catch (e) {
      return null;
    }
  }

  function writeCache(payload) {
    try {
      localStorage.setItem(CACHE_STORAGE_KEY, JSON.stringify(payload));
    } catch (e) {
      // Quota / private mode — ignore, cache is best-effort.
    }
  }

  function updateCounterEl(payload) {
    var el = document.getElementById(COUNTER_EL_ID);
    if (!el) return;
    var saved = (payload && (payload.lkr_saved != null ? payload.lkr_saved : payload.lkr_projected));
    el.textContent = formatLkr(saved);
    if (payload && payload.source) {
      el.setAttribute('data-source', payload.source);
    }
    if (payload && payload.fresh != null) {
      el.setAttribute('data-fresh', payload.fresh ? 'true' : 'false');
    }
  }

  function fetchSavings(opts) {
    opts = opts || {};
    // Cache check first unless force.
    if (!opts.force) {
      var cached = readCache();
      if (cached) {
        updateCounterEl(cached);
        return Promise.resolve(cached);
      }
    }
    if (typeof window.fetch !== 'function') {
      return Promise.resolve(null);
    }
    return window.fetch(SAVINGS_API_URL, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    }).then(function (resp) {
      // Auth-gated. 401 = anonymous (or expired session) — leave the
      // server-side fallback rendered. 404 = endpoint not deployed yet
      // (graceful skip during phased rollout).
      if (resp.status === 401 || resp.status === 404) return null;
      if (!resp.ok) return null;
      return resp.json();
    }).then(function (payload) {
      if (!payload) return null;
      writeCache(payload);
      updateCounterEl(payload);
      return payload;
    }).catch(function () {
      return null;
    });
  }

  function dispatchUpdate(eventName, detail) {
    try {
      window.dispatchEvent(new CustomEvent(eventName, { detail: detail || {} }));
    } catch (e) {
      // IE11 fallback (no CustomEvent constructor) — manually create.
      var ev = document.createEvent('CustomEvent');
      ev.initCustomEvent(eventName, true, true, detail || {});
      window.dispatchEvent(ev);
    }
  }

  function wireSavingsEvents() {
    EVENT_NAMES.forEach(function (name) {
      window.addEventListener(name, function () {
        // Always force-refresh on contract events — the cache is now stale
        // by definition because the underlying remittance/deduction set
        // just changed.
        fetchSavings({ force: true });
      });
    });
  }

  function wireMobileToggle() {
    var toggle = document.getElementById('fiesta-mobile-toggle');
    if (!toggle) return;
    toggle.addEventListener('click', function () {
      var body = document.body;
      var open = body.classList.toggle('fiesta-sidebar-open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    // Close the drawer when a nav link is tapped.
    var sidebar = document.getElementById('fiesta-sidebar');
    if (sidebar) {
      sidebar.addEventListener('click', function (e) {
        var link = e.target && e.target.closest && e.target.closest('a.fiesta-nav-link');
        if (link) {
          document.body.classList.remove('fiesta-sidebar-open');
          toggle.setAttribute('aria-expanded', 'false');
        }
      });
    }
  }

  function wireUserMenu() {
    var wrap = document.getElementById('fiesta-user');
    var btn = document.getElementById('fiesta-user-btn');
    if (!wrap || !btn) return;
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = wrap.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (!wrap.contains(e.target)) {
        wrap.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // ---------- Public API ----------
  window.fiesta = {
    refreshSavings: function (opts) { return fetchSavings(opts || { force: true }); },
    dispatchUpdate: dispatchUpdate,
    _formatLkr: formatLkr,
    _eventNames: EVENT_NAMES.slice()
  };

  // ---------- Boot ----------
  function boot() {
    wireMobileToggle();
    wireUserMenu();
    wireSavingsEvents();
    fetchSavings();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
