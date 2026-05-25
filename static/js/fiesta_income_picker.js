/* fiesta_income_picker.js — MS4 W3d G3.6 (Income-Source Picker).
 *
 * Responsibilities:
 *   1. Intercept submit on any <form data-fiesta-income-picker="1"> and
 *      POST it as application/json to /api/fiesta/income-sources.
 *   2. Hydrate the ".fiesta-isp-row.is-ticked" decoration on checkbox
 *      toggle (so the row background reflects the current selection
 *      pre-submit).
 *   3. Show inline success / error / warning status in
 *      "#<pickerId>-status".
 *   4. Dispatch a `fiesta:income-source-added` CustomEvent on
 *      window/document after a successful save so the sidebar +
 *      topbar counter refresh on the same render (no full page reload).
 *   5. Expose a single global helper FiestaIncomePicker.open() so any
 *      page (hub modal trigger, profile-page link) can show the picker
 *      in a modal overlay without re-implementing the wiring.
 *
 * IDEMPOTENT: the IIFE checks window.__FIESTA_INCOME_PICKER_INIT__
 * before binding, so multiple <script> includes (page-level + modal
 * trigger include) don't double-bind handlers.
 *
 * NO BUILD STEP: this is hand-rolled ES5+ that runs untranspiled in
 * every browser FIESTA supports. Mirrors fiesta.js conventions.
 */
(function () {
  'use strict';

  if (window.__FIESTA_INCOME_PICKER_INIT__) {
    return;
  }
  window.__FIESTA_INCOME_PICKER_INIT__ = true;

  var ENDPOINT_POST = '/api/fiesta/income-sources';
  var EVENT_NAME = 'fiesta:income-source-added';

  // ------------------------------------------------------------------
  // CSRF token helpers
  // ------------------------------------------------------------------
  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    // Fallback: try to find a hidden csrf_token input anywhere.
    var hidden = document.querySelector('input[name="csrf_token"]');
    return hidden ? hidden.value : '';
  }

  // ------------------------------------------------------------------
  // Status renderer
  // ------------------------------------------------------------------
  function setStatus(form, text, kind) {
    var pickerWrap = form.closest('[data-fiesta-isp="1"]') || form.parentNode;
    var pickerId = pickerWrap ? pickerWrap.id.replace(/-wrap$/, '') : '';
    var el = pickerId ? document.getElementById(pickerId + '-status') : null;
    if (!el) {
      el = form.querySelector('.fiesta-isp-status');
    }
    if (!el) return;
    el.textContent = text || '';
    el.classList.remove('is-success', 'is-error');
    if (kind === 'success') el.classList.add('is-success');
    if (kind === 'error') el.classList.add('is-error');
  }

  // ------------------------------------------------------------------
  // Row decoration — keep .is-ticked in sync with the checkbox state.
  // ------------------------------------------------------------------
  function bindRowDecorations(root) {
    var rows = root.querySelectorAll('.fiesta-isp-row');
    Array.prototype.forEach.call(rows, function (row) {
      var cb = row.querySelector('.fiesta-isp-cb');
      if (!cb) return;
      cb.addEventListener('change', function () {
        if (cb.checked) {
          row.classList.add('is-ticked');
        } else {
          row.classList.remove('is-ticked');
        }
      });
    });
  }

  // ------------------------------------------------------------------
  // Event dispatch — fires on window AND document so both listeners
  // pick it up. Matches the fiesta.js convention.
  // ------------------------------------------------------------------
  function dispatchIncomeSourceEvent(detail) {
    try {
      var evt = new CustomEvent(EVENT_NAME, { detail: detail || {}, bubbles: true });
      window.dispatchEvent(evt);
      document.dispatchEvent(evt);
    } catch (e) {
      // IE / very old Edge fallback
      try {
        var legacy = document.createEvent('CustomEvent');
        legacy.initCustomEvent(EVENT_NAME, true, false, detail || {});
        window.dispatchEvent(legacy);
        document.dispatchEvent(legacy);
      } catch (e2) {
        /* swallow — analytics-only */
      }
    }
  }

  // ------------------------------------------------------------------
  // Form submit handler
  // ------------------------------------------------------------------
  function submitForm(form) {
    var saveBtn = form.querySelector('[data-fiesta-isp-save="1"]');
    if (saveBtn) saveBtn.disabled = true;
    setStatus(form, 'Saving...', null);

    var checkboxes = form.querySelectorAll('input.fiesta-isp-cb');
    var selected = [];
    Array.prototype.forEach.call(checkboxes, function (cb) {
      if (cb.checked) selected.push(cb.value);
    });

    var headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json'
    };
    var csrf = getCsrfToken();
    if (csrf) headers['X-CSRFToken'] = csrf;

    return fetch(ENDPOINT_POST, {
      method: 'POST',
      headers: headers,
      credentials: 'same-origin',
      body: JSON.stringify({ income_sources: selected })
    }).then(function (r) {
      return r.json().then(function (data) {
        return { ok: r.ok, status: r.status, data: data };
      });
    }).then(function (result) {
      if (saveBtn) saveBtn.disabled = false;
      var d = result.data || {};
      if (!result.ok) {
        var msg = d.error || 'Could not save. Please try again.';
        setStatus(form, msg, 'error');
        return result;
      }
      // Success path — show success + any warnings (retained items).
      var msg2 = 'Saved.';
      if (d.warnings && d.warnings.length) {
        msg2 = d.warnings.join(' ');
      }
      setStatus(form, msg2, d.warnings && d.warnings.length ? 'error' : 'success');
      dispatchIncomeSourceEvent({
        income_sources: d.income_sources || [],
        added: d.added || [],
        removed: d.removed || [],
        retained: d.retained || []
      });
      // If the picker is inside a modal, close it after a short delay
      // so the user sees the toast. The hub picks up the event on the
      // same render so the sidebar refresh is immediate.
      var modal = form.closest('.fiesta-isp-modal');
      if (modal) {
        setTimeout(function () {
          closeModal(modal);
        }, 900);
      }
      return result;
    }).catch(function (err) {
      if (saveBtn) saveBtn.disabled = false;
      setStatus(form, 'Network error. Please try again.', 'error');
      return { ok: false, error: err };
    });
  }

  function bindFormSubmit(form) {
    if (form.__fiestaIspBound) return;
    form.__fiestaIspBound = true;
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      submitForm(form);
    });
  }

  // ------------------------------------------------------------------
  // Modal — open / close
  // ------------------------------------------------------------------
  function closeModal(modal) {
    if (!modal || !modal.parentNode) return;
    modal.parentNode.removeChild(modal);
    document.body.classList.remove('fiesta-isp-modal-open');
  }

  function buildModalShell() {
    var overlay = document.createElement('div');
    overlay.className = 'fiesta-isp-modal';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'What kinds of income do you earn?');

    var body = document.createElement('div');
    body.className = 'fiesta-isp-modal-body';
    overlay.appendChild(body);

    var closeBtn = document.createElement('button');
    closeBtn.className = 'fiesta-isp-modal-close';
    closeBtn.setAttribute('type', 'button');
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.innerHTML = '&times;';
    closeBtn.addEventListener('click', function () { closeModal(overlay); });
    body.appendChild(closeBtn);

    // Click-outside-to-close
    overlay.addEventListener('click', function (ev) {
      if (ev.target === overlay) closeModal(overlay);
    });

    // Escape-to-close
    var escHandler = function (ev) {
      if (ev.key === 'Escape' || ev.keyCode === 27) {
        closeModal(overlay);
        document.removeEventListener('keydown', escHandler);
      }
    };
    document.addEventListener('keydown', escHandler);

    return { overlay: overlay, body: body };
  }

  /**
   * Open the picker as a modal overlay. Fetches the partial HTML from
   * /fie/income-sources?modal=1 (the standalone page wrapped in our
   * modal shell) or — if a host page wants to bypass the AJAX fetch —
   * accepts a selector pointing at an existing in-page picker DOM
   * fragment that should be cloned + shown.
   *
   * Usage:
   *   FiestaIncomePicker.open();                  // ajax-load
   *   FiestaIncomePicker.open({ from: '#myPicker' });  // clone from DOM
   */
  function openPicker(opts) {
    opts = opts || {};
    document.body.classList.add('fiesta-isp-modal-open');
    var shell = buildModalShell();
    document.body.appendChild(shell.overlay);

    var fromSel = opts.from;
    if (fromSel) {
      var src = document.querySelector(fromSel);
      if (src) {
        var clone = src.cloneNode(true);
        // Ensure the clone has a unique id so status helpers still resolve.
        clone.id = (clone.id || 'fiestaIncomePicker') + '-modal';
        shell.body.appendChild(clone);
        var form = clone.querySelector('form[data-fiesta-income-picker="1"]');
        if (form) {
          bindFormSubmit(form);
          bindRowDecorations(clone);
        }
        return shell;
      }
    }

    // AJAX path — pull the standalone page and extract the picker
    // partial out of it. We don't render the full layout in the modal.
    fetch('/fie/income-sources', {
      credentials: 'same-origin',
      headers: { 'Accept': 'text/html' }
    }).then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) {
        var tmp = document.createElement('div');
        tmp.innerHTML = html;
        var picker = tmp.querySelector('[data-fiesta-isp="1"]');
        if (!picker) {
          shell.body.innerHTML += '<p>Sorry — the picker failed to load.</p>';
          return;
        }
        picker.id = (picker.id || 'fiestaIncomePicker') + '-modal';
        shell.body.appendChild(picker);
        // Also pull any inline <style> from the partial so the modal
        // is styled even if the host page never loaded the partial.
        var styles = tmp.querySelectorAll('style');
        Array.prototype.forEach.call(styles, function (s) {
          // Avoid duplicating styles already on the page.
          if (s.textContent && s.textContent.indexOf('.fiesta-isp-row') > -1) {
            if (!document.querySelector('style[data-fiesta-isp-style="1"]')) {
              var clone = s.cloneNode(true);
              clone.setAttribute('data-fiesta-isp-style', '1');
              document.head.appendChild(clone);
            }
          }
        });
        var form = picker.querySelector('form[data-fiesta-income-picker="1"]');
        if (form) {
          bindFormSubmit(form);
          bindRowDecorations(picker);
        }
      })
      .catch(function () {
        shell.body.innerHTML +=
          '<p style="padding:16px 0;color:#b91c1c">' +
          'Sorry — the picker failed to load. ' +
          '<a href="/fie/income-sources">Open it on its own page instead.</a>' +
          '</p>';
      });

    return shell;
  }

  // ------------------------------------------------------------------
  // Init on DOMContentLoaded — bind any picker forms already on the page.
  // ------------------------------------------------------------------
  function initOnReady() {
    var forms = document.querySelectorAll('form[data-fiesta-income-picker="1"]');
    Array.prototype.forEach.call(forms, function (form) {
      bindFormSubmit(form);
      var wrap = form.closest('[data-fiesta-isp="1"]') || form;
      bindRowDecorations(wrap);
    });

    // Bind any opener triggers — buttons / links with data-fiesta-isp-open.
    var openers = document.querySelectorAll('[data-fiesta-isp-open]');
    Array.prototype.forEach.call(openers, function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        var fromSel = btn.getAttribute('data-fiesta-isp-open');
        openPicker(fromSel && fromSel !== '1' ? { from: fromSel } : {});
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initOnReady);
  } else {
    initOnReady();
  }

  // Public surface
  window.FiestaIncomePicker = {
    open: openPicker,
    submit: submitForm,
    bind: function (form) {
      bindFormSubmit(form);
      var wrap = form.closest('[data-fiesta-isp="1"]') || form;
      bindRowDecorations(wrap);
    },
    EVENT_NAME: EVENT_NAME,
    ENDPOINT: ENDPOINT_POST
  };
})();
