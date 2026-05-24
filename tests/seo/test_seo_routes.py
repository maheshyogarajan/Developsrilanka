"""
Tier D6 / A4 slice 1 — SEO + Article engine route tests (2026-05-24).

Spec checks (per the dispatched-task ledger):
  - test_sitemap_returns_xml_200
  - test_sitemap_lists_all_articles
  - test_robots_txt_renders
  - test_article_index_lists_articles
  - test_article_detail_renders_with_structured_data
  - test_article_canonical_url_present
  - test_article_og_tags_present
  - test_article_faq_renders_jsonld_when_present

Plus a few defensive tests:
  - 404 on unknown slug
  - article loader survives a malformed file
  - markdown_lite escapes HTML in source
"""
from __future__ import annotations

import json
import re

import pytest


# --------------------------------------------------------------------------- #
# Sitemap
# --------------------------------------------------------------------------- #
def test_sitemap_returns_xml_200(client):
    """/sitemap.xml -> 200 with application/xml content type and a valid
    urlset root."""
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200, (
        f"Expected 200; got {resp.status_code}. Body: {resp.data[:300]!r}"
    )
    ct = resp.headers.get("Content-Type", "")
    assert "application/xml" in ct, f"Wrong Content-Type: {ct!r}"
    body = resp.data.decode("utf-8", errors="replace")
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>'), (
        "Sitemap missing XML prologue"
    )
    assert (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in body
    ), "Sitemap missing urlset root"


def test_sitemap_lists_all_articles(client):
    """Every shipped article must appear in the sitemap as a <loc>."""
    from seo_routes import get_all_articles
    articles = get_all_articles()
    assert articles, (
        "No articles loaded — the loader can't find content/articles/*.md"
    )

    resp = client.get("/sitemap.xml")
    body = resp.data.decode("utf-8", errors="replace")

    for art in articles:
        slug_path = f"/articles/{art['slug']}"
        assert slug_path in body, (
            f"Article slug {slug_path!r} not in sitemap. "
            "Sitemap-loader integration is broken."
        )

    # Landing + articles index should also be present.
    assert "<loc>" in body
    assert ">/articles<" in body or "/articles</loc>" in body
    # And the FAQ surface should still be there (sitemap is a union).
    assert "/help" in body


def test_sitemap_cache_header(client):
    """The sitemap should be cacheable for an hour — sets Cache-Control."""
    resp = client.get("/sitemap.xml")
    cc = resp.headers.get("Cache-Control", "")
    assert "max-age=3600" in cc, (
        f"Sitemap Cache-Control missing/wrong: {cc!r}"
    )


# --------------------------------------------------------------------------- #
# Robots.txt
# --------------------------------------------------------------------------- #
def test_robots_txt_renders(client):
    """/robots.txt -> 200 with text/plain content type, references the
    sitemap, and disallows internal admin/api surface."""
    resp = client.get("/robots.txt")
    assert resp.status_code == 200, f"Got {resp.status_code}"
    ct = resp.headers.get("Content-Type", "")
    assert "text/plain" in ct, f"Wrong Content-Type: {ct!r}"
    body = resp.data.decode("utf-8")
    assert "User-agent: *" in body, "robots.txt missing User-agent line"
    assert "Sitemap:" in body, "robots.txt does not reference sitemap"
    assert "/sitemap.xml" in body, "robots.txt sitemap URL missing"
    assert "Disallow: /admin/" in body
    assert "Disallow: /api/" in body
    # Should NOT block the marketing surface.
    assert "Disallow: /articles" not in body, (
        "robots.txt accidentally blocks the article index"
    )


# --------------------------------------------------------------------------- #
# Article index
# --------------------------------------------------------------------------- #
def test_article_index_lists_articles(client):
    """/articles -> 200 with each shipped article title rendered."""
    from seo_routes import get_all_articles
    articles = get_all_articles()
    assert articles, "No articles to test against"

    resp = client.get("/articles")
    assert resp.status_code == 200, f"Got {resp.status_code}"
    body = resp.data.decode("utf-8", errors="replace")

    for art in articles:
        assert art["title"] in body, (
            f"Article title {art['title']!r} not on /articles page"
        )
        # Each card links to the detail URL.
        href = f'href="/articles/{art["slug"]}"'
        assert href in body, f"Missing card link {href!r}"


def test_article_index_has_organization_schema(client):
    """The index page emits Organization JSON-LD via _seo_macros."""
    resp = client.get("/articles")
    body = resp.data.decode("utf-8", errors="replace")
    # Find all JSON-LD blocks
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        body, re.DOTALL,
    )
    assert blocks, "No JSON-LD blocks on /articles"
    types_found = set()
    for b in blocks:
        try:
            payload = json.loads(b)
        except Exception:
            continue
        types_found.add(payload.get("@type"))
    assert "Organization" in types_found, (
        f"No Organization schema on /articles. Found: {types_found}"
    )
    assert "BreadcrumbList" in types_found, (
        f"No BreadcrumbList schema on /articles. Found: {types_found}"
    )


# --------------------------------------------------------------------------- #
# Article detail
# --------------------------------------------------------------------------- #
def _pick_pilot_slug():
    """Return the slug of one of the two shipped pilot articles. Used by
    detail tests below."""
    from seo_routes import get_all_articles
    arts = get_all_articles()
    assert arts, "Test setup error: no articles loaded"
    # Prefer the foreign-income pilot (has FAQ).
    for a in arts:
        if a["slug"] == "how-sri-lankans-abroad-pay-tax-on-foreign-income":
            return a["slug"]
    return arts[0]["slug"]


def test_article_detail_returns_200(client):
    slug = _pick_pilot_slug()
    resp = client.get(f"/articles/{slug}")
    assert resp.status_code == 200, (
        f"Detail page {slug} returned {resp.status_code}"
    )


def test_article_detail_404_on_unknown_slug(client):
    resp = client.get("/articles/does-not-exist-nope-not-real")
    assert resp.status_code == 404


def test_article_detail_renders_with_structured_data(client):
    """Detail page must emit Article + BreadcrumbList JSON-LD."""
    slug = _pick_pilot_slug()
    resp = client.get(f"/articles/{slug}")
    body = resp.data.decode("utf-8", errors="replace")

    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        body, re.DOTALL,
    )
    assert blocks, "No JSON-LD blocks on detail page"

    types_found = set()
    article_payload = None
    for b in blocks:
        try:
            payload = json.loads(b)
        except Exception:
            continue
        if payload.get("@type") == "Article":
            article_payload = payload
        types_found.add(payload.get("@type"))

    assert "Article" in types_found, (
        f"No Article schema. Found: {types_found}"
    )
    assert "BreadcrumbList" in types_found, (
        f"No BreadcrumbList schema. Found: {types_found}"
    )

    # Article payload must have the essentials.
    assert article_payload is not None
    assert article_payload.get("headline"), "Article schema missing headline"
    assert article_payload.get("datePublished"), "Article schema missing datePublished"
    assert article_payload.get("author"), "Article schema missing author"
    assert article_payload.get("publisher"), "Article schema missing publisher"
    assert article_payload.get("url"), "Article schema missing url"
    assert article_payload.get("wordCount", 0) > 0


def test_article_canonical_url_present(client):
    """Detail page must have a canonical <link>."""
    slug = _pick_pilot_slug()
    resp = client.get(f"/articles/{slug}")
    body = resp.data.decode("utf-8", errors="replace")
    pattern = r'<link\s+rel="canonical"\s+href="[^"]*/articles/' + re.escape(slug) + r'"'
    assert re.search(pattern, body), (
        f"Canonical link for /articles/{slug} missing. Body head: {body[:600]!r}"
    )


def test_article_og_tags_present(client):
    """Detail page must have og:title, og:description, og:type=article,
    og:url, twitter:card."""
    slug = _pick_pilot_slug()
    resp = client.get(f"/articles/{slug}")
    body = resp.data.decode("utf-8", errors="replace")

    assert re.search(r'<meta\s+property="og:title"\s+content="[^"]+"', body), (
        "og:title missing"
    )
    assert re.search(
        r'<meta\s+property="og:description"\s+content="[^"]+"', body
    ), "og:description missing"
    assert re.search(
        r'<meta\s+property="og:type"\s+content="article"', body
    ), "og:type=article missing (should be 'article' for article pages)"
    assert re.search(
        r'<meta\s+property="og:url"\s+content="[^"]*/articles/'
        + re.escape(slug) + r'"',
        body,
    ), "og:url for article missing"
    assert re.search(
        r'<meta\s+name="twitter:card"\s+content="(summary|summary_large_image)"',
        body,
    ), "twitter:card missing"


def test_article_faq_renders_jsonld_when_present(client):
    """An article with `faq` frontmatter must emit a FAQPage JSON-LD block."""
    # Use the foreign-income pilot — it has 6 FAQ items.
    slug = "how-sri-lankans-abroad-pay-tax-on-foreign-income"
    resp = client.get(f"/articles/{slug}")
    assert resp.status_code == 200, (
        f"Pilot article missing — got {resp.status_code}"
    )
    body = resp.data.decode("utf-8", errors="replace")

    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        body, re.DOTALL,
    )
    faq_payload = None
    for b in blocks:
        try:
            payload = json.loads(b)
        except Exception:
            continue
        if payload.get("@type") == "FAQPage":
            faq_payload = payload
            break

    assert faq_payload is not None, (
        "FAQPage JSON-LD missing from article that has frontmatter FAQ"
    )
    questions = faq_payload.get("mainEntity", [])
    assert len(questions) >= 4, (
        f"FAQPage has too few questions: {len(questions)}"
    )
    for q in questions:
        assert q.get("@type") == "Question"
        assert q.get("name"), "Question missing name (the question text)"
        ans = q.get("acceptedAnswer", {})
        assert ans.get("@type") == "Answer"
        assert ans.get("text"), "Answer missing text"

    # The HTML FAQ section also renders (via <details>/<summary>).
    assert 'class="article-faq"' in body, "FAQ HTML section not rendered"


# --------------------------------------------------------------------------- #
# Loader + converter behaviour
# --------------------------------------------------------------------------- #
def test_loader_picks_up_both_pilots():
    from seo_routes import get_all_articles
    arts = get_all_articles()
    slugs = {a["slug"] for a in arts}
    assert "how-sri-lankans-abroad-pay-tax-on-foreign-income" in slugs
    assert "sri-lanka-foreign-income-tax-2025-26-deadlines-rates" in slugs


def test_loader_skips_malformed_file(tmp_path, monkeypatch):
    """A .md file with no frontmatter should be skipped, not crash the index."""
    import seo_routes
    from pathlib import Path

    fake_dir = tmp_path / "articles"
    fake_dir.mkdir()
    # Good file
    (fake_dir / "good.md").write_text(
        "---\ntitle: Good\nslug: good\ndate: 2026-01-01\nsummary: ok\n---\nbody",
        encoding="utf-8",
    )
    # Bad file (no frontmatter)
    (fake_dir / "bad.md").write_text("just text, no frontmatter", encoding="utf-8")

    monkeypatch.setattr(seo_routes, "_ARTICLES_DIR", Path(fake_dir))
    seo_routes._reload_articles()
    try:
        arts = seo_routes.get_all_articles()
        slugs = {a["slug"] for a in arts}
        assert "good" in slugs
        assert "bad" not in slugs
    finally:
        # Reset for other tests in the session.
        from pathlib import Path as _P
        monkeypatch.setattr(
            seo_routes,
            "_ARTICLES_DIR",
            _P(__file__).resolve().parents[2] / "content" / "articles",
        )
        seo_routes._reload_articles()


def test_markdown_lite_escapes_raw_html():
    from markdown_lite import md_to_html
    out = md_to_html("This is <script>alert('xss')</script> not safe.")
    assert "<script>" not in out, "Raw <script> leaked through markdown_lite"
    assert "&lt;script&gt;" in out, "<script> was not HTML-escaped"


def test_markdown_lite_rejects_javascript_links():
    from markdown_lite import md_to_html
    out = md_to_html("[click](javascript:alert(1))")
    assert "<a" not in out, "javascript: URL became a real <a> tag — XSS risk"
    # It should fall through to literal-text rendering.
    assert "click" in out
