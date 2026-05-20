"""fiesta.triage — S1 post-signup triage (Wave 1, 2026-05-20).

3 neutral fact-finds that branch the user's downstream onboarding:
  1. earning_source   — pure_foreign / mixed / pure_local
  2. earning_vehicle  — solo_freelancer / studio_with_subcontractors /
                        employee_with_side / property / other  (multi-select)
  3. filing_history   — never_filed / filed_manually_with_help /
                        used_lankatax / used_other_platform

Answers persist to User.triage_answers (JSON column added by the idempotent
migration add_triage_answers_to_user.py). Downstream screens read this dict to
decide branching; S1 itself only writes.

Public surface:
  - routes.register_routes(app)         : Flask blueprint hook (called by main.py)
  - questions.QUESTIONS                 : the catalog
  - questions.QUESTION_ORDER            : canonical sequence
  - validators.validate_answer          : per-question validator
  - validators.validate_full_answers    : whole-payload validator

Mount point: /fie/triage (GET + POST), /fie/triage/restart (POST).
"""

from .routes import register_routes  # noqa: F401
from .questions import (  # noqa: F401
    QUESTIONS,
    QUESTIONS_BY_ID,
    QUESTION_ORDER,
)
from .validators import (  # noqa: F401
    TriageValidationError,
    validate_answer,
    validate_full_answers,
)

__all__ = [
    "register_routes",
    "QUESTIONS",
    "QUESTIONS_BY_ID",
    "QUESTION_ORDER",
    "TriageValidationError",
    "validate_answer",
    "validate_full_answers",
]
