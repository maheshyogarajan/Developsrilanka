"""
OCR provider abstraction.

Selects the active receipt-extraction provider and dispatches to it. Every
provider returns a dict matching the `Receipt` Pydantic schema (plus an
`extraction_model` annotation), so callers do not need to know which
backend ran.

Provider resolution order:
  1. explicit `provider=` argument
  2. `Organization.ocr_provider` column (if `organization_id` is given)
  3. `OCR_PROVIDER` environment variable
  4. DEFAULT_PROVIDER constant ("gemini")

Providers:
  - "gemini": legacy single-call Gemini Vision pipeline (lives in app.py).
  - "glm":    two-stage pipeline. Stage A = GLM-OCR (Z.ai) for vision OCR.
              Stage B = Gemini reasoner for tax classification. Falls back
              to the local rules engine if Stage B is unavailable.
"""

import os
import logging
from typing import Optional, Dict, Any

from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "gemini"
_ALLOWED = ("gemini", "glm")


def _resolve_per_org_provider(organization_id: Optional[int]) -> Optional[str]:
    if not organization_id:
        return None
    try:
        from models import Organization
        org = Organization.query.get(organization_id)
        if org and getattr(org, "ocr_provider", None):
            value = (org.ocr_provider or "").strip().lower()
            if value in _ALLOWED:
                return value
    except Exception as e:
        logger.debug(f"Per-org provider lookup failed: {e}")
    return None


def get_active_provider(
    override: Optional[str] = None,
    organization_id: Optional[int] = None,
) -> str:
    """Resolve which OCR provider should run for this call."""
    candidates = [
        override,
        _resolve_per_org_provider(organization_id),
        os.environ.get("OCR_PROVIDER"),
    ]
    for raw in candidates:
        if not raw:
            continue
        name = raw.strip().lower()
        if name in _ALLOWED:
            return name
        logger.warning(f"Unknown OCR provider {raw!r}, ignoring")
    return DEFAULT_PROVIDER


def _run_glm_pipeline(
    image: Image.Image, organization_id: Optional[int]
) -> Optional[Dict[str, Any]]:
    """Stage A (GLM-OCR) + Stage B (Gemini reasoner)."""
    from glm_ocr_client import extract_receipt_fields, GLMOCRError
    from gemini_reasoner import reason_receipt
    from activity_logger import ActivityLogger

    try:
        extracted = extract_receipt_fields(image)
    except Exception as e:
        # Catch every Stage A failure mode (GLMOCRError, circuit-breaker OPEN,
        # network errors, unexpected SDK exceptions) and degrade cleanly the
        # same way the legacy Gemini OCR path does, so callers always get a
        # `None` and a logged failed scan instead of an uncaught exception.
        is_breaker_open = "Circuit breaker OPEN" in str(e)
        if is_breaker_open:
            logger.error(f"GLM-OCR Stage A blocked by circuit breaker: {e}")
            err_category = "CIRCUIT_OPEN"
        else:
            logger.error(f"GLM-OCR Stage A failed: {e}")
            err_category = type(e).__name__
        try:
            ActivityLogger.log_receipt_scan(
                receipt_id=None,
                success=False,
                model_used="glm-ocr",
                error_category=err_category,
            )
        except Exception:
            pass
        return None

    stage_a_model_raw = extracted.get("_model_used", "glm-ocr")
    try:
        ActivityLogger.log_receipt_scan(
            receipt_id=None,
            success=True,
            model_used=stage_a_model_raw,
            extra={"provider": "glm", "stage": "A"},
        )
    except Exception:
        pass

    final = reason_receipt(extracted)
    stage_b_model = final.pop("_reasoner_model", "gemini-reasoner")

    try:
        ActivityLogger.log_receipt_scan(
            receipt_id=None, success=True, model_used=stage_b_model
        )
    except Exception:
        pass

    # Stable provider identifier for analytics and audit. The specific
    # underlying models (e.g. glm-4.5v, gemini-3-flash-preview) are kept on
    # the activity log entries above for cost attribution; the Receipt row
    # records the provider contract, not the concrete model chain.
    final["extraction_model"] = "glm-ocr"
    if organization_id:
        final["organization_id"] = organization_id
    return final


def _run_gemini_pipeline(
    image: Image.Image, organization_id: Optional[int]
) -> Optional[Dict[str, Any]]:
    """Legacy single-call Gemini Vision pipeline (lives in app.py)."""
    import app as app_module
    result = app_module._process_receipt_with_gemini_legacy(image)
    if isinstance(result, dict):
        result.setdefault("extraction_model", "gemini-2.5-flash")
        if organization_id:
            result["organization_id"] = organization_id
    return result


def process_receipt(
    image: Image.Image,
    provider: Optional[str] = None,
    organization_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Run receipt OCR using the active provider. Returns a dict matching the
    `Receipt` schema with `extraction_model` annotated, or None on
    irrecoverable failure.
    """
    name = get_active_provider(provider, organization_id)
    logger.info(f"OCR provider dispatch: {name} (org={organization_id})")
    if name == "glm":
        return _run_glm_pipeline(image, organization_id)
    return _run_gemini_pipeline(image, organization_id)
