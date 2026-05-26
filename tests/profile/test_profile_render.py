"""C3 Day-0 fix — /fie/profile gate accepts unified onboarding (2026-05-27).

Regression suite for the bug where /fie/profile redirect-looped users who came
through the new G4 unified onboarding flow (/onboarding/welcome →
income-sources → confirm) — that flow sets `onboarding_completed=True` +
`income_sources=[...]` but does NOT populate `triage_answers`. The old gate
required `triage_answers` and bounced the user to /fie/triage, so they had
no UI path to enter NIC / name / address / bank.

Fix: gate accepts EITHER `triage_answers` (legacy 3-question flow) OR
`onboarding_completed=True` (new unified flow).

Run: pytest tests/profile/test_profile_render.py -v
"""

from __future__ import annotations

import pytest

from tests.profile.conftest import login_as


def _set_onboarding_state(
    db_session,
    user,
    *,
    triage_answers=None,
    onboarding_completed=False,
):
    """Helper: explicitly set both fields on the user row.

    The base `user_a` fixture creates rows with `onboarding_completed=True`
    by default — these tests need to assert specific combinations of both
    flags, so we override them here per test.
    """
    user.triage_answers = triage_answers
    user.onboarding_completed = onboarding_completed
    db_session.add(user)
    db_session.commit()


def test_profile_renders_form_for_unified_onboarding_user(
    client, user_a, db_session
):
    """User completed G4 unified onboarding (no triage_answers) → form renders.

    Pre-fix: redirect-loop to /fie/triage (the bug).
    Post-fix: 200 response, form rendered, user can enter NIC + bank.
    """
    _set_onboarding_state(
        db_session,
        user_a,
        triage_answers=None,
        onboarding_completed=True,
    )
    login_as(client, user_a)

    resp = client.get("/fie/profile", follow_redirects=False)
    # MUST render the form (200), NOT redirect to triage (302).
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. "
        f"Location={resp.headers.get('Location')!r}. "
        "This is the C3 regression — the profile form must render for users "
        "who completed the unified onboarding."
    )


def test_profile_renders_form_for_legacy_triage_user(
    client, user_a, db_session
):
    """User completed the legacy 3-question triage → form renders."""
    _set_onboarding_state(
        db_session,
        user_a,
        triage_answers={
            "earning_source": "pure_foreign",
            "earning_vehicle": ["employed_remote"],
            "filing_history": "first_time",
            "completed_at": "2026-05-26T12:00:00Z",
        },
        onboarding_completed=False,
    )
    login_as(client, user_a)

    resp = client.get("/fie/profile", follow_redirects=False)
    assert resp.status_code == 200, (
        f"Legacy triage user should land on the profile form, "
        f"got {resp.status_code} Location={resp.headers.get('Location')!r}"
    )


def test_profile_redirects_to_onboarding_for_brand_new_user(
    client, user_a, db_session
):
    """Brand-new user (neither triage nor onboarding done) → /onboarding/welcome.

    D-N2 polish (2026-05-27): previously this branch silently bounced to
    /fie/triage which then chained another silent 302 to /onboarding/welcome
    (for sl_foreign_income personas). Customer saw the onboarding screen
    with no explanation. We now redirect directly to /onboarding/welcome
    with an explicit flash message.
    """
    _set_onboarding_state(
        db_session,
        user_a,
        triage_answers=None,
        onboarding_completed=False,
    )
    login_as(client, user_a)

    resp = client.get("/fie/profile", follow_redirects=False)
    assert resp.status_code == 302, (
        f"Brand-new user should be redirected, got {resp.status_code}"
    )
    assert "/onboarding/welcome" in (resp.headers.get("Location") or ""), (
        f"Expected redirect to /onboarding/welcome, got {resp.headers.get('Location')!r}"
    )


def test_profile_redirect_flashes_message_for_brand_new_user(
    client, user_a, db_session
):
    """D-N2 polish — the redirect-to-onboarding branch must flash an
    info message so the user understands WHY they were bounced.

    Pre-fix: silent 302 (user saw the welcome screen with no
    explanation, looked like a broken link).
    Post-fix: flash("Please complete onboarding before setting up
    your profile.", "info") is set in the session before the redirect.
    """
    _set_onboarding_state(
        db_session,
        user_a,
        triage_answers=None,
        onboarding_completed=False,
    )
    login_as(client, user_a)

    resp = client.get("/fie/profile", follow_redirects=False)
    assert resp.status_code == 302
    # The flash sits in the session; inspect it directly. We don't follow
    # the redirect because /onboarding/welcome's render is out of scope
    # for this test — we only need to assert the flash was emitted.
    with client.session_transaction() as sess:
        # Flask stores flashes under '_flashes' as a list of (category, msg)
        # tuples. The exact key has been stable since Flask 0.x.
        flashes = sess.get("_flashes") or []
        msgs = [m for (_cat, m) in flashes]
        assert any(
            "complete onboarding" in m.lower() for m in msgs
        ), (
            f"Expected a flash explaining the onboarding redirect, "
            f"got flashes={flashes!r}. Without this flash, customers "
            f"arrive at /onboarding/welcome with no explanation of why "
            f"their /fie/profile click bounced — D-N2 regression."
        )


def test_profile_renders_for_user_with_both_triage_and_onboarding(
    client, user_a, db_session
):
    """User who somehow has both flags set → form renders (the common case)."""
    _set_onboarding_state(
        db_session,
        user_a,
        triage_answers={
            "earning_source": "mixed",
            "earning_vehicle": ["business_sole_prop"],
            "filing_history": "returning",
            "completed_at": "2026-05-26T12:00:00Z",
        },
        onboarding_completed=True,
    )
    login_as(client, user_a)

    resp = client.get("/fie/profile", follow_redirects=False)
    assert resp.status_code == 200
