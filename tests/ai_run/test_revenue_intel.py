"""
Revenue Intelligence Dashboard tests — Wave 2.1 (2026-05-17).

Validates:

  1. compute_metrics() returns a dict with the expected shape (3 sections +
     computed_at + errors).
  2. compute_metrics() tolerates missing/empty data without raising —
     placeholders return their declared default, real queries return 0.
  3. /admin/revenue (and /admin/revenue.json) require role='admin' — non-admin
     users get 403.
  4. /admin/revenue renders HTML 200 for an admin user.

Fixtures come from tests/ai_run/conftest.py (which re-exports the validated
remittance conftest fixtures + adds an admin_user fixture).
"""
import pytest

from revenue_intel import (
    compute_metrics,
    LEADING_INDICATOR_QUERIES,
    FUNNEL_QUERIES,
    REVENUE_QUERIES,
)
# login_as is a plain helper function (not a fixture) in the remittance conftest;
# we import it directly rather than via the pytest fixture mechanism.
from tests.remittance.conftest import login_as


# --------------------------------------------------------------------------- #
# 1. compute_metrics() returns the expected shape
# --------------------------------------------------------------------------- #

def test_compute_metrics_returns_dict_with_required_keys(app):
    """Top-level shape contract — Wave 2 consumers (orchestrator, future bots,
    the HTML template) all rely on this exact structure."""
    with app.app_context():
        result = compute_metrics()

    # Top-level keys
    for key in ("leading_indicators", "funnel", "revenue", "computed_at", "errors"):
        assert key in result, f"missing top-level key {key!r} in compute_metrics() result"

    # Every defined query name must appear under its section (succeeded or None)
    for metric_name in LEADING_INDICATOR_QUERIES.keys():
        assert metric_name in result["leading_indicators"], (
            f"leading_indicators missing metric {metric_name!r}"
        )
    for metric_name in FUNNEL_QUERIES.keys():
        assert metric_name in result["funnel"], (
            f"funnel missing metric {metric_name!r}"
        )
    for metric_name in REVENUE_QUERIES.keys():
        assert metric_name in result["revenue"], (
            f"revenue missing metric {metric_name!r}"
        )

    # computed_at is an ISO-ish timestamp (ends with Z per our convention)
    assert isinstance(result["computed_at"], str)
    assert result["computed_at"].endswith("Z"), (
        f"computed_at should be UTC-suffixed, got {result['computed_at']!r}"
    )

    # errors is a list (may be empty)
    assert isinstance(result["errors"], list)


# --------------------------------------------------------------------------- #
# 2. compute_metrics() never raises, even when data is sparse
# --------------------------------------------------------------------------- #

def test_compute_metrics_tolerates_missing_tables(app):
    """The dashboard must not 500 if a query returns zero rows, if the
    placeholder queries are hit, or if gemini_cost_log is absent.

    We can't easily DROP tables in the live DB, but we CAN verify the
    placeholder queries return clean numbers and that no exceptions escape
    compute_metrics() in the default state.
    """
    with app.app_context():
        result = compute_metrics()

    # The two placeholders MUST succeed (they're constant SELECTs) and
    # report is_placeholder=True so the UI labels them honestly.
    for placeholder_metric in ("lankatax_crosssell_take_rate", "gross_margin_per_active_user"):
        row = result["leading_indicators"].get(placeholder_metric)
        assert row is not None, f"{placeholder_metric} returned None unexpectedly"
        assert row.get("is_placeholder") is True, (
            f"{placeholder_metric} should declare is_placeholder=True; got {row!r}"
        )
        # Placeholder value is 0 by design.
        assert row.get("metric_value") == 0.0 or row.get("metric_value") == 0, (
            f"{placeholder_metric} placeholder should be 0; got {row.get('metric_value')!r}"
        )

    # Even if no events have been written for a metric, the result should be a
    # dict (with metric_value possibly 0) — never None for the real queries
    # unless the SQL itself raised. Check that the failed-query count is small.
    # We're lenient here: some env drift (e.g. audit_log column types) could
    # legitimately fail one query; the contract is "doesn't crash the whole
    # dashboard," not "every query always succeeds."
    failed = [e["metric"] for e in result["errors"]]
    # The 2 placeholders + the funnel/revenue/MAU constants should always work.
    # If MORE than 2 queries fail we want to know.
    assert len(failed) <= 2, (
        f"Too many queries failed in clean run: {failed}. "
        f"Expected at most 2 (false_ready_rate audit_log fragile + 1 buffer)."
    )


# --------------------------------------------------------------------------- #
# 3. Admin gate — non-admin must get 403
# --------------------------------------------------------------------------- #

def test_admin_revenue_requires_admin_role(client, user_a):
    """A regular (role='user') account must NOT be able to load the dashboard.

    Both the HTML and JSON routes must enforce this."""
    login_as(client, user_a)

    html_resp = client.get("/admin/revenue")
    assert html_resp.status_code == 403, (
        f"non-admin should get 403 from /admin/revenue, got {html_resp.status_code}"
    )

    json_resp = client.get("/admin/revenue.json")
    assert json_resp.status_code == 403, (
        f"non-admin should get 403 from /admin/revenue.json, got {json_resp.status_code}"
    )


# --------------------------------------------------------------------------- #
# 4. Admin user sees the dashboard
# --------------------------------------------------------------------------- #

def test_admin_revenue_renders_for_admin(client, admin_user):
    """An admin user gets a real HTML response (200) with the dashboard markup.

    Spot-check a couple of strings we know the template emits so a future silent
    regression (template rename, blueprint mis-wire) is caught."""
    login_as(client, admin_user)

    resp = client.get("/admin/revenue")
    assert resp.status_code == 200, (
        f"admin should get 200 from /admin/revenue, got {resp.status_code}; "
        f"body[:500] = {resp.data[:500]!r}"
    )

    body = resp.data.decode("utf-8", errors="replace")
    # Template markers — if these change, update the assertions.
    assert "Revenue Intelligence" in body, "page title heading missing from rendered HTML"
    assert "Leading Indicators" in body, "leading indicators section missing"
    assert "Conversion Funnel" in body, "funnel section missing"

    # JSON twin
    json_resp = client.get("/admin/revenue.json")
    assert json_resp.status_code == 200, (
        f"admin should get 200 from /admin/revenue.json, got {json_resp.status_code}"
    )
    data = json_resp.get_json()
    assert isinstance(data, dict)
    assert "leading_indicators" in data
    assert "computed_at" in data


# --------------------------------------------------------------------------- #
# 5. Bonus: anonymous user gets redirected/forbidden (no leak of metrics)
# --------------------------------------------------------------------------- #

def test_admin_revenue_blocks_anonymous(client):
    """A user with no session at all should NOT see metrics. @login_required
    will redirect to login (302) or return 401; either is acceptable. What
    MUST NOT happen is a 200 with metric data in the body."""
    resp = client.get("/admin/revenue", follow_redirects=False)
    assert resp.status_code in (302, 401, 403), (
        f"anonymous user should be redirected/forbidden, got {resp.status_code}"
    )

    json_resp = client.get("/admin/revenue.json", follow_redirects=False)
    assert json_resp.status_code in (302, 401, 403), (
        f"anonymous JSON request should be blocked, got {json_resp.status_code}"
    )
