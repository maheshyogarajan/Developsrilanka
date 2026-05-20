"""
FIESTA admin-gate decorator (Wave 6, 2026-05-20).

This is the canonical gate for all FIESTA-branded ``/admin/*`` surfaces
introduced from Wave 6 onward (S15, S16, S17, ...). The legacy
``decorators.admin_required`` at the repo root keeps wrapping the 21 pre-Wave-6
admin routes; new routes should import from here.

Why a second module?
--------------------
1. Spec ask: "Put this in ``fiesta/auth/decorators.py`` or wherever similar
   decorators already live."  -- the FIESTA package boundary is where we want
   new code to land; the legacy ``decorators.py`` is shared with org-scope and
   email-verification gates, which is a wider blast radius.
2. Tests: we want a focused unit-test surface for the admin gate. The legacy
   decorator has zero tests; this one ships with a 7-test suite.
3. Future refactor: when the model adds a ``User.is_admin`` boolean column as
   a direct attribute (instead of the current ``is_admin()`` method), only
   this decorator needs to evolve. The legacy one can be retired by
   re-exporting our ``admin_required`` from ``decorators.py``.

Spec ambiguity / resolution
---------------------------
The brief proposed ``getattr(current_user, 'is_admin', False)``. On the
current FIESTA User model ``is_admin`` is a **bound method** (returns
``role == 'admin'``). ``getattr(...) -> bound method`` is *truthy*, so the
naive pattern would admit every authenticated user — a security bug.

We resolve this in one place by calling the attribute when it's callable:

    flag = getattr(current_user, "is_admin", False)
    is_admin = flag() if callable(flag) else bool(flag)

This works for:
  * Current model (``is_admin`` is a method)
  * Future model (``is_admin`` is a boolean column / property)
  * AnonymousUserMixin (returns False; method shape via Flask-Login default)
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import flash, redirect, request, url_for
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


def admin_required(f):
    """Gate ``/admin/*`` routes by ``User.is_admin``.

    * Anonymous user        → 302 to ``url_for('login', next=request.url)``
    * Authenticated non-admin → 302 to ``url_for('index')`` + flash
    * Admin                 → invoke wrapped view

    The flash category is ``"error"`` (per spec) — alert-error in the base
    layout. Bootstrap renders it as ``alert-error``; the admin layout's
    flash handler renders it as ``alert-{{ category }}`` so we don't need
    a CSS-class shim.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # 1) Unauthenticated → bounce to login, preserve ``next``.
        if not getattr(current_user, "is_authenticated", False):
            try:
                return redirect(url_for("login", next=request.url))
            except Exception:
                # If the login endpoint doesn't exist (e.g. test app stub),
                # fall back to '/'. Don't raise — the gate must close.
                return redirect("/")
        # 2) Authenticated but not admin → bounce to index + flash.
        if not _user_is_admin(current_user):
            flash("Admin access required.", "error")
            try:
                return redirect(url_for("index"))
            except Exception:
                # 'index' isn't always the home endpoint name (e.g. some
                # apps use 'home'). Try 'home' next, then '/'.
                try:
                    return redirect(url_for("home"))
                except Exception:
                    return redirect("/")
        # 3) Admin → run the view.
        return f(*args, **kwargs)

    return decorated


__all__ = ["admin_required", "_user_is_admin"]
