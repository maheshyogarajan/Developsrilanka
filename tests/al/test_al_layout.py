"""C4 Day-0 P0 regression — /fie/al layout shell.

Locks the contract introduced 2026-05-27:

  1. /fie/al returns 200 for an authenticated user (no DB rows required).
  2. The shell (.fiesta-shell + .fiesta-layout) wraps the page.
  3. The A&L "Assets & Liabilities" h1 + "+ Add Entry" button are present
     in the rendered HTML (the symptoms in audit C4 reported that the
     content was empty above the fold — these assertions guard against
     the page rendering an empty <main>).
  4. The scoped <style> block from the template's head_extra slot is
     actually emitted into <head> (audit root cause: it was assigned to
     `additional_styles`, a block layout_fiesta.html does not define, so
     Jinja silently dropped it). Looking for the .al-page CSS rule in
     the rendered HTML is the direct check.
"""
from __future__ import annotations

import pytest


def login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_fie_al_returns_200_for_authenticated_user(client, user_factory):
    u = user_factory("returns_200")
    login_as(client, u)
    resp = client.get("/fie/al")
    assert resp.status_code == 200, \
        f"GET /fie/al -> {resp.status_code} (expected 200)"


def test_fie_al_renders_inside_fiesta_shell(client, user_factory):
    """The shell anchors must be present so the .fiesta-layout grid runs."""
    u = user_factory("shell_present")
    login_as(client, u)
    body = client.get("/fie/al").get_data(as_text=True)
    assert 'class="fiesta-shell"' in body, \
        "page must render inside body.fiesta-shell"
    assert 'class="fiesta-layout"' in body, \
        ".fiesta-layout grid container must wrap sidebar + main"
    # The .fiesta-main main element is where {% block content %} lands.
    assert 'class="fiesta-main"' in body, \
        ".fiesta-main slot must be rendered (no content = no slot)"


def test_fie_al_main_content_is_not_empty(client, user_factory):
    """The audit symptom: empty main content above the fold. This test
    locks the positive contract — the page must contain both the hero
    h1 AND the primary action button."""
    u = user_factory("main_content")
    login_as(client, u)
    body = client.get("/fie/al").get_data(as_text=True)
    assert "Assets &amp; Liabilities" in body, \
        "hero h1 (Assets & Liabilities) must be present"
    assert "+ Add Entry" in body, \
        "primary CTA button (+ Add Entry) must be present"
    assert "Part A" in body or "Part A &mdash; Assets" in body or "Part A — Assets" in body, \
        "Part A (assets section) heading must be present"
    assert "Part B" in body, "Part B (liabilities section) heading must be present"


def test_fie_al_head_extra_block_emits_template_styles(client, user_factory):
    """Root-cause regression for C4: the template's scoped <style> block
    was bound to `additional_styles` which layout_fiesta.html does not
    define — silently dropped. Renaming to `head_extra` (the block the
    shell DOES define) restores the emit. We assert one of the
    template-scoped class selectors (.al-page) lands in the response
    body, which proves the head_extra block reached the page."""
    u = user_factory("head_extra")
    login_as(client, u)
    body = client.get("/fie/al").get_data(as_text=True)
    assert ".al-page" in body, \
        "scoped A&L stylesheet must be emitted into <head> via head_extra"
    assert ".al-hero" in body, "A&L hero CSS rule must be emitted"
    assert ".al-net-worth" in body, "A&L net-worth CSS rule must be emitted"


def test_fie_al_edit_page_also_extends_shell(client, user_factory):
    """The edit form has the same block-name bug — confirm it's fixed too."""
    u = user_factory("edit_shell")
    login_as(client, u)
    resp = client.get("/fie/al/edit")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="fiesta-shell"' in body
    assert 'class="fiesta-main"' in body
