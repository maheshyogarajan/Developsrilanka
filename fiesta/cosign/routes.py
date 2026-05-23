"""fiesta.cosign.routes -- Flask blueprint for the S10 co-sign experience.

Wave 3 (2026-05-20). Per S10 dispatch brief.

Customer-side routes (login required, ownership-checked on every query)
---------------------------------------------------------------------
GET  /cosign/<agreement_id>                main walkthrough (post-S8)
POST /cosign/<agreement_id>/send-to-sp     send PDF + tracking link to SP
GET  /cosign/<agreement_id>/status         JSON status snapshot
POST /cosign/<agreement_id>/remind-sp      manual reminder trigger
POST /cosign/<agreement_id>/abandon        customer "I'll do it offline"
POST /cosign/<agreement_id>/countersign    customer countersigns after SP

SP-side routes (NO auth -- tracking-token-gated only)
-----------------------------------------------------
GET  /cosign/sp/<tracking_token>           SP signing page (PDF + sign form)
POST /cosign/sp/<tracking_token>/sign      SP types name + submits

Auth model
----------
Customer routes use Flask-Login. Ownership is enforced via:
    ServiceAgreement.user_id == current_user.id
on every load. SP routes are token-only -- a valid (not-expired, not-
already-signed) tracking_token is the proof of authorisation; the SP
sees only the agreement bound to that token. Tampered tokens -> 401.
Expired -> 410. Already-signed token -> 410 (single-use, prevents replay).

Wiring
------
Registered by main.py via:
    from fiesta.cosign.routes import register_routes as register_cosign
    register_cosign(app)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from fiesta.paywall.gate import paywall_required

logger = logging.getLogger(__name__)

bp = Blueprint("fiesta_cosign", __name__, url_prefix="/cosign")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip() -> str:
    """Best-effort client IP -- X-Forwarded-For first, then remote_addr."""
    forwarded = request.headers.get("X-Forwarded-For") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:60]
    return (request.remote_addr or "")[:60]


def _user_agent() -> str:
    return (request.headers.get("User-Agent") or "")[:255]


def _ownership_or_404(agreement_id: int):
    """Load a ServiceAgreement scoped to current_user; abort 404 otherwise."""
    from fiesta.agreements.models import ServiceAgreement  # late import

    row = ServiceAgreement.query.filter_by(
        id=agreement_id, user_id=int(getattr(current_user, "id", -1))
    ).first()
    if not row:
        abort(404)
    return row


def _get_or_create_workflow(agreement) -> "object":
    """Return (workflow, created_flag). One workflow per agreement; if the
    agreement was regenerated (S8 creates a new row each time) the new
    agreement gets a fresh workflow.
    """
    from fiesta.cosign.models import CosignWorkflow  # late import
    from app import db  # late import

    existing = CosignWorkflow.query.filter_by(
        service_agreement_id=agreement.id,
        user_id=agreement.user_id,
    ).first()
    if existing:
        return existing, False

    wf = CosignWorkflow(
        user_id=agreement.user_id,
        service_agreement_id=agreement.id,
    )
    try:
        db.session.add(wf)
        db.session.commit()
        return wf, True
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign workflow create failed: %s", exc)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return existing, False  # may be None on fresh DB error


def _send_sp_email(workflow, agreement, kind: str = "initial") -> tuple[bool, str]:
    """Send the SP outreach email. Returns (ok, status_message).

    kind == "initial" | "first_reminder" | "second_reminder" | "escalate"
    """
    from fiesta.cosign.email_sender import send_cosign_email  # late import

    try:
        ok, status = send_cosign_email(
            kind=kind,
            workflow=workflow,
            agreement=agreement,
        )
        return ok, status
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign email send failed: %s", exc)
        return False, f"error: {exc}"


# ---------------------------------------------------------------------------
# Customer-side: index (empty state + list of in-progress workflows)
# ---------------------------------------------------------------------------


@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S10", action="index")
def index():
    """Index page for /cosign.

    Customers reach /cosign by following a sidebar link or by manually typing
    the URL. Before this route existed (Wave 3 ship), /cosign returned a hard
    404 because the blueprint only registered /<int:agreement_id>. That dead-
    ended customers who had zero ServiceAgreements yet.

    Now: list the customer's existing CosignWorkflows (one per service
    agreement) with status pills + a "continue" link, and if there are none,
    show the empty-state CTA pointing at /agreements/service (where a
    workflow is born).
    """
    from fiesta.cosign.models import CosignWorkflow  # late import
    from fiesta.agreements.models import ServiceAgreement  # late import

    user_id = int(getattr(current_user, "id", -1))

    # Load workflows for the user, newest first. Join is best-effort -- if
    # the agreement row was hard-deleted we still surface the workflow row
    # so the customer can see "deleted" instead of a silent disappearance.
    workflows = (
        CosignWorkflow.query
        .filter_by(user_id=user_id)
        .order_by(CosignWorkflow.id.desc())
        .all()
    )

    # Hydrate agreement reference per workflow for the list UI.
    rows: list[dict[str, Any]] = []
    for wf in workflows:
        agreement = ServiceAgreement.query.filter_by(
            id=wf.service_agreement_id, user_id=user_id,
        ).first()
        rows.append({
            "workflow": wf,
            "agreement": agreement,
            "status": wf.status,
            "sp_email": wf.sp_email,
        })

    return render_template("cosign/index.html", rows=rows)


# ---------------------------------------------------------------------------
# Customer-side: walkthrough
# ---------------------------------------------------------------------------


@bp.route("/<int:agreement_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S10", action="walkthrough")
def walkthrough(agreement_id: int):
    """Main co-sign walkthrough screen. Post-S8 generation, pre-send."""
    agreement = _ownership_or_404(agreement_id)
    workflow, _ = _get_or_create_workflow(agreement)

    # Parse the SP snapshot so the template can prefill SP email if S6
    # captured it earlier.
    try:
        sp_snapshot = json.loads(agreement.sp_snapshot_json or "{}")
    except (ValueError, TypeError):
        sp_snapshot = {}

    sp_prefilled_email = sp_snapshot.get("notice_email") or ""
    sp_prefilled_name = sp_snapshot.get("name") or ""

    return render_template(
        "cosign/walkthrough.html",
        agreement=agreement,
        workflow=workflow,
        sp_prefilled_email=sp_prefilled_email,
        sp_prefilled_name=sp_prefilled_name,
        sp_snapshot=sp_snapshot,
    )


# ---------------------------------------------------------------------------
# Customer-side: send to SP
# ---------------------------------------------------------------------------


@bp.route("/<int:agreement_id>/send-to-sp", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S10", action="send_to_sp")
def send_to_sp(agreement_id: int):
    """Send the agreement PDF + signing link to the SP via SendGrid.

    Two paths from the walkthrough form:
      - mode="fiesta" -- we send the email + open the workflow for tracking
      - mode="offline" -- we DON'T send; we just mark the workflow as
                          handled offline (a degenerate, abandon-equivalent
                          state for tracking purposes -- customer downloads
                          the PDF and emails it themselves).
    """
    from fiesta.cosign.models import (
        CosignWorkflow,
        COSIGN_STATUS_SENT_TO_SP,
        COSIGN_STATUS_ABANDONED,
    )
    from app import db

    agreement = _ownership_or_404(agreement_id)
    workflow, _ = _get_or_create_workflow(agreement)
    if workflow is None:
        flash("Could not create co-sign workflow -- DB error.", "danger")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    if workflow.is_terminal:
        flash("This co-sign workflow is already complete or abandoned.", "warning")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    mode = (request.form.get("mode") or "fiesta").strip().lower()
    sp_email = (request.form.get("sp_email") or "").strip()
    sp_name = (request.form.get("sp_name") or "").strip()

    if mode == "offline":
        # Customer chose to handle it themselves -- abandon the FIESTA track.
        workflow.status = COSIGN_STATUS_ABANDONED
        workflow.abandoned_at = datetime.utcnow()
        if sp_email:
            workflow.sp_email = sp_email
        if sp_name:
            workflow.sp_name = sp_name
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("cosign abandon (offline) failed: %s", exc)
            db.session.rollback()
        flash("Marked as handled offline. Download the PDF below and email it to your Service Provider.", "info")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    # FIESTA-sent path -- require a valid SP email.
    if not sp_email or "@" not in sp_email:
        flash("A Service Provider email is required to send via FIESTA.", "danger")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    workflow.sp_email = sp_email
    workflow.sp_name = sp_name or workflow.sp_name

    ok, status = _send_sp_email(workflow, agreement, kind="initial")

    workflow.status = COSIGN_STATUS_SENT_TO_SP
    workflow.customer_email_sent_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign send-to-sp commit failed: %s", exc)
        db.session.rollback()

    if ok:
        flash(f"Sent to {sp_email}. They'll get a link to sign.", "success")
    else:
        flash(
            f"We saved the request but couldn't deliver email ({status}). "
            "You can resend from this page.",
            "warning",
        )

    return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))


# ---------------------------------------------------------------------------
# Customer-side: status JSON
# ---------------------------------------------------------------------------


@bp.route("/<int:agreement_id>/status", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S10", action="status_json")
def status_json(agreement_id: int):
    """Tracking-status JSON for AJAX polling from the walkthrough page."""
    from fiesta.cosign.models import CosignWorkflow

    agreement = _ownership_or_404(agreement_id)
    workflow = CosignWorkflow.query.filter_by(
        service_agreement_id=agreement.id,
        user_id=agreement.user_id,
    ).first()
    if not workflow:
        return jsonify({"status": "not_started"})

    return jsonify(
        {
            "status": workflow.status,
            "sp_email": workflow.sp_email,
            "customer_email_sent_at": (
                workflow.customer_email_sent_at.isoformat()
                if workflow.customer_email_sent_at
                else None
            ),
            "sp_email_clicked_at": (
                workflow.sp_email_clicked_at.isoformat()
                if workflow.sp_email_clicked_at
                else None
            ),
            "sp_signed_at": (
                workflow.sp_signed_at.isoformat() if workflow.sp_signed_at else None
            ),
            "customer_countersigned_at": (
                workflow.customer_countersigned_at.isoformat()
                if workflow.customer_countersigned_at
                else None
            ),
            "completed_at": (
                workflow.completed_at.isoformat() if workflow.completed_at else None
            ),
            "abandoned_at": (
                workflow.abandoned_at.isoformat() if workflow.abandoned_at else None
            ),
            "sp_typed_name": workflow.sp_typed_name,
            "sp_signing_method": workflow.sp_signing_method,
            "reminder_count": workflow.reminder_count,
        }
    )


# ---------------------------------------------------------------------------
# Customer-side: manual reminder
# ---------------------------------------------------------------------------


@bp.route("/<int:agreement_id>/remind-sp", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S10", action="remind_sp")
def remind_sp(agreement_id: int):
    """Customer manually triggers another reminder to the SP."""
    from fiesta.cosign.models import CosignWorkflow, CosignReminder
    from app import db

    agreement = _ownership_or_404(agreement_id)
    workflow = CosignWorkflow.query.filter_by(
        service_agreement_id=agreement.id,
        user_id=agreement.user_id,
    ).first()
    if not workflow or workflow.is_terminal or not workflow.is_in_progress:
        flash("No active workflow to remind.", "warning")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    if not workflow.sp_email:
        flash("No SP email on file -- cannot remind.", "danger")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    ok, status_msg = _send_sp_email(workflow, agreement, kind="first_reminder")
    rec = CosignReminder(
        workflow_id=workflow.id,
        kind="manual",
        sendgrid_status="ok" if ok else "failed",
        error_message=None if ok else status_msg,
    )
    workflow.last_reminder_at = datetime.utcnow()
    workflow.reminder_count = (workflow.reminder_count or 0) + 1
    try:
        db.session.add(rec)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign manual remind commit failed: %s", exc)
        db.session.rollback()

    if ok:
        flash("Reminder sent.", "success")
    else:
        flash(f"Reminder couldn't be delivered ({status_msg}).", "warning")

    return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))


# ---------------------------------------------------------------------------
# Customer-side: abandon
# ---------------------------------------------------------------------------


@bp.route("/<int:agreement_id>/abandon", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S10", action="abandon")
def abandon(agreement_id: int):
    """Customer marks the workflow as 'I did this offline, stop tracking'."""
    from fiesta.cosign.models import CosignWorkflow, COSIGN_STATUS_ABANDONED
    from app import db

    agreement = _ownership_or_404(agreement_id)
    workflow = CosignWorkflow.query.filter_by(
        service_agreement_id=agreement.id,
        user_id=agreement.user_id,
    ).first()
    if not workflow:
        flash("No workflow to abandon.", "warning")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))
    if workflow.is_terminal:
        flash("Workflow already in a terminal state.", "info")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    workflow.status = COSIGN_STATUS_ABANDONED
    workflow.abandoned_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign abandon commit failed: %s", exc)
        db.session.rollback()

    flash("Marked as handled offline. We won't send any more reminders.", "info")
    return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))


# ---------------------------------------------------------------------------
# Customer-side: countersign (after SP signs)
# ---------------------------------------------------------------------------


@bp.route("/<int:agreement_id>/countersign", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S10", action="countersign")
def countersign(agreement_id: int):
    """Customer countersigns after the SP has signed. Transitions to complete."""
    from fiesta.cosign.models import (
        CosignWorkflow,
        COSIGN_STATUS_SP_SIGNED,
        COSIGN_STATUS_CUSTOMER_COUNTERSIGNED,
        COSIGN_STATUS_COMPLETE,
    )
    from app import db

    agreement = _ownership_or_404(agreement_id)
    workflow = CosignWorkflow.query.filter_by(
        service_agreement_id=agreement.id,
        user_id=agreement.user_id,
    ).first()
    if not workflow:
        flash("No workflow to countersign.", "warning")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    if workflow.status != COSIGN_STATUS_SP_SIGNED:
        flash(
            "We're not at the countersign step yet. SP must sign first.",
            "warning",
        )
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    typed_name = (request.form.get("typed_name") or "").strip()
    if not typed_name:
        flash("Please type your full name to countersign.", "danger")
        return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))

    workflow.customer_typed_name = typed_name
    workflow.customer_signature_ip = _client_ip()
    workflow.customer_countersigned_at = datetime.utcnow()
    workflow.status = COSIGN_STATUS_CUSTOMER_COUNTERSIGNED
    workflow.completed_at = datetime.utcnow()
    workflow.status = COSIGN_STATUS_COMPLETE  # collapsed -- terminal
    try:
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign countersign commit failed: %s", exc)
        db.session.rollback()

    flash("Agreement complete -- both parties have signed.", "success")
    return redirect(url_for("fiesta_cosign.walkthrough", agreement_id=agreement_id))


# ---------------------------------------------------------------------------
# SP-side: signing page (token-gated, no auth)
# ---------------------------------------------------------------------------


@bp.route("/sp/<tracking_token>", methods=["GET"])
def sp_signing_page(tracking_token: str):
    """SP-side: view the agreement + sign options. Token-gated, no auth."""
    from fiesta.cosign.models import CosignWorkflow, COSIGN_STATUS_SENT_TO_SP, COSIGN_STATUS_SP_VIEWED
    from app import db

    # Token validation: format check + DB lookup.
    if not tracking_token or len(tracking_token) < 20:
        # Tampered / hand-typed token.
        abort(401)

    workflow = CosignWorkflow.query.filter_by(tracking_token=tracking_token).first()
    if not workflow:
        abort(401)

    if workflow.is_token_expired:
        abort(410)

    # Single-use: once SP has signed (or it's terminal), refuse re-access.
    if workflow.status in ("sp_signed", "customer_countersigned", "complete"):
        return render_template(
            "cosign/sp_already_signed.html",
            workflow=workflow,
        )

    if workflow.status == "abandoned":
        abort(410)

    # First view -- record click timestamp and bump status.
    if workflow.status == COSIGN_STATUS_SENT_TO_SP:
        workflow.status = COSIGN_STATUS_SP_VIEWED
        workflow.sp_email_clicked_at = datetime.utcnow()
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("cosign sp_viewed update failed: %s", exc)
            db.session.rollback()

    # Load the agreement (no user_id filter -- this is token-gated access).
    from fiesta.agreements.models import ServiceAgreement
    agreement = ServiceAgreement.query.filter_by(
        id=workflow.service_agreement_id
    ).first()
    if not agreement:
        abort(410)

    try:
        customer_snapshot = json.loads(agreement.customer_snapshot_json or "{}")
    except (ValueError, TypeError):
        customer_snapshot = {}
    customer_name = customer_snapshot.get("full_name") or "the customer"
    customer_email = customer_snapshot.get("notice_email") or ""

    return render_template(
        "cosign/sp_signing.html",
        workflow=workflow,
        agreement=agreement,
        customer_name=customer_name,
        customer_email=customer_email,
        tracking_token=tracking_token,
    )


# ---------------------------------------------------------------------------
# SP-side: submit signature
# ---------------------------------------------------------------------------


@bp.route("/sp/<tracking_token>/sign", methods=["POST"])
def sp_sign(tracking_token: str):
    """SP submits typed-name signature OR records a concern."""
    from fiesta.cosign.models import (
        CosignWorkflow,
        COSIGN_STATUS_SP_SIGNED,
        SIGNING_METHOD_TYPED_NAME,
        SIGNING_METHOD_PRINTED_PDF,
    )
    from app import db

    if not tracking_token or len(tracking_token) < 20:
        abort(401)

    workflow = CosignWorkflow.query.filter_by(tracking_token=tracking_token).first()
    if not workflow:
        abort(401)
    if workflow.is_token_expired:
        abort(410)
    if workflow.status in ("sp_signed", "customer_countersigned", "complete", "abandoned"):
        abort(410)

    action = (request.form.get("action") or "sign").strip().lower()

    if action == "concern":
        # SP raised a concern -- email Lanka.tax support@, mark decline.
        message = (request.form.get("concern_message") or "").strip()
        workflow.sp_declined_at = datetime.utcnow()
        workflow.sp_decline_message = message[:4000]
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("cosign sp_concern commit failed: %s", exc)
            db.session.rollback()
        # Best-effort handoff to support@ -- never raises.
        try:
            from fiesta.cosign.email_sender import send_concern_to_support
            send_concern_to_support(workflow=workflow, message=message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cosign concern -> support send failed: %s", exc)
        return render_template("cosign/sp_concern_received.html")

    typed_name = (request.form.get("typed_name") or "").strip()
    method = (request.form.get("method") or SIGNING_METHOD_TYPED_NAME).strip()

    if method not in (
        SIGNING_METHOD_TYPED_NAME,
        SIGNING_METHOD_PRINTED_PDF,
    ):
        method = SIGNING_METHOD_TYPED_NAME

    if method == SIGNING_METHOD_TYPED_NAME and not typed_name:
        return render_template(
            "cosign/sp_signing.html",
            workflow=workflow,
            agreement=_resolve_agreement(workflow),
            customer_name=_resolve_customer_name(workflow),
            customer_email=_resolve_customer_email(workflow),
            tracking_token=tracking_token,
            error="Please type your full name to sign.",
        )

    workflow.sp_typed_name = typed_name or "(uploaded scan)"
    workflow.sp_signing_method = method
    workflow.sp_signature_ip = _client_ip()
    workflow.sp_signature_ua = _user_agent()
    workflow.sp_signed_at = datetime.utcnow()
    workflow.status = COSIGN_STATUS_SP_SIGNED

    try:
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign sp_sign commit failed: %s", exc)
        db.session.rollback()

    # Notify the customer that the SP signed.
    try:
        agreement = _resolve_agreement(workflow)
        if agreement is not None:
            from fiesta.cosign.email_sender import notify_customer_sp_signed
            notify_customer_sp_signed(workflow=workflow, agreement=agreement)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cosign notify_customer_sp_signed failed: %s", exc)

    return render_template(
        "cosign/sp_signed_thank_you.html",
        workflow=workflow,
    )


# ---------------------------------------------------------------------------
# Internal helpers used by sp_sign on the error path
# ---------------------------------------------------------------------------


def _resolve_agreement(workflow):
    from fiesta.agreements.models import ServiceAgreement
    return ServiceAgreement.query.filter_by(
        id=workflow.service_agreement_id
    ).first()


def _resolve_customer_name(workflow) -> str:
    agreement = _resolve_agreement(workflow)
    if not agreement:
        return "the customer"
    try:
        snap = json.loads(agreement.customer_snapshot_json or "{}")
    except (ValueError, TypeError):
        snap = {}
    return snap.get("full_name") or "the customer"


def _resolve_customer_email(workflow) -> str:
    agreement = _resolve_agreement(workflow)
    if not agreement:
        return ""
    try:
        snap = json.loads(agreement.customer_snapshot_json or "{}")
    except (ValueError, TypeError):
        snap = {}
    return snap.get("notice_email") or ""


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_routes(app) -> None:
    """Register the cosign blueprint on a Flask app. Idempotent."""
    if "fiesta_cosign" in app.blueprints:
        return
    app.register_blueprint(bp)
    logger.info("FIESTA S10 cosign blueprint registered: /cosign/*")


__all__ = ["bp", "register_routes"]
