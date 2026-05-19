"""fiesta.inbound.webhook — POST /webhooks/inbound-email handler.

Receives parsed inbound email from the email provider (SendGrid inbound
parse webhook is what FIESTA uses today, per the sendgrid_logger.py / SES
plumbing in the repo; the handler accepts both SendGrid Inbound Parse JSON
and Postmark Inbound JSON shapes).

Pipeline:
  1. Verify webhook signature.
  2. Normalize the parsed payload into a canonical InboundEmail DTO.
  3. Match customer by from_address (lower(email) on User table; falls
     back to threading via in_reply_to message_id when available).
  4. Persist InboundEmail row.
  5. Build customer_context for classifier (best-effort).
  6. Classify -> route -> persist OutboundDraft (Tier-1 ONLY, never sent).
  7. Return 201 with summary.

Stdlib + pydantic + (optional) Flask. The core handler is `process_inbound(
payload, ...)` -- it has zero Flask dependency so unit tests can call it
directly with a fake DB-like object.

The Flask route is registered by `register_webhook_routes(app)`, called
from main.py at app init time.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from fiesta.inbound import classifier as _cls
from fiesta.inbound import router as _router
from fiesta.inbound.models import (
    FROM_ADDR_MAX_CHARS,
    INBOUND_BODY_MAX_CHARS,
    PRIVACY_UNMATCHED_BODY_CHARS,
    SUBJECT_MAX_CHARS,
    InboundEmailDTO,
    OutboundDraftDTO,
    _redact_body_for_unmatched,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Webhook signing secret env var. When missing, signature check is bypassed
# (dev mode). Production set via Fly secret / Replit secret.
WEBHOOK_SECRET_ENV = "FIESTA_INBOUND_WEBHOOK_SECRET"

# Header name used by SendGrid Event Webhook (HMAC-SHA256 signature). For
# Postmark we'd use the Basic Auth header instead.
SIGNATURE_HEADER_SENDGRID = "X-Twilio-Email-Event-Webhook-Signature"
SIGNATURE_HEADER_POSTMARK = "X-Postmark-Signature"
# Custom FIESTA header (works with any provider that supports custom signing).
SIGNATURE_HEADER_FIESTA = "X-Fiesta-Signature"


# Email-from regex to strip "Name <email@x>" wrappers.
_EMAIL_FROM_RE = re.compile(r"<([^>]+)>")


# ---------------------------------------------------------------------------
# Pydantic v2 payload normalization
# ---------------------------------------------------------------------------

class ParsedInbound(BaseModel):
    """Canonical inbound shape after provider-specific normalization."""

    from_addr: str = Field(..., max_length=FROM_ADDR_MAX_CHARS)
    to_addr: Optional[str] = Field(default=None, max_length=FROM_ADDR_MAX_CHARS)
    subject: Optional[str] = Field(default=None, max_length=SUBJECT_MAX_CHARS)
    body_text: Optional[str] = Field(default=None, max_length=INBOUND_BODY_MAX_CHARS)
    body_html: Optional[str] = Field(default=None, max_length=INBOUND_BODY_MAX_CHARS)
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_address(raw: Optional[str]) -> str:
    """Extract bare email from "Display Name <a@b.c>" or just "a@b.c"."""
    if not raw:
        return ""
    raw = raw.strip()
    m = _EMAIL_FROM_RE.search(raw)
    if m:
        return m.group(1).strip().lower()
    return raw.lower()


def normalize_payload(payload: dict[str, Any]) -> ParsedInbound:
    """Accept SendGrid Inbound Parse OR Postmark Inbound shapes -> canonical.

    SendGrid keys (multipart/form-data parsed):
      from, to, subject, text, html, headers (raw), envelope, attachments
    Postmark keys (application/json):
      From, To, Subject, TextBody, HtmlBody, MessageID, Headers (list of dicts)

    Raises ValidationError on unparseable payloads.
    """
    # Try Postmark shape first (json, capitalized keys).
    if "From" in payload or "TextBody" in payload:
        headers_list = payload.get("Headers") or []
        in_reply_to = None
        references = None
        message_id = payload.get("MessageID") or payload.get("MessageId")
        for h in headers_list:
            name = (h.get("Name") or "").lower()
            value = h.get("Value")
            if name == "in-reply-to":
                in_reply_to = value
            elif name == "references":
                references = value
            elif name == "message-id" and not message_id:
                message_id = value
        return ParsedInbound(
            from_addr=_strip_address(payload.get("From") or payload.get("FromFull", {}).get("Email"))[:FROM_ADDR_MAX_CHARS],
            to_addr=_strip_address(payload.get("To"))[:FROM_ADDR_MAX_CHARS] or None,
            subject=(payload.get("Subject") or "")[:SUBJECT_MAX_CHARS] or None,
            body_text=(payload.get("TextBody") or "")[:INBOUND_BODY_MAX_CHARS] or None,
            body_html=(payload.get("HtmlBody") or "")[:INBOUND_BODY_MAX_CHARS] or None,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references,
        )

    # SendGrid Inbound Parse shape (multipart form keys, lowercase).
    # Headers come as a raw string in the 'headers' field; parse minimally.
    in_reply_to = None
    references = None
    message_id = None
    headers_raw = payload.get("headers") or ""
    if isinstance(headers_raw, str) and headers_raw:
        for line in headers_raw.splitlines():
            if line.lower().startswith("in-reply-to:"):
                in_reply_to = line.split(":", 1)[1].strip()
            elif line.lower().startswith("references:"):
                references = line.split(":", 1)[1].strip()
            elif line.lower().startswith("message-id:"):
                message_id = line.split(":", 1)[1].strip()

    return ParsedInbound(
        from_addr=_strip_address(payload.get("from"))[:FROM_ADDR_MAX_CHARS],
        to_addr=_strip_address(payload.get("to"))[:FROM_ADDR_MAX_CHARS] or None,
        subject=(payload.get("subject") or "")[:SUBJECT_MAX_CHARS] or None,
        body_text=(payload.get("text") or "")[:INBOUND_BODY_MAX_CHARS] or None,
        body_html=(payload.get("html") or "")[:INBOUND_BODY_MAX_CHARS] or None,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
    )


def verify_signature(
    *,
    raw_body: bytes,
    headers: dict[str, str],
    secret: Optional[str] = None,
) -> bool:
    """Verify HMAC-SHA256 signature in X-Fiesta-Signature (or provider headers).

    Algorithm: hex(hmac_sha256(secret, raw_body))

    Returns True iff signature matches OR no secret configured (dev mode).

    Args:
        raw_body: raw HTTP body bytes
        headers: case-insensitive-ish dict of HTTP headers (lower-cased keys
            accepted)
        secret: explicit secret; falls back to env var.
    """
    sec = secret if secret is not None else os.environ.get(WEBHOOK_SECRET_ENV)
    if not sec:
        # No secret configured -> dev mode, allow.
        log.warning(
            "inbound webhook: no %s configured -- signature check bypassed",
            WEBHOOK_SECRET_ENV,
        )
        return True

    # Look for any of the known signature headers (case-insensitive).
    lower_headers = {k.lower(): v for k, v in headers.items()}
    sig_value = (
        lower_headers.get(SIGNATURE_HEADER_FIESTA.lower())
        or lower_headers.get(SIGNATURE_HEADER_SENDGRID.lower())
        or lower_headers.get(SIGNATURE_HEADER_POSTMARK.lower())
    )
    if not sig_value:
        return False

    expected = hmac.new(
        key=sec.encode(),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # constant-time compare; accept either hex or "sha256=hex" framing.
    sig_clean = sig_value.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, sig_clean)


# ---------------------------------------------------------------------------
# Customer matching
# ---------------------------------------------------------------------------

def match_customer_by_email(
    parsed: ParsedInbound,
    customer_lookup_fn: Callable[[str], Optional[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    """Look up a customer by from_addr (lowercase email).

    customer_lookup_fn: returns dict with at least {id, email, name} or None.
    Provided by the caller / Flask glue layer so this module is DB-agnostic.

    Returns the matched customer record or None.
    """
    if not parsed.from_addr:
        return None
    return customer_lookup_fn(parsed.from_addr.lower())


def match_customer_by_thread(
    parsed: ParsedInbound,
    thread_lookup_fn: Callable[[str], Optional[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    """Fallback: if from_addr doesn't match a user, try the inbound's
    in_reply_to message id against prior outbound logs."""
    if not parsed.in_reply_to:
        return None
    return thread_lookup_fn(parsed.in_reply_to)


# ---------------------------------------------------------------------------
# Core handler -- pure function, testable without Flask
# ---------------------------------------------------------------------------

class ProcessResult(BaseModel):
    """Result of process_inbound() for the webhook response."""

    ok: bool
    inbound_email_id: Optional[int] = None
    outbound_draft_id: Optional[int] = None
    classified_as: Optional[str] = None
    customer_matched: bool = False
    customer_id: Optional[int] = None
    status: str = "pending"
    error: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class WebhookDeps(BaseModel):
    """Bag of injected dependencies for process_inbound -- keeps the core
    handler decoupled from Flask + SQLAlchemy."""

    # Functions match by signature; pydantic v2 with arbitrary_types_allowed.
    model_config = {"arbitrary_types_allowed": True}

    customer_lookup_fn: Any  # Callable[[str], Optional[dict]]
    thread_lookup_fn: Any    # Callable[[str], Optional[dict]]
    customer_context_fn: Any  # Callable[[int], dict[str, Any]]
    persist_inbound_fn: Any  # Callable[[InboundEmailDTO], int]  -> id
    persist_draft_fn: Any    # Callable[[OutboundDraftDTO], int] -> id


def process_inbound(
    *,
    payload: dict[str, Any],
    deps: WebhookDeps,
    fiesta_base_url: str = _router.DEFAULT_FIESTA_BASE_URL,
) -> ProcessResult:
    """Process one inbound email payload end-to-end.

    Does NOT verify the signature -- caller (webhook route) is responsible
    for that. This function assumes the payload is trusted.
    """
    # Step 1: normalize
    try:
        parsed = normalize_payload(payload)
    except ValidationError as e:
        return ProcessResult(ok=False, error=f"payload_validation_failed: {e}")

    if not parsed.from_addr:
        return ProcessResult(ok=False, error="missing_from_addr")

    # Step 2: customer match (by from_addr; fall back to threading)
    matched = None
    try:
        matched = match_customer_by_email(parsed, deps.customer_lookup_fn)
        if not matched and parsed.in_reply_to:
            matched = match_customer_by_thread(parsed, deps.thread_lookup_fn)
    except Exception as e:
        log.exception("customer match failed: %s", e)
        # Treat as unmatched; proceed.

    customer_matched = matched is not None
    customer_id = matched.get("id") if matched else None
    customer_name = matched.get("name") if matched else None
    customer_email = matched.get("email") if matched else None

    # Step 3: pull customer context for precondition boosts
    customer_context: dict[str, Any] = {}
    if customer_matched and customer_id is not None:
        try:
            customer_context = deps.customer_context_fn(customer_id) or {}
        except Exception as e:
            log.exception("customer_context_fn failed: %s", e)
            customer_context = {}

    # Step 4: persist InboundEmail (with body redaction when unmatched)
    body_text_to_persist = parsed.body_text
    body_html_to_persist = parsed.body_html
    if not customer_matched:
        body_text_to_persist = _redact_body_for_unmatched(parsed.body_text)
        body_html_to_persist = _redact_body_for_unmatched(parsed.body_html)

    inbound_dto = InboundEmailDTO(
        from_addr=parsed.from_addr,
        to_addr=parsed.to_addr,
        subject=parsed.subject,
        body_text=body_text_to_persist,
        body_html=body_html_to_persist,
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
        received_at=datetime.utcnow(),
        customer_id=customer_id,
        customer_matched=customer_matched,
        status="classifying",
    )

    # Step 5: classify
    classification = _cls.classify(
        subject=parsed.subject or "",
        body=parsed.body_text or "",
        customer_context=customer_context,
        customer_matched=customer_matched,
    )

    inbound_dto.classified_as = classification.category
    inbound_dto.classifier_score = classification.score
    inbound_dto.classifier_reasoning = classification.reasoning
    inbound_dto.is_autoreply_noise = classification.is_autoreply_noise

    # Step 6: route -> RoutingDecision (draft + linkback + tag)
    decision = _router.route(
        classification=classification,
        customer_name=customer_name,
        customer_email=customer_email,
        fiesta_base_url=fiesta_base_url,
    )

    # Decide InboundEmail final status before persisting.
    if classification.is_autoreply_noise:
        inbound_dto.status = "noise_discarded"
    elif not customer_matched:
        inbound_dto.status = "flagged_for_staff"
    elif decision.draft_subject and decision.draft_body:
        inbound_dto.status = "drafted"
    else:
        inbound_dto.status = "classified"

    # Step 7: persist inbound row
    try:
        inbound_id = deps.persist_inbound_fn(inbound_dto)
    except Exception as e:
        log.exception("persist_inbound failed: %s", e)
        return ProcessResult(
            ok=False,
            error=f"persist_inbound_failed: {e}",
            customer_matched=customer_matched,
            customer_id=customer_id,
            classified_as=classification.category,
        )

    # Step 8: persist OutboundDraft when we have one (Tier-1 ONLY, never sent)
    draft_id: Optional[int] = None
    if (
        decision.draft_subject
        and decision.draft_body
        and not classification.is_autoreply_noise
    ):
        draft_dto = OutboundDraftDTO(
            inbound_email_id=inbound_id,
            draft_subject=decision.draft_subject,
            draft_body=decision.draft_body,
            linkback_url=decision.linkback_url,
            category=decision.category,
            route_hint=decision.route_hint,
            gates_failed=(
                json.dumps(decision.gates_failed) if decision.gates_failed else None
            ),
            warnings=(
                json.dumps(decision.warnings) if decision.warnings else None
            ),
        )
        try:
            draft_id = deps.persist_draft_fn(draft_dto)
        except Exception as e:
            log.exception("persist_draft failed: %s", e)
            # Don't fail the whole call -- inbound row is already saved,
            # staff can compose manually from the staff queue.
            return ProcessResult(
                ok=True,
                inbound_email_id=inbound_id,
                outbound_draft_id=None,
                classified_as=classification.category,
                customer_matched=customer_matched,
                customer_id=customer_id,
                status=inbound_dto.status,
                warnings=decision.warnings + [f"persist_draft_failed: {e}"],
            )

    return ProcessResult(
        ok=True,
        inbound_email_id=inbound_id,
        outbound_draft_id=draft_id,
        classified_as=classification.category,
        customer_matched=customer_matched,
        customer_id=customer_id,
        status=inbound_dto.status,
        warnings=decision.warnings,
    )


# ---------------------------------------------------------------------------
# Flask route registration -- optional, imported lazily so unit tests run
# without Flask present.
# ---------------------------------------------------------------------------

def register_webhook_routes(
    app: Any,
    *,
    deps_factory: Callable[[], WebhookDeps],
    url_path: str = "/webhooks/inbound-email",
    secret: Optional[str] = None,
) -> None:
    """Register the POST /webhooks/inbound-email route on the Flask app.

    Args:
        app: Flask app instance.
        deps_factory: function returning a WebhookDeps each request (so DB
            session is per-request).
        url_path: override route path (default: /webhooks/inbound-email).
        secret: explicit signing secret; falls back to env var.
    """
    try:
        from flask import jsonify, request  # imported lazily
    except ImportError:
        raise RuntimeError(
            "register_webhook_routes requires Flask -- install flask "
            "or call process_inbound() directly."
        )

    @app.route(url_path, methods=["POST"], endpoint="fiesta_inbound_webhook")
    def _route():  # pragma: no cover - exercised in integration only
        raw = request.get_data()
        headers = dict(request.headers)
        if not verify_signature(raw_body=raw, headers=headers, secret=secret):
            log.warning("inbound webhook: signature verification failed")
            return jsonify({"ok": False, "error": "signature_invalid"}), 401

        # Try JSON first, then form data.
        try:
            if request.is_json:
                payload = request.get_json(silent=True) or {}
            else:
                payload = request.form.to_dict()
                # SendGrid Inbound Parse may include attachments as files;
                # we ignore those in v1 (no AI attachment handling yet).
        except Exception as e:
            return jsonify({"ok": False, "error": f"payload_parse_failed: {e}"}), 400

        deps = deps_factory()
        result = process_inbound(payload=payload, deps=deps)
        status_code = 201 if result.ok else 422
        return jsonify(result.model_dump()), status_code


__all__ = [
    "WEBHOOK_SECRET_ENV",
    "SIGNATURE_HEADER_FIESTA",
    "SIGNATURE_HEADER_SENDGRID",
    "SIGNATURE_HEADER_POSTMARK",
    "ParsedInbound",
    "ProcessResult",
    "WebhookDeps",
    "normalize_payload",
    "verify_signature",
    "match_customer_by_email",
    "match_customer_by_thread",
    "process_inbound",
    "register_webhook_routes",
]
