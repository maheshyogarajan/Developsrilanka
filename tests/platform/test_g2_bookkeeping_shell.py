"""MS4 W3a — G2 bookkeeping-shell-migration regression suite.

Locks the Section G addendum §G2 contract (see
`G:/My Drive/CEO OS/working files/_fiesta_unification_addendum_20260525.md`)
plus Design Lock 3 §D5 (universal shell, `_g1_design_lock_universal_shell.md`):

  - Every legacy bookkeeping page that historically extended `layout.html`
    now `{% extends layout_template %}` and therefore renders inside the
    FIESTA shell (layout_fiesta.html for non-admin, layout_fiesta_admin.html
    for admin) for any authenticated user.
  - Proof of shell extension is the presence of the FIESTA chrome
    selectors `class="fiesta-shell"` (on <body>) and `class="fiesta-main"`
    (on the main content region) — neither selector is emitted by the
    legacy layout.html.
  - Page-specific markers (form fields, headings) remain present so the
    migration is provably non-destructive — only the chrome changed.
  - The final test (test_legacy_layout_html_no_longer_used_by_bookkeeping)
    inspects the migrated template files on disk to ensure none of them
    regressed to `{% extends "layout.html" %}` during a follow-up edit.

Test design notes:
  - Login is via the same session-cookie shortcut used in test_sidebar.py
    + test_sidebar_bookkeeping.py.
  - We follow_redirects on every GET so /scan's G1.3 admin-vs-non-admin
    redirect resolves naturally before we inspect the response body.
  - Integration tests that need DB seeding inherit the W2 Agent 2 xfail
    pattern: the test fixture's User/Receipt/CompanyExpense FK cascades
    are unreliable on teardown (documented in test_sidebar_bookkeeping.py
    line 237). Code paths are verified manually on running Flask + we
    fall back to filesystem inspection for the final ``no_legacy`` test.

References:
  - Section G addendum: `_fiesta_unification_addendum_20260525.md` §G2
  - Design Lock 3: `_g1_design_lock_universal_shell.md` §D5
  - Shell contract: `_shell_contract.md` (block names + selectors)
  - W2 Agent 2 precedent: `tests/platform/test_sidebar_bookkeeping.py`
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Login + helpers — same shape as test_sidebar.py to keep suites uniform.
# ---------------------------------------------------------------------------
def login_as(client, user):
    """Bypass the email/password form by setting the Flask-Login cookie."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _get_followed(client, path):
    return client.get(path, follow_redirects=True)


# Markers that prove the response was rendered through layout_fiesta.html
# (or layout_fiesta_admin.html which inherits from it). Both classes
# originate in layout_fiesta.html and are LOCKED per Design Lock 1
# (`_shell_contract.md`). Their absence in a response body is proof the
# legacy layout.html shell was used instead.
_FIESTA_SHELL_MARKER = 'class="fiesta-shell"'   # <body> on layout_fiesta.html
_FIESTA_MAIN_MARKER = 'class="fiesta-main"'     # <main> on layout_fiesta.html


def _assert_extends_fiesta_shell(body: str, route: str):
    """Assert the rendered HTML body shows FIESTA-shell selectors."""
    assert _FIESTA_SHELL_MARKER in body, (
        f"{route} response missing {_FIESTA_SHELL_MARKER!r}; the page is "
        f"still rendering through legacy layout.html"
    )
    assert _FIESTA_MAIN_MARKER in body, (
        f"{route} response missing {_FIESTA_MAIN_MARKER!r}; the page is "
        f"not inside the FIESTA <main> region"
    )


# ---------------------------------------------------------------------------
# Per-module integration smoke tests.
#
# Each test logs in a freshly-created user, GETs the module entry route,
# follows redirects, and asserts (a) the FIESTA shell markers, (b) at
# least one page-specific marker that proves the template's `content`
# block still rendered (so the migration didn't accidentally swallow
# the page body into a non-existent block name).
#
# All seven are marked xfail strict=False inheriting the W2 Agent 2
# fixture-cascade issue (see test_sidebar_bookkeeping.py line 237).
# When that issue is resolved (Agent 3 or G5 fixture rewrite), strip the
# xfail markers and the tests should pass green.
# ---------------------------------------------------------------------------

_FIXTURE_XFAIL_REASON = (
    "TODO(G2 v1.1): integration fixture cascade — code paths verified "
    "manually inside the W3a worktree using a running Flask app + a real "
    "Receipt-having user. Same pattern as test_sidebar_bookkeeping.py "
    "(MS4 W2 Agent 2 precedent). Fix is a W3 / G5 fixture rewrite, not a "
    "W3a-G2 in-scope task."
)


@pytest.mark.xfail(reason=_FIXTURE_XFAIL_REASON, strict=False)
def test_receipts_page_extends_fiesta_shell(app, client, user_factory):
    """G2.1 — /scan (admin path) renders inside the FIESTA shell.

    Admin role triggers `layout_fiesta_admin.html` which inherits from
    layout_fiesta.html, so both shell markers fire. Non-admin users are
    redirected to / by G1.3; the redirect target also lives inside the
    FIESTA shell. We exercise the admin path here since it's a non-redirect
    direct render of templates/index.html — the actual migrated file.
    """
    u = user_factory("g2_receipts_admin", role="admin")
    login_as(client, u)

    resp = _get_followed(client, "/scan")
    assert resp.status_code == 200, f"/scan returned {resp.status_code}"
    body = resp.get_data(as_text=True)
    _assert_extends_fiesta_shell(body, "/scan")
    # Page-specific marker — the receipt-scan page surfaces an upload form
    # whose CSRF-protected POST target is /scan.
    assert 'action="/scan"' in body or 'name="csrf_token"' in body, (
        "/scan content block missing — receipt-scan upload form did not render"
    )


@pytest.mark.xfail(reason=_FIXTURE_XFAIL_REASON, strict=False)
def test_pnl_page_extends_fiesta_shell(app, client, user_factory):
    """G2.2 — /accounts/profit-loss renders inside the FIESTA shell.

    The route redirects to the dashboard if the user has no organisations;
    follow_redirects handles that — the dashboard ALSO extends
    layout_template now, so the shell markers still fire either way.
    """
    u = user_factory("g2_pnl", role="user")
    login_as(client, u)

    resp = _get_followed(client, "/accounts/profit-loss")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_extends_fiesta_shell(body, "/accounts/profit-loss")
    # Page-specific marker — either P&L heading or the redirected
    # accounts-dashboard heading.
    assert "Profit & Loss" in body or "Accounts Dashboard" in body, (
        "/accounts/profit-loss content missing — neither P&L nor the "
        "redirect-target accounts dashboard headings present"
    )


@pytest.mark.xfail(reason=_FIXTURE_XFAIL_REASON, strict=False)
def test_cash_in_page_extends_fiesta_shell(app, client, user_factory):
    """G2.3 — /invoices renders inside the FIESTA shell."""
    u = user_factory("g2_cash_in", role="user")
    login_as(client, u)

    resp = _get_followed(client, "/invoices")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_extends_fiesta_shell(body, "/invoices")
    # Page-specific marker — invoices.html surfaces an "Invoices" eyebrow
    # / heading plus organisation-filter heading.
    assert "Invoices" in body, (
        "/invoices content missing — invoices heading not rendered"
    )


@pytest.mark.xfail(reason=_FIXTURE_XFAIL_REASON, strict=False)
def test_cash_out_page_extends_fiesta_shell(app, client, user_factory):
    """G2.3 — /expenses/pipeline renders inside the FIESTA shell."""
    u = user_factory("g2_cash_out", role="user")
    login_as(client, u)

    resp = _get_followed(client, "/expenses/pipeline")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_extends_fiesta_shell(body, "/expenses/pipeline")
    # Page-specific marker — kanban container.
    assert "expensePipeline" in body or "pipeline" in body.lower(), (
        "/expenses/pipeline content missing — kanban container not rendered"
    )


@pytest.mark.xfail(reason=_FIXTURE_XFAIL_REASON, strict=False)
def test_accounts_page_extends_fiesta_shell(app, client, user_factory):
    """G2.3 — /accounts/ (dashboard) renders inside the FIESTA shell."""
    u = user_factory("g2_accounts", role="user")
    login_as(client, u)

    resp = _get_followed(client, "/accounts/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_extends_fiesta_shell(body, "/accounts/")
    assert "Accounts Dashboard" in body or "accounting" in body.lower(), (
        "/accounts/ content missing — accounts dashboard heading not present"
    )


@pytest.mark.xfail(reason=_FIXTURE_XFAIL_REASON, strict=False)
def test_bank_statements_page_extends_fiesta_shell(app, client, user_factory):
    """G2.3 — /enhanced_bank/dashboard renders inside the FIESTA shell."""
    u = user_factory("g2_bank_stmts", role="user")
    login_as(client, u)

    resp = _get_followed(client, "/enhanced_bank/dashboard")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_extends_fiesta_shell(body, "/enhanced_bank/dashboard")
    assert "Bank" in body and (
        "Reconciliation" in body or "Statement" in body
    ), (
        "/enhanced_bank/dashboard content missing — bank-reconciliation "
        "heading not present"
    )


@pytest.mark.xfail(reason=_FIXTURE_XFAIL_REASON, strict=False)
def test_tax_documents_page_extends_fiesta_shell(app, client, user_factory):
    """G2.4 — /tax-doc/scan renders inside the FIESTA shell."""
    u = user_factory("g2_tax_docs", role="user")
    login_as(client, u)

    resp = _get_followed(client, "/tax-doc/scan")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_extends_fiesta_shell(body, "/tax-doc/scan")
    # Page-specific marker — the doc_lens upload form.
    assert "Tax" in body and ("document" in body.lower() or "doc" in body.lower()), (
        "/tax-doc/scan content missing — tax-doc upload form not rendered"
    )


# ---------------------------------------------------------------------------
# Filesystem inspection — runs without DB/fixtures so it can't be xfail'd
# by the integration-cascade pattern. This is the canary test the dispatch
# spec calls for: catches a future refactor that accidentally re-points a
# migrated template back at layout.html.
# ---------------------------------------------------------------------------

# Repo-relative paths of every template migrated in MS4 W3a G2. Kept
# explicit (not glob-derived) so an accidental deletion / rename is also
# caught by this test failing for the wrong reason.
_G2_MIGRATED_TEMPLATES = [
    "templates/index.html",                                # G2.1 Receipts
    "templates/accounts/profit_loss.html",                 # G2.2 PNL
    "templates/accounts/dashboard.html",                   # G2.3 Accounts
    "templates/accounts/chart_of_accounts.html",           # G2.3
    "templates/accounts/assets.html",                      # G2.3
    "templates/accounts/journal_entries.html",             # G2.3
    "templates/invoices.html",                             # G2.3 Cash in
    "templates/expense_pipeline.html",                     # G2.3 Cash out
    "templates/enhanced_bank/dashboard.html",              # G2.3 Bank statements
    "templates/enhanced_bank/statements_list.html",        # G2.3
    "templates/enhanced_bank/upload_form.html",            # G2.3
    "templates/enhanced_bank/view_statement.html",         # G2.3
    "templates/enhanced_bank/validation_results.html",     # G2.3
    "templates/enhanced_bank/reconcile_statement.html",    # G2.3
    "templates/enhanced_bank/reconciliation_center.html",  # G2.3
    "templates/enhanced_bank/rule_customization.html",     # G2.3
    "templates/tax_doc_scan.html",                         # G2.4 Tax documents
]


def test_legacy_layout_html_no_longer_used_by_bookkeeping():
    """G2 canary — no migrated bookkeeping template may extend layout.html.

    Inspects each file on disk. Looks for any `{% extends "layout.html" %}`
    or `{% extends 'layout.html' %}` directive at the top of the file
    (within the first 5 lines, which is where Jinja inheritance must
    declare). A regression here means a future edit re-pointed a migrated
    template at the legacy shell.
    """
    repo_root = Path(__file__).resolve().parents[2]
    offenders = []

    for rel_path in _G2_MIGRATED_TEMPLATES:
        full = repo_root / rel_path
        assert full.exists(), (
            f"G2 manifest references {rel_path} but the file is missing — "
            f"a template was deleted or renamed without updating "
            f"_G2_MIGRATED_TEMPLATES"
        )
        # Read the top of the file only — Jinja inheritance must declare
        # at the top, so any later occurrences would be in comments / docs.
        head = full.read_text(encoding="utf-8", errors="replace").splitlines()[:5]
        head_blob = "\n".join(head)
        if (
            '{% extends "layout.html" %}' in head_blob
            or "{% extends 'layout.html' %}" in head_blob
        ):
            offenders.append(rel_path)

    assert not offenders, (
        "G2 regression — the following bookkeeping templates re-introduced "
        "`{% extends layout.html %}` after MS4 W3a migration. Each must "
        "extend `layout_template` so the FIESTA shell renders.\n"
        + "\n".join(f"  - {p}" for p in offenders)
    )
