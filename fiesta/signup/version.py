"""
Single source of truth for the live ToS / Privacy versions.

When counsel returns the legal-review pass (target 2026-05-27), bump the
version strings and flip the `_IS_DRAFT` flags. The User row persists the
version a customer accepted, so we can re-prompt for re-acceptance on a
material change.
"""

TOS_VERSION = "v0.1-draft"
PRIVACY_VERSION = "v0.1-draft"

# When True, the rendered /terms and /privacy pages display a DRAFT banner
# and the signup form labels the checkboxes "(draft)". Flip to False after
# counsel review (G.1.1).
TOS_IS_DRAFT = True
PRIVACY_IS_DRAFT = True

# Display strings used by the signup template.
TOS_DISPLAY = "Terms of Service" + (" (draft)" if TOS_IS_DRAFT else "")
PRIVACY_DISPLAY = "Privacy Policy" + (" (draft)" if PRIVACY_IS_DRAFT else "")

# Legal-review return target — surfaced in the draft banner.
LEGAL_REVIEW_RETURN_DATE = "2026-05-27"

# Feedback contact — surfaced in the legal-doc footers.
FEEDBACK_EMAIL = "mahesh@yogarajan.com"
