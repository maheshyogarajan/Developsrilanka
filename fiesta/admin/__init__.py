"""
fiesta.admin — FIESTA-branded admin surface (Wave 6, 2026-05-20).

Houses the new ``/admin/fie/*`` screens introduced from Wave 6 onward:

  * S15  — Admin Users list  (``/admin/fie/users``)
  * S16  — (placeholder, future)
  * S17  — (placeholder, future)

Distinct from the legacy ``admin_routes.py`` at the repo root (mounted at
``/admin/*``) which keeps serving the older operational pages. The two
surfaces coexist; new work lands here and benefits from
``fiesta.auth.admin_required`` + an isolated blueprint + a focused test suite.
"""
from .routes import register_routes  # noqa: F401

__all__ = ["register_routes"]
