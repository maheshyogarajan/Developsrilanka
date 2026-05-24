"""
FAQ / Knowledge-base routes — Tier D3 (2026-05-24).

Public (no auth):
  GET  /help               -> published FAQ index, with Schema.org FAQPage
                              JSON-LD for SEO + LLM-citation.
  GET  /help/<slug>        -> single FAQ entry page. Increments view_count.
  GET  /sitemap.xml        -> XML sitemap listing all published /help URLs.

Admin (admin_required gate):
  GET  /admin/faq                  -> staff dashboard: drafts + published.
  POST /admin/faq/<id>/publish     -> toggle is_published (no body needed).
  POST /admin/faq/<id>/delete      -> delete draft (defensive: published
                                       rows require an additional flag).

Why /sitemap.xml lives here (not in main.py):
  D3's entire deliverable is "make these pages discoverable". Owning the
  sitemap route in the same blueprint keeps the SEO surface area in one
  place. If a future tier ships more sitemap-worthy URLs (blog posts,
  glossary, etc.), they can register sitemap entries via a small hook —
  for now there's nothing else to list, so the route returns FAQ URLs
  only.

CSRF:
  Public GETs need none. Admin POSTs use Flask-WTF's default token (we
  don't csrf.exempt these). The admin form template renders
  `csrf_token()`, matching the pattern in `templates/admin/dashboard.html`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from flask import (
    Blueprint,
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
    Response,
)


log = logging.getLogger(__name__)

faq_bp = Blueprint("faq_bp", __name__)


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #
@faq_bp.route("/help", methods=["GET"])
def help_index():
    """Render the public FAQ index — published rows only, grouped by
    category, with Schema.org FAQPage JSON-LD embedded for SEO + LLM
    citation."""
    from faq_models import FAQEntry, FAQ_CATEGORIES
    rows = (
        FAQEntry.query
        .filter_by(is_published=True)
        .order_by(FAQEntry.category.asc(), FAQEntry.view_count.desc(),
                  FAQEntry.created_at.desc())
        .all()
    )

    grouped = {cat: [] for cat in sorted(FAQ_CATEGORIES)}
    for row in rows:
        grouped.setdefault(row.category, []).append(row)

    # Build the Schema.org FAQPage payload here so the template stays
    # simple (no Jinja-side JSON construction).
    schema_payload = _build_faqpage_jsonld(rows)

    return render_template(
        "help/index.html",
        grouped=grouped,
        total=len(rows),
        schema_jsonld=schema_payload,
    )


@faq_bp.route("/help/<string:slug>", methods=["GET"])
def help_entry(slug: str):
    """Render a single published FAQ entry. Drafts 404."""
    from app import db
    from faq_models import FAQEntry
    row = FAQEntry.query.filter_by(slug=slug, is_published=True).first()
    if not row:
        abort(404)
    # Best-effort view-count increment. A failure here must NOT block the
    # page render — abusive auto-refresh would already be cheap, and we
    # prefer to lose a count than a pageview.
    try:
        row.view_count = (row.view_count or 0) + 1
        db.session.commit()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("help_entry: view_count bump failed for slug=%s: %s",
                    slug, exc)
        try:
            db.session.rollback()
        except Exception:
            pass
    return render_template("help/entry.html", entry=row)


@faq_bp.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    """Bot-discoverable sitemap. Lists published FAQ URLs + the help index.

    XML is hand-built (no jinja) so we can stream a clean Content-Type
    without worrying about Jinja autoescape edge cases inside CDATA-ish
    content. The set is small (O(100s)) so the in-memory build is fine.
    """
    from faq_models import FAQEntry
    rows = (
        FAQEntry.query
        .filter_by(is_published=True)
        .order_by(FAQEntry.updated_at.desc())
        .all()
    )
    base = (request.host_url or "").rstrip("/")

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    # Top-level /help index always present (even if empty) so crawlers
    # discover the section.
    parts.append(_sitemap_url(f"{base}/help", datetime.utcnow()))
    for row in rows:
        loc = f"{base}/help/{row.slug}"
        parts.append(_sitemap_url(loc, row.updated_at))
    parts.append("</urlset>")

    return Response(
        "\n".join(parts),
        mimetype="application/xml",
    )


def _sitemap_url(loc: str, lastmod: datetime) -> str:
    """One <url> entry, with XML-escaped loc and ISO lastmod."""
    iso = (lastmod or datetime.utcnow()).strftime("%Y-%m-%d")
    return (
        "  <url>"
        f"<loc>{xml_escape(loc)}</loc>"
        f"<lastmod>{iso}</lastmod>"
        "</url>"
    )


def _build_faqpage_jsonld(rows) -> str:
    """Return a JSON string ready for <script type='application/ld+json'>.

    We hand-build the JSON (not json.dumps of a dict) so the template can
    drop it in verbatim without Jinja autoescape mangling the angle
    brackets. Empty rows -> empty mainEntity (still valid Schema.org)."""
    import json
    payload = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": r.question,
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (r.answer or "").strip(),
                },
            }
            for r in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Admin surface
# --------------------------------------------------------------------------- #
@faq_bp.route("/admin/faq", methods=["GET"])
def admin_faq_index():
    """Staff dashboard: lists ALL FAQ entries (drafts + published),
    grouped by status, with publish/unpublish + delete buttons.

    Decorator is applied at the route level (not @route decorator
    composition) so the import order doesn't matter — admin_required
    is imported lazily inside the closure used by view registration
    below in register_routes()."""
    from faq_models import FAQEntry
    drafts = (
        FAQEntry.query
        .filter_by(is_published=False)
        .order_by(FAQEntry.created_at.desc())
        .all()
    )
    published = (
        FAQEntry.query
        .filter_by(is_published=True)
        .order_by(FAQEntry.updated_at.desc())
        .all()
    )
    return render_template(
        "admin/faq/index.html",
        drafts=drafts,
        published=published,
    )


@faq_bp.route("/admin/faq/<int:entry_id>/publish", methods=["POST"])
def admin_faq_publish(entry_id: int):
    """Toggle is_published on a single FAQ entry. POST-only so a casual
    bot/crawler GET can't flip drafts live."""
    from app import db
    from faq_models import FAQEntry
    row = FAQEntry.query.get(entry_id)
    if not row:
        abort(404)
    row.is_published = not bool(row.is_published)
    row.updated_at = datetime.utcnow()
    try:
        db.session.commit()
        flash(
            f"FAQ '{row.question[:60]}' is now "
            f"{'published' if row.is_published else 'unpublished'}.",
            "success",
        )
    except Exception as exc:
        db.session.rollback()
        log.warning("admin_faq_publish: commit failed for id=%s: %s",
                    entry_id, exc)
        flash("Failed to update FAQ — check logs.", "error")
    return redirect(url_for("faq_bp.admin_faq_index"))


@faq_bp.route("/admin/faq/<int:entry_id>/delete", methods=["POST"])
def admin_faq_delete(entry_id: int):
    """Delete a draft. Published entries refuse delete unless the form
    explicitly sends `confirm_published=yes` — guards against an
    accidental click removing a live SEO surface."""
    from app import db
    from faq_models import FAQEntry
    row = FAQEntry.query.get(entry_id)
    if not row:
        abort(404)
    if row.is_published and request.form.get("confirm_published") != "yes":
        flash(
            "Refusing to delete a published FAQ without explicit "
            "confirm_published=yes. Unpublish first or tick the confirm box.",
            "error",
        )
        return redirect(url_for("faq_bp.admin_faq_index"))
    try:
        db.session.delete(row)
        db.session.commit()
        flash(f"FAQ '{row.question[:60]}' deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        log.warning("admin_faq_delete: commit failed for id=%s: %s",
                    entry_id, exc)
        flash("Failed to delete FAQ — check logs.", "error")
    return redirect(url_for("faq_bp.admin_faq_index"))


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Wire the FAQ blueprint into the host Flask app. Idempotent.

    Applies admin_required to the admin views by wrapping them after the
    blueprint is registered — keeps the decorator import out of module
    import time (so this module can be imported in environments where
    fiesta.auth.decorators isn't on the path, e.g. some unit-test stubs).
    """
    if app.config.get("_FIESTA_FAQ_REGISTERED"):
        return
    app.config["_FIESTA_FAQ_REGISTERED"] = True
    app.register_blueprint(faq_bp)

    # Apply admin_required to the admin views post-registration.
    try:
        from fiesta.auth.decorators import admin_required
        for endpoint in (
            "faq_bp.admin_faq_index",
            "faq_bp.admin_faq_publish",
            "faq_bp.admin_faq_delete",
        ):
            view = app.view_functions.get(endpoint)
            if view is not None:
                app.view_functions[endpoint] = admin_required(view)
    except Exception as exc:
        log.error(
            "faq_routes: failed to gate /admin/faq behind admin_required: %s",
            exc,
        )
    log.info(
        "FAQ routes registered: /help, /help/<slug>, /sitemap.xml, "
        "/admin/faq (admin-gated)"
    )


__all__ = ["register_routes", "faq_bp"]
