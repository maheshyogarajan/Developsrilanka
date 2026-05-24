"""
Tier D3 (2026-05-24) — public FAQ + sitemap + admin gating tests.

Spec checks:
  1. Anonymous GET /help returns 200 and only lists published rows.
  2. Anonymous GET /sitemap.xml lists published URLs only (no drafts).
  3. POST /admin/faq/<id>/publish toggles is_published when admin.
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_entry(app, **kwargs):
    """Insert one FAQEntry and return its id. Caller controls slug to
    avoid uniqueness collisions across tests."""
    from app import db
    from faq_models import FAQEntry
    defaults = {
        "slug": kwargs.pop("slug"),
        "question": kwargs.pop("question", "Test question?"),
        "answer": kwargs.pop("answer", "Test answer."),
        "category": kwargs.pop("category", "general"),
        "source": kwargs.pop("source", "manual"),
        "is_published": kwargs.pop("is_published", False),
    }
    with app.app_context():
        row = FAQEntry(**defaults)
        db.session.add(row)
        db.session.commit()
        return row.id


# --------------------------------------------------------------------------- #
# 1) /help renders published; drafts hidden
# --------------------------------------------------------------------------- #
def test_help_index_anonymous_returns_200_and_lists_only_published(
    app, client, cleanup_faqs,
):
    published_id = _make_entry(
        app,
        slug="d3-test-published-q",
        question="How do I file my Sri Lankan return?",
        answer="You file via IRD's e-filing portal.",
        category="filing",
        is_published=True,
    )
    draft_id = _make_entry(
        app,
        slug="d3-test-draft-q",
        question="What is APIT?",
        answer="Draft only — not for public consumption.",
        category="deductions",
        is_published=False,
    )

    resp = client.get("/help")
    assert resp.status_code == 200, (
        f"Expected 200; got {resp.status_code}. Body: {resp.data[:300]!r}"
    )
    body = resp.data.decode("utf-8", errors="replace")
    # Published shows up.
    assert "How do I file my Sri Lankan return?" in body, (
        "Published entry's question was not rendered on /help"
    )
    # Draft must NOT show up.
    assert "What is APIT?" not in body, (
        "Draft entry leaked onto the public /help index — gate broken"
    )
    # Schema.org marker proves the FAQPage JSON-LD is embedded.
    assert "FAQPage" in body, (
        "Schema.org FAQPage JSON-LD missing from /help response — SEO regression"
    )

    # Cleanup is handled by `cleanup_faqs`.
    assert published_id and draft_id  # silence unused-var lint


# --------------------------------------------------------------------------- #
# 2) Sitemap lists published only, in valid XML
# --------------------------------------------------------------------------- #
def test_sitemap_xml_lists_published_urls_only(
    app, client, cleanup_faqs,
):
    pub_id = _make_entry(
        app,
        slug="d3-test-sitemap-published",
        question="Sitemap published?",
        is_published=True,
    )
    draft_id = _make_entry(
        app,
        slug="d3-test-sitemap-draft",
        question="Sitemap draft?",
        is_published=False,
    )

    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200, (
        f"Expected 200; got {resp.status_code}. Body: {resp.data[:300]!r}"
    )
    assert resp.mimetype == "application/xml", (
        f"Expected mimetype application/xml; got {resp.mimetype!r}"
    )
    body = resp.data.decode("utf-8", errors="replace")
    assert "<urlset" in body and "</urlset>" in body, (
        "Sitemap response is not a valid <urlset> envelope"
    )
    # Published slug present, draft slug absent.
    assert "/help/d3-test-sitemap-published" in body, (
        "Published entry missing from sitemap.xml"
    )
    assert "/help/d3-test-sitemap-draft" not in body, (
        "Draft slug leaked into public sitemap.xml — bot will index it"
    )
    # Top-level /help index always present.
    assert "/help</loc>" in body or "/help<" in body, (
        "Top-level /help URL missing from sitemap.xml"
    )
    assert pub_id and draft_id  # silence unused-var lint


# --------------------------------------------------------------------------- #
# 3) Admin publish toggle requires admin (anonymous gets gated)
# --------------------------------------------------------------------------- #
def test_admin_publish_blocked_for_anonymous(app, client, cleanup_faqs):
    """The /admin/faq surface is admin_required. An anonymous POST must
    NOT flip is_published. We accept any of {302 redirect-to-login,
    401 unauthorized, 403 forbidden} — all three are valid expressions
    of the admin gate, and the production decorator emits 302 to /login
    for unauthenticated HTML callers."""
    entry_id = _make_entry(
        app,
        slug="d3-test-admin-gate-target",
        question="Should anonymous flip me?",
        is_published=False,
    )

    resp = client.post(
        f"/admin/faq/{entry_id}/publish",
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302, 401, 403), (
        f"Anonymous bypassed admin gate; got {resp.status_code}. "
        f"Body: {resp.data[:200]!r}"
    )

    # Verify the row was NOT mutated despite the POST.
    from faq_models import FAQEntry
    with app.app_context():
        row = FAQEntry.query.get(entry_id)
        assert row is not None
        assert row.is_published is False, (
            "Anonymous POST flipped is_published — admin gate broken"
        )
