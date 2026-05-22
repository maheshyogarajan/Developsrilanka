"""
fiesta.legal — /legal/tos and /legal/privacy routes (E2 F1.8, Wave 1).

Renders Terms of Service and Privacy Policy pages in the FIESTA hub shell
(layout_template dynamic extend). Pages ship with placeholder content marked
DRAFT pending Lanka.tax legal counsel review (non-blocking per PLAN_X9_COMPLETION
§0.5 non-halt condition 4).

Routes (canonical):
  GET /legal/tos      → legal.tos
  GET /legal/privacy  → legal.privacy

The legacy /terms and /privacy routes on the signup blueprint now redirect here
so old links and the signup form's anchor hrefs remain valid.
"""
from .routes import register_routes  # noqa: F401
