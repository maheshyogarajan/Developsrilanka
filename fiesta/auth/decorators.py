"""
FIESTA admin-gate decorator — canonical single definition (C3 F8.3, Wave 4).

ALL admin_required callers across the repo import from here. The legacy
``decorators.admin_required`` at the repo root has been retired (it re-exports
this implementation to avoid breaking any remaining indirect import paths).

Behaviour
---------
* Anonymous user          → 302 to login (preserves ``next`` param)
* Authenticated non-admin →
    - HTML request  → 403 with styled ``admin/403.html`` template
    - JSON request  → 403 JSON ``{"error": "admin_required", "status": 403}``
* Admin                   → invoke wrapped view

JSON detection uses ``request.is_json`` (Content-Type: application/json) with
a fallback check on the ``Accept`` header — consistent with the pattern used
throughout fiesta/agreements/rental_routes.py, fiesta/property/routes.py, etc.

Admin-check robustness
----------------------
``User.is_admin`` is currently a **bound method** (returns ``role == 'admin'``).
A naive ``getattr(..., False)`` would return the bound method itself, which is
truthy, admitting every authenticated user.  ``_user_is_admin`` resolves this
by calling the attribute when it's callable:

    flag = getattr(current_user, "is_admin", False)
    is_admin = flag() if callable(flag) else bool(flag)

This forward-compatible pattern works for:
  * Current model (``is_admin`` is a method)
  * Future model (``is_admin`` is a boolean column / property)
  * AnonymousUserMixin (returns False)
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

logger = logging.getLogger(__name__)


def _user_is_admin(user) -> bool:
    """Return True iff ``user`` is authenticated AND has admin privileges.

    Robust to both the current method-shape (``user.is_admin()``) and the
    future column-shape (``user.is_admin`` as bool). Falls back to False on
    any exception so a malformed model never elevates a non-admin.
    """
    try:
        if not getattr(user, "is_authenticated", False):
            return False
        flag = getattr(user, "is_admin", False)
        if callable(flag):
            try:
                return bool(flag())
            except Exception:  # pragma: no cover — defensive
                return False
        return bool(flag)
    except Exception:  # pragma: no cover — defensive
        return False


def _wants_json() -> bool:
    """Return True if the caller expects a JSON response.

    Checks in priority order:
    1. ``request.is_json``  — Content-Type: application/json (API clients)
    2. Accept header best-match — e.g. ``Accept: application/json``
    """
    if request.is_json:
        return True
    best = request.accept_mimetypes.best_match(
        ["application/json", "text/html"], default="text/html"
    )
    return best == "application/json"


def admin_required(f):
    """Gate ``/admin/*`` routes by ``User.is_admin``.

    HTML callers receive a styled 403 page (``templates/admin/403.html``).
    JSON callers receive ``{"error": "admin_required", "status": 403}``, 403.

    Anonymous users are always redirected to login (no 403 — they need a
    credential, not an explanation that the resource is admin-only).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # 1) Unauthenticated → bounce to login, preserve ``next``.
        if not getattr(current_user, "is_authenticated", False):
            if _wants_json():
                return jsonify({"error": "login_required", "status": 401}), 401
            try:
                return redirect(url_for("login", next=request.url))
            except Exception:
                # If the login endpoint doesn't exist (e.g. test app stub),
                # fall back to '/'. Don't raise — the gate must close.
                return redirect("/")

        # 2) Authenticated but not admin → 403 (HTML or JSON).
        if not _user_is_admin(current_user):
            if _wants_json():
                return jsonify({"error": "admin_required", "status": 403}), 403
            # HTML: styled 403 page extending admin/layout.html
            try:
                return render_template("admin/403.html"), 403
            except Exception:
                # Template not found (shouldn't happen post-C3, but fail safe)
                flash("Admin access required.", "error")
                try:
                    return redirect(url_for("index"))
                except Exception:
                    try:
                        return redirect(url_for("home"))
                    except Exception:
                        return redirect("/")

        # 3) Admin → run the view.
        return f(*args, **kwargs)

    return decorated


__all__ = ["admin_required", "_user_is_admin", "_wants_json"]
