"""
S17 Admin — Autoreply Queue (Wave 6, 2026-05-21).

Routes (all gated by ``@admin_required``, mounted under ``/admin/fie``):

  * ``GET  /admin/fie/autoreply``             — paginated queue list view
  * ``POST /admin/fie/autoreply/<id>/approve`` — mark draft approved_by_admin
                                                  (CEO-OS send pipeline picks
                                                  it up via send_gate; FIESTA
                                                  does NOT call SF Apex
                                                  directly — that's the
                                                  send_gate's job per MP-04 G01)
  * ``POST /admin/fie/autoreply/<id>/reject``  — discard the draft + mark
                                                  ledger row cancelled_by_admin
  * ``POST /admin/fie/autoreply/cohort-cb``    — toggle the cohort circuit
                                                  breaker (FIESTA-side latch)
  * ``GET  /admin/fie/autoreply/healthz``      — JSON health snapshot
                                                  (send-pipeline status card)

Storage
-------
The queue itself is **Supabase ``approval_queue``** — written by
``fiesta.delivery_ops.autoreply.submit_for_tier1_approval`` (the upstream
classifier). FIESTA's admin surface is the approval UI; the actual outbound
send is delegated to the CEO-OS send pipeline (which owns the SF Apex call
behind ``send_gate``). This file deliberately does NOT reference the SF Apex
endpoint — the project hook ``send_gate_enforcement_hook`` (MP-04 G01)
restricts that to ``send_gate.py`` / ``send_adapter.py``.

When the admin clicks Approve we flip the row to ``approved_by_admin``. The
CEO-OS poller (or human operator) picks the row up, runs the nine
``send_gate`` gates, and on success transitions the row to
``approved_and_sent``. On rejection / send failure the row moves to
``cancelled_by_admin`` or ``pending_send_retry`` respectively. The two
systems share Supabase + SF as the only common state; no FIESTA-local
mirror.

Gate badges
-----------
Each queued row carries provenance the admin must see at a glance:

  * **case-file** — ``case_file_built`` flag on the row
  * **proof**     — ``classifier_inputs.verified_fact_count`` (Step 2c)
  * **RC**        — ``resolver_change_id`` (Rule P1: SF row created FIRST)
  * **cb**        — cohort circuit breaker state at queue time

Per S17 spec the "Approve & Send" button is **disabled** if any badge is
red. Admin can still Reject regardless.

Cohort Circuit Breaker
----------------------
FIESTA-side circuit breaker latch lives in the ``cohort_cb_state`` row in
Supabase ``system_config`` (singleton). When ``open`` the Approve button is
hard-disabled. Toggling requires an admin click + a free-form reason; both
are logged.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import (
    Blueprint, Flask, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)
from flask_login import current_user

from fiesta.auth.decorators import admin_required

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Blueprint
# --------------------------------------------------------------------------- #
fiesta_admin_autoreply_bp = Blueprint(
    "fiesta_admin_autoreply",
    __name__,
    template_folder="../../templates",
)


SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_KEY_ENV = "SUPABASE_ANON_KEY"
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100


# --------------------------------------------------------------------------- #
# Supabase REST helpers (thin — only what S17 needs).
# --------------------------------------------------------------------------- #

def _supabase_creds() -> Optional[Dict[str, str]]:
    url = os.environ.get(SUPABASE_URL_ENV) or ""
    key = os.environ.get(SUPABASE_KEY_ENV) or ""
    if not url or not key:
        return None
    return {"url": url.rstrip("/"), "key": key}


def _supabase_request(method: str, path: str, *,
                      params: Optional[Dict[str, str]] = None,
                      body: Optional[Dict[str, Any]] = None,
                      timeout: float = 8.0,
                      extra_prefer: Optional[str] = None) -> Dict[str, Any]:
    """Tiny urllib-based Supabase REST client. Returns a dict with keys
    ``ok``, ``status``, ``body`` (parsed JSON or string), ``error``.

    Defensive: ANY network error returns ok=False, never raises. Callers
    handle the failure mode at the route level (degrade gracefully so a
    transient Supabase blip doesn't 500 the admin queue).
    """
    creds = _supabase_creds()
    if creds is None:
        return {"ok": False, "status": 0, "body": None,
                "error": "SUPABASE_URL / SUPABASE_ANON_KEY not configured"}

    qs = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
    url = creds["url"] + path + qs
    prefer = "return=representation"
    if extra_prefer:
        prefer = prefer + "," + extra_prefer
    headers = {
        "apikey": creds["key"],
        "Authorization": f"Bearer {creds['key']}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed: Any
            try:
                parsed = json.loads(raw) if raw else None
            except Exception:
                parsed = raw
            return {"ok": True, "status": resp.status, "body": parsed,
                    "error": None}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        return {"ok": False, "status": e.code, "body": err_body,
                "error": f"HTTP {e.code}: {err_body[:200]}"}
    except Exception as e:
        return {"ok": False, "status": 0, "body": None, "error": str(e)}


def _list_pending_drafts(*, limit: int = DEFAULT_PER_PAGE,
                          offset: int = 0) -> Dict[str, Any]:
    """Return up to ``limit`` pending autoreply drafts from approval_queue.

    Filters: ``action_type=eq.email_send`` AND ``status=eq.pending``.
    Ordering: ``priority asc, created_at asc`` (most urgent first, then FIFO).
    """
    params = {
        "action_type": "eq.email_send",
        "status": "eq.pending",
        "order": "priority.asc,created_at.asc",
        "limit": str(int(limit)),
        "offset": str(int(offset)),
    }
    return _supabase_request("GET", "/rest/v1/approval_queue", params=params)


def _get_draft(approval_queue_id: str) -> Dict[str, Any]:
    params = {"id": f"eq.{approval_queue_id}", "limit": "1"}
    return _supabase_request("GET", "/rest/v1/approval_queue", params=params)


def _update_draft_status(approval_queue_id: str, *, new_status: str,
                          responded_at: Optional[str] = None,
                          extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """PATCH the approval_queue row's status + responded_at."""
    body: Dict[str, Any] = {
        "status": new_status,
        "responded_at": responded_at or datetime.utcnow().isoformat() + "Z",
    }
    if extras:
        body.update(extras)
    params = {"id": f"eq.{approval_queue_id}"}
    return _supabase_request("PATCH", "/rest/v1/approval_queue",
                              params=params, body=body)


def _cb_state() -> Dict[str, Any]:
    """Read the cohort circuit breaker singleton row from system_config.

    Row shape (one row, key='cohort_cb_state'):
      { key: 'cohort_cb_state', value: { state: 'closed'|'open',
                                          reason: str, set_by: str,
                                          set_at: iso8601 } }

    Returns dict with keys ``state``, ``reason``, ``set_by``, ``set_at``,
    and ``available`` (False if Supabase is down — fail-closed: treat as open).
    """
    params = {"key": "eq.cohort_cb_state", "limit": "1"}
    r = _supabase_request("GET", "/rest/v1/system_config", params=params)
    if not r["ok"]:
        return {"state": "open", "reason": "supabase_unavailable",
                "set_by": None, "set_at": None, "available": False}
    rows = r["body"] or []
    if not rows:
        return {"state": "closed", "reason": "default_closed_on_first_use",
                "set_by": None, "set_at": None, "available": True}
    row = rows[0]
    val = row.get("value") or {}
    return {
        "state": val.get("state", "closed"),
        "reason": val.get("reason", ""),
        "set_by": val.get("set_by"),
        "set_at": val.get("set_at"),
        "available": True,
    }


def _set_cb_state(*, new_state: str, reason: str, set_by: str) -> Dict[str, Any]:
    body = {
        "key": "cohort_cb_state",
        "value": {
            "state": new_state,
            "reason": reason[:500],
            "set_by": set_by,
            "set_at": datetime.utcnow().isoformat() + "Z",
        },
    }
    # Upsert via PostgREST: resolution=merge-duplicates on the unique key.
    return _supabase_request(
        "POST", "/rest/v1/system_config",
        params={"on_conflict": "key"},
        body=body,
        extra_prefer="resolution=merge-duplicates",
    )


# --------------------------------------------------------------------------- #
# Gate badges — render-time inspection of the queued payload.
# --------------------------------------------------------------------------- #

def _badges_for_draft(row: Dict[str, Any], cb_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return a dict of {badge_key -> {state, label, note}} for the queue UI.

    States: 'green' (all clear), 'amber' (warning, send still allowed),
    'red' (blocks Approve button).
    """
    sf_value = row.get("sf_value") or {}
    if isinstance(sf_value, str):
        try:
            sf_value = json.loads(sf_value)
        except Exception:
            sf_value = {}
    details = row.get("details") or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except Exception:
            details = {}

    case_file_built = bool(details.get("case_file_built")
                            or sf_value.get("case_file_built"))
    verified_count = (details.get("verified_fact_count")
                      or sf_value.get("verified_fact_count")
                      or 0)
    rc_id = (sf_value.get("resolver_change_id")
              or details.get("resolver_change_id"))

    badges: Dict[str, Dict[str, Any]] = {}
    badges["case_file"] = {
        "state": "green" if case_file_built else "red",
        "label": "case-file",
        "note": ("built" if case_file_built else "MISSING — block send"),
    }
    badges["proof"] = {
        "state": "green" if verified_count and verified_count >= 1 else "amber",
        "label": "proof",
        "note": f"verified={verified_count}",
    }
    badges["rc"] = {
        "state": "green" if rc_id else "red",
        "label": "RC",
        "note": (str(rc_id) if rc_id else "MISSING — Rule P1 violation"),
    }
    badges["cb"] = {
        "state": "red" if cb_state.get("state") == "open" else "green",
        "label": "cb",
        "note": ("cohort breaker OPEN — sends paused"
                 if cb_state.get("state") == "open"
                 else "closed"),
    }
    return badges


def _any_red(badges: Dict[str, Dict[str, Any]]) -> bool:
    return any(b.get("state") == "red" for b in badges.values())


# --------------------------------------------------------------------------- #
# Routes.
# --------------------------------------------------------------------------- #

@fiesta_admin_autoreply_bp.route("/admin/fie/autoreply", methods=["GET"])
@admin_required
def queue_index():
    """List view — pending autoreply drafts + gate badges + pipeline card."""
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    try:
        per_page = min(MAX_PER_PAGE, max(1, int(request.args.get("per_page", str(DEFAULT_PER_PAGE)))))
    except ValueError:
        per_page = DEFAULT_PER_PAGE

    offset = (page - 1) * per_page
    cb_state = _cb_state()
    fetched = _list_pending_drafts(limit=per_page, offset=offset)
    rows: List[Dict[str, Any]] = []
    fetch_error: Optional[str] = None

    if fetched["ok"]:
        for r in (fetched["body"] or []):
            badges = _badges_for_draft(r, cb_state)
            rows.append({
                "row": r,
                "badges": badges,
                "send_blocked": _any_red(badges),
            })
    else:
        fetch_error = fetched["error"]

    # send-pipeline status card — show whether the downstream CEO-OS
    # pipeline can pick up our 'approved_by_admin' rows. We don't call the
    # SF Apex endpoint from here (MP-04 G01); we only surface that Supabase
    # is reachable, the cohort CB is closed, and the operator knows the
    # admin's role is approval-not-send.
    pipeline_card = {
        "name": "CEO-OS send_gate (delegated)",
        "supabase_reachable": fetched["ok"],
        "cb_state": cb_state.get("state"),
        "ready": fetched["ok"] and cb_state.get("state") == "closed",
        "note": ("admin approval flips status to 'approved_by_admin'; "
                  "the CEO-OS send_gate pipeline runs the 9 G-gates and "
                  "performs the actual send"),
    }

    return render_template(
        "fiesta_admin/autoreply_queue.html",
        rows=rows,
        page=page,
        per_page=per_page,
        cb_state=cb_state,
        pipeline_card=pipeline_card,
        fetch_error=fetch_error,
    )


@fiesta_admin_autoreply_bp.route(
    "/admin/fie/autoreply/<approval_queue_id>/approve",
    methods=["POST"],
)
@admin_required
def approve(approval_queue_id: str):
    """Mark draft approved_by_admin. The CEO-OS send_gate pipeline picks it
    up and performs the actual send through the gated send path.

    Refuses if any gate badge is red OR cohort CB is open.
    """
    fetched = _get_draft(approval_queue_id)
    if not fetched["ok"] or not fetched["body"]:
        flash(f"Draft {approval_queue_id} not found (or Supabase unreachable).",
              "error")
        return redirect(url_for("fiesta_admin_autoreply.queue_index"))

    row = fetched["body"][0]
    if row.get("status") != "pending":
        flash(f"Draft {approval_queue_id} is no longer pending "
              f"(state={row.get('status')}).", "warning")
        return redirect(url_for("fiesta_admin_autoreply.queue_index"))

    cb = _cb_state()
    badges = _badges_for_draft(row, cb)
    if _any_red(badges):
        red = [b["label"] for b in badges.values() if b["state"] == "red"]
        flash(f"Send blocked — red gates: {', '.join(red)}.", "error")
        return redirect(url_for("fiesta_admin_autoreply.queue_index"))

    patch = _update_draft_status(
        approval_queue_id,
        new_status="approved_by_admin",
        extras={
            "details": {
                "approved_by_admin_id": getattr(current_user, "id", None),
                "approved_by_admin_email": getattr(current_user, "email", None),
                "approved_at": datetime.utcnow().isoformat() + "Z",
                "pipeline_next": ("CEO-OS send_gate — awaiting 9-gate pass "
                                    "then send"),
            },
        },
    )
    if patch["ok"]:
        log.info("S17 approve: queue_id=%s flipped to approved_by_admin",
                 approval_queue_id)
        flash(f"Approved — draft {approval_queue_id} queued for send by the "
              f"CEO-OS pipeline.", "success")
    else:
        log.warning("S17 approve: queue_id=%s PATCH failed: %s",
                    approval_queue_id, patch.get("error"))
        flash(f"Could not update approval state: {patch.get('error')}",
              "error")

    return redirect(url_for("fiesta_admin_autoreply.queue_index"))


@fiesta_admin_autoreply_bp.route(
    "/admin/fie/autoreply/<approval_queue_id>/reject",
    methods=["POST"],
)
@admin_required
def reject(approval_queue_id: str):
    """Discard the draft. Sets approval_queue.status='cancelled_by_admin'."""
    reason = (request.form.get("reason") or "").strip()[:500]
    fetched = _get_draft(approval_queue_id)
    if not fetched["ok"] or not fetched["body"]:
        flash(f"Draft {approval_queue_id} not found.", "error")
        return redirect(url_for("fiesta_admin_autoreply.queue_index"))
    row = fetched["body"][0]
    if row.get("status") != "pending":
        flash(f"Draft {approval_queue_id} is no longer pending "
              f"(state={row.get('status')}).", "warning")
        return redirect(url_for("fiesta_admin_autoreply.queue_index"))

    _update_draft_status(
        approval_queue_id,
        new_status="cancelled_by_admin",
        extras={
            "details": {
                "rejected_by_admin_id": getattr(current_user, "id", None),
                "rejected_by_admin_email": getattr(current_user, "email", None),
                "reject_reason": reason or "(no reason given)",
                "rejected_at": datetime.utcnow().isoformat() + "Z",
            },
        },
    )
    flash(f"Draft {approval_queue_id} rejected.", "success")
    return redirect(url_for("fiesta_admin_autoreply.queue_index"))


@fiesta_admin_autoreply_bp.route(
    "/admin/fie/autoreply/cohort-cb",
    methods=["POST"],
)
@admin_required
def toggle_cohort_cb():
    """Toggle the cohort circuit breaker.

    Form fields: ``state`` (``open`` | ``closed``), ``reason`` (free text).
    """
    new_state = (request.form.get("state") or "").strip().lower()
    reason = (request.form.get("reason") or "").strip()[:500]

    if new_state not in {"open", "closed"}:
        flash("Invalid CB state — must be 'open' or 'closed'.", "error")
        return redirect(url_for("fiesta_admin_autoreply.queue_index"))
    if not reason:
        flash("CB state changes require a reason.", "error")
        return redirect(url_for("fiesta_admin_autoreply.queue_index"))

    set_by = getattr(current_user, "email", None) or "unknown_admin"
    r = _set_cb_state(new_state=new_state, reason=reason, set_by=set_by)
    if r["ok"]:
        flash(f"Cohort CB set to '{new_state}'.", "success")
    else:
        flash(f"Could not persist CB state: {r.get('error')}", "error")
    return redirect(url_for("fiesta_admin_autoreply.queue_index"))


@fiesta_admin_autoreply_bp.route("/admin/fie/autoreply/healthz", methods=["GET"])
@admin_required
def healthz():
    """JSON health snapshot — for monitoring + the admin pipeline card."""
    creds = _supabase_creds()
    cb = _cb_state()
    return jsonify({
        "supabase": {
            "configured": creds is not None,
            "url_host": (creds["url"].split("://", 1)[-1].split("/")[0]
                          if creds else None),
        },
        "send_pipeline": {
            "owner": "CEO-OS send_gate (delegated)",
            "note": ("FIESTA admin's approve handler flips the row to "
                      "approved_by_admin; CEO-OS picks it up + sends via "
                      "the 9-gate pipeline."),
        },
        "cohort_cb": cb,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    })


# --------------------------------------------------------------------------- #
# Public registration hook
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Standard FIESTA blueprint hook. Called from main.py via the parent
    ``fiesta.admin`` package's ``register_routes`` aggregator."""
    if "fiesta_admin_autoreply" in app.blueprints:
        log.debug("fiesta_admin_autoreply already registered — skipping.")
        return
    app.register_blueprint(fiesta_admin_autoreply_bp)
    log.info("S17 fiesta_admin_autoreply blueprint registered "
             "(/admin/fie/autoreply, /approve, /reject, /cohort-cb, /healthz)")
