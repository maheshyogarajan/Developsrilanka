"""Defect D6 (SEV-2) regression tests — A6 live projection doesn't paint.

CEO's reproduction (T2 / earn_deduct report, 2026-05-22):
    Manual keyboard fill of foreign_amount + cbsl_rate on
    /remittance/new triggers `#rem-projection` to flip from
    `display: none` to visible (the IIFE at the bottom of
    templates/remittance/new.html hits /preview/calc and paints
    `Rs <saving>`). Programmatic fill (Playwright .fill) did NOT
    trigger the listener — projection stayed hidden.

Root cause: the IIFE only bound 'input' on amountInput; some
automation harnesses dispatch only 'change'. cbslInput already
listened for both. The Tier D-Perf 2026-05-25 fix adds 'change'
to amountInput AND a DOMContentLoaded-time recovery path that
fires the projection if both inputs arrived pre-populated.

These are JS-source assertions (cheap, deterministic) since the
project doesn't have a Playwright fixture wired to the Flask app.
They guard against the listener regressing back to 'input'-only.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "templates" / "remittance" / "new.html"
)


@pytest.fixture(scope="module")
def template_source() -> str:
    assert _TEMPLATE.exists(), f"Template not found: {_TEMPLATE}"
    return _TEMPLATE.read_text(encoding="utf-8")


def test_remittance_new_template_exists(template_source):
    """Sanity — template loads and contains the projection element."""
    assert 'id="rem-projection"' in template_source
    assert 'id="rem-saving-amount"' in template_source


def test_amount_input_listens_for_both_input_and_change(template_source):
    """D6 regression guard: foreign_amount input MUST have BOTH 'input'
    and 'change' listeners. Programmatic fill mechanisms differ — some
    only dispatch 'change' on blur; some dispatch 'input' on value set.
    Binding both guarantees the projection paints under either.

    The previous binding was 'input'-only, which the master defect log
    filed as D6 ("Manual fill works, programmatic Playwright fill does
    not trigger the listener").
    """
    # We assert against the IIFE block by anchoring on the schedule()
    # function call — that's the closure-local handler the listeners
    # wire to. Both event names must appear bound to amountInput.
    assert "amountInput.addEventListener('input', schedule)" in template_source, (
        "amountInput must listen for 'input' (manual typing path)."
    )
    assert "amountInput.addEventListener('change', schedule)" in template_source, (
        "D6 regression: amountInput must ALSO listen for 'change' so "
        "programmatic fill (Playwright, autofill, dispatchEvent('change')) "
        "still paints the #rem-projection element."
    )


def test_cbsl_input_listens_for_both_input_and_change(template_source):
    """cbslInput has the same dual-binding requirement — already in
    place before D6 but pinned here so a future refactor can't strip it."""
    assert "cbslInput.addEventListener('input', schedule)" in template_source
    assert "cbslInput.addEventListener('change', schedule)" in template_source


def test_dom_ready_recovery_path_present(template_source):
    """D6 hardening: if the page arrives with both inputs already
    populated (back/forward cache, server pre-fill, automation harness
    that set values BEFORE the IIFE ran), the projection must fire at
    load time. This guards against the 'fill happened too early' race."""
    # Look for the recovery block guarded on both .value being set.
    pattern = re.compile(
        r"if\s*\(\s*amountInput\.value\s+&&\s+cbslInput\.value\s+&&"
        r"\s+parseFloat\(amountInput\.value\)\s*>\s*0\s+&&"
        r"\s+parseFloat\(cbslInput\.value\)\s*>\s*0\s*\)\s*\{\s*schedule\(\)",
        re.MULTILINE,
    )
    assert pattern.search(template_source), (
        "D6 hardening missing: page-load recovery (`schedule()` when "
        "both values arrive pre-populated) was stripped — projection "
        "will stay hidden for any harness that fills before listener bind."
    )


def test_projection_starts_hidden(template_source):
    """The projection element starts hidden — the JS reveals it on
    successful /preview/calc response. This is the inverse of the bug
    (if the element was visible by default, it would mask projection
    bugs rather than expose them)."""
    # Look for display:none in the inline style of the projection div.
    pattern = re.compile(
        r'id="rem-projection"\s+style="\s*display:\s*none',
        re.MULTILINE,
    )
    assert pattern.search(template_source), (
        "Projection default style should be display:none so the JS "
        "show/hide mechanism is detectable in tests."
    )


def test_cbsl_autofill_dispatches_input_event(template_source):
    """The CBSL auto-fill IIFE programmatically sets cbslInput.value
    after fetching from /remittance/api/cbsl-rate. To preserve D6 fix
    on that path, the auto-fill MUST dispatch an 'input' event so the
    projection listener fires."""
    assert "cbslInput.dispatchEvent(new Event('input', { bubbles: true }))" in template_source, (
        "CBSL auto-fill no longer dispatches 'input' on cbslInput — "
        "projection won't paint after rate auto-fill."
    )
