"""F3.3 (P2 polish, 2026-05-27) — live savings projection on /remittance/new.

Locks the new `POST /api/fiesta/preview-savings` endpoint contract plus
the form-wiring on /remittance/new that calls it.

Two test layers:

1. ENDPOINT contract — the response shape, error handling, tax-year
   normalisation, and reuse of the existing fiesta.tax.quick_preview
   engine. We hit the live endpoint with the Flask test client, no DB
   row fixtures needed (the calc is pure).

2. UI WIRING (JS-source assertions) — the `/remittance/new` IIFE
   actually calls the new endpoint, sends the 3-field payload the
   endpoint expects, and dispatches the topbar pill refresh event on
   success. JS-source-level checks avoid needing a Playwright fixture
   wired to Flask (project doesn't currently have one).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO_ROOT / "templates" / "remittance" / "new.html"


# --------------------------------------------------------------------------- #
# Endpoint contract — POST /api/fiesta/preview-savings
# --------------------------------------------------------------------------- #


def _post(client, payload: dict):
    return client.post(
        "/api/fiesta/preview-savings",
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_preview_savings_happy_path_lkr(client):
    """Plain LKR amount → response contains the 4 contract fields and
    the savings number is positive for a 5M-LKR foreign-income amount
    (well above the personal relief threshold)."""
    r = _post(client, {
        "amount": 5_000_000,
        "currency": "LKR",
        "tax_year": "25/26",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    # Contract fields the brief specifies.
    assert "taxable_lkr" in body
    assert "tax_without_deductions_lkr" in body
    assert "savings_lkr" in body
    assert "projected_bill_lkr" in body
    # And the extra echo fields the wrapper adds for the UI.
    assert "gross_lkr" in body
    assert "fx_rate_used" in body
    assert "year" in body

    # Sanity: a 5M-LKR foreign-income year should produce a non-zero
    # bill AND non-zero savings (FIESTA-deduction heuristic > 0).
    assert body["projected_bill_lkr"] > 0
    assert body["savings_lkr"] > 0
    # And the savings can't be larger than the naive bill (savings is
    # naive - fiesta, both >= 0, so savings <= naive).
    assert body["savings_lkr"] <= body["tax_without_deductions_lkr"]


def test_preview_savings_normalises_year_forms(client):
    """The endpoint accepts the same tax-year forms the topbar selector
    emits ('25/26', '2025/26', '2025-26', '25_26'). All four normalise
    to the engine's '25_26' key and produce the same numeric result."""
    bodies = []
    for ya_form in ("25/26", "2025/26", "2025-26", "25_26"):
        r = _post(client, {
            "amount": 3_000_000,
            "currency": "LKR",
            "tax_year": ya_form,
        })
        assert r.status_code == 200, (ya_form, r.get_data(as_text=True))
        bodies.append(r.get_json())

    # All four normalise to the same engine year + identical math.
    canonical = bodies[0]
    for b in bodies[1:]:
        assert b["year"] == canonical["year"], (b, canonical)
        assert b["projected_bill_lkr"] == canonical["projected_bill_lkr"]
        assert b["savings_lkr"] == canonical["savings_lkr"]


def test_preview_savings_rejects_unknown_year(client):
    """A tax_year string the engine doesn't know about returns 400
    with kind='input_error' — no silent fallback to the default year
    (silent fallback would mask UI bugs that send the wrong YA form)."""
    r = _post(client, {
        "amount": 1_000_000,
        "currency": "LKR",
        "tax_year": "garbage_yr",
    })
    assert r.status_code == 400
    body = r.get_json()
    assert body["kind"] == "input_error"
    assert "garbage_yr" in body["error"]


def test_preview_savings_rejects_non_numeric_amount(client):
    """Amount must be numeric — anything else is an input error, not a
    500."""
    r = _post(client, {
        "amount": "not-a-number",
        "currency": "LKR",
        "tax_year": "25/26",
    })
    assert r.status_code == 400
    assert r.get_json()["kind"] == "input_error"


def test_preview_savings_rejects_negative_amount(client):
    """Negative amount also 400, not 500."""
    r = _post(client, {
        "amount": -100,
        "currency": "LKR",
        "tax_year": "25/26",
    })
    assert r.status_code == 400
    assert r.get_json()["kind"] == "input_error"


def test_preview_savings_zero_amount_returns_zero(client):
    """Zero amount is valid input (user just opened the form) — must
    return 200 with all-zero contract fields, not 400. UI relies on
    this so it can soft-show a placeholder card."""
    r = _post(client, {
        "amount": 0,
        "currency": "LKR",
        "tax_year": "25/26",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["projected_bill_lkr"] == 0
    assert body["savings_lkr"] == 0


def test_preview_savings_defaults_year_when_missing(client):
    """tax_year is optional — missing/empty falls back to the engine's
    default year (currently 25_26) so a stale UI doesn't break."""
    r = _post(client, {
        "amount": 2_000_000,
        "currency": "LKR",
    })
    assert r.status_code == 200
    body = r.get_json()
    # Default is the engine's 25_26 key.
    assert body["year"] == "25_26"


def test_preview_savings_is_csrf_exempt(client):
    """The endpoint must be CSRF-exempt — /remittance/new fires it on
    every input event and can't afford to round-trip a CSRF token. The
    same posture as /preview/calc."""
    # If CSRF were enforced, this would 400 since we send no token
    # header. We verify by sending without any CSRF token and asserting
    # 200 (or 400 only for input-error reasons, not CSRF).
    r = _post(client, {
        "amount": 1_000_000,
        "currency": "LKR",
        "tax_year": "25/26",
    })
    assert r.status_code == 200, (
        "Endpoint rejected a token-less POST. The IIFE on /remittance/new "
        "may send X-CSRFToken when the meta tag is present, but the "
        "endpoint must not REQUIRE one."
    )


# --------------------------------------------------------------------------- #
# UI wiring — /remittance/new IIFE calls the new endpoint correctly.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def remittance_new_source() -> str:
    assert _TEMPLATE.exists(), f"Template not found: {_TEMPLATE}"
    return _TEMPLATE.read_text(encoding="utf-8")


def test_ui_calls_preview_savings_endpoint(remittance_new_source):
    """The IIFE at the bottom of /remittance/new must POST to the new
    /api/fiesta/preview-savings endpoint (not the old /preview/calc).
    The four-field response shape is what the UI reads, and pinning
    the URL here catches a future refactor that bypasses the wrapper."""
    assert "/api/fiesta/preview-savings" in remittance_new_source, (
        "IIFE no longer references the F3.3 endpoint. The form will not "
        "fetch live savings on amount/rate change."
    )


def test_ui_payload_uses_brief_contract_field_names(remittance_new_source):
    """The brief specifies the request shape `{amount, currency, tax_year}`.
    Pin those keys so a refactor that flips back to the legacy
    /preview/calc names (gross_income / income_source / year) is caught."""
    # The JS body should reference `amount:`, `currency:`, and `tax_year:`.
    assert re.search(r"\bamount\s*:", remittance_new_source)
    assert re.search(r"\bcurrency\s*:", remittance_new_source)
    assert re.search(r"\btax_year\s*:", remittance_new_source)


def test_ui_reads_savings_lkr_from_response(remittance_new_source):
    """The IIFE must read `d.savings_lkr` (the contract field name on
    the new endpoint), NOT `d.saving_lkr` (the legacy /preview/calc
    name). A typo here breaks the projection silently — element stays
    hidden, no console error."""
    assert "d.savings_lkr" in remittance_new_source, (
        "IIFE no longer reads `d.savings_lkr` from the response. The "
        "new endpoint emits `savings_lkr` (plural) per the F3.3 contract."
    )


def test_ui_debounce_is_300ms(remittance_new_source):
    """Brief specifies 300ms debounce target. Lock the value so a future
    perf-fiddle doesn't drift it back to 250ms (the old value) or push
    it to 500ms+ (which makes the projection feel laggy)."""
    # Look for the setTimeout(runProjection, 300) pattern.
    pattern = re.compile(
        r"setTimeout\s*\(\s*runProjection\s*,\s*300\s*\)",
        re.MULTILINE,
    )
    assert pattern.search(remittance_new_source), (
        "Debounce delay drifted off 300ms. Pin per F3.3 brief target."
    )


def test_ui_dispatches_topbar_pill_refresh_event(remittance_new_source):
    """On successful preview-savings response, the IIFE must dispatch
    the contract-locked `fiesta:savings-counter-refresh` event so the
    topbar pill re-fetches its server-side projection. Without this,
    the form-inline card updates but the topbar number goes stale."""
    assert "fiesta:savings-counter-refresh" in remittance_new_source, (
        "IIFE no longer dispatches `fiesta:savings-counter-refresh`. "
        "Topbar pill won't refresh after a live projection runs."
    )
    # Belt: must use the documented dispatchUpdate API, not raw
    # window.dispatchEvent (because the wrapper handles the case where
    # window.fiesta isn't loaded yet — anon, race, error).
    assert "window.fiesta.dispatchUpdate" in remittance_new_source
