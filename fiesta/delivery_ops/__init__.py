"""fiesta.delivery_ops — Delivery Ops Command new modules.

Per PCSE Strategist D §7.1, three new modules: doc_lens_pipeline,
computation_engine, automation_runner. This package houses the
doc_lens v1.0 Wave 2b deliverable from subagent_ocr_v2 (council #2 §1).
The automation_runner ships on sibling branch wave2b/automation-runner-sl-adapter;
computation_engine deferred.
"""

from .doc_lens import DocType, validate_doc

__all__ = ["validate_doc", "DocType"]
