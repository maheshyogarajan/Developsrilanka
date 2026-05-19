"""fiesta.delivery_ops — Delivery Ops Command new modules.

Per PCSE Strategist D §7.1, three new modules: doc_lens_pipeline,
computation_engine, automation_runner.

Wave 2b v2 ships:
- automation_runner v1 (SL adapter, council #2 §3 + D §3, §9) — branch wave2b/automation-runner-sl-adapter
- doc_lens v1 (DataSciLT port, council #2 §1) — branch wave2b/ocr-doclens-port
- computation_engine deferred to a later wave
"""

from .doc_lens import DocType, validate_doc  # noqa: F401

__all__ = ["validate_doc", "DocType"]
