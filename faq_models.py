"""
FAQ / Knowledge-base entry model — Tier D3 (2026-05-24).

Auto-generated FAQ pages from in-app `Feedback` corpus (D4) and the
AI Q&A misses corpus (D1). Bot-discoverable: every published row gets
a `/help/<slug>` URL and a sitemap.xml entry, with Schema.org `FAQPage`
JSON-LD on the index page for SEO + LLM-citation.

Workflow (auto-gen draft, MANUAL publish):

  Feedback rows (or QA misses)
        |
        v
  faq_autogen.generate_faq_from_feedback()          [weekly Celery beat]
        |  groups similar questions, drafts entries
        v
  FAQEntry(is_published=False, source='auto_from_feedback')
        |
        v
  Staff reviews at /admin/faq, toggles is_published=True
        |
        v
  Public sees /help/<slug>, sitemap.xml lists URL

Why staff-gates the publish step:
  * Feedback is free text from anonymous users — abusive / wrong /
    embarrassing content must not auto-publish.
  * Schema.org `FAQPage` markup is read by Google / LLMs — wrong
    answers there poison search results and AI summaries.
  * Auto-gen is heuristic clustering, not AI rewriting; raw question
    text needs human polish before it's customer-facing.

Schema-additive pattern (matches `feedback_models.py`):
  (a) ORM model below.
  (b) Raw `CREATE TABLE IF NOT EXISTS faq_entries (...)` runs at every
      entry point via `app._ensure_additive_schema()`.
  (c) Explicit migration `migrations/add_faq_entries.py`.
"""
from datetime import datetime

from app import db


# --------------------------------------------------------------------------- #
# Categories — tax-domain enum chosen over the Feedback-mirroring shape.
#
# Rationale: a public FAQ page reads "Foreign income", "Filing & deadlines",
# "Deductions"... not "bug", "feature", "confusion". The autogen task maps
# Feedback rows whose category is 'confusion' (most likely to become a real
# user question) into one of these tax-domain buckets via keyword heuristics
# (see faq_autogen._classify_into_tax_category). Other Feedback categories
# (bug/feature/praise) are excluded from autogen.
# --------------------------------------------------------------------------- #
FAQ_CATEGORIES = frozenset({
    "foreign_income",     # remittance basis, foreign salary, NRO/NRE
    "deductions",         # APIT relief, R3, donations, life insurance
    "filing",             # how to file, deadlines, return types
    "payment",            # how to pay tax, refunds, instalments
    "general",            # catch-all, default bucket
})


# --------------------------------------------------------------------------- #
# Source — provenance tracking so staff can prioritise review.
# --------------------------------------------------------------------------- #
FAQ_SOURCES = frozenset({
    "manual",                # staff hand-wrote it
    "auto_from_feedback",    # autogen clustered from `feedback` rows
    "auto_from_qa",          # autogen clustered from D1's QA-miss corpus
})


class FAQEntry(db.Model):
    """A single FAQ / KB entry. Drafts default is_published=False; staff
    flips them live after review at /admin/faq."""

    __tablename__ = "faq_entries"

    id = db.Column(db.Integer, primary_key=True)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # URL-safe identifier — used by /help/<slug>. UNIQUE so /help URLs are
    # stable and dedupable across re-runs of the autogen task.
    slug = db.Column(db.String(160), nullable=False, unique=True, index=True)

    # The question, as the user (or autogen clustering) phrased it.
    # Capped at 200 chars so the index page renders cleanly.
    question = db.Column(db.String(200), nullable=False)

    # The answer body — markdown / plain text. Cap is at the application
    # layer (admin form rejects > 16 KB), not the column type.
    answer = db.Column(db.Text, nullable=False, default="")

    # One of FAQ_CATEGORIES — VARCHAR + CHECK constraint mirroring the
    # Feedback pattern so adding a category later is a one-line code change
    # plus a CHECK update, not an enum migration.
    category = db.Column(
        db.String(32), nullable=False, default="general", index=True
    )

    # One of FAQ_SOURCES — used by the admin dashboard to highlight rows
    # that need staff attention (auto-generated drafts).
    source = db.Column(
        db.String(32), nullable=False, default="manual"
    )

    # Public-facing pageviews counter — incremented on every GET /help/<slug>.
    # No PII, just a lightweight popularity signal so staff can prioritise
    # rewriting the most-read drafts first.
    view_count = db.Column(db.Integer, nullable=False, default=0)

    # Hard gate: only is_published=True rows render on /help, /help/<slug>,
    # and sitemap.xml. Drafts are visible to admins only at /admin/faq.
    is_published = db.Column(
        db.Boolean, nullable=False, default=False, index=True
    )

    def __repr__(self):
        return (
            f"<FAQEntry {self.id} {self.category} pub={self.is_published} "
            f"slug={self.slug!r}>"
        )


__all__ = ["FAQEntry", "FAQ_CATEGORIES", "FAQ_SOURCES"]
