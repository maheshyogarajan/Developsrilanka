"""
fiesta.legal.routes — Legal document pages in the FIESTA hub shell.

E2 F1.8 (PLAN_X9_COMPLETION §6). Placeholder content; counsel review async
and non-blocking. Banner is displayed while TOS_IS_DRAFT / PRIVACY_IS_DRAFT
are True in fiesta.signup.version.
"""
from __future__ import annotations

import logging

from flask import Blueprint, Flask, redirect, render_template, url_for

log = logging.getLogger(__name__)

legal_bp = Blueprint(
    "legal",
    __name__,
    template_folder="../../templates",
)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@legal_bp.route("/legal/tos")
def tos():
    """Terms of Service page in the FIESTA hub shell."""
    try:
        from fiesta.signup.version import (
            TOS_VERSION,
            TOS_IS_DRAFT,
            LEGAL_REVIEW_RETURN_DATE,
            FEEDBACK_EMAIL,
        )
    except ImportError:
        TOS_VERSION = "v0.1-draft"
        TOS_IS_DRAFT = True
        LEGAL_REVIEW_RETURN_DATE = "pending"
        FEEDBACK_EMAIL = "legal@lanka.tax"

    return render_template(
        "legal/tos.html",
        tos_version=TOS_VERSION,
        is_draft=TOS_IS_DRAFT,
        legal_review_return_date=LEGAL_REVIEW_RETURN_DATE,
        feedback_email=FEEDBACK_EMAIL,
    )


@legal_bp.route("/legal/privacy")
def privacy():
    """Privacy Policy page in the FIESTA hub shell."""
    try:
        from fiesta.signup.version import (
            PRIVACY_VERSION,
            PRIVACY_IS_DRAFT,
            LEGAL_REVIEW_RETURN_DATE,
            FEEDBACK_EMAIL,
        )
    except ImportError:
        PRIVACY_VERSION = "v0.1-draft"
        PRIVACY_IS_DRAFT = True
        LEGAL_REVIEW_RETURN_DATE = "pending"
        FEEDBACK_EMAIL = "legal@lanka.tax"

    return render_template(
        "legal/privacy.html",
        privacy_version=PRIVACY_VERSION,
        is_draft=PRIVACY_IS_DRAFT,
        legal_review_return_date=LEGAL_REVIEW_RETURN_DATE,
        feedback_email=FEEDBACK_EMAIL,
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def register_routes(app: Flask) -> None:
    """Standard FIESTA blueprint hook called from main.py."""
    if "legal" in app.blueprints:
        log.debug("Legal blueprint already registered — skipping.")
        return
    app.register_blueprint(legal_bp)
    log.info("Legal blueprint registered: /legal/tos, /legal/privacy")
