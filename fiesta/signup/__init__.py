"""
fiesta.signup — S2 signup (Wave 1, 2026-05-20).

Zero-friction account creation: email + password + ToS/Privacy acceptance.
No NIC, no TIN, no PIN at signup (per Self-File-only v1, THE_PATH_20260520.md).

Three concerns live in this package:

  1. routes.py        — Flask blueprint with /signup, /login, /logout,
                        /signup/verify/<token>, /terms, /privacy
  2. password.py      — password complexity + bcrypt helpers
  3. version.py       — single source of truth for the current legal-doc
                        versions (advertised + persisted on User)

The blueprint is registered in main.py via the standard `register_routes(app)`
pattern. The existing `/register` flow in app.py is left in place; this is the
*new* FIESTA-branded entry point at `/signup`.

Schema dependency: User.tos_accepted_version, tos_accepted_at,
privacy_accepted_version, privacy_accepted_at — added by
`add_tos_privacy_acceptance_to_user.py` (idempotent migration).
"""
from .routes import register_routes  # noqa: F401
from .version import (  # noqa: F401
    TOS_VERSION,
    PRIVACY_VERSION,
    TOS_IS_DRAFT,
    PRIVACY_IS_DRAFT,
)
