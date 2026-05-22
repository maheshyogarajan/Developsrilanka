"""X9 F8.1 — repair the walkthrough admin user's role.

The walkthrough seed (run as a one-shot on prod, not committed to this repo)
did `u.is_admin = spec["admin"]`. Since `is_admin` is a METHOD on `User`,
the assignment created a transient attribute and `role` stayed at 'user'.
The admin user therefore looped /scan -> /onboarding -> /scan, because
`current_user.role != 'admin'` and the org-check redirect to /onboarding
fired before F-Platform-3 was deployed (and still fires for non-FIESTA
personas without an org).

Run on prod once:

    flyctl ssh console -a fiesta-mvp -C \
        "python scripts/x9_f8_1_promote_walkthrough_admin.py"

Idempotent — if the user already has role='admin', it reports and exits 0.
Safe to re-run.
"""
from __future__ import annotations

import os
import sys

# Make the repo root importable from anywhere.
_HERE = os.path.abspath(os.path.dirname(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import app  # noqa: E402  (path manipulation above)
from models import User, db  # noqa: E402


# Walkthrough seed emails by suffix — any user whose email starts with these
# stems and ends with @walkthrough.test is treated as a walkthrough seed user.
ADMIN_STEMS = ("admin",)
WALKTHROUGH_DOMAIN = "@walkthrough.test"


def find_walkthrough_admin_candidates():
    """Return User rows whose email looks like a walkthrough admin seed."""
    candidates = []
    rows = User.query.filter(User.email.ilike(f"%{WALKTHROUGH_DOMAIN}")).all()
    for u in rows:
        local = u.email.split("@", 1)[0].lower()
        if any(local == stem or local.startswith(stem + "_") for stem in ADMIN_STEMS):
            candidates.append(u)
    return candidates


def main() -> int:
    with app.app_context():
        candidates = find_walkthrough_admin_candidates()
        if not candidates:
            print("[X9 F8.1] No walkthrough admin candidates found "
                  "(no users matching admin*@walkthrough.test).")
            return 0

        promoted = 0
        already = 0
        for u in candidates:
            if u.is_admin():
                print(f"[X9 F8.1] {u.email} already role='admin' — skipping.")
                already += 1
                continue
            previous = u.role
            u.promote_to_admin(reason="x9_f8_1_walkthrough_seed_repair")
            print(f"[X9 F8.1] Promoted {u.email}: role '{previous}' -> 'admin'.")
            promoted += 1

        if promoted:
            db.session.commit()
            print(f"[X9 F8.1] Committed. promoted={promoted} already_admin={already}")
        else:
            print(f"[X9 F8.1] Nothing to commit. already_admin={already}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
