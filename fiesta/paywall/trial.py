"""
fiesta.paywall.trial — free-trial helpers.

Trial logic (X1 v1, council brief 2026-05-20):

  * Trial = 14 days from User.created_at.
  * Trial users can access free-tier screens (S0-S5) unlimited.
  * Trial users hitting Self-File screens see the upsell modal — they are
    NOT given temporary access to paid features. The "trial" is for the
    free features; paid features always sit behind the paywall from day 1.
  * No "trial expired" state mid-flow. After 14 days, the user is still
    free-tier — they just lose the "you're on a trial" framing in the UI.
    This is honest framing and avoids the dark-pattern of "soft locking"
    features the user could see during a trial.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


TRIAL_DAYS = 14


def trial_ends_at(user) -> Optional[datetime]:
    """Return the datetime the user's trial ends, or None if anon / no row."""
    if user is None or not getattr(user, "created_at", None):
        return None
    return user.created_at + timedelta(days=TRIAL_DAYS)


def is_in_trial(user, now: Optional[datetime] = None) -> bool:
    """True iff user is within the 14-day window from signup.

    Anonymous / missing-row users return False (no trial framing for them).
    """
    ends = trial_ends_at(user)
    if ends is None:
        return False
    now = now or datetime.utcnow()
    return now < ends


def trial_days_remaining(user, now: Optional[datetime] = None) -> int:
    """Whole days left in the trial. 0 once the trial has ended (or for
    anon / missing-row users)."""
    ends = trial_ends_at(user)
    if ends is None:
        return 0
    now = now or datetime.utcnow()
    if now >= ends:
        return 0
    delta = ends - now
    return delta.days


__all__ = [
    "TRIAL_DAYS",
    "trial_ends_at",
    "is_in_trial",
    "trial_days_remaining",
]
