"""fiesta.professional_fees.routes — G3.2 Professional Fees Flask surface (MS4 W3b).

Routes (all login-gated, paywall-tier 'self_file'):

    GET  /income/professional-fees/                — list this user's invoices
    GET  /income/professional-fees/new             — add invoice form
    POST /income/professional-fees/new             — create + paired Income row
    GET  /income/professional-fees/<id>            — detail view
    POST /income/professional-fees/<id>/edit       — update invoice row
    POST /income/professional-fees/<id>/delete     — delete (with paired Income)
    GET  /income/professional-fees/import          — CSV import form (invoices)
    POST /income/professional-fees/import          — process CSV

Currency: LKR-default (§85(1C) covers LKR-source service fees to resident
professionals). Foreign-currency invoices captured via Money block (the
WHT regime for non-resident clients differs — out of scope for v1).

Persistence: routes thin-wrap fiesta.tax.professional_fees. The engine
owns transactionality + idempotency.

Provenance: Section G G3.2 (Universal Shell addendum). IRA §85(1C)
effective 2023-01-01: 5% resident-professional WHT on payments above
Rs 100K/month.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defensive imports
# ---------------------------------------------------------------------------
try:
    from flask import (
        Blueprint, render_template, request, redirect, url_for,
        flash, abort, g,
    )
    _HAS_FLASK = True
except ImportError:  # pragma: no cover
    _HAS_FLASK = False

    class _Stub:
        def __init__(self, *a, **kw): pass
        def route(self, *a, **kw):
            def deco(fn): return fn
            return deco

    class Blueprint(_Stub):  # type: ignore
        pass

    def render_template(*a, **kw): return ""  # type: ignore
    def redirect(*a, **kw): return None  # type: ignore
    def url_for(*a, **kw): return "#"  # type: ignore
    def flash(*a, **kw): return None  # type: ignore
    def abort(*a, **kw): return None  # type: ignore
    request = None  # type: ignore
    g = None  # type: ignore

try:
    from flask_login import login_required, current_user
    _HAS_LOGIN = True
except ImportError:  # pragma: no cover
    _HAS_LOGIN = False

    def login_required(fn):  # type: ignore
        return fn
    current_user = None  # type: ignore

try:
    from fiesta.paywall.gate import paywall_required
    _HAS_PAYWALL = True
except ImportError:  # pragma: no cover
    _HAS_PAYWALL = False

    def paywall_required(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco


from fiesta.tax.money import Money
from fiesta.tax.professional_fees import (
    SECTION_85_MONTHLY_THRESHOLD_LKR,
    SECTION_85_NONRESIDENT_RATE_DEFAULT,
    SECTION_85_RESIDENT_RATE_DEFAULT,
    compute_professional_fee_tax,
    delete_professional_fee_for_user,
    get_professional_fee_for_user,
    list_professional_fees_for_user,
    record_professional_fee,
)


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_professional_fees",
    __name__,
    url_prefix="/income/professional-fees",
    template_folder="../../templates",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _current_user_obj():
    if not _HAS_LOGIN or current_user is None:
        return None
    if not getattr(current_user, "is_authenticated", False):
        return None
    return current_user


def _layout() -> str:
    try:
        return getattr(g, "layout_template", "layout_fiesta.html")
    except Exception:
        return "layout_fiesta.html"


def _resolve_fx_rate(currency: str, fx_date: date) -> tuple[Decimal, str]:
    cur = (currency or "").upper()
    if cur == "LKR":
        return Decimal("1.0"), "lkr_native"
    try:
        from cbsl_fx_service import get_cbsl_middle_rate  # type: ignore
        rate = get_cbsl_middle_rate(cur, fx_date)
        if rate:
            return Decimal(str(rate)), "CBSL"
    except Exception:
        pass
    fallback_map = {
        "USD": (Decimal("302.00"), "manual"),
        "GBP": (Decimal("385.00"), "manual"),
        "EUR": (Decimal("327.00"), "manual"),
        "AUD": (Decimal("198.00"), "manual"),
    }
    rate, src = fallback_map.get(cur, (Decimal("302.00"), "manual"))
    return rate, src


def _parse_money(
    amount_raw: Any,
    currency_raw: Any,
    date_raw: Any,
    fx_rate_raw: Any = None,
    fx_source_raw: Any = None,
) -> tuple[Optional[Money], Optional[str]]:
    try:
        amount = Decimal(str(amount_raw or "0").strip() or "0")
    except InvalidOperation:
        return None, "Invalid amount"
    currency = str(currency_raw or "LKR").upper().strip() or "LKR"
    try:
        date_str = str(date_raw or "").strip()
        as_of_date = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        return None, "Invalid date (expected YYYY-MM-DD)"

    fxr = str(fx_rate_raw or "").strip()
    fxs = str(fx_source_raw or "").strip()
    if fxr:
        try:
            fx_rate = Decimal(fxr)
        except InvalidOperation:
            return None, "Invalid fx_rate"
        fx_source = fxs or "manual"
    else:
        fx_rate, fx_source = _resolve_fx_rate(currency, as_of_date)

    return Money(
        amount=amount,
        currency=currency,
        fx_rate=fx_rate,
        fx_source=fx_source,
        fx_date=as_of_date,
    ), None


# ---------------------------------------------------------------------------
# GET /income/professional-fees/ — list view
# ---------------------------------------------------------------------------
@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def list_view():
    user = _current_user_obj()
    if not user:
        abort(401)
    tax_year_filter = (request.args.get("tax_year") or "").strip() or None
    rows = list_professional_fees_for_user(user, tax_year=tax_year_filter)
    summary = None
    if tax_year_filter:
        try:
            summary = compute_professional_fee_tax(user, tax_year_filter)
        except Exception:
            summary = None
    return render_template(
        "professional_fees/list.html",
        layout_template=_layout(),
        fee_rows=rows,
        tax_year_filter=tax_year_filter,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# GET/POST /income/professional-fees/new
# ---------------------------------------------------------------------------
@bp.route("/new", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def new_form():
    return render_template(
        "professional_fees/new.html",
        layout_template=_layout(),
        error=None,
        form={},
        section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
        section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
    )


@bp.route("/new", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def new_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    client = (request.form.get("client_name") or "").strip()
    invoice_number = (request.form.get("invoice_number") or "").strip() or None
    wht_cert = (request.form.get("wht_certificate_ref") or "").strip() or None
    description = (request.form.get("service_description") or "").strip() or None
    tax_year = (request.form.get("tax_year") or "").strip() or None

    invoice_date_raw = (request.form.get("invoice_date") or "").strip()
    if not invoice_date_raw:
        return render_template(
            "professional_fees/new.html",
            layout_template=_layout(),
            error="invoice_date is required (YYYY-MM-DD)",
            form=request.form,
            section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
            section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
        ), 400
    try:
        invoice_date = date.fromisoformat(invoice_date_raw)
    except ValueError:
        return render_template(
            "professional_fees/new.html",
            layout_template=_layout(),
            error="Invalid invoice_date (YYYY-MM-DD)",
            form=request.form,
            section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
            section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
        ), 400

    if not client:
        return render_template(
            "professional_fees/new.html",
            layout_template=_layout(),
            error="client_name is required",
            form=request.form,
            section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
            section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
        ), 400

    gross_money, err = _parse_money(
        request.form.get("gross_amount"),
        request.form.get("currency"),
        request.form.get("as_of_date") or invoice_date_raw,
        request.form.get("fx_rate"),
        request.form.get("fx_source"),
    )
    if err or gross_money is None:
        return render_template(
            "professional_fees/new.html",
            layout_template=_layout(),
            error=err or "Invalid gross amount",
            form=request.form,
            section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
            section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
        ), 400

    wht_money = None
    wht_raw = (request.form.get("wht_amount") or "").strip()
    if wht_raw:
        wht_money, err = _parse_money(
            wht_raw,
            request.form.get("currency"),
            request.form.get("as_of_date") or invoice_date_raw,
            request.form.get("fx_rate"),
            request.form.get("fx_source"),
        )
        if err or wht_money is None:
            return render_template(
                "professional_fees/new.html",
                layout_template=_layout(),
                error=err or "Invalid WHT amount",
                form=request.form,
                section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
                section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
            ), 400

    try:
        meta = record_professional_fee(
            user=user,
            client_name=client,
            gross_money=gross_money,
            wht_withheld_money=wht_money,
            invoice_date=invoice_date,
            service_description=description,
            invoice_number=invoice_number,
            wht_certificate_ref=wht_cert,
            tax_year=tax_year,
        )
    except ValueError as exc:
        return render_template(
            "professional_fees/new.html",
            layout_template=_layout(),
            error=str(exc),
            form=request.form,
            section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
            section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
        ), 400
    except Exception as exc:  # pragma: no cover
        logger.exception("record_professional_fee failed: %s", exc)
        return render_template(
            "professional_fees/new.html",
            layout_template=_layout(),
            error=f"Could not save: {exc}",
            form=request.form,
            section85_resident_rate=SECTION_85_RESIDENT_RATE_DEFAULT,
            section85_threshold=SECTION_85_MONTHLY_THRESHOLD_LKR,
        ), 500

    try:
        flash(
            f"Invoice for '{meta.client_name}' saved.",
            "success",
        )
    except Exception:
        pass
    return redirect(url_for("fiesta_professional_fees.detail", meta_id=meta.id))


# ---------------------------------------------------------------------------
# GET /income/professional-fees/<id>
# ---------------------------------------------------------------------------
@bp.route("/<int:meta_id>", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def detail(meta_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    meta = get_professional_fee_for_user(user, meta_id)
    if meta is None:
        abort(404)
    summary = compute_professional_fee_tax(user, meta.tax_year)
    this_row = next(
        (c for c in summary["clients"] if int(c["meta_id"]) == int(meta.id)),
        None,
    )
    return render_template(
        "professional_fees/detail.html",
        layout_template=_layout(),
        meta=meta,
        row=this_row,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# POST /income/professional-fees/<id>/edit
# ---------------------------------------------------------------------------
@bp.route("/<int:meta_id>/edit", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def edit_submit(meta_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    meta = get_professional_fee_for_user(user, meta_id)
    if meta is None:
        abort(404)

    client = (request.form.get("client_name") or meta.client_name).strip()
    invoice_number = (
        request.form.get("invoice_number") or (meta.invoice_number or "")
    ).strip() or None
    wht_cert = (
        request.form.get("wht_certificate_ref")
        or (meta.wht_certificate_ref or "")
    ).strip() or None
    description = (
        request.form.get("service_description")
        or (meta.service_description or "")
    ).strip() or None
    tax_year = (request.form.get("tax_year") or meta.tax_year).strip()

    invoice_date_raw = (request.form.get("invoice_date") or "").strip()
    try:
        invoice_date = (
            date.fromisoformat(invoice_date_raw)
            if invoice_date_raw else meta.invoice_date
        )
    except ValueError:
        try:
            flash("Invalid invoice_date", "error")
        except Exception:
            pass
        return redirect(url_for("fiesta_professional_fees.detail", meta_id=meta.id))

    gross_money, err = _parse_money(
        request.form.get("gross_amount"),
        request.form.get("currency"),
        request.form.get("as_of_date") or invoice_date.isoformat(),
        request.form.get("fx_rate"),
        request.form.get("fx_source"),
    )
    if err or gross_money is None:
        try:
            flash(err or "Invalid gross amount", "error")
        except Exception:
            pass
        return redirect(url_for("fiesta_professional_fees.detail", meta_id=meta.id))

    wht_money = None
    wht_raw = (request.form.get("wht_amount") or "").strip()
    if wht_raw:
        wht_money, err = _parse_money(
            wht_raw,
            request.form.get("currency"),
            request.form.get("as_of_date") or invoice_date.isoformat(),
            request.form.get("fx_rate"),
            request.form.get("fx_source"),
        )
        if err or wht_money is None:
            try:
                flash(err or "Invalid WHT amount", "error")
            except Exception:
                pass
            return redirect(url_for("fiesta_professional_fees.detail", meta_id=meta.id))

    try:
        record_professional_fee(
            user=user,
            client_name=client,
            gross_money=gross_money,
            wht_withheld_money=wht_money,
            invoice_date=invoice_date,
            service_description=description,
            invoice_number=invoice_number,
            wht_certificate_ref=wht_cert,
            tax_year=tax_year,
        )
    except ValueError as exc:
        try:
            flash(str(exc), "error")
        except Exception:
            pass
    return redirect(url_for("fiesta_professional_fees.detail", meta_id=meta.id))


# ---------------------------------------------------------------------------
# POST /income/professional-fees/<id>/delete
# ---------------------------------------------------------------------------
@bp.route("/<int:meta_id>/delete", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def delete_submit(meta_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    deleted = delete_professional_fee_for_user(user, meta_id)
    try:
        flash(
            "Invoice deleted." if deleted else "Not found.",
            "success" if deleted else "error",
        )
    except Exception:
        pass
    return redirect(url_for("fiesta_professional_fees.list_view"))


# ---------------------------------------------------------------------------
# GET/POST /income/professional-fees/import — CSV import for invoices
# ---------------------------------------------------------------------------
@bp.route("/import", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_form():
    return render_template(
        "professional_fees/import.html",
        layout_template=_layout(),
        error=None,
        result=None,
    )


@bp.route("/import", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_submit():
    """Import invoice CSV.

    Expected CSV columns (header row required, case-insensitive):
        client_name, invoice_date, gross_lkr, wht_lkr
        [, invoice_number] [, service_description] [, wht_cert_ref]
    """
    user = _current_user_obj()
    if not user:
        abort(401)

    uploaded = request.files.get("invoices_csv") if request.files else None
    if uploaded is None or not getattr(uploaded, "filename", ""):
        return render_template(
            "professional_fees/import.html",
            layout_template=_layout(),
            error="invoices_csv file is required",
            result=None,
        ), 400

    try:
        raw = uploaded.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return render_template(
            "professional_fees/import.html",
            layout_template=_layout(),
            error=f"Could not read upload: {exc}",
            result=None,
        ), 400

    rdr = csv.DictReader(io.StringIO(raw))
    if not rdr.fieldnames:
        return render_template(
            "professional_fees/import.html",
            layout_template=_layout(),
            error="CSV missing header row",
            result=None,
        ), 400

    headers_lc = {h.lower().strip(): h for h in rdr.fieldnames}
    required = {"client_name", "invoice_date", "gross_lkr"}
    missing = required - set(headers_lc.keys())
    if missing:
        return render_template(
            "professional_fees/import.html",
            layout_template=_layout(),
            error=f"CSV missing required columns: {sorted(missing)}",
            result=None,
        ), 400

    def _get(row: dict, key: str) -> str:
        actual = headers_lc.get(key)
        if actual is None:
            return ""
        return (row.get(actual) or "").strip()

    created = 0
    updated = 0
    errors: list[str] = []
    for idx, row in enumerate(rdr, start=2):
        client = _get(row, "client_name")
        if not client:
            errors.append(f"row {idx}: missing client_name")
            continue
        try:
            inv_date = date.fromisoformat(_get(row, "invoice_date"))
        except ValueError as exc:
            errors.append(f"row {idx}: bad invoice_date — {exc}")
            continue
        try:
            gross = Decimal(_get(row, "gross_lkr") or "0")
        except InvalidOperation:
            errors.append(f"row {idx}: bad gross_lkr")
            continue
        wht_raw = _get(row, "wht_lkr")
        wht_dec: Optional[Decimal] = None
        if wht_raw:
            try:
                wht_dec = Decimal(wht_raw)
            except InvalidOperation:
                errors.append(f"row {idx}: bad wht_lkr")
                continue
        invoice_number = _get(row, "invoice_number") or None
        description = _get(row, "service_description") or None
        wht_cert = _get(row, "wht_cert_ref") or None

        gross_money = Money.lkr(amount=gross, fx_date=inv_date)
        wht_money = (
            Money.lkr(amount=wht_dec, fx_date=inv_date)
            if wht_dec is not None else None
        )

        try:
            meta = record_professional_fee(
                user=user,
                client_name=client,
                gross_money=gross_money,
                wht_withheld_money=wht_money,
                invoice_date=inv_date,
                service_description=description,
                invoice_number=invoice_number,
                wht_certificate_ref=wht_cert,
            )
            if meta.created_at and meta.updated_at:
                delta = (meta.updated_at - meta.created_at).total_seconds()
                if abs(delta) < 2.0:
                    created += 1
                else:
                    updated += 1
            else:
                created += 1
        except Exception as exc:
            errors.append(f"row {idx}: {exc}")
            continue

    result = {
        "created": created,
        "updated": updated,
        "errors": errors,
        "total": created + updated + len(errors),
    }
    return render_template(
        "professional_fees/import.html",
        layout_template=_layout(),
        error=None,
        result=result,
    )


# ---------------------------------------------------------------------------
# C6 Day-0 fix (2026-05-27) — /income/professional/* alias blueprint
# ---------------------------------------------------------------------------
# The income-source picker offers "Professional fees / consulting (LKR)" with
# id `professional_fees_lkr`. The customer-flow audit
# (CUSTOMER_FLOW_AUDIT_2026-05-26, finding C6) called /income/professional/new
# a 404. The canonical mount is /income/professional-fees/, with the hyphen.
# This alias blueprint catches the shorter /income/professional/* form and
# 302-redirects to the canonical /income/professional-fees/* path so any
# downstream link generator that follows the /income/<source>/new
# convention resolves cleanly.
bp_alias = Blueprint(
    "fiesta_professional_alias",
    __name__,
    url_prefix="/income/professional",
)


@bp_alias.route("", methods=["GET", "POST"], strict_slashes=False)
@bp_alias.route("/", methods=["GET", "POST"], strict_slashes=False)
def _alias_root():
    """302: /income/professional[/] -> /income/professional-fees/"""
    return redirect("/income/professional-fees/", code=302)


@bp_alias.route("/<path:subpath>", methods=["GET", "POST"])
def _alias_subpath(subpath: str):
    """302: /income/professional/<anything> -> /income/professional-fees/<anything>

    Preserves query string. Covers /new, /import, /<id>, /<id>/edit, etc.
    """
    from flask import request as _req
    target = f"/income/professional-fees/{subpath}"
    qs = _req.query_string.decode("utf-8")
    if qs:
        target = f"{target}?{qs}"
    return redirect(target, code=302)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def register_blueprint(app) -> None:
    """Register the G3.2 professional-fees blueprint + /income/professional alias."""
    app.register_blueprint(bp)
    # C6 Day-0 fix — also register the /income/professional alias blueprint
    # (idempotent: skip if already present).
    if "fiesta_professional_alias" not in app.blueprints:
        app.register_blueprint(bp_alias)
    logger.info(
        "FIESTA MS4 W3b G3.2 professional-fees blueprint registered at /income/professional-fees "
        "(+ /income/professional 302 alias)"
    )


__all__ = ["bp", "bp_alias", "register_blueprint"]
