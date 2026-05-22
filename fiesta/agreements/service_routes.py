"""fiesta.agreements.service_routes -- Flask blueprint for S8 Service Agreement generator.

Wave 3 (2026-05-20).

Routes
------
GET  /agreements/service/<sp_id>             preview form (term + fees + variants)
POST /agreements/service/<sp_id>/generate    generate PDF -> redirect to /pdf/<gen_id>
GET  /agreements/service/<sp_id>/pdf/<gen_id> download the PDF (application/pdf)
GET  /agreements/service/<sp_id>/history     list prior generations
GET  /agreements/service/<sp_id>/preview_json/<gen_id>  JSON disclosure snapshot (audit)

Wiring
------
Registered by main.py via:
    from fiesta.agreements.service_routes import register_routes as register_agreements
    register_agreements(app)

PDF artefact storage
--------------------
By default we write to AGREEMENT_ARTEFACT_DIR (env var, default
"./working_files/agreements"). S3 hook is reserved on the model
(pdf_s3_key) for a later deploy; not wired in v0.1.

Compliance gate
---------------
We call fiesta.compliance.gate.gate_check("S8", customer_data, "generate")
BEFORE rendering. If gate.blocks is non-empty, we refuse generation and
surface the block to the user.

Login
-----
Login-required. The customer can only see / generate / download their own
agreements (user_id filter on every query). Auth check uses
flask_login.current_user.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    url_for,
)
from flask_login import current_user, login_required

from fiesta.paywall.gate import paywall_required

logger = logging.getLogger(__name__)


# Reserved import + module names; we lazy-import below in functions so the
# blueprint can be imported in tests that don't have the full Flask app
# context.


_ARTEFACT_DIR_ENV = "AGREEMENT_ARTEFACT_DIR"
_DEFAULT_ARTEFACT_DIR = "./working_files/agreements"


def _artefact_dir() -> Path:
    p = Path(os.environ.get(_ARTEFACT_DIR_ENV, _DEFAULT_ARTEFACT_DIR))
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_agreements_service",
    __name__,
    url_prefix="/agreements/service",
)


# ---------------------------------------------------------------------------
# Inline HTML templates (render_template_string) -- only _HISTORY_PAGE
# remains here. The S8 preview was extracted to
# templates/agreements/service_preview.html (B2 — F5.3).
# ---------------------------------------------------------------------------
_HISTORY_PAGE = """
<!doctype html>
<html><head><title>Service Agreement -- history</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 760px; margin: 30px auto;">
<h1>Generated Service Agreements -- history</h1>
{% if rows %}
<table border="1" cellpadding="6" cellspacing="0">
  <tr><th>Reference</th><th>Generated</th><th>Variants</th><th>§195</th><th>PDF</th></tr>
  {% for r in rows %}
  <tr>
    <td>{{ r.reference_id }}</td>
    <td>{{ r.generated_at }}</td>
    <td>fee={{ r.fee_structure_variant }} ip={{ r.ip_variant }} law={{ r.governing_law_variant }}</td>
    <td>{{ "YES" if r.sec195_disclosure_applied else "no" }}</td>
    <td><a href="{{ url_for('fiesta_agreements_service.download_pdf', sp_id=r.service_provider_id, gen_id=r.id) }}">download</a></td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p>No agreements generated yet.</p>
{% endif %}
<p><a href="{{ url_for('fiesta_agreements_service.preview', sp_id=sp_id) }}">Back to generator</a></p>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_initials(user) -> str:
    """Derive 2-letter initials from a Flask-Login user; fall back to 'XX'."""
    name = ""
    for attr in ("full_name", "name", "first_name", "email", "username"):
        v = getattr(user, attr, None)
        if v:
            name = str(v)
            break
    if not name:
        return "XX"
    parts = [p for p in re.split(r"\s+", name) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return "XX"


def _customer_dict_from_user(user) -> dict[str, Any]:
    """Snapshot a logged-in FIESTA user's KYC fields onto the customer dict."""
    return {
        "full_name": getattr(user, "full_name", None) or getattr(user, "name", None),
        "nic": getattr(user, "nic", None),
        "tin": getattr(user, "tin", None),
        "address": getattr(user, "address", None),
        "bank": getattr(user, "bank_name", None),
        "account": getattr(user, "bank_account", None),
        "notice_email": getattr(user, "email", None),
        "stated_relationship_to_service_provider": (
            getattr(user, "sp_relationship", None)
        ),
    }


def _service_provider_dict(sp_id: str, form: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the service_provider dict.

    In v0.1 we accept form-supplied counterparty details directly (a future
    iteration will fetch from a ServiceProvider model). The sp_id is opaque
    so we don't 404 on missing records.
    """
    form = form or {}
    return {
        "name": form.get("sp_name") or f"Service Provider {sp_id}",
        "entity_type": form.get("sp_entity_type"),
        "jurisdiction": form.get("sp_jurisdiction"),
        "address": form.get("sp_address"),
        "registration_number": form.get("sp_registration_number"),
        "signatory_name": form.get("sp_signatory_name"),
        "signatory_title": form.get("sp_signatory_title"),
        "notice_email": form.get("sp_notice_email"),
    }


def _parse_date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parameters_from_form(form: dict[str, Any]) -> dict[str, Any]:
    return {
        "agreement_date": date.today().isoformat(),
        "services_description": (form.get("services_description") or "").strip(),
        "fee_structure_variant": (form.get("fee_structure_variant") or "A")[:1],
        "ip_variant": (form.get("ip_variant") or "A")[:1],
        "governing_law_variant": (form.get("governing_law_variant") or "A")[:1],
        "renewal_variant": (form.get("renewal_variant") or "A")[:1],
        "currency": (form.get("currency") or "LKR").upper(),
        "monthly_fee_amount": form.get("monthly_fee_amount") or None,
        "hourly_rate": form.get("hourly_rate") or None,
        "start_date": form.get("start_date") or None,
        "end_date": form.get("end_date") or None,
        "chosen_law": form.get("chosen_law") or None,
        "arbitration_rules": form.get("arbitration_rules") or None,
        "arbitration_seat": form.get("arbitration_seat") or None,
    }


def _persist_agreement(
    *,
    user_id: int,
    service_provider_id: str,
    customer: dict[str, Any],
    service_provider: dict[str, Any],
    parameters: dict[str, Any],
    result,
    gate_result,
) -> int:
    """Write the ServiceAgreement row + PDF artefact to disk; return row id.

    Returns 0 if persistence fails (DB unavailable, etc.); caller decides
    how to surface that to the user. This keeps the PDF artefact in hand
    even when the DB is down.
    """
    from fiesta.agreements.models import ServiceAgreement  # late import
    from app import db  # late import

    pdf_path = _artefact_dir() / f"{result.reference_id}.pdf"
    try:
        pdf_path.write_bytes(result.pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.error("agreement pdf write failed: %s", exc)
        return 0

    term_start = _parse_date(parameters.get("start_date"))
    term_end = _parse_date(parameters.get("end_date"))
    monthly_fee_lkr = None
    if parameters.get("monthly_fee_amount") and parameters.get("currency") == "LKR":
        try:
            monthly_fee_lkr = float(parameters["monthly_fee_amount"])
        except (TypeError, ValueError):
            monthly_fee_lkr = None

    row = ServiceAgreement(
        user_id=user_id,
        service_provider_id=str(service_provider_id),
        reference_id=result.reference_id,
        template_version=result.template_version,
        generated_at=result.generated_at,
        generated_by_ip=(request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:60],
        customer_snapshot_json=json.dumps(_sanitise_for_json(customer)),
        sp_snapshot_json=json.dumps(_sanitise_for_json(service_provider)),
        parameters_snapshot_json=json.dumps(_sanitise_for_json(parameters)),
        governing_law_variant=parameters.get("governing_law_variant", "A"),
        fee_structure_variant=parameters.get("fee_structure_variant", "A"),
        ip_variant=parameters.get("ip_variant", "A"),
        renewal_variant=parameters.get("renewal_variant", "A"),
        currency=parameters.get("currency", "LKR"),
        term_start=term_start,
        term_end=term_end,
        monthly_fee_lkr=monthly_fee_lkr,
        pdf_path=str(pdf_path),
        pdf_sha256=result.sha256,
        pdf_byte_size=result.byte_size,
        sec195_disclosure_applied=bool(result.disclosure.should_render),
        sec195_default_was_on=bool(result.disclosure.detector_default_on),
        sec195_override_reason=result.disclosure.customer_override_reason,
        sec195_confidence=float(result.disclosure.confidence),
        sec195_signals_json=json.dumps(list(result.disclosure.signals)),
        gate_passed=bool(gate_result.passed) if gate_result else True,
        gate_warnings_count=len(gate_result.warnings) if gate_result else 0,
        gate_blocks_count=len(gate_result.blocks) if gate_result else 0,
        gate_trace_json=(
            json.dumps(gate_result.reasoning_trace) if gate_result else None
        ),
    )

    try:
        db.session.add(row)
        db.session.commit()
        return int(row.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("agreement DB persist failed: %s", exc)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


def _sanitise_for_json(d: dict[str, Any]) -> dict[str, Any]:
    """Drop unserialisable values + unicode-normalise strings."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = (
                unicodedata.normalize("NFKC", v) if isinstance(v, str) else v
            )
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        else:
            try:
                out[k] = json.loads(json.dumps(v))
            except (TypeError, ValueError):
                out[k] = str(v)
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("", methods=["GET"], strict_slashes=False)
@bp.route("/", methods=["GET"], strict_slashes=False)
@login_required
def index():
    """HOTFIX 2026-05-22 — Bare-prefix landing for /agreements/service.

    Sidebar nav links to /agreements/service (no sp_id). Per-record preview
    routes require an id, so the bare prefix used to 404. Behaviour:
      - 0 service providers: flash + redirect to /service-providers (add one)
      - 1 SP:                redirect straight to that SP's preview
      - >1 SPs:              redirect to /service-providers listing (each
                             card already has a "Generate agreement" button
                             per B5)
    """
    from fiesta.service_providers.models import ServiceProvider  # type: ignore[import-not-found]
    user_id = getattr(current_user, "id", None)
    sps = ServiceProvider.query.filter_by(user_id=user_id).all()
    if not sps:
        flash(
            "Add a service provider first — then we'll generate the agreement.",
            "info",
        )
        return redirect("/service-providers")
    if len(sps) == 1:
        return redirect(url_for("fiesta_agreements_service.preview", sp_id=sps[0].id))
    return redirect("/service-providers")


@bp.route("/<sp_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S8", action="preview")
def preview(sp_id: str):
    """Preview / parameter-input screen for the Service Agreement."""
    from fiesta.compliance import gate_check  # late import
    from fiesta.agreements.disclosure import decide_disclosure, DisclosureDecisionInput
    from fiesta.agreements.helpers import compute_protected_deductions_lkr  # B4 F5.5

    customer = _customer_dict_from_user(current_user)
    service_provider = _service_provider_dict(sp_id)

    gate = gate_check("S8", {**customer, "service_provider": service_provider}, "preview")

    decision = decide_disclosure(
        DisclosureDecisionInput(
            customer=customer,
            service_provider=service_provider,
        )
    )

    # B4 — resolve SP ORM object for the savings projection (best-effort).
    sp_obj = None
    try:
        from fiesta.service_providers.models import ServiceProvider  # type: ignore[import-not-found]
        sp_obj = ServiceProvider.query.filter_by(
            id=sp_id, user_id=int(getattr(current_user, "id", -1))
        ).first()
    except Exception:  # noqa: BLE001
        pass  # SP model unavailable in test context — helper returns 0

    protected_lkr = compute_protected_deductions_lkr(
        current_user, sp_obj, is_property=False
    )

    return render_template(
        "agreements/service_preview.html",
        sp_id=sp_id,
        disclosure_default_on=decision.detector_default_on,
        evidence_prompt=decision.evidence_prompt,
        gate_warnings=gate.warnings,
        gate_blocks=gate.blocks,
        protected_deductions_lkr=protected_lkr,
    )


@bp.route("/<sp_id>/generate", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S8", action="generate")
def generate(sp_id: str):
    """Generate a Service Agreement PDF and persist a ServiceAgreement row."""
    from fiesta.compliance import gate_check  # late import
    from fiesta.agreements.service_pdf import generate_service_agreement_pdf

    form = request.form.to_dict()
    customer = _customer_dict_from_user(current_user)
    service_provider = _service_provider_dict(sp_id, form)
    parameters = _parameters_from_form(form)

    gate = gate_check(
        "S8",
        {**customer, "service_provider": service_provider, **parameters},
        "generate",
    )
    if gate.blocks:
        flash(
            "We can't generate this agreement -- "
            f"{len(gate.blocks)} blocking compliance issue(s). Please fix and retry.",
            "danger",
        )
        return redirect(url_for("fiesta_agreements_service.preview", sp_id=sp_id))

    customer_opt_in = (form.get("customer_opt_in_disclosure") or "").lower() in {
        "yes",
        "on",
        "true",
        "1",
    }
    customer_override = (form.get("customer_override_reason") or "").strip() or None

    result = generate_service_agreement_pdf(
        user_id=getattr(current_user, "id", None),
        user_initials=_safe_initials(current_user),
        customer=customer,
        service_provider=service_provider,
        parameters=parameters,
        customer_override_reason=customer_override,
        customer_opt_in_disclosure=customer_opt_in,
        tax_year="25-26",
        is_draft_preview=False,
    )

    row_id = _persist_agreement(
        user_id=int(getattr(current_user, "id", 0)),
        service_provider_id=sp_id,
        customer=customer,
        service_provider=service_provider,
        parameters=parameters,
        result=result,
        gate_result=gate,
    )

    if row_id == 0:
        flash(
            "PDF generated but not saved to the audit trail (database error). "
            f"Reference: {result.reference_id}",
            "warning",
        )
        return redirect(url_for("fiesta_agreements_service.preview", sp_id=sp_id))

    return redirect(
        url_for(
            "fiesta_agreements_service.download_pdf",
            sp_id=sp_id,
            gen_id=row_id,
        )
    )


@bp.route("/<sp_id>/pdf/<int:gen_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S8", action="download_pdf")
def download_pdf(sp_id: str, gen_id: int):
    """Stream the previously generated PDF."""
    from fiesta.agreements.models import ServiceAgreement  # late import

    row = ServiceAgreement.query.filter_by(
        id=gen_id, user_id=int(getattr(current_user, "id", -1))
    ).first()
    if not row:
        abort(404)
    if row.service_provider_id != str(sp_id):
        abort(404)
    if not row.pdf_path or not Path(row.pdf_path).exists():
        abort(404)
    pdf_bytes = Path(row.pdf_path).read_bytes()
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{row.reference_id}.pdf"',
            "X-FIESTA-SHA256": row.pdf_sha256,
            "X-FIESTA-Template-Version": row.template_version,
        },
    )


@bp.route("/<sp_id>/history", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S8", action="history")
def history(sp_id: str):
    """List prior generations for this user + service-provider."""
    from fiesta.agreements.models import ServiceAgreement  # late import

    rows = (
        ServiceAgreement.query.filter_by(
            user_id=int(getattr(current_user, "id", -1)),
            service_provider_id=str(sp_id),
        )
        .order_by(ServiceAgreement.generated_at.desc())
        .all()
    )
    # B9 (F5.11) — proper FIESTA template with inline PDF iframe modal.
    return render_template("agreements/service_history.html", rows=rows, sp_id=sp_id)


@bp.route("/<sp_id>/preview_json/<int:gen_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S8", action="audit_snapshot")
def audit_snapshot(sp_id: str, gen_id: int):
    """JSON view of the disclosure snapshot for a generated agreement."""
    from fiesta.agreements.models import ServiceAgreement  # late import

    row = ServiceAgreement.query.filter_by(
        id=gen_id, user_id=int(getattr(current_user, "id", -1))
    ).first()
    if not row:
        abort(404)
    if row.service_provider_id != str(sp_id):
        abort(404)
    return jsonify(
        {
            "reference_id": row.reference_id,
            "template_version": row.template_version,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "pdf_sha256": row.pdf_sha256,
            "pdf_byte_size": row.pdf_byte_size,
            "sec195_disclosure_applied": row.sec195_disclosure_applied,
            "sec195_default_was_on": row.sec195_default_was_on,
            "sec195_confidence": row.sec195_confidence,
            "sec195_override_reason": row.sec195_override_reason,
            "gate_passed": row.gate_passed,
            "gate_warnings_count": row.gate_warnings_count,
            "gate_blocks_count": row.gate_blocks_count,
        }
    )


# ---------------------------------------------------------------------------
# Public registration helper
# ---------------------------------------------------------------------------


def register_routes(app) -> None:
    """Register the agreements blueprint on a Flask app.

    Idempotent -- safe to call twice (a second call is a no-op).
    """
    if "fiesta_agreements_service" in app.blueprints:
        return
    app.register_blueprint(bp)
    logger.info(
        "FIESTA Service Agreement blueprint registered: "
        "/agreements/service/<sp_id>"
    )


__all__ = ["bp", "register_routes"]
