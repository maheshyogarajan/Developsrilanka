"""fiesta.employment.routes — G3.1 Employment Income Flask surface (MS4 W3b).

Routes (all login-gated, paywall-tier 'self_file'):

    GET  /income/employment/                — list this user's employment rows
    GET  /income/employment/new             — add employment form
    POST /income/employment/new             — create + paired Income row
    GET  /income/employment/<id>            — detail view
    POST /income/employment/<id>/edit       — update employment row
    POST /income/employment/<id>/delete     — delete (with paired Income)
    GET  /income/employment/import          — CSV import form (monthly payslips)
    POST /income/employment/import          — process CSV

Currency: LKR-default (most SL employment is LKR). Foreign-currency
employer captured via the Money block (currency + FX rate) but the source
country tagging is NOT applied — employment_lkr is a domestic-source
type per the canonical model.

Persistence: routes thin-wrap fiesta.tax.employment. The engine owns
transactionality + idempotency.

Provenance: Section G G3.1 (Universal Shell addendum).
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
# Defensive imports (mirrors fiesta.business.routes pattern)
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


from fiesta.tax.employment import (
    compute_employment_tax,
    delete_employment_for_user,
    get_employment_for_user,
    list_employment_for_user,
    record_employment_income,
)
from fiesta.tax.money import Money


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_employment",
    __name__,
    url_prefix="/income/employment",
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
# GET /income/employment/ — list view
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
    rows = list_employment_for_user(user, tax_year=tax_year_filter)
    summary = None
    if tax_year_filter:
        try:
            summary = compute_employment_tax(user, tax_year_filter)
        except Exception:
            summary = None
    return render_template(
        "employment/list.html",
        layout_template=_layout(),
        employment_rows=rows,
        tax_year_filter=tax_year_filter,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# GET/POST /income/employment/new
# ---------------------------------------------------------------------------
@bp.route("/new", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def new_form():
    return render_template(
        "employment/new.html",
        layout_template=_layout(),
        error=None,
        form={},
    )


@bp.route("/new", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def new_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    employer = (request.form.get("employer_name") or "").strip()
    apit_cert = (request.form.get("apit_certificate_ref") or "").strip() or None
    tax_year = (request.form.get("tax_year") or "").strip() or None

    period_start_raw = (request.form.get("period_start") or "").strip()
    period_end_raw = (request.form.get("period_end") or "").strip()
    if not period_start_raw or not period_end_raw:
        return render_template(
            "employment/new.html",
            layout_template=_layout(),
            error="period_start and period_end are required (YYYY-MM-DD)",
            form=request.form,
        ), 400
    try:
        period_start = date.fromisoformat(period_start_raw)
        period_end = date.fromisoformat(period_end_raw)
    except ValueError:
        return render_template(
            "employment/new.html",
            layout_template=_layout(),
            error="Invalid period dates (expected YYYY-MM-DD)",
            form=request.form,
        ), 400

    if not employer:
        return render_template(
            "employment/new.html",
            layout_template=_layout(),
            error="employer_name is required",
            form=request.form,
        ), 400

    gross_money, err = _parse_money(
        request.form.get("gross_amount"),
        request.form.get("currency"),
        request.form.get("as_of_date") or period_start_raw,
        request.form.get("fx_rate"),
        request.form.get("fx_source"),
    )
    if err or gross_money is None:
        return render_template(
            "employment/new.html",
            layout_template=_layout(),
            error=err or "Invalid gross amount",
            form=request.form,
        ), 400

    apit_money = None
    apit_raw = (request.form.get("apit_amount") or "").strip()
    if apit_raw:
        apit_money, err = _parse_money(
            apit_raw,
            request.form.get("currency"),
            request.form.get("as_of_date") or period_start_raw,
            request.form.get("fx_rate"),
            request.form.get("fx_source"),
        )
        if err or apit_money is None:
            return render_template(
                "employment/new.html",
                layout_template=_layout(),
                error=err or "Invalid APIT amount",
                form=request.form,
            ), 400

    try:
        meta = record_employment_income(
            user=user,
            employer_name=employer,
            gross_money=gross_money,
            apit_withheld_money=apit_money,
            period_start=period_start,
            period_end=period_end,
            apit_certificate_ref=apit_cert,
            tax_year=tax_year,
        )
    except ValueError as exc:
        return render_template(
            "employment/new.html",
            layout_template=_layout(),
            error=str(exc),
            form=request.form,
        ), 400
    except Exception as exc:  # pragma: no cover
        logger.exception("record_employment_income failed: %s", exc)
        return render_template(
            "employment/new.html",
            layout_template=_layout(),
            error=f"Could not save: {exc}",
            form=request.form,
        ), 500

    try:
        flash(
            f"Employment '{meta.employer_name}' saved for {meta.tax_year}.",
            "success",
        )
    except Exception:
        pass
    return redirect(url_for("fiesta_employment.detail", meta_id=meta.id))


# ---------------------------------------------------------------------------
# GET /income/employment/<id>
# ---------------------------------------------------------------------------
@bp.route("/<int:meta_id>", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def detail(meta_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    meta = get_employment_for_user(user, meta_id)
    if meta is None:
        abort(404)
    summary = compute_employment_tax(user, meta.tax_year)
    this_row = next(
        (e for e in summary["employers"] if int(e["meta_id"]) == int(meta.id)),
        None,
    )
    return render_template(
        "employment/detail.html",
        layout_template=_layout(),
        meta=meta,
        row=this_row,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# POST /income/employment/<id>/edit
# ---------------------------------------------------------------------------
@bp.route("/<int:meta_id>/edit", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def edit_submit(meta_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    meta = get_employment_for_user(user, meta_id)
    if meta is None:
        abort(404)

    employer = (request.form.get("employer_name") or meta.employer_name).strip()
    apit_cert = (
        request.form.get("apit_certificate_ref")
        or (meta.apit_certificate_ref or "")
    ).strip() or None
    tax_year = (request.form.get("tax_year") or meta.tax_year).strip()

    period_start_raw = (request.form.get("period_start") or "").strip()
    period_end_raw = (request.form.get("period_end") or "").strip()
    try:
        period_start = (
            date.fromisoformat(period_start_raw)
            if period_start_raw else meta.period_start
        )
        period_end = (
            date.fromisoformat(period_end_raw)
            if period_end_raw else meta.period_end
        )
    except ValueError:
        try:
            flash("Invalid period date", "error")
        except Exception:
            pass
        return redirect(url_for("fiesta_employment.detail", meta_id=meta.id))

    gross_money, err = _parse_money(
        request.form.get("gross_amount"),
        request.form.get("currency"),
        request.form.get("as_of_date") or period_start.isoformat(),
        request.form.get("fx_rate"),
        request.form.get("fx_source"),
    )
    if err or gross_money is None:
        try:
            flash(err or "Invalid gross amount", "error")
        except Exception:
            pass
        return redirect(url_for("fiesta_employment.detail", meta_id=meta.id))

    apit_money = None
    apit_raw = (request.form.get("apit_amount") or "").strip()
    if apit_raw:
        apit_money, err = _parse_money(
            apit_raw,
            request.form.get("currency"),
            request.form.get("as_of_date") or period_start.isoformat(),
            request.form.get("fx_rate"),
            request.form.get("fx_source"),
        )
        if err or apit_money is None:
            try:
                flash(err or "Invalid APIT amount", "error")
            except Exception:
                pass
            return redirect(url_for("fiesta_employment.detail", meta_id=meta.id))

    try:
        record_employment_income(
            user=user,
            employer_name=employer,
            gross_money=gross_money,
            apit_withheld_money=apit_money,
            period_start=period_start,
            period_end=period_end,
            apit_certificate_ref=apit_cert,
            tax_year=tax_year,
        )
    except ValueError as exc:
        try:
            flash(str(exc), "error")
        except Exception:
            pass
    return redirect(url_for("fiesta_employment.detail", meta_id=meta.id))


# ---------------------------------------------------------------------------
# POST /income/employment/<id>/delete
# ---------------------------------------------------------------------------
@bp.route("/<int:meta_id>/delete", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def delete_submit(meta_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    deleted = delete_employment_for_user(user, meta_id)
    try:
        flash(
            "Employment row deleted." if deleted else "Not found.",
            "success" if deleted else "error",
        )
    except Exception:
        pass
    return redirect(url_for("fiesta_employment.list_view"))


# ---------------------------------------------------------------------------
# GET/POST /income/employment/import — CSV import for monthly payslips
# ---------------------------------------------------------------------------
@bp.route("/import", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_form():
    return render_template(
        "employment/import.html",
        layout_template=_layout(),
        error=None,
        result=None,
    )


@bp.route("/import", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_submit():
    """Import monthly payslip CSV.

    Expected CSV columns (header row required, case-insensitive):
        employer_name, period_start, period_end, gross_lkr, apit_lkr [, apit_cert_ref]

    Each row creates / updates one EmploymentIncomeMetadata via the
    standard idempotent record_employment_income path.
    """
    user = _current_user_obj()
    if not user:
        abort(401)

    uploaded = request.files.get("payslips_csv") if request.files else None
    if uploaded is None or not getattr(uploaded, "filename", ""):
        return render_template(
            "employment/import.html",
            layout_template=_layout(),
            error="payslips_csv file is required",
            result=None,
        ), 400

    try:
        raw = uploaded.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return render_template(
            "employment/import.html",
            layout_template=_layout(),
            error=f"Could not read upload: {exc}",
            result=None,
        ), 400

    rdr = csv.DictReader(io.StringIO(raw))
    if not rdr.fieldnames:
        return render_template(
            "employment/import.html",
            layout_template=_layout(),
            error="CSV missing header row",
            result=None,
        ), 400

    # Lower-case header lookup
    headers_lc = {h.lower().strip(): h for h in rdr.fieldnames}
    required = {"employer_name", "period_start", "period_end", "gross_lkr"}
    missing = required - set(headers_lc.keys())
    if missing:
        return render_template(
            "employment/import.html",
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
    for idx, row in enumerate(rdr, start=2):  # row 1 is header
        employer = _get(row, "employer_name")
        if not employer:
            errors.append(f"row {idx}: missing employer_name")
            continue
        try:
            ps = date.fromisoformat(_get(row, "period_start"))
            pe = date.fromisoformat(_get(row, "period_end"))
        except ValueError as exc:
            errors.append(f"row {idx}: bad date — {exc}")
            continue
        try:
            gross = Decimal(_get(row, "gross_lkr") or "0")
        except InvalidOperation:
            errors.append(f"row {idx}: bad gross_lkr")
            continue
        apit_raw = _get(row, "apit_lkr")
        apit_dec: Optional[Decimal] = None
        if apit_raw:
            try:
                apit_dec = Decimal(apit_raw)
            except InvalidOperation:
                errors.append(f"row {idx}: bad apit_lkr")
                continue
        cert_ref = _get(row, "apit_cert_ref") or None

        gross_money = Money.lkr(amount=gross, fx_date=ps)
        apit_money = (
            Money.lkr(amount=apit_dec, fx_date=ps) if apit_dec is not None else None
        )

        try:
            existed = bool(
                # cheap "did the row already exist before this call?" check
                # using the engine's natural-key index — we can detect via
                # the post-call meta.created_at vs updated_at by reading
                # back, but simpler: call and bucket by whether the id is
                # new. We instead call and check the returned row's
                # created_at-vs-updated_at gap; tolerance 2s.
                False  # placeholder; overwritten below after the call.
            )
            meta = record_employment_income(
                user=user,
                employer_name=employer,
                gross_money=gross_money,
                apit_withheld_money=apit_money,
                period_start=ps,
                period_end=pe,
                apit_certificate_ref=cert_ref,
            )
            if meta.created_at and meta.updated_at:
                delta = (meta.updated_at - meta.created_at).total_seconds()
                if abs(delta) < 2.0:
                    created += 1
                else:
                    updated += 1
            else:
                created += 1
            del existed
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
        "employment/import.html",
        layout_template=_layout(),
        error=None,
        result=result,
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def register_blueprint(app) -> None:
    """Register the G3.1 employment blueprint."""
    app.register_blueprint(bp)
    logger.info(
        "FIESTA MS4 W3b G3.1 employment income blueprint registered at /income/employment"
    )


__all__ = ["bp", "register_blueprint"]
