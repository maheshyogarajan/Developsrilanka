"""
OCR provider abstraction.

Selects the active receipt-extraction provider based on the `OCR_PROVIDER`
environment variable (or per-call override) and dispatches to it. Every
provider returns a dict matching the `Receipt` Pydantic schema, so callers
never have to care which backend ran.

Providers:
- "gemini": legacy single-call Gemini Vision pipeline (does both OCR and
  tax reasoning in one shot). Implementation lives in app.py for now and is
  resolved lazily to avoid circular imports.
- "glm":   two-stage pipeline. Stage A = GLM-OCR (Z.ai) for cheap, accurate
  vision OCR. Stage B = Gemini reasoner for tax-deductibility / IFRS
  category. Falls back to the rules engine if Stage B is unavailable.
"""

import os
import logging
from typing import Optional, Dict, Any

from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "gemini"


def get_active_provider(override: Optional[str] = None) -> str:
    """Return the canonical name of the OCR provider that should run."""
    name = (override or os.environ.get("OCR_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if name not in ("gemini", "glm"):
        logger.warning(f"Unknown OCR_PROVIDER={name!r}, falling back to {DEFAULT_PROVIDER}")
        name = DEFAULT_PROVIDER
    return name


def _run_glm_pipeline(image: Image.Image) -> Optional[Dict[str, Any]]:
    """Stage A (GLM-OCR) + Stage B (Gemini reasoner)."""
    from glm_ocr_client import extract_receipt_fields, GLMOCRError
    from gemini_reasoner import reason_receipt
    from activity_logger import ActivityLogger

    try:
        extracted = extract_receipt_fields(image)
    except GLMOCRError as e:
        logger.error(f"GLM-OCR Stage A failed: {e}")
        try:
            ActivityLogger.log_receipt_scan(
                receipt_id=None,
                success=False,
                model_used="glm-ocr",
                error_category=type(e).__name__,
            )
        except Exception:
            pass
        return None

    try:
        ActivityLogger.log_receipt_scan(
            receipt_id=None,
            success=True,
            model_used=extracted.get("_model_used", "glm-ocr"),
        )
    except Exception:
        pass

    final = reason_receipt(extracted)

    try:
        ActivityLogger.log_receipt_scan(
            receipt_id=None,
            success=True,
            model_used=final.get("_reasoner_model", "gemini-reasoner"),
        )
    except Exception:
        pass

    final.pop("_reasoner_model", None)
    return final


def _run_gemini_pipeline(image: Image.Image) -> Optional[Dict[str, Any]]:
    """Legacy single-call Gemini Vision pipeline (lives in app.py)."""
    import app as app_module
    return app_module._process_receipt_with_gemini_legacy(image)


def process_receipt(image: Image.Image, provider: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Extract a receipt's contents (and tax classification) using the active
    OCR provider. Returns a dict matching the `Receipt` schema, or None on
    irrecoverable failure.
    """
    name = get_active_provider(provider)
    logger.info(f"OCR provider dispatch: {name}")
    if name == "glm":
        return _run_glm_pipeline(image)
    return _run_gemini_pipeline(image)
