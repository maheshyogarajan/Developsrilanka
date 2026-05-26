"""Phase C Wave 2 / F1.4 — anon landing chrome regression suite.

Locks the contract that the anonymous `/` landing page (s0_landing.html)
renders WITHOUT the legacy bookkeeping sidebar chrome. The bookkeeping
sidebar is the multi-section navigation rendered by layout.html for
authenticated users (Cash In / Cash Out / Accounts / etc.). Anon visitors
to `/` are pre-signup and must not see a bookkeeping nav — that surface
is owned by the FIESTA hub post-auth.

Earlier mitigations hid the sidebar via CSS (`body.is-anonymous .sidebar
{ display: none !important; }`), but the markup was still emitted. The
current contract is stronger: the sidebar `<aside class="sidebar">` and
its inner submenu links must NOT be present in the anon `/` DOM at all.

We assert against two signals:
  1. The `<aside ... class="sidebar"...>` element is absent.
  2. The bookkeeping submenu link texts (Cash In, Cash Out, Accounts,
     Bank Statements, Organizations) are absent — these are the legacy
     navigation entries that betray the sidebar even if the wrapper
     element name changes.

We also lock the positive signals so we know the page actually rendered
(not 500'd silently): a FIESTA marker class + the canonical hero headline.
"""
from __future__ import annotations

import re


# Legacy bookkeeping sidebar nav labels. These appear only inside the
# authed sidebar's <ul class="sidebar-menu"> in layout.html. If any of
# them appear in an anon `/` body, the sidebar leaked.
BOOKKEEPING_NAV_LABELS = [
    ">Cash Out<",
    ">Cash In<",
    ">Accounts<",
    ">Bank Statements<",
    ">Chart of Accounts<",
    ">Profit & Loss<",
    ">Asset Register<",
    ">Journal Entries<",
]


def _get_anon_landing(client):
    """GET / as an anonymous (logged-out) client."""
    r = client.get("/")
    assert r.status_code == 200, (
        f"GET / returned {r.status_code} (expected 200). "
        f"Body preview: {r.get_data(as_text=True)[:400]!r}"
    )
    return r.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Positive checks — the page rendered the s0 landing, not a 500 / redirect.
# --------------------------------------------------------------------------- #


def test_f1_4_anon_landing_renders_200(client):
    """Smoke: anonymous GET / must return 200 with the s0 landing body.
    We assert on a stable marker (the `x8a-landing` wrapper class on
    s0_landing.html) so the suite catches accidental redirects."""
    body = _get_anon_landing(client)
    assert "x8a-landing" in body, (
        "Anon GET / did not render templates/fiesta_public/s0_landing.html. "
        "Either the home view dispatched to a different template or the "
        "landing template's wrapper class drifted."
    )


def test_f1_4_anon_landing_carries_unified_hero(client):
    """Defence-in-depth tie-in to F1.2: anon / must include the shared
    hero partial (marker class `fiesta-hero-unified`)."""
    body = _get_anon_landing(client)
    assert "fiesta-hero-unified" in body, (
        "Anon GET / is missing the `fiesta-hero-unified` marker class. "
        "Either the F1.2 hero include broke, or the F1.4 sidebar removal "
        "broke template inheritance."
    )


def test_f1_4_anon_landing_carries_data_anon_marker(client):
    """The s0 landing wrapper must carry `data-anon-landing="1"` for
    anonymous visitors. The belt-and-braces CSS in s0_landing.html keys
    off this attribute to hide any leaked sidebar/bookkeeping chrome
    via `body:has(.x8a-landing[data-anon-landing="1"]) .sidebar ...`.
    Without the marker, the defence layer does nothing."""
    body = _get_anon_landing(client)
    assert 'data-anon-landing="1"' in body, (
        "Anon GET / wrapper is missing `data-anon-landing=\"1\"`. The "
        "F1.4 second-wall CSS defence keys off this attribute — removing "
        "it disables the sidebar-suppression backstop."
    )


# --------------------------------------------------------------------------- #
# Negative checks — the legacy bookkeeping sidebar MUST NOT render for anon.
# --------------------------------------------------------------------------- #


def test_f1_4_anon_landing_has_no_sidebar_aside(client):
    """The `<aside ... class="sidebar"...>` element renders for authed
    users (layout.html lines ~408-662). For anon visitors it is gated
    behind `{% if current_user.is_authenticated %}` and must NOT appear
    in the DOM. We match the opening tag with a tolerant regex so
    attribute-order shuffles do not bypass the check."""
    body = _get_anon_landing(client)
    sidebar_aside_re = re.compile(
        r'<aside\b[^>]*class\s*=\s*"[^"]*\bsidebar\b[^"]*"',
        re.IGNORECASE,
    )
    match = sidebar_aside_re.search(body)
    assert match is None, (
        "Anon GET / body contains a legacy <aside class=\"sidebar\"> "
        "element. The bookkeeping sidebar must not render for anonymous "
        f"visitors. First match: {match.group(0) if match else None!r}"
    )


def test_f1_4_anon_landing_has_no_sidebar_overlay(client):
    """The mobile sidebar overlay (`<div class="sidebar-overlay">`) is
    rendered only inside the authed branch in layout.html. Anon body must
    not contain it."""
    body = _get_anon_landing(client)
    assert 'class="sidebar-overlay"' not in body, (
        "Anon GET / body contains `class=\"sidebar-overlay\"` — the "
        "legacy mobile-sidebar overlay leaked into the anonymous "
        "landing DOM."
    )


def test_f1_4_anon_landing_has_no_bookkeeping_nav_labels(client):
    """Even if the <aside> wrapper changes name, the bookkeeping nav
    labels (Cash In / Cash Out / Accounts / Bank Statements / submenu
    entries) must not appear in the anon body. We anchor each label to
    `>label<` to scope the match to inner-text positions."""
    body = _get_anon_landing(client)
    leaked = [label.strip("><") for label in BOOKKEEPING_NAV_LABELS if label in body]
    assert not leaked, (
        f"Anon GET / body contains bookkeeping nav labels: {leaked!r}. "
        "These are the legacy sidebar's submenu entries — their presence "
        "means the sidebar markup is rendering for anon visitors."
    )


def test_f1_4_anon_landing_has_no_mobile_header(client):
    """The authed `<header class="mobile-header">` should also be
    suppressed for anon (it includes the sidebar-toggle button which
    would do nothing without the sidebar)."""
    body = _get_anon_landing(client)
    mobile_header_re = re.compile(
        r'<header\b[^>]*class\s*=\s*"[^"]*\bmobile-header\b[^"]*"',
        re.IGNORECASE,
    )
    match = mobile_header_re.search(body)
    assert match is None, (
        "Anon GET / body contains a `<header class=\"mobile-header\">` "
        "element. The mobile sidebar toggle is meaningful only for "
        f"authed users. First match: {match.group(0) if match else None!r}"
    )


# --------------------------------------------------------------------------- #
# D3 (2026-05-27) — main-header page-title h1 must be suppressed for anon.
# --------------------------------------------------------------------------- #


def test_d3_anon_landing_has_no_main_header(client):
    """The authed bookkeeping `<header class="main-header">` block in
    layout.html carries a `<h1 class="page-title">` hard-coded to "Home"
    for `request.path == '/'`. Before D3, that h1 rendered BEFORE the
    unified hero h1 from `_hero_partial.html`, so any DOM-order H1 reader
    captured "Home" instead of the canonical "Cut your tax bill. Keep
    the records clean." headline. The fix gates the whole main-header
    behind `current_user.is_authenticated`."""
    body = _get_anon_landing(client)
    main_header_re = re.compile(
        r'<header\b[^>]*class\s*=\s*"[^"]*\bmain-header\b[^"]*"',
        re.IGNORECASE,
    )
    match = main_header_re.search(body)
    assert match is None, (
        "Anon GET / body contains a `<header class=\"main-header\">` "
        "element. The bookkeeping page-title h1 inside it competes with "
        "the unified hero h1 from `_hero_partial.html` and was the H1 "
        "the customer-flow audit captured as \"Home\" on 2026-05-26. "
        f"First match: {match.group(0) if match else None!r}"
    )


def test_d3_anon_landing_hero_h1_is_first_h1(client):
    """Stronger D3 assertion: the FIRST `<h1>` element in the anon GET /
    DOM must be the unified hero headline. If a future regression
    re-introduces a chrome h1 above the hero, this test catches it
    before the audit does."""
    body = _get_anon_landing(client)
    first_h1_re = re.compile(r'<h1\b[^>]*>(.*?)</h1>', re.IGNORECASE | re.DOTALL)
    match = first_h1_re.search(body)
    assert match is not None, "Anon GET / has no <h1> element at all."
    first_h1_inner = match.group(1)
    assert "Cut your tax bill." in first_h1_inner, (
        f"First <h1> on anon GET / is not the canonical hero headline. "
        f"Inner text: {first_h1_inner!r}. Expected the substring "
        f"'Cut your tax bill.' from `_hero_partial.html`."
    )


# --------------------------------------------------------------------------- #
# F1.7 (P2 polish, 2026-05-27) — legacy /preview orphan hidden from anon nav.
#
# /preview is a legacy receipt-preview route (app.py:1685). Route still
# resolves (deep-link backstop), but it must not surface in any anon nav
# chrome or sidebar — first-time visitors get distracted by an orphan
# entry that leads to a half-built receipt-preview flow.
#
# The protection lives in templates/layout.html L429 (`E1 F1.7` comment)
# where the anon sidebar menu intentionally OMITS a Preview <li>. This
# test pins that protection so any future "let me just add a Preview
# link back" PR fails before merge.
# --------------------------------------------------------------------------- #


def test_f1_7_anon_landing_has_no_preview_nav_link(client):
    """The anon `GET /` body must contain NO `<a href="/preview">` link.
    The /preview route itself remains (deep-link compatibility) — we are
    only forbidding the navigation surface from advertising it.

    Two patterns to forbid:
      - `href="/preview"` literal (a static-link regression)
      - `url_for('preview')` is server-rendered into the same literal —
        catching the literal catches both.

    We DO allow `/preview/calc`, `/preview/scan`, etc. (the JSON API
    endpoints called from JS) because they are not user-clickable links.
    The regex anchors on `/preview"` to scope only the bare orphan."""
    body = _get_anon_landing(client)
    # Catch href="/preview" and href='/preview' with the trailing quote
    # so /preview/calc + /preview/scan (API endpoints) don't false-match.
    orphan_link_re = re.compile(
        r'''href\s*=\s*["']/preview["']''',
        re.IGNORECASE,
    )
    matches = orphan_link_re.findall(body)
    assert not matches, (
        "Anon GET / body contains a link to the orphan /preview route. "
        "/preview is a legacy receipt-preview surface and must not appear "
        "in anon nav per F1.7. The route itself still resolves for deep "
        "links — only the navigation entry is forbidden. "
        f"Matches: {matches!r}"
    )


def test_f1_7_anon_landing_has_no_preview_nav_text(client):
    """Belt-and-braces: the anon nav must not carry the literal nav-text
    "Preview" with adjacent menu-link markup. We anchor on the unique
    construction `>Preview</a>` to scope to the nav-text position; the
    word "preview" in body-copy contexts (e.g. "Tax preview") is fine."""
    body = _get_anon_landing(client)
    # Match `>Preview</a>` exactly — the orphan link's inner text was
    # always the bare word "Preview" in layout.html.bak L213/L278.
    # We tolerate trailing whitespace/newlines inside the anchor.
    preview_text_re = re.compile(
        r">\s*Preview\s*</a>",
        re.IGNORECASE,
    )
    matches = preview_text_re.findall(body)
    assert not matches, (
        "Anon GET / contains a `>Preview</a>` nav-text occurrence. The "
        "F1.7 orphan-hide contract forbids any anon nav entry whose "
        "inner text is the bare word \"Preview\". "
        f"Matches: {matches!r}"
    )
