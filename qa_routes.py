"""
AI Q&A endpoint + page — Sprint 4 Tier D3 / D1 (2026-05-24).

Wires the TF-IDF retrieval in `ai_qa.py` to:
  * POST /api/qa     — JSON Q&A endpoint, same CSRF/Origin shape as /api/event.
  * GET  /support/qa — public Q&A page (chat-style box + answer panel +
                       3 example quick-click queries).

Architectural parity with `analytics_beacon_routes.py` and
`feedback_routes.py`:

  * CSRF exempted from Flask-WTF on /api/qa (consistent JSON /api/*
    surface).
  * Protected instead by an Origin/Referer check against the request's
    own host + opt-in `BEACON_ALLOWED_ORIGINS` env var.
  * Payload cap = 4 KB (queries are short, anything larger is hostile).
  * Failure mode never raises: a retrieval error returns 503, never
    a 500.

Scope cap (council-binding):
  * Pure read-only retrieval — no agent actions.
  * Single-shot — no conversation history.
  * Sources cited but not deep-linked (no live IRA URL fetching).
  * No rate-limiting on /api/qa in MVP (the corpus is static, retrieval
    is cheap, and per-IP throttling lives one layer up at Fly).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
_QUERY_MAX_CHARS = 500          # typical FAQ query, anything longer is unusual
_PAYLOAD_MAX_BYTES = 4096       # 4 KB envelope cap


# --------------------------------------------------------------------------- #
# Origin / Referer gate — same shape as analytics_beacon_routes._origin_ok
# --------------------------------------------------------------------------- #
def _allowed_origins() -> set:
    out = set()
    try:
        out.add(request.host_url.rstrip("/"))
    except Exception:
        pass
    extra = os.environ.get("BEACON_ALLOWED_ORIGINS", "")
    for raw in extra.split(","):
        raw = raw.strip()
        if raw:
            out.add(raw.rstrip("/"))
    return out


def _origin_ok() -> bool:
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    allowed = _allowed_origins()

    if origin:
        return origin.rstrip("/") in allowed
    if referer:
        try:
            parsed = urlparse(referer)
            ref_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            return ref_origin in allowed
        except Exception:
            return False
    ctype = (request.headers.get("Content-Type") or "").lower()
    return ctype.startswith("application/json")


# --------------------------------------------------------------------------- #
# /api/qa view
# --------------------------------------------------------------------------- #
def _build_qa_view(csrf):
    def api_qa():
        # ---- Origin/Referer gate ---- #
        if not _origin_ok():
            return jsonify({"error": "origin not allowed"}), 403

        # ---- Body parse + size cap ---- #
        raw = request.get_data(cache=False, as_text=False) or b""
        if len(raw) > _PAYLOAD_MAX_BYTES:
            return jsonify({"error": "payload too large"}), 413
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return jsonify({"error": "invalid json"}), 400
        if not isinstance(body, dict):
            return jsonify({"error": "json object required"}), 400

        query = body.get("query")
        if not isinstance(query, str):
            return jsonify({"error": "query (string) required"}), 400
        query = query.strip()
        if not query:
            return jsonify({"error": "query must not be empty"}), 400
        if len(query) > _QUERY_MAX_CHARS:
            query = query[:_QUERY_MAX_CHARS]

        # ---- Retrieve + answer ---- #
        try:
            import ai_qa  # local import — avoids import-time YAML read
            result = ai_qa.answer(query)
        except Exception as exc:
            log.warning("/api/qa: retrieval failed: %s", exc)
            return jsonify({"error": "qa unavailable, try again"}), 503

        return jsonify({
            "answer": result["answer"],
            "sources": result["sources"],
            "matched_question": result["matched_question"],
            "confidence": round(result["confidence"], 4),
            "fallback": result["fallback"],
            "alternatives": result["alternatives"],
        }), 200

    try:
        csrf.exempt(api_qa)
    except Exception as exc:
        log.warning(
            "/api/qa: csrf.exempt failed (%s) — endpoint will require token.",
            exc,
        )
    return api_qa


# --------------------------------------------------------------------------- #
# /support/qa page view
# --------------------------------------------------------------------------- #
def _support_qa_page():
    """Render the chat-style Q&A page. No auth required (public)."""
    examples = [
        "I'm a Sri Lankan dev earning $2,500/mo — what tax do I pay?",
        "What's the 15% foreign income rule?",
        "When is the tax return deadline?",
    ]
    return render_template("support/qa.html", examples=examples)


# --------------------------------------------------------------------------- #
# Public registration hook
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Wire POST /api/qa + GET /support/qa into the Flask app. Idempotent."""
    if app.config.get("_FIESTA_AIQA_REGISTERED"):
        return
    app.config["_FIESTA_AIQA_REGISTERED"] = True

    from app import csrf as _csrf

    api_view = _build_qa_view(_csrf)
    app.add_url_rule(
        "/api/qa",
        endpoint="ai_qa_api",
        view_func=api_view,
        methods=["POST"],
    )
    app.add_url_rule(
        "/support/qa",
        endpoint="ai_qa_page",
        view_func=_support_qa_page,
        methods=["GET"],
    )
    log.info("AI Q&A endpoints registered: POST /api/qa, GET /support/qa")


__all__ = ["register_routes"]
