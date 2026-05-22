"""
fiesta.assets_liabilities — /fie/al assets & liabilities declaration tracker.

Feature 9 (PLAN_X9_COMPLETION §5 D6-D9, Wave 2).

Replaces the v1.0 deep-link out to Lanka.tax FA 5192455 with a full
in-app A&L declaration tracker:

  D6  Blueprint scaffold + SQLAlchemy models (AssetEntry, LiabilityEntry)
  D7  Routes: /fie/al list + /fie/al/edit form
  D8  ReportLab PDF generator → IRD-ready A&L declaration
  D9  Optional FA 5192455 push for Lanka.tax-linked customers

Routes (canonical):
  GET  /fie/al           → fiesta_al.list_view    — all entries + net worth
  GET  /fie/al/edit      → fiesta_al.edit_view    — add/edit form (progressive)
  POST /fie/al/edit      → fiesta_al.edit_save    — persist transaction
  GET  /fie/al/pdf       → fiesta_al.download_pdf — IRD A&L PDF download
  POST /fie/al/push      → fiesta_al.push_to_fa   — FA 5192455 push (D9)
"""
from .routes import register_routes  # noqa: F401
