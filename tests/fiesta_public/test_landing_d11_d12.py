"""tests.fiesta_public.test_landing_d11_d12 — Tier-D6 minor landing fixes.

D11 — Landing page CTA not detectable.
    The anon `/` landing page must expose a primary CTA with a stable
    selector (`data-testid="hero-cta-signup"`) that's server-rendered
    in the hero section (not deferred to the bottom cta-row, not lazy
    via JS).

D12 — Anonymous CSRF calc error.
    `runCalc()` must short-circuit for anon visitors and compute the
    estimator client-side. The root container must expose
    `data-user-authenticated="0"` for anon and `="1"` for authed users.
    The inline JS must contain the localApprox() routine and the
    auth-guard branch.

We render the template against a Jinja env shimmed with `url_for`,
`csrf_token`, and a duck-typed `current_user`. No Flask boot — keeps
the test fast and isolated from Stripe/Neon/Flask-Login.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


_TEMPLATE_PATH = _ROOT / "templates" / "fiesta_public" / "s0_landing.html"


def _render_landing(authenticated: bool) -> str:
    """Render s0_landing.html standalone with the given auth state."""
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    raw = _TEMPLATE_PATH.read_text(encoding="utf-8")
    no_extends = re.sub(r"\{%\s*extends\s+'layout\.html'\s*%\}", "", raw, count=1)

    base = (
        "{% block title %}{% endblock %}"
        "{% block additional_styles %}{% endblock %}"
        "{% block content %}{% endblock %}"
        "{% block scripts %}{% endblock %}"
    )

    # F1.2 (Phase C W2): s0_landing.html now `{% include %}`s
    # `fiesta_public/_hero_partial.html`. ChoiceLoader keeps the in-memory
    # `landing.html` shim while also resolving real templates from disk so
    # the include works without booting Flask.
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"base.html": base, "landing.html": no_extends}),
                FileSystemLoader(str(_ROOT / "templates")),
            ]
        ),
        autoescape=True,
    )

    def _url_for(name, **kwargs):
        # Mirror Flask's url_for signature loosely — return a stable path.
        return f"/{name}/" + "/".join(str(v) for v in kwargs.values())

    def _csrf_token():
        return "test-csrf-token"

    env.globals["url_for"] = _url_for
    env.globals["csrf_token"] = _csrf_token

    # Shim Flask-Login's current_user proxy.
    current_user = SimpleNamespace(
        is_authenticated=bool(authenticated),
        persona=None,
        role="user",
    )

    return env.get_template("landing.html").render(
        current_user=current_user,
        fx_rate_lkr_per_usd=302,
        hero_example=SimpleNamespace(
            naive_tax_lkr="999,000",
            saving_lkr="298,980",
            saving_pct="30",
            fiesta_tax_lkr="700,020",
            gross_income_lkr="9,060,000",
        ),
    )


# --------------------------------------------------------------------------- #
# D11 — primary hero CTA must be present + detectable                          #
# --------------------------------------------------------------------------- #


def test_d11_anon_landing_has_hero_cta_with_stable_selector():
    """Anon `/` must expose a `data-testid="hero-cta-signup"` button."""
    html = _render_landing(authenticated=False)

    assert 'data-testid="hero-cta-signup"' in html, (
        "Anon landing must expose a stable selector on the hero CTA (D11)"
    )
    # Visible text — accept either "Sign up" / "sign up" or "Get started"
    # so we don't over-couple to wording.
    visible_text_match = (
        "Get started" in html
        or "sign up" in html.lower()
    )
    assert visible_text_match, (
        "Hero CTA must contain 'Get started' or 'sign up' visible text"
    )
    # Secondary CTA — also present in the anon hero row
    assert 'data-testid="hero-cta-secondary"' in html, (
        "Anon landing should also have a secondary 'See the full result' CTA"
    )


def test_d11_authed_landing_hero_cta_routes_to_setup():
    """Authed users get the same selector but routed to the setup flow."""
    html = _render_landing(authenticated=True)
    assert 'data-testid="hero-cta-signup"' in html, (
        "Authed hero CTA must keep the stable selector for monitoring/probes"
    )
    # Authed branch routes to triage/fiesta_triage, never to the register page.
    # The secondary CTA is anon-only, so it must NOT appear here.
    assert 'data-testid="hero-cta-secondary"' not in html, (
        "Secondary CTA must be anon-only"
    )


def test_d11_cta_is_server_rendered_not_js_deferred():
    """The CTA element must be in the static HTML — not injected by JS."""
    html = _render_landing(authenticated=False)
    # Pull out everything outside <script>...</script> blocks
    no_scripts = re.sub(r"<script\b.*?</script>", "", html, flags=re.DOTALL)
    assert 'data-testid="hero-cta-signup"' in no_scripts, (
        "Hero CTA must be in the rendered HTML, not generated by JS (D11)"
    )


# --------------------------------------------------------------------------- #
# D12 — runCalc fetch is guarded on auth state                                 #
# --------------------------------------------------------------------------- #


def test_d12_anon_landing_marks_root_as_unauthenticated():
    """Anon root container must carry data-user-authenticated="0"."""
    html = _render_landing(authenticated=False)
    assert 'data-user-authenticated="0"' in html, (
        "Anon landing must expose data-user-authenticated=\"0\" "
        "on the .x8a-landing root (D12)"
    )
    assert 'data-user-authenticated="1"' not in html


def test_d12_authed_landing_marks_root_as_authenticated():
    """Authed root container must carry data-user-authenticated="1"."""
    html = _render_landing(authenticated=True)
    assert 'data-user-authenticated="1"' in html, (
        "Authed landing must expose data-user-authenticated=\"1\" "
        "on the .x8a-landing root (D12)"
    )
    assert 'data-user-authenticated="0"' not in html


def test_d12_runcalc_short_circuits_for_anon_in_js():
    """The inline JS must contain the auth-guard branch and localApprox."""
    html = _render_landing(authenticated=False)
    assert "IS_AUTHENTICATED" in html, (
        "Inline JS must declare IS_AUTHENTICATED for the runCalc guard"
    )
    assert "localApprox" in html, (
        "Inline JS must define localApprox() for anon client-side compute"
    )
    # The guard pattern: `if (!IS_AUTHENTICATED) { applyCalc(localApprox(...)); ... return; }`
    guard_pattern = re.search(
        r"if\s*\(\s*!IS_AUTHENTICATED\s*\)\s*\{[^}]*localApprox",
        html,
    )
    assert guard_pattern, (
        "runCalc must short-circuit to localApprox when !IS_AUTHENTICATED"
    )


def test_d12_localapprox_does_not_fetch():
    """localApprox must contain NO fetch() calls — it's pure client compute."""
    html = _render_landing(authenticated=False)
    # Extract the localApprox function body
    match = re.search(
        r"function\s+localApprox\s*\([^)]*\)\s*\{(.+?)\n\s*\}",
        html,
        flags=re.DOTALL,
    )
    assert match, "localApprox function must exist in the inline JS"
    body = match.group(1)
    assert "fetch(" not in body, (
        "localApprox must not call fetch() — it's pure client-side math (D12)"
    )
