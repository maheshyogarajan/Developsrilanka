"""
fiesta.admin — FIESTA-branded admin surface (Wave 6, 2026-05-20).

Houses the ``/admin/fie/*`` screens introduced from Wave 6 onward:

  * S15  — Admin Users list  (``/admin/fie/users``)
  * S16  — Admin PCSE Inspector (``/admin/pcse`` — pcse_inspector_routes.py
            at the repo root, kept independent of this package)
  * S17  — Admin Autoreply Queue (``/admin/fie/autoreply``)

Distinct from the legacy ``admin_routes.py`` at the repo root (mounted at
``/admin/*``) which keeps serving the older operational pages. The two
surfaces coexist; new work lands here and benefits from
``fiesta.auth.admin_required`` + an isolated blueprint + a focused test suite.
"""
from .routes import register_routes as _register_users_routes
from .autoreply_routes import register_routes as _register_autoreply_routes


def register_routes(app):
    """Aggregate registration — every fiesta_admin blueprint lands here so
    main.py wiring stays a one-liner."""
    _register_users_routes(app)
    _register_autoreply_routes(app)


__all__ = ["register_routes"]
