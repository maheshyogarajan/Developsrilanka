/**
 * tax_preview.js — Live calc for the FIESTA S0 Tax Math Breakdown.
 *
 * Wires the input strip in /tax-preview to POST /preview/calc and renders
 * the bracket table + saving banner.
 *
 * Design notes:
 *   - Debounce input changes (300ms) to keep the server unbothered.
 *   - Optimistic UI: while waiting for the round-trip we keep the previous
 *     numbers visible (no flash-to-zero).
 *   - SL number formatting: standard comma-thousand by default (Western
 *     convention). Lakh-comma is a TODO — needs CEO call (see PM finding).
 *   - Sub-100ms perceived latency: debounce ON CHANGE, render IMMEDIATELY
 *     on response. No animations on the critical path.
 *   - Graceful degradation: if /preview/calc 4xx, show inline error.
 */
(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ---- INPUT ELEMENTS -----------------------------------------------------
  const grossEl = $('#gross-income');
  const currencyEl = $('#currency');
  const sourceEl = $('#income-source');
  const yearEl = $('#year');
  const spEl = $('#sp-fee');
  const rentalEl = $('#rental');
  const seniorEl = $('#senior');

  // ---- OUTPUT ELEMENTS ----------------------------------------------------
  const statusEl = $('#breakdown-status');
  const bodyEl = $('#breakdown-body');
  const reliefEl = $('#b-relief');
  const seniorExtraEl = $('#b-senior-extra');
  const taxableEl = $('#b-taxable');
  const tbodyEl = $('#brackets-tbody');
  const naiveTotalEl = $('#b-naive-total');
  const effNaiveEl = $('#b-eff-naive');
  const marginalEl = $('#b-marginal');

  const cmpNaiveEl = $('#cmp-naive');
  const cmpFiestaEl = $('#cmp-fiesta');
  const cmpDeductionsEl = $('#cmp-deductions');
  const cmpDeductionPctEl = $('#cmp-deduction-pct');

  const savingEl = $('#b-saving');
  const savingPctEl = $('#b-saving-pct');
  const currencyLineEl = $('#currency-line');

  // ---- HELPERS ------------------------------------------------------------
  function fmtLkr(amount) {
    if (amount === null || amount === undefined || amount === '') return 'Rs 0';
    const n = Number(amount);
    if (!isFinite(n)) return 'Rs 0';
    // Standard comma-thousand (Western). Lakh-comma TBD per CEO call.
    return 'Rs ' + Math.round(n).toLocaleString('en-LK');
  }

  function parseNumeric(v) {
    if (v === null || v === undefined) return 0;
    const s = String(v).replace(/,/g, '').trim();
    if (!s) return 0;
    const n = Number(s);
    return isFinite(n) ? n : 0;
  }

  function getInputs() {
    return {
      gross_income: parseNumeric(grossEl.value),
      currency: currencyEl.value || 'USD',
      income_source: sourceEl.value || 'foreign',
      sp_fee: parseNumeric(spEl.value),
      rental: parseNumeric(rentalEl.value),
      senior: seniorEl.checked,
      year: yearEl.value || '25_26',
    };
  }

  // ---- DEBOUNCE -----------------------------------------------------------
  let debounceTimer = null;
  function debounced(fn, ms) {
    return function () {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(fn, ms);
    };
  }

  // ---- RENDER -------------------------------------------------------------
  function renderError(msg) {
    statusEl.innerHTML = '<p style="color: var(--color-warning-700, #8F5A0E);">' +
      msg + '</p>';
    statusEl.style.display = '';
    bodyEl.style.display = 'none';
  }

  function showBody() {
    statusEl.style.display = 'none';
    bodyEl.style.display = '';
  }

  function renderBrackets(result) {
    showBody();

    reliefEl.textContent = fmtLkr(result.personal_relief_lkr);
    const senior = Number(result.senior_relief_lkr || 0);
    if (senior > 0) {
      seniorExtraEl.textContent = ' + ' + fmtLkr(senior) + ' senior-citizen relief';
    } else {
      seniorExtraEl.textContent = '';
    }
    taxableEl.textContent = fmtLkr(result.taxable_income_naive_lkr);

    // Render bracket rows (naive — that's what the IRD walk on raw income would do).
    tbodyEl.innerHTML = '';
    let maxActiveRate = '0';
    (result.bracket_breakdown_naive || []).forEach((b, idx) => {
      const tr = document.createElement('tr');
      const hasIncome = Number(b.income_in_band_lkr) > 0;
      if (hasIncome) tr.classList.add('active');

      const bandLabel = (b.band_upper_lkr === null || b.band_upper_lkr === undefined)
        ? fmtLkr(b.band_lower_lkr) + ' and above'
        : fmtLkr(b.band_lower_lkr) + ' – ' + fmtLkr(b.band_upper_lkr);

      tr.innerHTML =
        '<td>' + bandLabel + '</td>' +
        '<td>' + b.rate_pct_display + '</td>' +
        '<td>' + fmtLkr(b.income_in_band_lkr) + '</td>' +
        '<td>' + fmtLkr(b.tax_in_band_lkr) + '</td>';
      tbodyEl.appendChild(tr);

      if (hasIncome) maxActiveRate = b.rate_pct_display;
    });

    naiveTotalEl.textContent = fmtLkr(result.naive_tax_lkr);
    effNaiveEl.textContent = result.effective_rate_naive_pct || '0';
    marginalEl.textContent = maxActiveRate.replace('%', '');

    cmpNaiveEl.textContent = fmtLkr(result.naive_tax_lkr);
    cmpFiestaEl.textContent = fmtLkr(result.fiesta_tax_lkr);
    cmpDeductionsEl.textContent = Math.round(Number(result.fiesta_deductions_lkr || 0)).toLocaleString('en-LK');
    cmpDeductionPctEl.textContent = result.fiesta_deduction_pct_applied || '0';

    savingEl.textContent = fmtLkr(result.saving_lkr);
    if (savingPctEl) {
      savingPctEl.innerHTML = '— at <span>' + (result.saving_pct || '0') + '</span>% of the tax you\'d otherwise pay';
    }
  }

  function updateCurrencyLine() {
    const cur = currencyEl.value;
    if (cur === 'LKR') {
      currencyLineEl.textContent = 'In Sri Lankan Rupees (LKR).';
    } else {
      currencyLineEl.textContent = 'In ' + cur + '. Toggle currency to LKR if you prefer.';
    }
  }

  // ---- FETCH --------------------------------------------------------------
  let inflightController = null;
  async function fetchPreview() {
    const inputs = getInputs();
    if (inputs.gross_income <= 0) {
      statusEl.innerHTML = '<p style="color: var(--color-ink-400);">Enter your annual income above to see the bracket walk.</p>';
      statusEl.style.display = '';
      bodyEl.style.display = 'none';
      return;
    }

    // Abort previous request if still inflight (avoids race condition stale render)
    if (inflightController) inflightController.abort();
    inflightController = new AbortController();

    try {
      const resp = await fetch('/preview/calc', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify(inputs),
        signal: inflightController.signal,
      });
      if (!resp.ok) {
        let detail = '';
        try {
          const errPayload = await resp.json();
          detail = errPayload.error || '';
        } catch (e) { /* swallow */ }
        renderError('Could not compute preview' + (detail ? ': ' + detail : '') + '.');
        return;
      }
      const data = await resp.json();
      renderBrackets(data);
    } catch (err) {
      if (err.name === 'AbortError') return;
      renderError('Network error — try again in a moment.');
    }
  }

  const debouncedFetch = debounced(fetchPreview, 300);

  // ---- BINDINGS -----------------------------------------------------------
  [grossEl, spEl, rentalEl].forEach((el) => {
    el.addEventListener('input', debouncedFetch);
  });
  [sourceEl, seniorEl].forEach((el) => {
    el.addEventListener('change', debouncedFetch);
  });

  // Currency-toggle buttons
  $$('#currency-toggle button').forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('#currency-toggle button').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currencyEl.value = btn.getAttribute('data-cur');
      updateCurrencyLine();
      debouncedFetch();
    });
  });

  // Initial state
  updateCurrencyLine();
})();
