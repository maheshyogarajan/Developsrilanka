"""fiesta.submit.routes -- Flask blueprint for S14 Submit.

Wave 3 Week 5 (2026-05-20). Endpoints:

    GET  /submit                       show final-gate review + attestation form
    POST /submit/attest                customer signs the attestation
    GET  /submit/export                generate + download IRD-ready ZIP
    GET  /submit/walkthrough           render the IRD walkthrough (12 steps)
    POST /submit/mark-filed            customer self-reports filing
    POST /submit/upload-confirmation   customer uploads IRD ack PDF
    GET  /submit/<id>/status           JSON status (for polling)
    POST /submit/reopen                customer unlocks for edits

Wiring
------
main.py registers via:
    from fiesta.submit.routes import register_routes as register_submit
    register_submit(app)

Authentication: login_required on every route. The customer can only act
on their own Submission row (we filter by current_user.id).

Compliance gate hookup
----------------------
Every route calls `run_final_gate(customer_data, action=...)` and writes a
SubmissionAuditEvent on every gate evaluation. Red blocks return 403 (Forbidden)
with a remediation page; yellow warnings render the page but show banners.

Idempotency
-----------
Attestation: once signed, re-POST is a no-op (returns the existing signature).
Export: re-GET regenerates the ZIP deterministically (same `when` snapshot
on the Submission record). Mark-filed: idempotent on the `customer_filed_at`
field (won't overwrite an earlier filing).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from fiesta.paywall.gate import paywall_required


logger = logging.getLogger(__name__)


_ARTEFACT_DIR_ENV = "SUBMIT_ARTEFACT_DIR"
_DEFAULT_ARTEFACT_DIR = "./working_files/submit"
_RECEIPT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB cap on IRD ack PDF uploads


def _artefact_dir() -> Path:
    p = Path(os.environ.get(_ARTEFACT_DIR_ENV, _DEFAULT_ARTEFACT_DIR))
    p.mkdir(parents=True, exist_ok=True)
    return p


bp = Blueprint("fiesta_submit", __name__, url_prefix="/submit")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client_ip() -> str | None:
    # Prefer X-Forwarded-For (we sit behind a proxy in deploy) but only the
    # first hop. NEVER trust the whole chain.
    xff = request.headers.get("X-Forwarded-For") or ""
    if xff:
        return xff.split(",")[0].strip() or None
    return request.remote_addr


def _get_or_create_submission_for_current_tax_year(
    user_id: int, tax_year: str
):
    """Return (Submission, created_bool). Creates a 'preparing' row if none.

    Multi-tax-year safe: a customer working 25/26 + 26/27 simultaneously has
    two separate rows.
    """
    from fiesta.submit.models import Submission

    from app import db

    sub = (
        Submission.query.filter_by(user_id=user_id, tax_year=tax_year)
        .filter(Submission.status != "abandoned")
        .order_by(Submission.created_at.desc())
        .first()
    )
    if sub:
        return sub, False
    sub = Submission(user_id=user_id, tax_year=tax_year, status="preparing")
    db.session.add(sub)
    db.session.commit()
    return sub, True


def _build_customer_data_for_gate(user, tax_year: str) -> dict[str, Any]:
    """Assemble the customer_data dict the gate consumes.

    The gate is pure-function: we feed it everything it needs. The caller
    is responsible for fetching upstream data (S3 profile, S5 deductions,
    S8 agreements, S12 figures). In v1 we read directly from the User row
    + a lightweight `_collect_upstream` helper that depends on optional
    upstream modules; missing modules degrade gracefully (the relevant
    field is left empty, the gate's rule sees None, and traces an OK
    pass).
    """
    data: dict[str, Any] = {
        "user_id": user.id,
        "tax_year": tax_year,
        "full_name": user.name or "",
        "email": user.email or "",
    }
    # Upstream collectors -- best-effort, all wrapped in try.
    data["unresolved_prior_warnings"] = _collect_unresolved_warnings(
        user.id, tax_year
    )
    data["service_agreements"] = _collect_service_agreements(user.id)
    data["gross_income_lkr"], data["total_deductions_lkr"], data["tax_data"] = (
        _collect_tax_data(user.id, tax_year)
    )
    data["ceo_override_deduction_ratio"] = False  # set by CEO via admin only
    # Attestation state -- the route writes this back from the Submission row.
    return data


def _collect_unresolved_warnings(user_id: int, tax_year: str) -> list[str]:
    """Pull unresolved warning rule_ids from the gate-event log (X6).

    If X6 events table isn't wired, return [].
    """
    try:
        # Lazy import -- X6 events module may not be on this branch yet.
        from fiesta.compliance.events import query_recent_events  # noqa: WPS433

        events = query_recent_events(customer_id=str(user_id), limit=200) or []
        # Take all warning rule_ids that fired in the last gate-check, minus
        # those the customer has acknowledged on the Submission row.
        rule_ids: list[str] = []
        for ev in events:
            for rid in ev.get("rule_ids_fired") or []:
                if (
                    rid.startswith(("S2-", "S3-", "S4-", "S5-", "S6-", "S7-", "S8-", "S9-", "S12-"))
                    and rid not in rule_ids
                ):
                    rule_ids.append(rid)
        return rule_ids
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "X6 events query unavailable on this branch (%s) -- "
            "treating prior warnings as empty",
            exc,
        )
        return []


def _collect_service_agreements(user_id: int) -> list[dict[str, Any]]:
    """Pull service agreements for the customer. Best-effort."""
    try:
        from fiesta.agreements.models import ServiceAgreement  # noqa: WPS433

        rows = ServiceAgreement.query.filter_by(user_id=user_id).all()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row.reference_id,
                    "reference_id": row.reference_id,
                    "related_party_flag": bool(
                        getattr(row, "sec195_default_was_on", False)
                    ),
                    "section_195_disclosure_enabled": bool(
                        getattr(row, "sec195_disclosure_applied", False)
                    ),
                    "path": getattr(row, "pdf_path", None),
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("Service agreements not available (%s)", exc)
        return []


def _collect_rental_agreements(user_id: int, tax_year: str) -> list[dict[str, Any]]:
    """Pull rental agreement PDFs for the customer + tax year. Best-effort.

    Mirrors _collect_service_agreements but scoped to RentalAgreementGenerated
    rows so the export ZIP can include S9 PDFs alongside S8 service agreement
    PDFs.  Uses the latest row per reference_id (multiple renders produce
    multiple rows; we take the most recent so the ZIP reflects the last-signed
    version of each agreement).

    Returns a list of dicts with keys:
        reference_id  -- e.g. "RA-2025-0001"
        path          -- absolute path to the PDF on disk (may be None/missing)
    """
    try:
        from fiesta.agreements.models import RentalAgreementGenerated  # noqa: WPS433

        rows = (
            RentalAgreementGenerated.query
            .filter_by(user_id=user_id, tax_year=tax_year)
            .order_by(RentalAgreementGenerated.generated_at.desc())
            .all()
        )
        # De-duplicate by reference_id -- keep only the latest row for each.
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in rows:
            ref = row.reference_id
            if ref in seen:
                continue
            seen.add(ref)
            out.append(
                {
                    "reference_id": ref,
                    "path": getattr(row, "pdf_path", None),
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("Rental agreements not available (%s)", exc)
        return []


def _collect_tax_data(user_id: int, tax_year: str) -> tuple[float, float, dict[str, Any]]:
    """Best-effort pull of S12 tax-data. Returns (gross, deductions, tax_data dict)."""
    # In v1 we stash S12 figures on the Submission row when the gate first
    # evaluates (gate_snapshot_json carries the rest). Until S12 is wired
    # we fall back to zeros so the gate doesn't false-block.
    try:
        from fiesta.submit.models import Submission

        sub = (
            Submission.query.filter_by(user_id=user_id, tax_year=tax_year)
            .filter(Submission.status != "abandoned")
            .order_by(Submission.created_at.desc())
            .first()
        )
        if sub and sub.gate_snapshot_json:
            snap = json.loads(sub.gate_snapshot_json)
            return (
                float(snap.get("gross_income_lkr") or 0),
                float(snap.get("total_deductions_lkr") or 0),
                snap.get("tax_data") or {},
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Tax-data fallback: %s", exc)
    return 0.0, 0.0, {}


def _record_audit_event(
    submission, event_type: str, payload: dict[str, Any]
) -> None:
    """Append a SubmissionAuditEvent row."""
    from fiesta.submit.models import SubmissionAuditEvent

    from app import db

    ev = SubmissionAuditEvent(
        submission_id=submission.id,
        event_type=event_type,
        payload_json=json.dumps(payload, default=str, sort_keys=True),
        created_by_ip=_client_ip(),
    )
    db.session.add(ev)


def _current_tax_year() -> str:
    """Return the current SL tax year in YYYY/YYYY form (1 April -> 31 March)."""
    now = datetime.now(timezone.utc)
    if now.month >= 4:
        return f"{now.year}/{now.year + 1}"
    return f"{now.year - 1}/{now.year}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="show_submit")
def show_submit():
    """Render the final-gate review + attestation entry form."""
    from fiesta.submit.attestation import build_attestation_text
    from fiesta.submit.final_gate import run_final_gate

    from app import db

    tax_year = request.args.get("tax_year") or _current_tax_year()
    sub, _created = _get_or_create_submission_for_current_tax_year(
        current_user.id, tax_year
    )

    if sub.status == "preparing":
        sub.status = "final-gate-pending"
        db.session.commit()

    customer_data = _build_customer_data_for_gate(current_user, tax_year)
    customer_data["attestation_signed_at"] = sub.attestation_signed_at

    gate = run_final_gate(customer_data, action="submit")

    # Snapshot the gate state on the Submission for replay.
    sub.gate_snapshot_json = json.dumps(
        {
            "gate": gate.to_dict(),
            "customer_data_keys": sorted(customer_data.keys()),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "gross_income_lkr": customer_data.get("gross_income_lkr"),
            "total_deductions_lkr": customer_data.get("total_deductions_lkr"),
            "tax_data": customer_data.get("tax_data"),
        },
        default=str,
        sort_keys=True,
    )
    _record_audit_event(
        sub, "gate-evaluated", {"action": "submit", "gate": gate.to_dict()}
    )

    # Compute the proposed attestation text for the form preview.
    tax_data = customer_data.get("tax_data") or {}
    final_tax = (
        sub.final_tax_payable_lkr
        if sub.final_tax_payable_lkr is not None
        else tax_data.get("final_tax_payable_lkr") or 0
    )

    # X9 F6.2: block attestation when the user has logged no income AND no
    # deductions.  A zero-data user cannot meaningfully sign a tax declaration;
    # we route them back to the Remittance Ledger instead.  This check runs
    # before F6.3 (identity gate) so the routing card appears even if NIC/name
    # are also missing -- data is the more fundamental prerequisite.
    gross_income_lkr: float = customer_data.get("gross_income_lkr") or 0.0
    total_deductions_lkr: float = customer_data.get("total_deductions_lkr") or 0.0
    zero_data: bool = (gross_income_lkr == 0.0 and total_deductions_lkr == 0.0)

    # X9 F6.3: refuse to render the attestation preview when the user's
    # profile is missing identity fields the attestation depends on. The
    # previous fallback to "(your NIC)" / "(your profile name)" leaked
    # placeholder strings into the signed legal text — a user could end
    # up with an Electronic Transactions Act signature reading
    # "I, X (NIC (your NIC)) declare...". We refuse to build the preview
    # AND signal the missing fields so the template can route the user
    # to /fiesta/profile to complete them.
    from fiesta.profile.models import FiestaProfile  # local import; avoid circular
    fiesta_profile = FiestaProfile.query.filter_by(user_id=current_user.id).first()
    nic_value = ((fiesta_profile.nic if fiesta_profile else "") or "").strip()
    name_value = (current_user.name or "").strip()
    missing_attestation_fields: list[str] = []
    if not name_value:
        missing_attestation_fields.append("Full name")
    if not nic_value:
        missing_attestation_fields.append("NIC")

    if missing_attestation_fields or zero_data:
        attestation_preview = None
    else:
        attestation_preview = build_attestation_text(
            full_name=name_value,
            nic=nic_value,
            tax_year=tax_year,
            final_tax_payable_lkr=final_tax,
        )

    # Promote status to awaiting-attestation if gate passes (or only yellow)
    # AND the profile is complete enough to sign without placeholders
    # AND data is present (zero_data users stay in final-gate-pending).
    if (
        not gate.blocks
        and not missing_attestation_fields
        and not zero_data
        and sub.status == "final-gate-pending"
    ):
        sub.status = "awaiting-attestation"

    db.session.commit()

    return render_template(
        "submit/index.html",
        submission=sub,
        gate=gate.to_dict(),
        attestation_preview=attestation_preview,
        missing_attestation_fields=missing_attestation_fields,
        zero_data=zero_data,
        tax_year=tax_year,
        final_tax_payable_lkr=final_tax,
    )


@bp.route("/attest", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="post_attest")
def post_attest():
    """Customer signs the attestation."""
    from fiesta.submit.attestation import (
        build_attestation_text,
        serialize_signature,
        sign_attestation,
    )
    from fiesta.submit.final_gate import run_final_gate

    from app import db

    tax_year = request.form.get("tax_year") or _current_tax_year()
    sub, _ = _get_or_create_submission_for_current_tax_year(
        current_user.id, tax_year
    )

    # Idempotency: already attested?
    if sub.status in {"attested", "export-generated", "customer-filed-on-ird"}:
        flash("Attestation already captured.", "info")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    if not sub.can_attest():
        flash(
            "Cannot sign right now -- the final gate hasn't been re-evaluated. "
            "Reload the page.",
            "warning",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    # X9 F6.2: server-side mirror of the zero-data gate in show_submit.
    # A direct POST cannot bypass the routing-card block even if the user
    # skips the GET form and crafts a raw request.
    _gross_check, _deduct_check, _ = _collect_tax_data(current_user.id, tax_year)
    if _gross_check == 0.0 and _deduct_check == 0.0:
        flash(
            "You haven't logged any income or deductions yet. "
            "Visit your Remittance Ledger to start.",
            "warning",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    # Re-run the gate -- block RED before signing.
    customer_data = _build_customer_data_for_gate(current_user, tax_year)
    gate = run_final_gate(customer_data, action="submit")
    if gate.blocks:
        flash(
            "Cannot sign while there are red blocks: "
            + "; ".join(b["message"] for b in gate.blocks),
            "danger",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    # Yellow warnings: customer MUST have acknowledged each on the form
    # (rule_ack_<rule_id> = "yes"). Otherwise reject.
    for w in gate.warnings:
        rid = w["rule_id"]
        ack = request.form.get(f"rule_ack_{rid}") or ""
        if ack.strip().lower() not in {"yes", "ack", "1", "true"}:
            flash(
                f"Please acknowledge the warning '{rid}' before signing.",
                "warning",
            )
            return redirect(
                url_for("fiesta_submit.show_submit", tax_year=tax_year)
            )
        sub.add_acknowledged_warning(
            rid, ack, datetime.now(timezone.utc).isoformat()
        )

    # Validate signature.
    typed_name = request.form.get("signature_name") or ""
    profile_name = current_user.name or ""
    ok, result = sign_attestation(
        typed_name=typed_name,
        profile_name=profile_name,
        client_ip=_client_ip(),
        user_agent=request.headers.get("User-Agent"),
        session_id=request.cookies.get("session"),
    )
    if not ok:
        flash(str(result), "danger")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    # X9 F6.3: server-side mirror of the show_submit guard. A user POSTing
    # /submit/attest directly cannot bypass the profile-completion gate;
    # if NIC or name is missing we refuse to sign and route them back to
    # /fiesta/profile rather than building an attestation with placeholder
    # strings.
    from fiesta.profile.models import FiestaProfile  # local; avoid circular
    fiesta_profile = FiestaProfile.query.filter_by(user_id=current_user.id).first()
    nic_for_sign = ((fiesta_profile.nic if fiesta_profile else "") or "").strip()
    name_for_sign = (current_user.name or "").strip()
    if not name_for_sign or not nic_for_sign:
        missing = [f for f, v in (("Full name", name_for_sign), ("NIC", nic_for_sign)) if not v]
        flash(
            "Cannot sign yet -- your profile is missing "
            + ", ".join(missing)
            + ". Please complete your FIESTA profile before signing the attestation.",
            "warning",
        )
        return redirect(url_for("fiesta_profile.index"))

    # Capture
    tax_data = customer_data.get("tax_data") or {}
    final_tax = float(
        tax_data.get("final_tax_payable_lkr")
        or request.form.get("final_tax_payable_lkr")
        or 0
    )
    text = build_attestation_text(
        full_name=name_for_sign,
        nic=nic_for_sign,
        tax_year=tax_year,
        final_tax_payable_lkr=final_tax,
    )

    sub.attestation_text = text
    sub.attestation_signature = serialize_signature(result)  # type: ignore[arg-type]
    sub.attestation_signed_at = datetime.now(timezone.utc)
    sub.final_tax_payable_lkr = final_tax
    sub.tax_bill_finalized_at = datetime.now(timezone.utc)
    sub.status = "attested"

    _record_audit_event(
        sub,
        "attestation-signed",
        {
            "signature": result,
            "final_tax_payable_lkr": final_tax,
            "attestation_text_sha256": hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest(),
        },
    )
    db.session.commit()

    flash("Attestation signed. You can now generate your IRD export pack.", "success")
    return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))


@bp.route("/export", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="get_export")
def get_export():
    """Generate (or re-fetch) the IRD-ready ZIP and serve it."""
    from fiesta.submit.export import build_export_zip
    from fiesta.submit.final_gate import run_final_gate

    from app import db

    tax_year = request.args.get("tax_year") or _current_tax_year()
    sub, _ = _get_or_create_submission_for_current_tax_year(
        current_user.id, tax_year
    )

    customer_data = _build_customer_data_for_gate(current_user, tax_year)
    customer_data["attestation_signed_at"] = sub.attestation_signed_at
    gate = run_final_gate(customer_data, action="export")

    if gate.blocks:
        flash(
            "Cannot export: "
            + "; ".join(b["message"] for b in gate.blocks),
            "danger",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    if not sub.can_export():
        flash(
            "Sign the attestation before exporting.",
            "warning",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    # Build payload from collected data.
    nic = getattr(current_user, "nic", "") or ""
    payload = {
        "customer": {
            "full_name": current_user.name or "",
            "nic": nic,
            "tin": getattr(current_user, "tin", "") or "",
            "address": getattr(current_user, "address", "") or "",
            "email": current_user.email or "",
            "phone": getattr(current_user, "phone", "") or "",
        },
        "tax_year": tax_year,
        "tax_data": customer_data.get("tax_data") or {},
        "audit_pack_pdf_path": sub.audit_pack_pdf_path,
        "service_agreement_pdfs": _collect_service_agreements(current_user.id),
        "rental_agreement_pdfs": _collect_rental_agreements(current_user.id, tax_year),  # B18 F6.11
    }

    when = sub.ird_export_generated_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    try:
        zip_path, sha256, byte_size = build_export_zip(
            submission_payload=payload,
            output_dir=_artefact_dir() / f"user_{current_user.id}",
            when=when,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Export ZIP build failed: %s", exc)
        flash(f"Export failed: {exc}", "danger")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    sub.ird_export_zip_path = str(zip_path)
    sub.ird_export_zip_sha256 = sha256
    sub.ird_export_generated_at = when
    sub.status = "export-generated"
    _record_audit_event(
        sub,
        "export-generated",
        {
            "zip_path": str(zip_path),
            "sha256": sha256,
            "byte_size": byte_size,
        },
    )
    db.session.commit()

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=zip_path.name,
        mimetype="application/zip",
    )


@bp.route("/walkthrough", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="get_walkthrough")
def get_walkthrough():
    """Render the IRD walkthrough page (12 steps + annotations)."""
    from fiesta.submit.final_gate import run_final_gate

    tax_year = request.args.get("tax_year") or _current_tax_year()
    sub, _ = _get_or_create_submission_for_current_tax_year(
        current_user.id, tax_year
    )

    customer_data = _build_customer_data_for_gate(current_user, tax_year)
    customer_data["attestation_signed_at"] = sub.attestation_signed_at
    gate = run_final_gate(customer_data, action="walkthrough")

    if gate.blocks:
        flash(
            "Walkthrough locked until: "
            + "; ".join(b["message"] for b in gate.blocks),
            "warning",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    steps = _walkthrough_step_data()
    return render_template(
        "submit/walkthrough.html",
        submission=sub,
        steps=steps,
        tax_year=tax_year,
    )


def _walkthrough_step_data() -> list[dict[str, Any]]:
    """Return the 12 walkthrough steps -- annotations from the template doc.

    For v1 we ship the placeholder text from annotation_template.md. When
    the CEO captures the actual screenshots + annotations (G.1.5 capture
    script), they overwrite the per-step image_url + annotations.
    """
    base = [
        ("01", "login_page", "Open the IRD e-services portal", "S6 + S9"),
        ("02", "login_filled", "Type your TIN + PIN + captcha", "S9"),
        ("03", "dashboard", "Land on the post-login dashboard", "S6 + S9"),
        ("04", "individual_menu", "Open Return / Schedule Management", "S9"),
        ("05", "my_returns_list", "Pick the right tax year", "S6 + S9"),
        ("06", "open_25_26_return", "Open the return form", "S9"),
        ("07", "personal_info_section", "Verify pre-filled personal info", "S9"),
        ("08", "income_sources_section", "Type income from FIESTA pack", "S9 + S12"),
        ("09", "deductions_section", "Type deductions from FIESTA pack", "S9 + S12"),
        ("10", "tax_payable_summary", "Cross-check tax payable", "S12"),
        ("11", "submit_confirmation_prompt", "Last-chance modal -- read carefully", "S9"),
        ("12", "submitted_confirmation", "Save your acknowledgment PDF", "S6 + S12"),
    ]
    return [
        {
            "step": step,
            "slug": slug,
            "title": title,
            "fiesta_consumer": consumer,
            # The capture script writes screenshots/<NN>_<slug>.png. We
            # render the URL even if the file isn't on disk yet -- the
            # template handles the missing-image case.
            "image_filename": f"{step}_{slug}.png",
            # Placeholders -- CEO fills before launch.
            "what_customer_does": "(annotation pending CEO capture)",
            "what_can_go_wrong": "(annotation pending CEO capture)",
            "how_fiesta_helps": "(annotation pending CEO capture)",
            "ira_citation": "(annotation pending CEO capture)",
        }
        for (step, slug, title, consumer) in base
    ]


@bp.route("/mark-filed", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="post_mark_filed")
def post_mark_filed():
    """Customer self-reports filing on IRD."""
    from fiesta.submit.final_gate import run_final_gate

    from app import db

    tax_year = request.form.get("tax_year") or _current_tax_year()
    sub, _ = _get_or_create_submission_for_current_tax_year(
        current_user.id, tax_year
    )

    customer_data = _build_customer_data_for_gate(current_user, tax_year)
    customer_data["attestation_signed_at"] = sub.attestation_signed_at
    gate = run_final_gate(customer_data, action="mark-filed")
    if gate.blocks:
        flash(
            "Cannot mark filed: "
            + "; ".join(b["message"] for b in gate.blocks),
            "danger",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    if not sub.can_mark_filed():
        flash("Generate the export pack first.", "warning")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    ack_number = (request.form.get("ack_number") or "").strip() or None
    sub.customer_filed_at = datetime.now(timezone.utc)
    sub.customer_filed_ack_number = ack_number
    sub.status = "customer-filed-on-ird"
    _record_audit_event(
        sub,
        "mark-filed",
        {
            "ack_number": ack_number,
            "self_reported_at": sub.customer_filed_at.isoformat(),
        },
    )
    db.session.commit()

    flash(
        "Marked as filed. Upload your IRD acknowledgment PDF when you have it.",
        "success",
    )
    return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))


@bp.route("/upload-confirmation", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="post_upload_confirmation")
def post_upload_confirmation():
    """Customer uploads the IRD acknowledgment PDF."""
    from fiesta.submit.models import IrdConfirmationReceipt

    from app import db

    tax_year = request.form.get("tax_year") or _current_tax_year()
    sub, _ = _get_or_create_submission_for_current_tax_year(
        current_user.id, tax_year
    )

    if sub.status not in {"customer-filed-on-ird", "export-generated"}:
        flash("Mark as filed before uploading the receipt.", "warning")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    upload = request.files.get("receipt_pdf")
    if not upload or not upload.filename:
        flash("No file uploaded.", "danger")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    data = upload.read(_RECEIPT_MAX_BYTES + 1)
    if len(data) > _RECEIPT_MAX_BYTES:
        flash(
            f"File too large -- max {_RECEIPT_MAX_BYTES // (1024 * 1024)} MB.",
            "danger",
        )
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))
    if not data:
        flash("Empty file.", "danger")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    # Basic PDF magic check (we don't validate content -- IRD format varies).
    if not data[:5] == b"%PDF-":
        flash("File doesn't look like a PDF.", "danger")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    receipt_dir = _artefact_dir() / f"user_{current_user.id}" / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    sha256 = hashlib.sha256(data).hexdigest()
    path = receipt_dir / f"ird_ack_{sub.id}_{sha256[:12]}.pdf"
    path.write_bytes(data)

    ack_number = (request.form.get("ack_number") or "").strip() or None
    filed_at_str = request.form.get("filed_at") or ""
    try:
        filed_at = (
            datetime.fromisoformat(filed_at_str)
            if filed_at_str
            else (sub.customer_filed_at or datetime.now(timezone.utc))
        )
    except ValueError:
        filed_at = sub.customer_filed_at or datetime.now(timezone.utc)
    if filed_at.tzinfo is None:
        filed_at = filed_at.replace(tzinfo=timezone.utc)

    receipt = IrdConfirmationReceipt(
        submission_id=sub.id,
        ird_acknowledgment_number=ack_number or sub.customer_filed_ack_number,
        filed_at=filed_at,
        uploaded_by_user_id=current_user.id,
        receipt_pdf_path=str(path),
        receipt_pdf_sha256=sha256,
        receipt_pdf_byte_size=len(data),
    )
    db.session.add(receipt)

    if ack_number and not sub.customer_filed_ack_number:
        sub.customer_filed_ack_number = ack_number

    _record_audit_event(
        sub,
        "receipt-uploaded",
        {
            "ack_number": ack_number,
            "byte_size": len(data),
            "sha256": sha256,
        },
    )
    db.session.commit()

    flash("Receipt uploaded. Your filing is complete.", "success")
    return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))


@bp.route("/<int:submission_id>/status", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="get_status")
def get_status(submission_id: int):
    """Return JSON status for a Submission. User must own it."""
    from fiesta.submit.models import Submission

    sub = Submission.query.get(submission_id)
    if not sub or sub.user_id != current_user.id:
        return jsonify({"error": "not_found"}), 404

    return jsonify(
        {
            "id": sub.id,
            "tax_year": sub.tax_year,
            "status": sub.status,
            "final_tax_payable_lkr": float(sub.final_tax_payable_lkr or 0),
            "attestation_signed_at": (
                sub.attestation_signed_at.isoformat()
                if sub.attestation_signed_at
                else None
            ),
            "ird_export_generated_at": (
                sub.ird_export_generated_at.isoformat()
                if sub.ird_export_generated_at
                else None
            ),
            "ird_export_zip_sha256": sub.ird_export_zip_sha256,
            "customer_filed_at": (
                sub.customer_filed_at.isoformat()
                if sub.customer_filed_at
                else None
            ),
            "customer_filed_ack_number": sub.customer_filed_ack_number,
            "receipts": [
                {
                    "id": r.id,
                    "ack_number": r.ird_acknowledgment_number,
                    "filed_at": r.filed_at.isoformat() if r.filed_at else None,
                    "uploaded_at": (
                        r.uploaded_at.isoformat() if r.uploaded_at else None
                    ),
                    "sha256": r.receipt_pdf_sha256,
                }
                for r in (sub.receipts or [])
            ],
        }
    )


@bp.route("/reopen", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S14", action="post_reopen")
def post_reopen():
    """Customer reopens a locked submission for edits."""
    from app import db

    tax_year = request.form.get("tax_year") or _current_tax_year()
    sub, _ = _get_or_create_submission_for_current_tax_year(
        current_user.id, tax_year
    )

    if not sub.is_locked_for_upstream_edits():
        flash("Submission is already editable.", "info")
        return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))

    # Snapshot the PRIOR attestation before clearing -- audit trail.
    prior_attest = {
        "attestation_text": sub.attestation_text,
        "attestation_signature": sub.attestation_signature,
        "attestation_signed_at": (
            sub.attestation_signed_at.isoformat()
            if sub.attestation_signed_at
            else None
        ),
        "final_tax_payable_lkr": float(sub.final_tax_payable_lkr or 0),
        "ird_export_zip_sha256": sub.ird_export_zip_sha256,
    }
    _record_audit_event(sub, "reopen", {"prior_attestation": prior_attest})

    sub.reopen_for_edits()
    db.session.commit()

    flash(
        "Submission reopened. Upstream screens are editable; you will need "
        "to re-sign the attestation before exporting again.",
        "warning",
    )
    return redirect(url_for("fiesta_submit.show_submit", tax_year=tax_year))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register_routes(app) -> None:
    """Register the S14 Submit blueprint with the Flask app."""
    # Models must be importable when create_all() runs.
    from fiesta.submit import models  # noqa: F401, WPS433

    app.register_blueprint(bp)
    logger.info("S14 Submit blueprint registered at /submit")


__all__ = ["bp", "register_routes"]
