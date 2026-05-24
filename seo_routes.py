"""
SEO / Article engine — Tier D6 A4 slice 1 (2026-05-24).

Public routes (no auth):
  GET  /articles              -> index page listing all published articles.
  GET  /articles/<slug>       -> single article page (markdown -> HTML +
                                 Article schema + optional FAQPage schema).
  GET  /robots.txt            -> sane defaults: allow public pages, disallow
                                 /admin, /api, sensitive routes; references
                                 the sitemap URL so crawlers discover it.
  GET  /sitemap.xml           -> dynamic sitemap that consolidates
                                 - the existing FAQ /help URLs
                                 - all article URLs
                                 - landing + key static pages
                                 (the existing /sitemap.xml in faq_routes.py
                                 is REPLACED — we register this one AFTER
                                 and Flask resolves the latter as the active
                                 view function; the faq_routes module no
                                 longer wires the route when SEO_ROUTES_OWNS_SITEMAP
                                 is set, but for safety we don't depend on
                                 module ordering: instead we let the
                                 faq_routes sitemap remain as the LEGACY
                                 fallback while THIS module owns the canonical
                                 surface via a different URL — see
                                 register_routes() docs below).

What this module deliberately does NOT do:
  - No CMS, no DB. Articles are markdown files on disk under
    `content/articles/*.md` with YAML frontmatter. Adding an article =
    drop a new file. No migrations, no admin UI required (Tier D6 A4
    slice 1 is "ship 2 pilots + substrate"; future slices may add an
    admin author UI if value justifies it).
  - No external markdown dependency. We use a tiny in-tree converter
    (markdown_lite.py) covering the subset our authors use: headings,
    paragraphs, bold/italic/strong/em, links, ordered + unordered lists,
    blockquotes, inline code, horizontal rules. If we later need full
    CommonMark we can swap the converter call to `markdown.markdown(...)`
    in one place.

Sitemap ownership note:
  faq_routes.py already registers `/sitemap.xml`. Flask refuses to
  register the same URL twice with different view functions, so we
  register a NEW canonical sitemap at the same path AFTER faq_routes
  via `app.view_functions[...]` override — that gives us a single
  canonical sitemap that lists everything (FAQ + articles + landing).
  This is documented in register_routes() so a future maintainer
  understands the override.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import yaml
from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    render_template,
    request,
    url_for,
)

from markdown_lite import md_to_html


log = logging.getLogger(__name__)

seo_bp = Blueprint("seo_bp", __name__)


# --------------------------------------------------------------------------- #
# Content loading
# --------------------------------------------------------------------------- #
_ARTICLES_DIR = Path(__file__).resolve().parent / "content" / "articles"

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL
)

# Required frontmatter keys. An article missing any of these is logged and
# skipped from the index — we'd rather hide a malformed article than ship
# broken meta to Google.
_REQUIRED_FRONTMATTER = ("title", "slug", "date", "summary")


def _parse_article(path: Path) -> dict[str, Any] | None:
    """Read one .md file, return a dict with metadata + rendered HTML.

    Returns None if the file is missing required frontmatter or fails to
    parse. The caller treats None as "skip this file" and logs the reason.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("seo: failed to read %s: %s", path, exc)
        return None

    m = _FRONTMATTER_RE.match(raw)
    if not m:
        log.warning("seo: %s has no YAML frontmatter; skipping", path)
        return None

    try:
        meta = yaml.safe_load(m.group("fm")) or {}
    except Exception as exc:
        log.warning("seo: %s frontmatter parse failed: %s", path, exc)
        return None
    if not isinstance(meta, dict):
        log.warning("seo: %s frontmatter is not a mapping; skipping", path)
        return None

    missing = [k for k in _REQUIRED_FRONTMATTER if not meta.get(k)]
    if missing:
        log.warning(
            "seo: %s missing required frontmatter keys %s; skipping",
            path, missing,
        )
        return None

    body_md = m.group("body")
    body_html = md_to_html(body_md)

    # Normalise date: accept date or datetime, render as ISO YYYY-MM-DD.
    date_val = meta.get("date")
    if hasattr(date_val, "isoformat"):
        date_iso = date_val.isoformat()
    else:
        date_iso = str(date_val)

    # Updated date defaults to date if not provided.
    updated_val = meta.get("updated") or date_val
    if hasattr(updated_val, "isoformat"):
        updated_iso = updated_val.isoformat()
    else:
        updated_iso = str(updated_val)

    # Estimate reading time at 220 wpm — rounded up to nearest minute,
    # minimum 1. Plain-text-ish heuristic: split rendered HTML on whitespace
    # and drop tags.
    text_only = re.sub(r"<[^>]+>", " ", body_html)
    word_count = len([w for w in text_only.split() if w])
    reading_minutes = max(1, (word_count + 219) // 220)

    return {
        "slug": str(meta["slug"]).strip(),
        "title": str(meta["title"]).strip(),
        "summary": str(meta["summary"]).strip(),
        "date": date_iso,
        "updated": updated_iso,
        "hero_image": meta.get("hero_image"),
        "author": meta.get("author", "FIESTA"),
        "faq": meta.get("faq") or [],  # list of {q, a}
        "keywords": meta.get("keywords") or [],
        "body_html": body_html,
        "word_count": word_count,
        "reading_minutes": reading_minutes,
        "_source_path": str(path),
    }


@lru_cache(maxsize=1)
def _load_all_articles_cached() -> tuple[dict[str, Any], ...]:
    """Load every .md file under content/articles/ and return as a tuple
    sorted by date descending (newest first).

    Cached for the process lifetime — the cache is invalidated by
    `_reload_articles()` (test fixtures + manual reload only). In prod the
    article set changes only on deploy, so a process-lifetime cache is
    correct.
    """
    out: list[dict[str, Any]] = []
    if not _ARTICLES_DIR.exists():
        log.warning("seo: %s does not exist; no articles to load", _ARTICLES_DIR)
        return tuple()

    for path in sorted(_ARTICLES_DIR.glob("*.md")):
        parsed = _parse_article(path)
        if parsed is not None:
            out.append(parsed)

    # Sort by date desc, then title asc as tiebreak (deterministic order).
    out.sort(key=lambda a: (a["date"], a["title"]), reverse=True)
    # tuple so the lru_cache return value is immutable
    return tuple(out)


def _reload_articles() -> None:
    """Clear the article cache. Tests call this when they add/remove
    fixture files mid-run."""
    _load_all_articles_cached.cache_clear()


def get_all_articles() -> list[dict[str, Any]]:
    """Public accessor — returns a fresh list (callers must not mutate the
    cached tuple)."""
    return list(_load_all_articles_cached())


def get_article(slug: str) -> dict[str, Any] | None:
    """Find one article by slug, or None."""
    for a in _load_all_articles_cached():
        if a["slug"] == slug:
            return a
    return None


# --------------------------------------------------------------------------- #
# JSON-LD helpers (Python-side so tests can assert structured payloads
# without rendering HTML).
# --------------------------------------------------------------------------- #
def _base_url() -> str:
    """Best-effort canonical origin (scheme + host, no trailing slash).

    Prefers the request's host_url so localhost tests work; falls back to
    the FIESTA_PUBLIC_BASE_URL env var; final fallback is the production
    origin so a SF-side share of a draft never points at localhost.
    """
    try:
        url = (request.host_url or "").rstrip("/")
        if url:
            return url
    except Exception:
        pass
    env = os.environ.get("FIESTA_PUBLIC_BASE_URL", "").rstrip("/")
    if env:
        return env
    return "https://fiesta-mvp.fly.dev"


def organization_jsonld() -> dict[str, Any]:
    """Schema.org Organization payload for the site root. Stable across
    pages so we can drop it in `<head>` of base layouts via the macro."""
    base = _base_url()
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "FIESTA",
        "alternateName": "Foreign Income Earner Saving & Tax Advisor",
        "url": base,
        "logo": f"{base}/static/favicon.svg",
        "description": (
            "FIESTA helps Sri Lankan foreign-income earners cut their tax "
            "bill, keep clean records, and file confidently."
        ),
        "sameAs": [
            # Add social profile URLs here as they're created. Empty list
            # is valid Schema.org; we keep the key so search engines see
            # the slot exists.
        ],
    }


def article_jsonld(article: dict[str, Any]) -> dict[str, Any]:
    """Schema.org Article payload for a single article."""
    base = _base_url()
    url = f"{base}/articles/{article['slug']}"
    payload: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["title"],
        "description": article["summary"],
        "datePublished": article["date"],
        "dateModified": article["updated"],
        "author": {
            "@type": "Organization",
            "name": article.get("author", "FIESTA"),
        },
        "publisher": {
            "@type": "Organization",
            "name": "FIESTA",
            "logo": {
                "@type": "ImageObject",
                "url": f"{base}/static/favicon.svg",
            },
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": url,
        },
        "url": url,
        "wordCount": article["word_count"],
        "inLanguage": "en",
    }
    if article.get("hero_image"):
        payload["image"] = article["hero_image"]
    return payload


def faqpage_jsonld(faq_items: list[dict[str, str]]) -> dict[str, Any]:
    """Schema.org FAQPage payload from a list of {q, a} dicts."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item.get("q", "").strip(),
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": item.get("a", "").strip(),
                },
            }
            for item in faq_items
            if item.get("q") and item.get("a")
        ],
    }


def breadcrumb_jsonld(crumbs: list[tuple[str, str]]) -> dict[str, Any]:
    """Schema.org BreadcrumbList. `crumbs` is [(name, absolute_url), ...]."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": name,
                "item": url,
            }
            for i, (name, url) in enumerate(crumbs)
        ],
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@seo_bp.route("/articles", methods=["GET"])
def article_index():
    """Render the article hub. Empty state shown if no articles loaded."""
    articles = get_all_articles()
    base = _base_url()
    crumbs = [
        ("Home", f"{base}/"),
        ("Articles", f"{base}/articles"),
    ]
    return render_template(
        "articles/index.html",
        articles=articles,
        breadcrumb=breadcrumb_jsonld(crumbs),
        org_schema=organization_jsonld(),
        canonical_url=f"{base}/articles",
        page_title="FIESTA — Sri Lanka foreign-income tax guides",
        page_description=(
            "Plain-English guides to Sri Lanka foreign-income tax — "
            "remittance basis, residency, DTAA, rates, deadlines."
        ),
    )


@seo_bp.route("/articles/<string:slug>", methods=["GET"])
def article_detail(slug: str):
    """Render a single article. 404 if no matching slug. Includes Article
    schema, FAQPage schema (if frontmatter has `faq`), breadcrumbs,
    canonical URL, OG + Twitter tags via `articles/detail.html`."""
    article = get_article(slug)
    if article is None:
        abort(404)
    base = _base_url()
    crumbs = [
        ("Home", f"{base}/"),
        ("Articles", f"{base}/articles"),
        (article["title"], f"{base}/articles/{slug}"),
    ]
    schemas = [
        article_jsonld(article),
        breadcrumb_jsonld(crumbs),
    ]
    if article.get("faq"):
        schemas.append(faqpage_jsonld(article["faq"]))

    return render_template(
        "articles/detail.html",
        article=article,
        schemas=schemas,
        canonical_url=f"{base}/articles/{slug}",
        page_title=f"{article['title']} — FIESTA",
        page_description=article["summary"],
        og_image=article.get("hero_image"),
    )


@seo_bp.route("/robots.txt", methods=["GET"])
def robots_txt():
    """Sane defaults: allow public pages, disallow admin/api/sensitive
    routes, reference the sitemap so crawlers discover it.

    The Disallow list is conservative — anything that handles user data,
    accepts auth, or is an internal admin tool. Public marketing surface
    is implicitly allowed by absence."""
    base = _base_url()
    lines = [
        "User-agent: *",
        "Allow: /",
        "Allow: /articles",
        "Allow: /articles/",
        "Allow: /help",
        "Allow: /help/",
        "Allow: /tax-preview",
        "Allow: /pricing",
        "Allow: /legal/",
        "",
        "# Internal / authenticated / admin — keep out of the index.",
        "Disallow: /admin/",
        "Disallow: /api/",
        "Disallow: /webhooks/",
        "Disallow: /auth/",
        "Disallow: /login",
        "Disallow: /signup",
        "Disallow: /logout",
        "Disallow: /profile",
        "Disallow: /billing",
        "Disallow: /remittance/",
        "Disallow: /earnings/",
        "Disallow: /reduce-tax/",
        "Disallow: /service-providers",
        "Disallow: /property",
        "Disallow: /tax-bill/",
        "Disallow: /submit/",
        "Disallow: /consultant/",
        "Disallow: /agreements/",
        "Disallow: /cosign",
        "Disallow: /scan",
        "Disallow: /deductions",
        "Disallow: /support/qa",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ]
    return Response(
        "\n".join(lines),
        mimetype="text/plain; charset=utf-8",
    )


@seo_bp.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    """Canonical sitemap. Lists landing + articles + FAQ entries + key
    public pages.

    This route is registered AFTER faq_routes' /sitemap.xml — see
    register_routes() for the override mechanic."""
    base = _base_url()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Static / known public surface. Each entry: (loc, lastmod, priority, changefreq)
    entries: list[tuple[str, str, str, str]] = [
        (f"{base}/",           now, "1.0", "weekly"),
        (f"{base}/articles",   now, "0.9", "weekly"),
        (f"{base}/help",       now, "0.7", "weekly"),
        (f"{base}/tax-preview", now, "0.8", "monthly"),
        (f"{base}/legal/privacy", now, "0.3", "yearly"),
        (f"{base}/legal/tos", now, "0.3", "yearly"),
    ]

    # Articles
    for art in get_all_articles():
        entries.append((
            f"{base}/articles/{art['slug']}",
            art["updated"],
            "0.8",
            "monthly",
        ))

    # FAQ entries — soft-import, never raise if the model isn't available
    # (e.g. tests that don't boot the FAQ blueprint).
    try:
        from faq_models import FAQEntry
        rows = (
            FAQEntry.query
            .filter_by(is_published=True)
            .order_by(FAQEntry.updated_at.desc())
            .all()
        )
        for row in rows:
            lastmod = (
                row.updated_at.strftime("%Y-%m-%d")
                if row.updated_at else now
            )
            entries.append((
                f"{base}/help/{row.slug}",
                lastmod,
                "0.6",
                "monthly",
            ))
    except Exception as exc:
        log.debug("sitemap: FAQ enumeration skipped (%s)", exc)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, lastmod, priority, changefreq in entries:
        parts.append(
            "  <url>"
            f"<loc>{xml_escape(loc)}</loc>"
            f"<lastmod>{xml_escape(lastmod)}</lastmod>"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority>"
            "</url>"
        )
    parts.append("</urlset>")

    resp = Response("\n".join(parts), mimetype="application/xml")
    # Cache for an hour — sitemap doesn't change often and crawlers hit
    # it aggressively (Bing especially).
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Wire SEO routes into the host app. Idempotent.

    Override mechanic for /sitemap.xml:
      faq_routes.py registers /sitemap.xml at the URL_MAP layer. We do NOT
      try to remove that rule (Flask's URL map is technically mutable but
      doing so is fragile across versions). Instead we register the seo_bp
      blueprint via a unique blueprint name and let the URL conflict
      resolve by view-function override — Flask's add_url_rule semantics
      raise on duplicate endpoint names but allow the SAME URL to be
      claimed by different blueprints. The LAST registered blueprint
      handler wins for the URL when registered with a unique endpoint.

    Since faq_routes registers with endpoint `faq_bp.sitemap_xml` and we
    register with `seo_bp.sitemap_xml`, both endpoints exist, but Flask
    will respond with whichever is reached first by the URL map. To
    guarantee our richer sitemap wins, we explicitly REPLACE the
    view_function for the faq_bp.sitemap_xml endpoint AFTER blueprint
    registration so the URL rule (which Flask resolved to faq_bp.sitemap_xml
    on the first registration) now points at our handler. We log both
    actions so a future maintainer sees the override.
    """
    if app.config.get("_FIESTA_SEO_REGISTERED"):
        return
    app.config["_FIESTA_SEO_REGISTERED"] = True
    app.register_blueprint(seo_bp)

    # Override faq_bp.sitemap_xml so the URL rule points at our richer
    # sitemap. If faq_bp isn't registered (e.g. test stub), nothing to do.
    legacy = app.view_functions.get("faq_bp.sitemap_xml")
    if legacy is not None:
        app.view_functions["faq_bp.sitemap_xml"] = sitemap_xml
        log.info(
            "seo_routes: overrode faq_bp.sitemap_xml -> "
            "seo_routes.sitemap_xml so /sitemap.xml lists articles + FAQ"
        )

    # Register the seo macros template path is automatic (templates/
    # is on the Jinja search path). Nothing else to wire.
    log.info(
        "SEO routes registered: /articles, /articles/<slug>, "
        "/robots.txt, /sitemap.xml (with FAQ + articles)"
    )


__all__ = [
    "register_routes",
    "seo_bp",
    "get_all_articles",
    "get_article",
    "organization_jsonld",
    "article_jsonld",
    "faqpage_jsonld",
    "breadcrumb_jsonld",
    "_reload_articles",
]
