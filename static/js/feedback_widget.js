/* FIESTA in-app feedback widget — Sprint 4 Tier D4 (2026-05-24).
 *
 * Floating "Feedback" button (bottom-right corner) on every page. Clicking
 * opens a modal with:
 *   - Category dropdown: bug / feature / confusion / praise / other
 *   - Free-text body (max ~4000 chars; server truncates)
 *   - Send + Cancel buttons
 *
 * On Send: POSTs to /api/feedback. Server returns 204 on success.
 *
 * Design constraints (parity with beacon.js):
 *   - Pure vanilla JS, no jQuery, no framework deps.
 *   - Idempotent: window.fiestaFeedback set once; no duplicate buttons if
 *     the script is included on both layout.html and empty_layout.html.
 *   - Never throws. Network errors surface as an inline error message;
 *     the user can edit + retry without losing their text.
 *   - Same-origin only. The /api/feedback endpoint enforces an Origin
 *     check; this widget never sets a cross-origin header.
 *   - Anon-friendly. session_anon_id cookie (set by the analytics beacon's
 *     after_request hook) flows automatically as a credential.
 *
 * Out of scope (Wave 3): admin viewer, file upload, screenshot, email
 * notification, category-based routing. Submission lands in the DB and
 * the CEO reads with `SELECT * FROM feedback ORDER BY created_at DESC LIMIT 50;`.
 */
(function () {
  'use strict';

  if (window.fiestaFeedback) return;  // idempotent

  var ENDPOINT = '/api/feedback';
  var BODY_MAX = 4000;

  // ---------- DOM helpers ---------- //
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
        if (k === 'style' && typeof attrs[k] === 'object') {
          for (var s in attrs[k]) {
            if (Object.prototype.hasOwnProperty.call(attrs[k], s)) {
              node.style[s] = attrs[k][s];
            }
          }
        } else if (k.indexOf('on') === 0 && typeof attrs[k] === 'function') {
          node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        } else {
          node.setAttribute(k, attrs[k]);
        }
      }
    }
    if (children) {
      if (!Array.isArray(children)) children = [children];
      for (var i = 0; i < children.length; i++) {
        var c = children[i];
        if (c == null) continue;
        if (typeof c === 'string') node.appendChild(document.createTextNode(c));
        else node.appendChild(c);
      }
    }
    return node;
  }

  // ---------- Styles ---------- //
  function injectStyles() {
    if (document.getElementById('fiesta-feedback-styles')) return;
    var css = (
      '.fiesta-fb-button{' +
      'position:fixed;right:18px;bottom:18px;z-index:2147483640;' +
      'background:#2b5fff;color:#fff;border:none;border-radius:24px;' +
      'padding:10px 18px;font:600 14px/1 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;' +
      'box-shadow:0 4px 16px rgba(0,0,0,.18);cursor:pointer;' +
      'transition:transform .12s ease, box-shadow .12s ease;' +
      '}' +
      '.fiesta-fb-button:hover{transform:translateY(-1px);box-shadow:0 6px 22px rgba(0,0,0,.22);}' +
      '.fiesta-fb-overlay{' +
      'position:fixed;inset:0;background:rgba(20,24,40,.5);z-index:2147483641;' +
      'display:flex;align-items:center;justify-content:center;padding:18px;' +
      '}' +
      '.fiesta-fb-modal{' +
      'background:#fff;color:#1a1f33;border-radius:12px;width:100%;max-width:440px;' +
      'box-shadow:0 12px 40px rgba(0,0,0,.28);padding:22px;' +
      'font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;' +
      '}' +
      '.fiesta-fb-modal h3{margin:0 0 12px;font-size:18px;font-weight:700;}' +
      '.fiesta-fb-modal label{display:block;font-weight:600;margin:12px 0 6px;font-size:13px;}' +
      '.fiesta-fb-modal select,.fiesta-fb-modal textarea{' +
      'width:100%;box-sizing:border-box;border:1px solid #c8cee0;border-radius:8px;' +
      'padding:8px 10px;font:14px/1.4 inherit;background:#fff;color:inherit;' +
      '}' +
      '.fiesta-fb-modal textarea{min-height:120px;resize:vertical;}' +
      '.fiesta-fb-modal select:focus,.fiesta-fb-modal textarea:focus{' +
      'outline:none;border-color:#2b5fff;box-shadow:0 0 0 3px rgba(43,95,255,.18);' +
      '}' +
      '.fiesta-fb-counter{font-size:12px;color:#6b7180;text-align:right;margin-top:4px;}' +
      '.fiesta-fb-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px;}' +
      '.fiesta-fb-btn{' +
      'border:none;border-radius:8px;padding:8px 14px;font:600 13px inherit;cursor:pointer;' +
      '}' +
      '.fiesta-fb-btn.cancel{background:#eef0f5;color:#1a1f33;}' +
      '.fiesta-fb-btn.send{background:#2b5fff;color:#fff;}' +
      '.fiesta-fb-btn[disabled]{opacity:.55;cursor:not-allowed;}' +
      '.fiesta-fb-status{margin-top:10px;font-size:13px;min-height:18px;}' +
      '.fiesta-fb-status.ok{color:#0b8a4a;}' +
      '.fiesta-fb-status.err{color:#c0392b;}'
    );
    var style = el('style', { id: 'fiesta-feedback-styles', type: 'text/css' });
    style.appendChild(document.createTextNode(css));
    document.head.appendChild(style);
  }

  // ---------- Modal ---------- //
  var modalState = null;

  function closeModal() {
    if (!modalState) return;
    try { document.body.removeChild(modalState.overlay); } catch (e) { /* swallow */ }
    document.removeEventListener('keydown', modalState.escHandler);
    modalState = null;
  }

  function postFeedback(category, body, statusEl, sendBtn, cancelBtn) {
    if (typeof fetch !== 'function') {
      statusEl.className = 'fiesta-fb-status err';
      statusEl.textContent = 'Your browser does not support sending feedback.';
      return;
    }
    sendBtn.setAttribute('disabled', 'disabled');
    cancelBtn.setAttribute('disabled', 'disabled');
    statusEl.className = 'fiesta-fb-status';
    statusEl.textContent = 'Sending…';

    var payload = JSON.stringify({
      category: category,
      body: body,
      url: (location && location.href) || null
    });

    fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      credentials: 'same-origin',
      mode: 'same-origin'
    }).then(function (resp) {
      if (resp.status === 204) {
        statusEl.className = 'fiesta-fb-status ok';
        statusEl.textContent = 'Thanks — we received your feedback.';
        setTimeout(closeModal, 1100);
        return;
      }
      // Try to surface the server-side error message.
      resp.text().then(function (t) {
        var msg = 'Sorry, that didn’t go through.';
        try {
          var j = JSON.parse(t);
          if (j && j.error) msg = j.error;
        } catch (e) { /* swallow */ }
        statusEl.className = 'fiesta-fb-status err';
        statusEl.textContent = msg + ' Please try again.';
        sendBtn.removeAttribute('disabled');
        cancelBtn.removeAttribute('disabled');
      }).catch(function () {
        statusEl.className = 'fiesta-fb-status err';
        statusEl.textContent = 'Could not reach the server. Please try again.';
        sendBtn.removeAttribute('disabled');
        cancelBtn.removeAttribute('disabled');
      });
    }).catch(function () {
      statusEl.className = 'fiesta-fb-status err';
      statusEl.textContent = 'Could not reach the server. Please try again.';
      sendBtn.removeAttribute('disabled');
      cancelBtn.removeAttribute('disabled');
    });
  }

  function openModal() {
    if (modalState) return;
    injectStyles();

    var categorySelect = el('select', { id: 'fiesta-fb-category' }, [
      el('option', { value: 'bug' }, 'Bug — something is broken'),
      el('option', { value: 'feature' }, 'Feature — I want this to do X'),
      el('option', { value: 'confusion' }, 'Confusion — I don’t understand'),
      el('option', { value: 'praise' }, 'Praise — this worked / I liked it'),
      el('option', { value: 'other' }, 'Other')
    ]);

    var textarea = el('textarea', {
      id: 'fiesta-fb-body',
      maxlength: String(BODY_MAX),
      placeholder: 'Tell us what happened, or what you’d like to see.'
    });

    var counter = el('div', { class: 'fiesta-fb-counter' }, '0 / ' + BODY_MAX);
    textarea.addEventListener('input', function () {
      counter.textContent = textarea.value.length + ' / ' + BODY_MAX;
    });

    var statusEl = el('div', { class: 'fiesta-fb-status', role: 'status' });

    var cancelBtn = el('button', { type: 'button', class: 'fiesta-fb-btn cancel' }, 'Cancel');
    cancelBtn.addEventListener('click', closeModal);

    var sendBtn = el('button', { type: 'button', class: 'fiesta-fb-btn send' }, 'Send');
    sendBtn.addEventListener('click', function () {
      var body = (textarea.value || '').trim();
      if (!body) {
        statusEl.className = 'fiesta-fb-status err';
        statusEl.textContent = 'Please add a message before sending.';
        textarea.focus();
        return;
      }
      postFeedback(categorySelect.value, body, statusEl, sendBtn, cancelBtn);
    });

    // Tier D3 / D1: surface an "Ask AI first" link so users with a quick
    // question route to the FAQ retrieval (free, instant) instead of the
    // feedback queue (human, slower). Pure progressive enhancement — the
    // widget still works without it for users who just want to leave
    // feedback.
    var askAi = el('a', {
      href: '/support/qa',
      class: 'fiesta-fb-ai-link',
      style: { display: 'block', marginBottom: '12px', fontSize: '13px', color: '#2b5fff', textDecoration: 'none' }
    }, 'Have a question? Try Ask FIESTA first →');

    var modal = el('div', { class: 'fiesta-fb-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Send feedback' }, [
      el('h3', null, 'Send feedback'),
      askAi,
      el('label', { for: 'fiesta-fb-category' }, 'Category'),
      categorySelect,
      el('label', { for: 'fiesta-fb-body' }, 'Your message'),
      textarea,
      counter,
      statusEl,
      el('div', { class: 'fiesta-fb-actions' }, [cancelBtn, sendBtn])
    ]);

    var overlay = el('div', { class: 'fiesta-fb-overlay' }, modal);
    overlay.addEventListener('click', function (ev) {
      if (ev.target === overlay) closeModal();
    });

    function escHandler(ev) {
      if (ev.key === 'Escape' || ev.keyCode === 27) closeModal();
    }
    document.addEventListener('keydown', escHandler);

    document.body.appendChild(overlay);
    modalState = { overlay: overlay, escHandler: escHandler };

    setTimeout(function () { try { textarea.focus(); } catch (e) { /* swallow */ } }, 30);
  }

  // ---------- Floating button ---------- //
  function mountButton() {
    if (document.getElementById('fiesta-fb-button')) return;  // already mounted
    injectStyles();
    var btn = el('button', {
      id: 'fiesta-fb-button',
      type: 'button',
      class: 'fiesta-fb-button',
      'aria-label': 'Send feedback'
    }, 'Feedback');
    btn.addEventListener('click', openModal);
    try {
      document.body.appendChild(btn);
    } catch (e) { /* swallow — body may not be ready in edge cases */ }
  }

  // Expose + mount on DOM ready.
  window.fiestaFeedback = { open: openModal, close: closeModal };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountButton);
  } else {
    mountButton();
  }
})();
