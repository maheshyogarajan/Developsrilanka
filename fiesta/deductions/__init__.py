"""fiesta.deductions — S5 "Reduce your tax — 10 ways" screen.

Wave 3 educational screen for the FIESTA self-file flow. Customer sees 10
deduction categories (IRA-cited), learns what each one means, claims the
ones that apply to them, and sees the live tax saving estimate.

Modules:
    catalog       : YAML data file (10 categories with IRA citations)
    models        : DeductionClaim SQLAlchemy model
    routes        : Flask blueprint /reduce-tax + /claim, /unclaim, /estimate
    personalize   : recommended_deductions(profile, income_summary)
    estimate      : estimate_saving(claims, income, slabs) — marginal-rate math
    tests/        : 15+ test cases covering happy/personalize/estimate/edge

Voice: empowerment + jurisdiction-neutral. "Here's what the law allows" —
not "what we recommend". Every card carries an IRA section citation.

Author: CEO-OS subagent, FIESTA Week 2 Wave 3 dispatch (2026-05-20).
"""
