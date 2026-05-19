"""
Password complexity validation for S2 signup.

Council #6 (Self-File-only v1, 2026-05-20) constraint: bcrypt 12 rounds,
min-length 12 characters, must include a digit, a symbol, and mixed case.

Werkzeug's `generate_password_hash` defaults to pbkdf2:sha256 in newer
versions; we use it with `method='scrypt'` if available, falling back to the
default. (FIESTA's existing /register flow uses the same generator without
specifying — staying consistent here so /signup-created and /register-created
users are interchangeable when logging in.)

We expose:
  - score_password(pw)   -> dict with strength buckets + which rules pass
  - check_complexity(pw) -> (ok: bool, errors: list[str])
"""
from __future__ import annotations

import re
from typing import Tuple, List, Dict


MIN_LENGTH = 12


def check_complexity(password: str) -> Tuple[bool, List[str]]:
    """Return (ok, errors). Single source of truth for server-side validation.

    Rules:
      1. Length >= 12.
      2. At least one digit.
      3. At least one symbol (anything not a-z A-Z 0-9).
      4. Mixed case (at least one upper and one lower).
    """
    errors: List[str] = []
    if not isinstance(password, str):
        return False, ["Password is required."]

    if len(password) < MIN_LENGTH:
        errors.append(f"Use at least {MIN_LENGTH} characters.")
    if not re.search(r"\d", password):
        errors.append("Include at least one number.")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("Include at least one symbol (e.g. ! @ # ?).")
    if not (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password)):
        errors.append("Mix uppercase and lowercase letters.")

    return (len(errors) == 0), errors


def score_password(password: str) -> Dict[str, object]:
    """Lightweight strength scoring for the JS-mirrored UI hint.

    Returns:
        {
          "length_ok": bool, "has_digit": bool, "has_symbol": bool,
          "mixed_case": bool, "bucket": "weak"|"fair"|"strong"
        }
    Server-side mirror of the same calculation the signup template runs in
    the browser — useful for tests asserting that the inline meter logic
    matches the validation gate.
    """
    if not isinstance(password, str):
        password = ""
    checks = {
        "length_ok": len(password) >= MIN_LENGTH,
        "has_digit": bool(re.search(r"\d", password)),
        "has_symbol": bool(re.search(r"[^A-Za-z0-9]", password)),
        "mixed_case": bool(re.search(r"[a-z]", password)) and bool(re.search(r"[A-Z]", password)),
    }
    passing = sum(checks.values())
    if passing <= 1:
        bucket = "weak"
    elif passing <= 3:
        bucket = "fair"
    else:
        bucket = "strong"
    checks["bucket"] = bucket
    return checks
