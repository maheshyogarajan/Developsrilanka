"""fiesta.cosign.email_sender -- SendGrid wrappers for S10 co-sign emails.

Wave 3 (2026-05-20). Per S10 dispatch brief.

What goes through here
----------------------
- Initial SP outreach (sp_initial_request.html, sent by customer voice)
- T+3d / T+7d reminders to SP (gentle, non-pestering)
- Customer notification when SP signs
- Customer countersign-prompt reminder
- SP "concern" handoff to support@lanka.tax

Failure mode
------------
ALL functions are best-effort. They return (ok: bool, status_message: str)
and NEVER raise. SendGrid errors are logged + the caller can retry.

Mailing identity
----------------
Sender: "Team FIESTA <info@developsrilanka.com>" (consistent with rest of app).
Reply-to: the customer (so SP can reply directly).

Tokens
------
Sign link: url_for('fiesta_cosign.sp_signing_page', tracking_token=...)
which resolves to /cosign/sp/<token>. Token is single-use + 30-day TTL.
"""
from __future__ import annotations

import logging
import os
from typing import Tuple

from flask import current_app, render_template, url_for

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FIESTA_SENDER_EMAIL = "info@developsrilanka.com"
FIESTA_SENDER_NAME = "Team FIESTA"
SUPPORT_EMAIL = "support@lanka.tax"


# ---------------------------------------------------------------------------
# Low-level: send_via_sendgrid
# ---------------------------------------------------------------------------


def _send_via_sendgrid(
    *,
    to_email: str,
    to_name: str | None,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    reply_to_email: str | None = None,
    reply_to_name: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> Tuple[bool, str]:
    """Send a single email via SendGrid. Returns (ok, status).

    On any error -- key missing, network failure, non-2xx response -- we
    log + return False. We never raise. The caller decides how to surface.

    Attachments: list of (filename, bytes, mimetype).
    """
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        logger.error("cosign email: SENDGRID_API_KEY missing -- skip send")
        return False, "no_sendgrid_key"

    try:
        # Late import so tests that don't have sendgrid installed can still
        # exercise the public functions via monkeypatching.
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import (
            Mail,
            Email,
            To,
            ReplyTo,
            Content,
            Attachment,
            FileContent,
            FileName,
            FileType,
            Disposition,
        )
        import base64

        msg = Mail(
            from_email=Email(FIESTA_SENDER_EMAIL, FIESTA_SENDER_NAME),
            to_emails=To(to_email, to_name) if to_name else To(to_email),
            subject=subject,
            html_content=Content("text/html", html_body),
        )
        if text_body:
            msg.add_content(Content("text/plain", text_body))
        if reply_to_email:
            msg.reply_to = ReplyTo(reply_to_email, reply_to_name)

        for fn, blob, mime in attachments or []:
            att = Attachment()
            att.file_content = FileContent(base64.b64encode(blob).decode())
            att.file_name = FileName(fn)
            att.file_type = FileType(mime)
            att.disposition = Disposition("attachment")
            msg.add_attachment(att)

        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        if 200 <= resp.status_code < 300:
            return True, f"sent_{resp.status_code}"
        return False, f"sendgrid_{resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign email: SendGrid exception: %s", exc)
        return False, f"exception_{exc.__class__.__name__}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _customer_snapshot(agreement) -> dict:
    import json
    try:
        return json.loads(agreement.customer_snapshot_json or "{}")
    except (ValueError, TypeError):
        return {}


def _agreement_pdf_bytes(agreement) -> bytes | None:
    """Read the PDF bytes from disk; None on any failure."""
    from pathlib import Path
    if not agreement.pdf_path:
        return None
    p = Path(agreement.pdf_path)
    if not p.exists():
        return None
    try:
        return p.read_bytes()
    except Exception:  # noqa: BLE001
        return None


def _signing_link_for(workflow) -> str:
    """Build the public SP-signing URL. Uses current app's url_for so it
    respects SERVER_NAME / scheme. Falls back to the relative path when
    we're outside an app context (e.g. unit tests).
    """
    try:
        return url_for(
            "fiesta_cosign.sp_signing_page",
            tracking_token=workflow.tracking_token,
            _external=True,
        )
    except RuntimeError:
        return f"/cosign/sp/{workflow.tracking_token}"


# ---------------------------------------------------------------------------
# Public sends
# ---------------------------------------------------------------------------


def send_cosign_email(
    *, kind: str, workflow, agreement
) -> Tuple[bool, str]:
    """Dispatch by kind to one of the templated SP-outreach mails."""
    kind = (kind or "initial").lower()

    customer = _customer_snapshot(agreement)
    customer_name = customer.get("full_name") or "your client"
    customer_email = customer.get("notice_email") or ""
    sp_email = workflow.sp_email
    sp_name = workflow.sp_name or "(no name on file)"

    if not sp_email:
        return False, "no_sp_email"

    signing_link = _signing_link_for(workflow)
    pdf_bytes = _agreement_pdf_bytes(agreement)
    attachments = (
        [
            (
                f"{agreement.reference_id}.pdf",
                pdf_bytes,
                "application/pdf",
            )
        ]
        if pdf_bytes
        else None
    )

    if kind == "initial":
        template_name = "cosign/email/sp_initial_request.html"
        subject = f"{customer_name} has prepared a Service Agreement for your review"
    elif kind == "first_reminder":
        template_name = "cosign/email/sp_reminder_3d.html"
        subject = f"Reminder: Service Agreement from {customer_name}"
    elif kind == "second_reminder":
        template_name = "cosign/email/sp_reminder_7d.html"
        subject = f"Second reminder: Service Agreement from {customer_name}"
    elif kind == "escalate":
        template_name = "cosign/email/sp_reminder_7d.html"  # reuse content
        subject = f"Final reminder: Service Agreement from {customer_name}"
    else:
        return False, f"unknown_kind_{kind}"

    try:
        html_body = render_template(
            template_name,
            customer_name=customer_name,
            customer_email=customer_email,
            sp_name=sp_name,
            signing_link=signing_link,
            agreement_reference=agreement.reference_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign email: template render failed: %s", exc)
        # Plain-text fallback so we don't entirely fail to communicate.
        html_body = (
            f"<p>Hi {sp_name},</p>"
            f"<p>{customer_name} has prepared a Service Agreement for the "
            f"work you do for them. To review and sign, please open: "
            f"<a href='{signing_link}'>{signing_link}</a></p>"
            f"<p>Reference: {agreement.reference_id}</p>"
        )

    return _send_via_sendgrid(
        to_email=sp_email,
        to_name=sp_name,
        subject=subject,
        html_body=html_body,
        reply_to_email=customer_email or None,
        reply_to_name=customer_name,
        attachments=attachments,
    )


def notify_customer_sp_signed(*, workflow, agreement) -> Tuple[bool, str]:
    """Customer notification: 'Your SP signed -- time to countersign.'"""
    customer = _customer_snapshot(agreement)
    customer_email = customer.get("notice_email")
    customer_name = customer.get("full_name") or "there"
    if not customer_email:
        return False, "no_customer_email"

    try:
        html_body = render_template(
            "cosign/email/customer_sp_signed.html",
            customer_name=customer_name,
            sp_name=workflow.sp_name or "your Service Provider",
            sp_typed_name=workflow.sp_typed_name or "",
            countersign_link=url_for(
                "fiesta_cosign.walkthrough",
                agreement_id=agreement.id,
                _external=True,
            ) if _has_app_context() else f"/cosign/{agreement.id}",
            agreement_reference=agreement.reference_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign customer_sp_signed render failed: %s", exc)
        html_body = (
            f"<p>Hi {customer_name},</p>"
            f"<p>{workflow.sp_name or 'Your Service Provider'} has signed the "
            f"agreement. Log in to FIESTA to countersign and finalise.</p>"
        )

    return _send_via_sendgrid(
        to_email=customer_email,
        to_name=customer_name,
        subject="Your Service Provider signed -- ready to countersign",
        html_body=html_body,
    )


def remind_customer_to_countersign(*, workflow, agreement) -> Tuple[bool, str]:
    """Cron-driven nudge for customers who haven't countersigned after SP did."""
    customer = _customer_snapshot(agreement)
    customer_email = customer.get("notice_email")
    customer_name = customer.get("full_name") or "there"
    if not customer_email:
        return False, "no_customer_email"

    try:
        html_body = render_template(
            "cosign/email/customer_countersign_prompt.html",
            customer_name=customer_name,
            sp_name=workflow.sp_name or "your Service Provider",
            countersign_link=url_for(
                "fiesta_cosign.walkthrough",
                agreement_id=agreement.id,
                _external=True,
            ) if _has_app_context() else f"/cosign/{agreement.id}",
            agreement_reference=agreement.reference_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign countersign_prompt render failed: %s", exc)
        html_body = (
            f"<p>Hi {customer_name},</p>"
            f"<p>Don't forget -- your Service Provider has signed. Log in to "
            f"FIESTA to add your countersignature.</p>"
        )

    return _send_via_sendgrid(
        to_email=customer_email,
        to_name=customer_name,
        subject="Just a nudge -- your agreement is ready for your signature",
        html_body=html_body,
    )


def send_concern_to_support(*, workflow, message: str) -> Tuple[bool, str]:
    """SP raised a concern; route to support@lanka.tax."""
    subject = f"S10 SP concern raised on agreement {workflow.service_agreement_id}"
    html_body = (
        f"<h3>SP raised a concern via FIESTA co-sign</h3>"
        f"<p>Workflow id: {workflow.id}</p>"
        f"<p>Agreement id: {workflow.service_agreement_id}</p>"
        f"<p>SP email: {workflow.sp_email or '(unknown)'}</p>"
        f"<p>SP name: {workflow.sp_name or '(unknown)'}</p>"
        f"<p>Customer user_id: {workflow.user_id}</p>"
        f"<h4>Message</h4>"
        f"<pre>{(message or '(empty)')[:4000]}</pre>"
    )
    return _send_via_sendgrid(
        to_email=SUPPORT_EMAIL,
        to_name="Lanka.tax Support",
        subject=subject,
        html_body=html_body,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_app_context() -> bool:
    try:
        return bool(current_app)
    except RuntimeError:
        return False


__all__ = [
    "send_cosign_email",
    "notify_customer_sp_signed",
    "remind_customer_to_countersign",
    "send_concern_to_support",
]
