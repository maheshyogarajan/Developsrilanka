"""fiesta.delivery_ops — Delivery Ops Command new modules.

Per PCSE Strategist D §7.1, three new modules: doc_lens_pipeline,
computation_engine, automation_runner. This package houses the
automation_runner Wave 2b deliverable from subagent_automation_v2
(council #2 §3 / Strategist D §3, §9). The doc_lens_pipeline arrives on
sibling branch wave2b/ocr-doclens-port; computation_engine deferred.
"""

# wave2b/ocr-doclens-port additions (subagent_ocr_v2 — council #2 §1):
# doc_lens v1.0 = T10 + BANK_INTEREST_WHT (stubs for BALANCE/A&L/EMPLOYER_LETTER).
# Sibling branch wave2b/automation-runner-sl-adapter does not import these so
# the merge is symmetric (each branch adds its own top-level export).
try:
    from .doc_lens import DocType, validate_doc  # noqa: F401
except Exception:  # pragma: no cover — keeps package importable if doc_lens.py absent
    pass
