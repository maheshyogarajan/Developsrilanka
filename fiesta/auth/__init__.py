"""
fiesta.auth — admin role gating + auth decorators for FIESTA admin surface
(Wave 6, 2026-05-20).

The single export consumers care about is ``admin_required``, used by every
``/admin/*`` route added under the FIESTA admin blueprints (S15, S16, S17).

Backward compatibility
----------------------
A pre-existing ``decorators.admin_required`` lives at the repo root and is
imported by 21+ routes in ``admin_routes.py`` / ``customer_brain_routes.py`` /
``ops_routes.py`` / ``expense_reports.py``. We do NOT touch that legacy import
site. The FIESTA copy here mirrors the same redirect semantics so the gate
behaves identically whichever decorator a future route picks up.

Semantics (both decorators)
---------------------------
* Anonymous → 302 ``url_for('login', next=request.url)``
* Authenticated but ``not current_user.is_admin`` → 302 ``url_for('index')``
  + flash("Admin access required.", "error")
* Admin → through.

``is_admin`` on the User model is a *bound method* (returns ``role == 'admin'``)
not a property — the decorator calls it if callable to support either shape.
This makes the decorator robust to the boolean-column refactor the spec
foresaw (Wave 6 also ships the additive ``user.is_admin`` boolean column for
future direct reads — see ``add_admin_and_stripe_columns_to_user.py``).
"""
from .decorators import admin_required  # noqa: F401

__all__ = ["admin_required"]
