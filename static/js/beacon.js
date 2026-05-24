/* FIESTA analytics beacon — Sprint 4 Tier B (2026-05-24).
 *
 * Tiny client library that POSTs funnel events to /api/event so the EVENT
 * SPINE can attribute acquisition channels and measure activation drop-off.
 *
 * Usage:
 *
 *   window.fiestaTrack('cta_click', { cta_id: 'hero_signup' });
 *   window.fiestaTrack('payment_started', { product: 'fiesta_basic' });
 *
 * On page unload (e.g. final 'beforeunload' event) the call automatically
 * uses navigator.sendBeacon so the event survives the navigation. For
 * in-page events we use fetch({keepalive: true}) which is more reliable
 * across Firefox + Safari than sendBeacon for content-typed JSON.
 *
 * The server holds the whitelist of valid event names — see
 * analytics_beacon_routes.ALLOWED_BEACON_EVENTS. We deliberately don't
 * mirror it in JS: keeping one source of truth means a server-side
 * rollout doesn't need a parallel client patch.
 *
 * Anonymous identity: every browser carries a `session_anon_id` cookie
 * (1y, SameSite=Lax, set by the after_request hook in
 * analytics_beacon_routes._ensure_anon_cookie). The cookie is NOT
 * HttpOnly — JS reads it here so we can attribute pre-signup activity to
 * a stable anon id even before the user authenticates.
 *
 * Failure mode: NEVER throws. Network errors are swallowed and logged
 * to console.debug at most. Analytics never break the user flow.
 */
(function () {
  'use strict';

  if (window.fiestaTrack) return;  // idempotent

  var ENDPOINT = '/api/event';

  // -------- Cookie helper -------- //
  function readCookie(name) {
    try {
      var pairs = (document.cookie || '').split(';');
      for (var i = 0; i < pairs.length; i++) {
        var idx = pairs[i].indexOf('=');
        if (idx === -1) continue;
        var k = pairs[i].slice(0, idx).trim();
        if (k === name) {
          return decodeURIComponent(pairs[i].slice(idx + 1));
        }
      }
    } catch (e) { /* swallow */ }
    return null;
  }

  // -------- Body builder -------- //
  function buildBody(eventName, properties) {
    var safeProps = (properties && typeof properties === 'object') ? properties : {};
    var anonId = readCookie('session_anon_id') || null;
    return JSON.stringify({
      event: String(eventName).slice(0, 64),
      properties: safeProps,
      path: (location && location.pathname) || null,
      referrer: document.referrer || null,
      anon_id: anonId  // server also reads it from cookie; included for proxies
    });
  }

  // -------- Send strategies -------- //
  function sendViaBeacon(body) {
    if (!navigator || typeof navigator.sendBeacon !== 'function') return false;
    try {
      // sendBeacon requires a Blob with content-type if we want the
      // server to parse JSON — text/plain is the default and would be
      // rejected by our origin check (which permits application/json
      // and same-origin XHR).
      var blob = new Blob([body], { type: 'application/json' });
      return navigator.sendBeacon(ENDPOINT, blob);
    } catch (e) {
      return false;
    }
  }

  function sendViaFetch(body) {
    if (typeof fetch !== 'function') return false;
    try {
      fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
        credentials: 'same-origin',
        keepalive: true,
        mode: 'same-origin'
      }).catch(function () { /* swallow */ });
      return true;
    } catch (e) {
      return false;
    }
  }

  // -------- Public API -------- //
  function fiestaTrack(eventName, properties, opts) {
    if (!eventName || typeof eventName !== 'string') return;
    var body;
    try {
      body = buildBody(eventName, properties);
    } catch (e) {
      if (console && console.debug) console.debug('fiestaTrack: build failed', e);
      return;
    }

    // For unload-time events the caller passes { unload: true } so we
    // prefer sendBeacon (the only reliable transport during 'unload').
    var preferBeacon = !!(opts && opts.unload);
    if (preferBeacon && sendViaBeacon(body)) return;
    if (sendViaFetch(body)) return;
    // Final fallback: sendBeacon (works even if fetch isn't available).
    sendViaBeacon(body);
  }

  // Convenience: bind to <a data-track="event_name"> click handlers
  // automatically. Pages that prefer explicit calls just ignore this.
  function autoBindClicks() {
    try {
      document.addEventListener('click', function (ev) {
        var el = ev.target;
        while (el && el !== document) {
          if (el.dataset && el.dataset.track) {
            var props = {};
            // Surface any data-track-* attributes as properties (sans
            // 'track' itself).
            if (el.dataset) {
              for (var k in el.dataset) {
                if (!Object.prototype.hasOwnProperty.call(el.dataset, k)) continue;
                if (k === 'track') continue;
                if (k.indexOf('track') === 0) {
                  var propKey = k.slice(5).replace(/^[A-Z]/, function (c) {
                    return c.toLowerCase();
                  });
                  props[propKey || k] = el.dataset[k];
                }
              }
            }
            fiestaTrack(el.dataset.track, props);
            break;
          }
          el = el.parentNode;
        }
      }, true);
    } catch (e) { /* swallow */ }
  }

  // Expose + autobind on DOM ready.
  window.fiestaTrack = fiestaTrack;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoBindClicks);
  } else {
    autoBindClicks();
  }
})();
