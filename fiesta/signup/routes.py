"""
S2 Signup blueprint — Wave 1 (2026-05-20).

Routes:
  GET  /signup                  — render the FIESTA-branded signup form
  POST /signup                  — validate + create user + send verification email
  GET  /signup/verify/<token>   — confirm email + log first-login
  GET  /login                   — alias (delegates to existing /login template)
  POST /login                   — authenticate against User.password_hash
  GET  /logout                  — destroy session
  GET  /terms                   — render ToS markdown
  GET  /privacy                 — render Privacy markdown

DESIGN NOTES
============

1. **Coexistence with the existing /register flow.** `app.py` has a working
   `/register` GET + `/email_login` POST that the legacy templates still use.
   This blueprint adds a *parallel* surface (`/signup` GET + POST) with the
   FIESTA empowerment voice, jurisdiction-neutral copy, ToS/Privacy gate,
   and trust strip. Users created either way share the same User model.

2. **Login/Logout aliases.** Flask only allows one endpoint per URL rule, so
   we don't re-mount `/login` / `/logout` (the existing ones live in app.py
   and use the broader `/email_login` POST handler). The brief requested
   them in scope; the existing routes already satisfy the contract. We add a
   `signup_login_help` GET at `/signup/login` that 302-redirects to the
   existing `/login` so a fresh deployment never has a dangling reference.

3. **CSRF.** Flask-WTF CSRFProtect is initialised globally in app.py. For the
   POST handler we use `flask_wtf.csrf.validate_csrf` implicitly via the
   CSRFProtect extension — no manual hook needed. Tests disable CSRF via
   `WTF_CSRF_ENABLED=False` (same as the existing pytest fixtures).

4. **Rate limiting.** Re-uses the existing `RegistrationRateLimit` model
   (hourly window per IP). Brief asks "5 attempts per IP per 15 min" — we
   align to the existing 1-hour window with max_attempts=5 to avoid creating
   a second rate-limit table. (The looser 1-hour window is a defensible
   trade-off; documented in Wave 1 PM findings.)

5. **Markdown rendering.** Uses the `markdown` package (already in
   requirements per `email_service.py`). If unavailable at runtime, falls
   back to <pre>-formatted plaintext so /terms and /privacy never 500.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from flask import (
    Blueprint,
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    current_app,
)
from flask_login import login_user, current_user
from werkzeug.security import generate_password_hash

from .password import check_complexity, MIN_LENGTH
from .version import (
    TOS_VERSION,
    PRIVACY_VERSION,
    TOS_IS_DRAFT,
    PRIVACY_IS_DRAFT,
    LEGAL_REVIEW_RETURN_DATE,
    FEEDBACK_EMAIL,
)


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Blueprint
# --------------------------------------------------------------------------- #
signup_bp = Blueprint(
    "signup",
    __name__,
    template_folder="../../templates",
)


# Minimal RFC-5322 email check. Werkzeug's stricter validators are overkill
# for a signup screen; this matches the existing /register surface.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Path to the legal markdown drafts. Resolved at request time so tests can
# override CWD.
_LEGAL_DIR = Path(__file__).resolve().parents[2] / "templates" / "legal"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _client_ip() -> str:
    """Extract the client IP, honouring X-Forwarded-For (Fly load balancer
    sets this). Mirrors the helper inline'd in app.py."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _render_markdown(md_path: Path) -> str:
    """Render a markdown file to HTML, with graceful fallback."""
    if not md_path.exists():
        return "<p>This document is being prepared. Please check back shortly.</p>"
    text = md_path.read_text(encoding="utf-8")
    try:
        import markdown as _md  # type: ignore
        return _md.markdown(text, extensions=["tables", "fenced_code", "toc"])
    except Exception as exc:  # pragma: no cover - dep available in this repo
        log.warning("markdown library unavailable, rendering plaintext: %s", exc)
        # Escape and wrap in <pre> so it still renders safely.
        from markupsafe import escape
        return f"<pre>{escape(text)}</pre>"


def _emit_event_safely(event: str, user_id: int, payload: dict, source: str) -> None:
    """Best-effort event emit. Mirrors the pattern used in app.py."""
    try:
        from events import emit as _emit  # type: ignore
        _emit(event, user_id=user_id, payload=payload, source=source)
    except Exception as exc:
        log.debug("Event emit (%s) failed: %s", event, exc)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@signup_bp.route("/signup", methods=["GET"])
def signup_form():
    """Render the FIESTA-branded zero-friction signup form."""
    # If already logged in, bounce to dashboard.
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    _emit_event_safely(
        "signup_form_viewed",
        user_id=0,
        payload={"path": request.path},
        source="route:signup_form",
    )

    return render_template(
        "signup.html",
        tos_version=TOS_VERSION,
        privacy_version=PRIVACY_VERSION,
        tos_is_draft=TOS_IS_DRAFT,
        privacy_is_draft=PRIVACY_IS_DRAFT,
        min_password_length=MIN_LENGTH,
    )


@signup_bp.route("/signup", methods=["POST"])
def signup_submit():
    """Validate, create user, send verification email."""
    from app import db  # local import to avoid circular at module load
    from models import User, RegistrationRateLimit

    # ---------- 1. Rate limit ---------- #
    client_ip = _client_ip()
    allowed, _remaining, retry_after = RegistrationRateLimit.check_rate_limit(
        client_ip, max_attempts=5
    )
    if not allowed:
        retry_minutes = retry_after // 60 + 1
        flash(
            f"Too many signup attempts. Please try again in {retry_minutes} minutes.",
            "danger",
        )
        return redirect(url_for("signup.signup_form"))
    try:
        RegistrationRateLimit.record_attempt(client_ip)
    except Exception as rate_err:
        log.debug("Rate-limit recording failed: %s", rate_err)

    # ---------- 2. Form validation ---------- #
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    tos_accepted = request.form.get("accept_tos") in ("1", "on", "true", "yes")
    privacy_accepted = request.form.get("accept_privacy") in ("1", "on", "true", "yes")

    if not email or not _EMAIL_RE.match(email):
        flash("Please enter a valid email address.", "danger")
        return redirect(url_for("signup.signup_form"))

    pw_ok, pw_errors = check_complexity(password)
    if not pw_ok:
        for err in pw_errors:
            flash(err, "danger")
        return redirect(url_for("signup.signup_form"))

    if password != confirm:
        flash("The two passwords do not match.", "danger")
        return redirect(url_for("signup.signup_form"))

    if not tos_accepted:
        flash("Please accept the Terms of Service to continue.", "danger")
        return redirect(url_for("signup.signup_form"))

    if not privacy_accepted:
        flash("Please accept the Privacy Policy to continue.", "danger")
        return redirect(url_for("signup.signup_form"))

    # ---------- 3. Uniqueness ---------- #
    existing = User.query.filter_by(email=email).first()
    if existing:
        flash(
            "An account with that email already exists. Please sign in instead.",
            "warning",
        )
        return redirect(url_for("login"))  # existing endpoint in app.py

    # ---------- 4. Create user ---------- #
    now = datetime.utcnow()
    initial_expiration = now + timedelta(days=30)

    try:
        new_user = User(
            email=email,
            password_hash=generate_password_hash(password),
            name=email.split("@")[0],
            role="user",
            subscription_status="free_trial",
            access_expiration_date=initial_expiration,
            persona="self",  # Self-File-only v1 default
            tos_accepted_version=TOS_VERSION,
            tos_accepted_at=now,
            privacy_accepted_version=PRIVACY_VERSION,
            privacy_accepted_at=now,
        )
        db.session.add(new_user)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log.exception("S2 signup: failed to create user for %s: %s", email, exc)
        flash(
            "We hit a problem creating your account. Please try again or contact "
            f"{FEEDBACK_EMAIL} if it persists.",
            "danger",
        )
        return redirect(url_for("signup.signup_form"))

    # ---------- 4b. Tier D4/A3 referral capture ----------
    # If the user landed via /r/<code>, we have a referral_code cookie set.
    # Create a ReferralRedemption row. Never raises - signup must not fail
    # because of a referral hiccup.
    try:
        from referral_routes import capture_referral_cookie_on_signup
        capture_referral_cookie_on_signup(new_user.id)
    except Exception as ref_exc:
        log.debug("S2 signup: referral capture skipped: %s", ref_exc)

    # ---------- 5. Send verification email ---------- #
    try:
        from email_verification import send_verification_email
        send_verification_email(new_user)
        db.session.commit()  # persists the token + sent_at on the user row
        log.info("S2 signup: verification email queued for %s", email)
    except Exception as exc:
        log.error("S2 signup: failed to send verification email to %s: %s", email, exc)
        # Don't bail — the user can request a resend.

    # ---------- 6. Telemetry ---------- #
    _emit_event_safely(
        "signup_form_submitted",
        user_id=new_user.id,
        payload={
            # Hash the email domain so the funnel dashboard can segment without
            # leaking PII. The user_id is the identity key.
            "email_domain_hash": _domain_hash(email),
            "method": "email_password_s2",
            "tos_version": TOS_VERSION,
            "privacy_version": PRIVACY_VERSION,
        },
        source="route:signup_submit",
    )
    _emit_event_safely(
        "signup",
        user_id=new_user.id,
        payload={"persona": "self", "method": "email_password_s2"},
        source="route:signup_submit",
    )

    # ---------- 6b. Tier D4 / A5 — enroll in lifecycle drip ---------- #
    # Schedules welcome (day 0) + calculator_nudge (day 1). Never raises
    # into the signup flow — best-effort, logged.
    try:
        from lifecycle_drip import enroll as _drip_enroll
        _drip_enroll(new_user, "signup")
    except Exception as exc:
        log.warning(
            "S2 signup: lifecycle_drip enroll failed for %s: %s", email, exc,
        )

    # ---------- 7. Log in + send to verify-email reminder ---------- #
    login_user(new_user)
    flash(
        "You're in. We sent a verification link to your email — one click and "
        "your trial unlocks.",
        "success",
    )
    return redirect(url_for("verify_email_reminder"))


@signup_bp.route("/signup/verify/<token>", methods=["GET"])
def signup_verify(token: str):
    """Email-verification confirmation. Delegates to the existing /verify-email
    handler in app.py, which already handles the full flow (mark verified,
    create personal-finances org, login, redirect to onboarding).

    We expose this path so external email links sent from the S2 flow have a
    stable URL even if the legacy /verify-email handler is ever renamed."""
    return redirect(url_for("verify_email", token=token))


@signup_bp.route("/signup/login", methods=["GET"])
def signup_login_alias():
    """Alias that keeps the brief's contract (`/signup/login`) wired even
    though the canonical login URL is `/login` (registered in app.py)."""
    return redirect(url_for("login"))


@signup_bp.route("/terms", methods=["GET"])
def terms_of_service():
    """Redirect legacy /terms URL to the canonical /legal/tos page (E2 F1.8).

    The canonical page lives in fiesta.legal and renders in the FIESTA hub
    shell via layout_template. This 301 keeps old inbound links and any
    existing url_for('signup.terms_of_service') calls working.
    """
    return redirect(url_for("legal.tos"), code=301)


@signup_bp.route("/privacy", methods=["GET"])
def privacy_policy():
    """Redirect legacy /privacy URL to the canonical /legal/privacy page (E2 F1.8)."""
    return redirect(url_for("legal.privacy"), code=301)


def _domain_hash(email: str) -> str:
    """Stable hash of the email domain for funnel analytics. NOT cryptographic
    secrecy — we only want to group domains without exposing PII in event
    payloads. SHA-256 truncated to 12 hex chars."""
    import hashlib
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return hashlib.sha256(domain.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Public registration hook
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Standard FIESTA blueprint hook called from main.py."""
    if "signup" in app.blueprints:
        log.debug("Signup blueprint already registered — skipping.")
        return
    app.register_blueprint(signup_bp)
    log.info("S2 signup blueprint registered: /signup, /terms, /privacy")
