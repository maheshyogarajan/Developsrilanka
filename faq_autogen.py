"""
FAQ auto-generation task — Tier D3 (2026-05-24).

Reads the `feedback` table (D4 corpus) on a weekly cadence, clusters
similar 'confusion'-category rows by token overlap, and writes draft
`FAQEntry` rows with `is_published=False, source='auto_from_feedback'`.

Why token-overlap rather than sklearn TF-IDF:
  * Zero new deps. sklearn pulls ~80 MB of numpy/scipy into the worker
    image — overkill for clustering O(100s) feedback rows weekly.
  * The clustering doesn't need to be optimal — staff reviews every
    draft at /admin/faq before it goes public. We just need "this
    looks like 5 variations of the same question" precision, not
    research-grade.

Scope cap (per task spec):
  * NO AI rewriting of feedback into polished FAQs (Wave 4+).
  * NO multi-language.
  * Drafts only — staff MUST publish manually at /admin/faq.

D1 QA-miss corpus integration:
  D1 ships `qa_corpus.yaml` in parallel. If that file exists at import
  time, `generate_faq_from_qa_misses()` reads QA "misses" (questions
  the bot couldn't answer with high confidence) and creates drafts with
  `source='auto_from_qa'`. If the file doesn't exist yet (e.g. D1 has
  not merged), that function silently returns 0 — no hard dependency.

Celery beat:
  scheduled in `celery_config.py` as
  `'faq_autogen-weekly'` -> `crontab(day_of_week=1, hour=4, minute=0)`
  (Mon 04:00 UTC = 09:30 IST).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from celery_config import app as celery_app


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tunables — conservative defaults; staff review is the real gate.
# --------------------------------------------------------------------------- #
_MIN_CLUSTER_SIZE = 3        # cluster needs >= 3 similar rows to become an FAQ
_TOKEN_OVERLAP_THRESHOLD = 0.45  # Jaccard similarity >= this -> same cluster
_LOOKBACK_DAYS = 90          # only consider feedback from last 90 days
_MAX_DRAFTS_PER_RUN = 25     # cap on new drafts per weekly run (safety)
_MIN_QUESTION_TOKENS = 3     # ignore feedback shorter than this (noise)


# Stopwords — minimal English set so we don't pull in a dep just for this.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do",
    "does", "for", "from", "have", "how", "i", "if", "in", "is",
    "it", "its", "me", "my", "no", "not", "of", "on", "or", "our",
    "so", "than", "that", "the", "this", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "why", "will", "with",
    "you", "your", "can", "could", "would", "should", "did", "had",
    "has", "i'm", "im", "ive", "i've", "tax", "lanka",  # very generic in this corpus
})


# Tax-domain keyword map -> faq_models.FAQ_CATEGORIES bucket.
# First match wins; falls back to 'general'.
_CATEGORY_KEYWORDS: List[Tuple[str, Sequence[str]]] = [
    ("foreign_income", (
        "foreign", "remittance", "overseas", "abroad",
        "nro", "nre", "expat", "non-resident", "non resident",
    )),
    ("deductions", (
        "deduction", "deductible", "relief", "apit", "r3",
        "donation", "donations", "insurance", "epf", "etf",
    )),
    ("filing", (
        "file", "filing", "return", "submit", "deadline",
        "extension", "form", "iit",
    )),
    ("payment", (
        "pay", "payment", "refund", "instalment", "installment",
        "due", "owe", "bill", "invoice",
    )),
]


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _tokenize(text: str) -> set:
    """Lowercase, strip punctuation, drop stopwords + short tokens."""
    if not text:
        return set()
    raw = re.findall(r"[a-z0-9']+", text.lower())
    return {t for t in raw if len(t) >= 3 and t not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


def _classify_into_tax_category(text: str) -> str:
    """Map free-text feedback into one of FAQ_CATEGORIES via keyword hits.
    First-match-wins; falls back to 'general'."""
    low = (text or "").lower()
    for bucket, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in low:
                return bucket
    return "general"


def _slugify(text: str, *, max_len: int = 80) -> str:
    """Slug rules: lowercase, alnum + dashes, no leading/trailing dash,
    capped length. We append a numeric suffix at the call site if needed
    to guarantee uniqueness in the DB."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not base:
        base = "faq"
    return base[:max_len].rstrip("-") or "faq"


def _pick_canonical_question(cluster_rows: Sequence) -> str:
    """The shortest 'feels like a question' body wins; otherwise the
    shortest body period. Capped at 200 chars (matches the column)."""
    candidates = [getattr(r, "body", "") or "" for r in cluster_rows]
    # Prefer rows that end with '?' — they're literal questions.
    qs = [c for c in candidates if c.strip().endswith("?")]
    pool = qs if qs else candidates
    pool.sort(key=lambda s: len(s))
    chosen = pool[0] if pool else "Question"
    # Title-case the first letter for display polish.
    chosen = chosen.strip()
    if chosen and chosen[0].islower():
        chosen = chosen[0].upper() + chosen[1:]
    return chosen[:200]


def _cluster_rows(rows: Sequence) -> List[List]:
    """Greedy single-pass clustering by Jaccard token overlap. Each row
    joins the first existing cluster whose centroid token-set hits the
    threshold; otherwise opens a new cluster.

    Greedy is fine for O(100s) inputs and is order-stable, so re-runs on
    the same input produce the same clusters (helps slug stability)."""
    clusters: List[Dict] = []  # each: {'rows': [...], 'tokens': set}
    for row in rows:
        body = getattr(row, "body", "") or ""
        toks = _tokenize(body)
        if len(toks) < _MIN_QUESTION_TOKENS:
            continue
        placed = False
        for cluster in clusters:
            if _jaccard(toks, cluster["tokens"]) >= _TOKEN_OVERLAP_THRESHOLD:
                cluster["rows"].append(row)
                # Update centroid to the intersection — keeps the cluster
                # tight; a token-set that drifts via union would let later
                # rows pull in unrelated content.
                cluster["tokens"] = cluster["tokens"] & toks if (
                    cluster["tokens"] & toks
                ) else cluster["tokens"]
                placed = True
                break
        if not placed:
            clusters.append({"rows": [row], "tokens": toks})
    return [c["rows"] for c in clusters if len(c["rows"]) >= _MIN_CLUSTER_SIZE]


def _draft_already_exists(slug: str) -> bool:
    """True if an FAQEntry with this slug already exists. Prevents the
    weekly run from creating duplicate drafts for the same recurring
    cluster."""
    from faq_models import FAQEntry
    return FAQEntry.query.filter_by(slug=slug).first() is not None


def _make_unique_slug(base: str) -> str:
    """Append -2, -3, ... if `base` is taken. We cap the search at 50
    suffixes so a runaway loop is impossible."""
    candidate = base
    for i in range(2, 52):
        if not _draft_already_exists(candidate):
            return candidate
        candidate = f"{base}-{i}"
    # Last-ditch: append timestamp; effectively guaranteed unique.
    return f"{base}-{int(datetime.utcnow().timestamp())}"


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def generate_faq_from_feedback(
    *, lookback_days: int = _LOOKBACK_DAYS, max_drafts: int = _MAX_DRAFTS_PER_RUN,
) -> Dict:
    """Cluster recent 'confusion' feedback and create draft FAQEntry rows.

    Returns a dict summary suitable for the Celery task's return value:
      {
        'feedback_rows_scanned': int,
        'clusters_found': int,
        'drafts_created': int,
        'drafts_skipped_existing': int,
      }
    """
    from app import db
    from feedback_models import Feedback
    from faq_models import FAQEntry

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    # Only 'confusion' becomes FAQ candidates. Bugs, features, praise
    # have their own workflows.
    rows = (
        Feedback.query
        .filter(Feedback.category == "confusion")
        .filter(Feedback.created_at >= cutoff)
        .order_by(Feedback.created_at.asc())
        .all()
    )
    log.info("faq_autogen: scanned %d 'confusion' feedback rows", len(rows))

    clusters = _cluster_rows(rows)
    log.info("faq_autogen: found %d clusters (min size %d)",
             len(clusters), _MIN_CLUSTER_SIZE)

    drafts_created = 0
    drafts_skipped = 0
    for cluster in clusters:
        if drafts_created >= max_drafts:
            log.info("faq_autogen: hit max_drafts cap (%d) — stopping", max_drafts)
            break
        question = _pick_canonical_question(cluster)
        category = _classify_into_tax_category(question)
        base_slug = _slugify(question)

        # Idempotency: if the *exact* canonical question already has an
        # entry, don't re-draft. (Cluster boundaries can shift week to
        # week as new feedback arrives, so we hash on the question itself,
        # not the cluster identity.)
        from faq_models import FAQEntry as _F
        if _F.query.filter_by(question=question).first():
            drafts_skipped += 1
            continue

        slug = _make_unique_slug(base_slug)
        answer_stub = (
            "Draft auto-generated from "
            f"{len(cluster)} user feedback note(s). "
            "Staff: please rewrite this answer with the correct, "
            "compliant explanation before publishing."
        )
        entry = FAQEntry(
            slug=slug,
            question=question,
            answer=answer_stub,
            category=category,
            source="auto_from_feedback",
            view_count=0,
            is_published=False,
        )
        db.session.add(entry)
        try:
            db.session.commit()
            drafts_created += 1
        except Exception as exc:
            db.session.rollback()
            log.warning("faq_autogen: insert failed for slug=%s: %s", slug, exc)

    return {
        "feedback_rows_scanned": len(rows),
        "clusters_found": len(clusters),
        "drafts_created": drafts_created,
        "drafts_skipped_existing": drafts_skipped,
    }


def generate_faq_from_qa_misses(*, max_drafts: int = _MAX_DRAFTS_PER_RUN) -> Dict:
    """Read D1's QA-miss corpus (if present) and draft FAQEntry rows.

    D1's corpus.yaml is built in parallel and may not exist yet. If
    import or file-read fails, this function returns zero counts — never
    raises — so the weekly Celery task can still complete the feedback
    side cleanly.

    Expected D1 contract (when shipped): a YAML file at
    `_tier_d1_qa/qa_corpus.yaml` containing a top-level `misses` list of
    objects with at least a `question` field. Other shapes are tolerated
    by falling through to zero counts and logging a warning.
    """
    summary = {
        "qa_misses_scanned": 0,
        "drafts_created": 0,
        "drafts_skipped_existing": 0,
        "skipped_reason": None,
    }
    try:
        from pathlib import Path
        corpus_path = Path(__file__).parent / "_tier_d1_qa" / "qa_corpus.yaml"
        if not corpus_path.exists():
            summary["skipped_reason"] = "qa_corpus.yaml not found (D1 not merged?)"
            return summary
        try:
            import yaml  # PyYAML is already a transitive dep in this project.
        except ImportError:
            summary["skipped_reason"] = "PyYAML not installed"
            return summary
        data = yaml.safe_load(corpus_path.read_text(encoding="utf-8")) or {}
        misses = data.get("misses", []) if isinstance(data, dict) else []
        summary["qa_misses_scanned"] = len(misses)
    except Exception as exc:  # pragma: no cover — defensive
        summary["skipped_reason"] = f"corpus read failed: {exc}"
        return summary

    from app import db
    from faq_models import FAQEntry

    created = 0
    skipped = 0
    for miss in misses:
        if created >= max_drafts:
            break
        if not isinstance(miss, dict):
            continue
        question = (miss.get("question") or "").strip()
        if not question or len(question) < 10:
            continue
        question = question[:200]
        # Dedup by question text.
        if FAQEntry.query.filter_by(question=question).first():
            skipped += 1
            continue
        slug = _make_unique_slug(_slugify(question))
        entry = FAQEntry(
            slug=slug,
            question=question,
            answer=(
                "Draft auto-generated from AI Q&A miss corpus. "
                "Staff: please write the answer before publishing."
            ),
            category=_classify_into_tax_category(question),
            source="auto_from_qa",
            is_published=False,
        )
        db.session.add(entry)
        try:
            db.session.commit()
            created += 1
        except Exception as exc:
            db.session.rollback()
            log.warning("faq_autogen(qa): insert failed for slug=%s: %s", slug, exc)
    summary["drafts_created"] = created
    summary["drafts_skipped_existing"] = skipped
    return summary


# --------------------------------------------------------------------------- #
# Celery task wrapper
# --------------------------------------------------------------------------- #
@celery_app.task(name="faq_autogen.weekly_run")
def weekly_run() -> Dict:
    """Weekly autogen run. Scheduled at Mon 04:00 UTC = 09:30 IST.

    Combines both source corpora and returns a merged summary so the
    worker logs make the impact of each run obvious.
    """
    fb_summary = generate_faq_from_feedback()
    qa_summary = generate_faq_from_qa_misses()
    merged = {
        "feedback": fb_summary,
        "qa": qa_summary,
        "ran_at": datetime.utcnow().isoformat(),
    }
    log.info("faq_autogen.weekly_run summary: %s", merged)
    return merged


__all__ = [
    "generate_faq_from_feedback",
    "generate_faq_from_qa_misses",
    "weekly_run",
]
