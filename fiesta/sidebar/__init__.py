"""fiesta.sidebar — server-side sidebar rendering helpers.

Sub-package introduced by MS4 W2 Agent 2 (G1.4 — single FIESTA sidebar
with bookkeeping modules conditional on legacy data presence). The only
public entrypoint today is `activity.compute_bookkeeping_modules_available`,
exposed via the `inject_sidebar_modules` context processor in `app.py`.

See `_g1_design_lock_universal_shell.md` §D6 for the binding sidebar
contract.
"""
