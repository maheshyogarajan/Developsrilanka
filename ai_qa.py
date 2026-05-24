"""
FIESTA AI Q&A — Tier D3 / D1 (2026-05-24).

Static FAQ corpus + TF-IDF retrieval, zero external API calls, zero
external embedding service. Stays free, offline, deterministic.

Architecture:
  * Corpus lives in `_tier_d3_ai_qa/corpus.yaml` (40 hand-curated Q&A).
  * Each entry has q (question), a (answer), tags (retrieval keywords),
    source_refs (provenance to support_kb files).
  * Retrieval = TF-IDF cosine similarity over (q + tags) joined text.
  * No conversation history, no agent actions, no live IRA section
    linking. Single-shot Q&A.

Why no sklearn:
  * Project does not depend on scikit-learn (verified at build time).
  * Implementing TF-IDF in ~60 lines of pure Python avoids adding a
    ~50MB dep for a feature that needs to score against 40 documents.
  * If the corpus grows past ~500 entries we'll re-evaluate; until then,
    pure-Python is faster to import, smaller in the container, and the
    math is identical.

Scope cap (council-binding for tier D3):
  * TF-IDF only — no LLM API calls, no embedding models.
  * Top 50 Qs static corpus (we have 40).
  * NO agent actions — pure read-only retrieval.
  * NO conversation history — single-shot Q&A.
  * Sources cited but text snippet-only — no live IRA section linking.

Public API:
  load_corpus()          → list[dict]
  retrieve(query, k=3)   → list[dict] of {entry, score}
  answer(query)          → dict {answer, sources, confidence, fallback}

CEO action loop:
  Visit /support/qa, try 5 queries. For any wrong answer, edit the
  corpus.yaml entry — do not edit ai_qa.py. The retrieval logic is
  generic; the knowledge lives in the corpus.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

try:
    import yaml  # PyYAML — already a project dep
except Exception as exc:  # pragma: no cover - import-time failure
    raise RuntimeError(
        "ai_qa.py requires PyYAML; install via `uv pip install pyyaml`"
    ) from exc

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Paths + tunables
# --------------------------------------------------------------------------- #
_CORPUS_PATH = Path(__file__).resolve().parent / "_tier_d3_ai_qa" / "corpus.yaml"

# Below this score the top retrieval is treated as low-confidence and we
# return the canned escalation answer instead of a possibly-wrong direct
# hit. Calibrated by eye against the corpus — see tests/ai_qa for the
# specific queries that ride this threshold.
CONFIDENCE_THRESHOLD = 0.18

CANNED_LOW_CONFIDENCE_ANSWER = (
    "I'm not sure how to answer that confidently. Please use the "
    "feedback widget at the bottom-right of any page to submit your "
    "question (a human will respond), or email tax@lanka.tax. For "
    "complex situations (DTA, multi-jurisdiction, cryptocurrency, "
    "business structures), we always recommend speaking to a human "
    "tax adviser."
)

# English stop-words — kept tight so domain terms (tax, IRD, foreign,
# remit, deduction, etc.) survive. Adding "tax" to the stop list would
# tank retrieval for the whole corpus.
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "of", "to",
    "in", "on", "for", "with", "as", "is", "are", "was", "were", "be",
    "been", "being", "by", "at", "this", "that", "these", "those", "it",
    "its", "i", "you", "we", "they", "he", "she", "my", "your", "our",
    "their", "his", "her", "do", "does", "did", "doing", "have", "has",
    "had", "having", "can", "could", "would", "should", "may", "might",
    "will", "shall", "what", "which", "who", "whom", "where", "when",
    "why", "how",
})

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


# --------------------------------------------------------------------------- #
# Corpus loader (lazy, single-load, thread-safe)
# --------------------------------------------------------------------------- #
_CORPUS_CACHE: Optional[list[dict]] = None
_VOCAB_CACHE: Optional[dict] = None
_LOAD_LOCK = threading.Lock()


def _tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric, drop stop-words."""
    if not text:
        return []
    return [
        t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text))
        if t not in _STOP_WORDS and len(t) > 1
    ]


def _doc_text(entry: dict) -> str:
    """Joined text used for retrieval — question + tags. Answer text is
    NOT included so retrieval relevance reflects topic match, not
    accidental keyword overlap with answer prose."""
    q = (entry.get("q") or "").strip()
    tags = entry.get("tags") or []
    if isinstance(tags, list):
        tags_str = " ".join(str(t) for t in tags)
    else:
        tags_str = str(tags)
    return f"{q} {tags_str}"


def _build_index(corpus: list[dict]) -> dict:
    """Build the TF-IDF index: per-doc token counts + global IDF."""
    docs_tokens: list[list[str]] = []
    df: Counter = Counter()
    for entry in corpus:
        tokens = _tokenize(_doc_text(entry))
        docs_tokens.append(tokens)
        for term in set(tokens):
            df[term] += 1

    n_docs = max(1, len(corpus))
    # Smoothed IDF: log((N + 1) / (df + 1)) + 1 — same as sklearn's
    # smooth_idf=True default, matched for unit-test reproducibility.
    idf = {term: math.log((n_docs + 1) / (df_t + 1)) + 1 for term, df_t in df.items()}

    doc_vectors: list[dict] = []
    doc_norms: list[float] = []
    for tokens in docs_tokens:
        tf = Counter(tokens)
        vec = {term: tf_t * idf.get(term, 0.0) for term, tf_t in tf.items()}
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        doc_vectors.append(vec)
        doc_norms.append(norm)

    return {
        "idf": idf,
        "doc_vectors": doc_vectors,
        "doc_norms": doc_norms,
        "n_docs": n_docs,
    }


def load_corpus(force_reload: bool = False) -> list[dict]:
    """Load the corpus from disk once, cache subsequent calls.
    Thread-safe via _LOAD_LOCK. `force_reload=True` is for tests."""
    global _CORPUS_CACHE, _VOCAB_CACHE

    if _CORPUS_CACHE is not None and not force_reload:
        return _CORPUS_CACHE

    with _LOAD_LOCK:
        if _CORPUS_CACHE is not None and not force_reload:
            return _CORPUS_CACHE

        if not _CORPUS_PATH.exists():
            log.error("ai_qa corpus missing at %s — returning empty corpus", _CORPUS_PATH)
            _CORPUS_CACHE = []
            _VOCAB_CACHE = _build_index([])
            return _CORPUS_CACHE

        try:
            raw = _CORPUS_PATH.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw) or []
        except Exception as exc:
            log.error("ai_qa corpus parse failed (%s) — returning empty corpus", exc)
            _CORPUS_CACHE = []
            _VOCAB_CACHE = _build_index([])
            return _CORPUS_CACHE

        if not isinstance(parsed, list):
            log.error("ai_qa corpus must be a YAML list, got %r", type(parsed))
            _CORPUS_CACHE = []
            _VOCAB_CACHE = _build_index([])
            return _CORPUS_CACHE

        valid: list[dict] = []
        for i, entry in enumerate(parsed):
            if not isinstance(entry, dict):
                log.warning("ai_qa: entry %d is not a dict — skipped", i)
                continue
            if not entry.get("q") or not entry.get("a"):
                log.warning("ai_qa: entry %d missing q/a — skipped", i)
                continue
            valid.append({
                "q": str(entry["q"]).strip(),
                "a": str(entry["a"]).strip(),
                "tags": list(entry.get("tags") or []),
                "source_refs": list(entry.get("source_refs") or []),
            })

        _CORPUS_CACHE = valid
        _VOCAB_CACHE = _build_index(valid)
        log.info("ai_qa corpus loaded: %d entries", len(valid))
        return _CORPUS_CACHE


def _vocab() -> dict:
    """Return the cached index, building it if load_corpus hasn't yet."""
    if _VOCAB_CACHE is None:
        load_corpus()
    return _VOCAB_CACHE  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #
def _vectorize_query(query: str, idf: dict) -> tuple[dict, float]:
    """Build the query's TF-IDF vector + L2 norm."""
    tokens = _tokenize(query)
    if not tokens:
        return {}, 0.0
    tf = Counter(tokens)
    vec = {term: tf_t * idf.get(term, 0.0) for term, tf_t in tf.items()}
    norm = math.sqrt(sum(w * w for w in vec.values())) or 0.0
    return vec, norm


def _cosine(qvec: dict, qnorm: float, dvec: dict, dnorm: float) -> float:
    if qnorm == 0.0 or dnorm == 0.0:
        return 0.0
    # iterate the smaller dict for speed
    if len(qvec) > len(dvec):
        qvec, dvec = dvec, qvec
    dot = 0.0
    for term, qw in qvec.items():
        dw = dvec.get(term)
        if dw:
            dot += qw * dw
    return dot / (qnorm * dnorm)


def retrieve(query: str, k: int = 3) -> list[dict]:
    """Return top-k entries with their cosine similarity scores.

    Result shape: [{"entry": <corpus dict>, "score": float}, ...]
    Scores are in [0.0, 1.0]; higher = better match.
    """
    corpus = load_corpus()
    if not corpus:
        return []

    vocab = _vocab()
    qvec, qnorm = _vectorize_query(query, vocab["idf"])
    if qnorm == 0.0:
        return []

    scored: list[tuple[float, int]] = []
    for i, dvec in enumerate(vocab["doc_vectors"]):
        score = _cosine(qvec, qnorm, dvec, vocab["doc_norms"][i])
        if score > 0.0:
            scored.append((score, i))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[: max(1, int(k))]
    return [{"entry": corpus[i], "score": float(s)} for s, i in top]


# --------------------------------------------------------------------------- #
# Public answer API
# --------------------------------------------------------------------------- #
def answer(query: str) -> dict:
    """Single-shot answer for a query.

    Returns:
      {
        "answer": str,
        "sources": list[str],         # source_refs of the top hit
        "matched_question": str|None, # the FAQ q we matched (for UI)
        "confidence": float,          # cosine sim of top hit [0..1]
        "fallback": bool,             # True if low-confidence canned reply
        "alternatives": list[dict],   # up to 2 near-misses for UI
      }
    """
    query = (query or "").strip()
    if not query:
        return {
            "answer": "Please ask a question.",
            "sources": [],
            "matched_question": None,
            "confidence": 0.0,
            "fallback": True,
            "alternatives": [],
        }

    hits = retrieve(query, k=3)
    if not hits or hits[0]["score"] < CONFIDENCE_THRESHOLD:
        # Surface near-misses so the UI can show "you may have meant…"
        # even when we decline to answer authoritatively.
        alts = []
        for h in hits[:2]:
            alts.append({
                "question": h["entry"]["q"],
                "score": h["score"],
            })
        return {
            "answer": CANNED_LOW_CONFIDENCE_ANSWER,
            "sources": [],
            "matched_question": None,
            "confidence": float(hits[0]["score"]) if hits else 0.0,
            "fallback": True,
            "alternatives": alts,
        }

    top = hits[0]
    alternatives = [
        {"question": h["entry"]["q"], "score": h["score"]}
        for h in hits[1:]
    ]
    return {
        "answer": top["entry"]["a"],
        "sources": list(top["entry"].get("source_refs") or []),
        "matched_question": top["entry"]["q"],
        "confidence": float(top["score"]),
        "fallback": False,
        "alternatives": alternatives,
    }


# --------------------------------------------------------------------------- #
# Test/debug helpers
# --------------------------------------------------------------------------- #
def _reset_for_tests() -> None:
    """Force-reload the corpus on next call. Tests use this between
    fixture setups so a swap-in test corpus is picked up."""
    global _CORPUS_CACHE, _VOCAB_CACHE
    with _LOAD_LOCK:
        _CORPUS_CACHE = None
        _VOCAB_CACHE = None


__all__ = [
    "load_corpus",
    "retrieve",
    "answer",
    "CONFIDENCE_THRESHOLD",
    "CANNED_LOW_CONFIDENCE_ANSWER",
]
