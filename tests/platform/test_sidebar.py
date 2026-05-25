"""F-Platform-2 (Stage C2) — FIESTA sidebar refinement regression tests.

Locks the PLAN_X9_WIRE_UP.md §F-Platform-2 sidebar contract + the Design
Lock 1 `_shell_contract.md` active-link CSS class (`.fiesta-nav-active`):

  1. Sidebar renders every required nav item from the spec list.
  2. Active-link detection: visiting /remittance/dashboard flags the
     Remittance Ledger entry .fiesta-nav-active (others stay inactive).
  3. Active-link detection: visiting /reduce-tax/ flags the Reduce your
     tax entry .fiesta-nav-active.
  4. New /cosign/pending index route renders 200 (via direct render to
     bypass the self_file paywall) and lists an empty state by default.
  5. Legacy bookkeeping users (persona=None) keep layout.html — the
     FIESTA sidebar must NOT render for them.

Active-link assertions are scoped via a small regex helper that isolates
the <a ...>label</a> element for the entry under test — otherwise
substring scans pick up <title>Remittance Ledger · FIESTA</title> or
a sibling-entry active class leaking through a too-wide window.

Test design notes:
  - We reuse the `app`, `client`, `user_factory`, login_as patterns
    from test_shell.py so the suite stays uniform.
  - The /cosign/pending route is paywall-gated at min_tier=self_file.
    Rather than wire up a Subscription fixture (the paywall conftest
    pattern is heavy), we exercise the template + view function via
    direct render_template in a test request context — same pattern as
    test_shell.py's admin-shell test (test 2).
"""
from __future__ import annotations

import re


def login_as(client, user):
    """Bypass the email/password form. Inlined for the same stdlib-
    ``platform`` collision reason documented in test_shell.py."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _get_followed(client, path):
    return client.get(path, follow_redirects=True)


# Match the <a ...class="..."> opening tag of the sidebar entry whose
# visible label text is `{label}`. The sidebar.html template always wraps
# the label in <span>label</span> immediately after the icon span, so
# anchoring on that shape uniquely identifies the entry.
_NAV_LINK_RE_TPL = (
    r'<a\b[^>]*class="([^"]*\bfiesta-nav-link\b[^"]*)"[^>]*>'
    r'(?:(?!</a>).)*?<span>{label}</span>'
)


def _nav_link_classes(body: str, label: str) -> str:
    """Return the class attribute string of the sidebar <a> whose visible
    label is `label`. Raises AssertionError if zero or multiple matches.

    Scoping by the <span>{label}</span> shape prevents collisions with
    <title> tags, breadcrumbs, paragraph text, or page-body links that
    happen to share the same label as a sidebar entry.
    """
    pattern = _NAV_LINK_RE_TPL.format(label=re.escape(label))
    matches = re.findall(pattern, body, re.DOTALL)
    assert len(matches) == 1, (
        f"expected exactly one sidebar <a> for label {label!r}; "
        f"got {len(matches)} matches (page has duplicates or label "
        f"appears outside the sidebar)"
    )
    return matches[0]


def _assert_active(body: str, label: str):
    classes = _nav_link_classes(body, label)
    assert "fiesta-nav-active" in classes, (
        f"sidebar entry {label!r} missing .fiesta-nav-active; "
        f"classes={classes!r}"
    )


def _assert_inactive(body: str, label: str):
    classes = _nav_link_classes(body, label)
    assert "fiesta-nav-active" not in classes, (
        f"sidebar entry {label!r} unexpectedly has .fiesta-nav-active; "
        f"classes={classes!r}"
    )


# -------------------------------------------------------------------- #
# 1. Sidebar contains every nav item from the F-Platform-2 spec.
# -------------------------------------------------------------------- #
def test_sidebar_has_all_nav_items(app, client, user_factory):
    """Every entry from PLAN_X9_WIRE_UP §F-Platform-2 must be present
    when the FIESTA shell renders. Hub / Earn-in / Deduct / Generate /
    Outcome / Help / Profile / Sign out — labels + URLs.
    """
    user = user_factory(
        "sidebar_all",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = _get_followed(client, "/remittance/dashboard")
    assert resp.status_code == 200, (
        f"/remittance/dashboard returned {resp.status_code}; "
        f"body={resp.get_data(as_text=True)[:200]!r}"
    )
    body = resp.get_data(as_text=True)

    # Group headings — small, uppercase, muted per spec; .fiesta-nav-label
    # is the class that styles them in fiesta.css.
    for heading in ("Earn-in", "Deduct", "Generate", "Outcome", "Help"):
        assert heading in body, f"sidebar group heading missing: {heading!r}"
    assert "fiesta-nav-label" in body, (
        "fiesta-nav-label class missing — group headings won't style correctly"
    )

    # Per-entry label list, in spec order. Each label MUST resolve to
    # exactly one sidebar <a> via the regex helper (zero = missing,
    # multiple = ambiguous wiring).
    expected_labels = [
        "Hub",
        "Remittance Ledger",
        "Add a remittance",
        "Import a bank statement",
        "Reduce your tax",
        "Your support team",
        "Home-office rent",
        "Service agreements",
        "Rental agreements",
        "Co-sign pending",
        "Your tax bill",
        "Submit",
        "Book a consultation",
        "Tax preview",
        "Profile",
        "Sign out",
    ]
    for label in expected_labels:
        # If the entry is absent the regex returns zero matches, which the
        # helper asserts on. We don't care about active/inactive here —
        # just presence.
        _ = _nav_link_classes(body, label)

    # Per-entry URL spot-check — confirms refinement wiring is the new
    # /cosign/pending and /fie/profile, not the old /cosign + /profile.
    assert 'href="/cosign/pending"' in body, (
        "Co-sign pending link must point at /cosign/pending (not /cosign)"
    )
    assert "/fie/profile" in body, (
        "Profile link must point at /fie/profile per spec (not /profile alias)"
    )


# -------------------------------------------------------------------- #
# 2. Active-link detection on /remittance/dashboard.
# -------------------------------------------------------------------- #
def test_active_link_on_remittance_dashboard(app, client, user_factory):
    """The Remittance Ledger entry should carry .fiesta-nav-active when
    rendered on /remittance/dashboard. Sibling entries in the same group
    (Add a remittance, Import a bank statement) MUST NOT carry it."""
    user = user_factory(
        "sidebar_remit_active",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = _get_followed(client, "/remittance/dashboard")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    _assert_active(body, "Remittance Ledger")
    _assert_inactive(body, "Add a remittance")
    _assert_inactive(body, "Import a bank statement")
    # Cross-group sanity: a Deduct entry must not be active either.
    _assert_inactive(body, "Reduce your tax")


# -------------------------------------------------------------------- #
# 3. Active-link detection on /reduce-tax/.
# -------------------------------------------------------------------- #
def test_active_link_on_reduce_tax(app, client, user_factory):
    """Reduce your tax entry should carry .fiesta-nav-active when
    rendered on /reduce-tax/ (no paywall on this route).
    """
    user = user_factory(
        "sidebar_reduce_active",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = _get_followed(client, "/reduce-tax/")
    assert resp.status_code == 200, (
        f"/reduce-tax/ returned {resp.status_code}; "
        f"body={resp.get_data(as_text=True)[:200]!r}"
    )
    body = resp.get_data(as_text=True)

    _assert_active(body, "Reduce your tax")
    # Sibling within Deduct group must NOT be active.
    _assert_inactive(body, "Your support team")
    _assert_inactive(body, "Home-office rent")
    # Cross-group sanity.
    _assert_inactive(body, "Remittance Ledger")


# -------------------------------------------------------------------- #
# 4. New /cosign/pending route renders + lists workflows.
# -------------------------------------------------------------------- #
def test_cosign_pending_index_renders(app, user_factory):
    """The new /cosign/pending route returns 200 and lists in-flight
    co-sign workflows. Free-tier users hit the self_file paywall on the
    GET, so we render the template directly via render_template inside
    a test request context (mirrors test_shell.py admin-shell test).
    """
    from flask import render_template
    from flask_login import login_user

    user = user_factory(
        "cosign_pending",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )

    # Render the pending template with an empty rows list — proves the
    # template + sidebar render cleanly without a DB-backed workflow.
    with app.test_request_context("/cosign/pending"):
        login_user(user)
        html = render_template(
            "cosign/pending.html",
            rows=[],
            error_state=None,
        )

    # FIESTA shell wired in.
    assert "fiesta-shell" in html, "cosign/pending must render the FIESTA shell"
    assert 'id="fiesta-savings-counter"' in html, (
        "cosign/pending must inherit the savings counter from layout_fiesta"
    )

    # Empty-state content + CTA.
    assert "Nothing pending" in html
    assert "/agreements/service" in html, (
        "empty state must link to /agreements/service so users know where "
        "to start a co-sign workflow"
    )

    # The route also handles graceful degradation (models_unavailable).
    with app.test_request_context("/cosign/pending"):
        login_user(user)
        html2 = render_template(
            "cosign/pending.html",
            rows=[],
            error_state="models_unavailable",
        )
    assert "Co-sign hasn't been set up" in html2, (
        "models_unavailable branch must surface a friendlier explainer"
    )


def test_cosign_pending_route_registered(app):
    """The /cosign/pending URL must resolve to the fiesta_cosign.pending
    view function — proves the route was registered, not just imported."""
    from flask import url_for
    with app.test_request_context():
        url = url_for("fiesta_cosign.pending")
    assert url == "/cosign/pending", (
        f"fiesta_cosign.pending must mount at /cosign/pending; got {url!r}"
    )


# -------------------------------------------------------------------- #
# 5. Legacy bookkeeping user keeps the old sidebar (NOT the FIESTA one).
#
# MS4 W2 Agent 1 — G1.2 (2026-05-25): INVERTED by Design Lock 3 §D1+§D5.
# Post-G1.2 every authenticated user gets the FIESTA shell + sidebar;
# G1.4 (W2 Agent 2) extends the FIESTA sidebar with bookkeeping module
# entries conditional on user activity. The "legacy sidebar keeps
# leaking" failure mode is now structurally prevented because layout.html
# only renders for anonymous. W3 follow-up will rewrite this assertion.
# -------------------------------------------------------------------- #
import pytest as _pt_w2a1


@_pt_w2a1.mark.xfail(
    reason=(
        "Pre-G1.2 contract. Post-G1.2 (Design Lock 3 §D1+§D5) every authenticated "
        "user is on the FIESTA shell; W2 Agent 2 (G1.4) extends the FIESTA sidebar "
        "with conditional bookkeeping entries. W3 follow-up will rewrite."
    ),
    strict=True,
)
def test_legacy_bookkeeping_user_keeps_old_sidebar(app, client, user_factory):
    """For persona=None (legacy bookkeeping), the app must render
    layout.html — NOT layout_fiesta.html. This is verified two ways:

      (a) use_fiesta_shell() returns False — the gating function the
          render path consults.
      (b) g.layout_template resolves to "layout.html" inside a request
          context — the actual value the templates use when extending
          {% extends layout_template %}.

    If either assertion fails, the FIESTA sidebar is leaking into legacy
    user surfaces — which would break the persona contract per
    `_shell_contract.md` "Persona gating" section.
    """
    legacy = user_factory(
        "sidebar_legacy",
        persona=None,
        role="user",
        is_email_verified=True,
        onboarding_completed=True,
    )

    # (a) Gate function returns False.
    from app import use_fiesta_shell
    assert use_fiesta_shell(legacy) is False, (
        "use_fiesta_shell() must return False for legacy bookkeeping persona; "
        "if True, the FIESTA sidebar will render for users who should keep "
        "the legacy bookkeeping nav."
    )

    # (b) layout_template context value is layout.html (NOT layout_fiesta.html).
    login_as(client, legacy)
    with client.application.test_request_context("/"):
        from flask import g
        from flask_login import login_user
        from app import check_authentication
        login_user(legacy)
        check_authentication()
        assert g.layout_template == "layout.html", (
            f"legacy user got layout_template={g.layout_template!r}; "
            f"expected 'layout.html' — FIESTA sidebar would leak"
        )
        assert g.is_fiesta_persona is False
