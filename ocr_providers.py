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
  4. DEFAULT_PROVIDER constant ("glm")

Providers:
  - "glm":    DEFAULT. Two-stage pipeline. Stage A = GLM-OCR (Z.ai) for
              vision OCR; Stage B = Gemini reasoner for tax classification.
              On Stage A failure (circuit breaker open, timeout, schema
              error, etc.) the dispatcher transparently falls back to the
              legacy Gemini single-call pipeline so the scan still
              completes. If Stage B is unavailable, the local
              `sri_lanka_tax_rules` engine plus keyword-based category
              inference produces a schema-valid receipt.
  - "gemini": legacy single-call Gemini Vision pipeline (lives in app.py).
              Also serves as the automatic fallback target for "glm".
"""

import os
import logging
from typing import Optional, Dict, Any

from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "glm"
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
        # Stage A (GLM-OCR) failed — could be GLMOCRError, circuit-breaker
        # OPEN, network/timeout, or any SDK exception. Log the failure for
        # audit / cost telemetry, then fall through to the legacy Gemini
        # vision pipeline so problem images still get processed instead of
        # returning a hard None to the caller.
        is_breaker_open = "Circuit breaker OPEN" in str(e)
        if is_breaker_open:
            logger.warning(
                f"GLM-OCR Stage A blocked by circuit breaker, "
                f"falling back to Gemini: {e}"
            )
            err_category = "CIRCUIT_OPEN"
        else:
            logger.warning(
                f"GLM-OCR Stage A failed, falling back to Gemini: {e}"
            )
            err_category = type(e).__name__
        try:
            ActivityLogger.log_receipt_scan(
                receipt_id=None,
                success=False,
                model_used="glm-ocr",
                error_category=err_category,
                extra={"provider": "glm", "stage": "A", "fallback": "gemini"},
            )
        except Exception:
            pass
        return _run_gemini_pipeline(image, organization_id)

    from activity_logger import estimate_cost_from_tokens

    stage_a_model_raw = extracted.get("_model_used", "glm-ocr")
    stage_a_in = extracted.get("_input_tokens") or 0
    stage_a_out = extracted.get("_output_tokens") or 0
    stage_a_cost = estimate_cost_from_tokens(stage_a_model_raw, stage_a_in, stage_a_out)
    try:
        ActivityLogger.log_receipt_scan(
            receipt_id=None,
            success=True,
            model_used="glm-ocr",
            input_tokens=stage_a_in or None,
            output_tokens=stage_a_out or None,
            estimated_cost_usd=stage_a_cost if (stage_a_in or stage_a_out) else None,
            extra={
                "provider": "glm",
                "stage": "A",
                "underlying_model": stage_a_model_raw,
            },
        )
    except Exception:
        pass

    final = reason_receipt(extracted)
    stage_b_model = final.pop("_reasoner_model", "gemini-reasoner")
    stage_b_in = final.pop("_input_tokens", 0) or 0
    stage_b_out = final.pop("_output_tokens", 0) or 0
    stage_b_cost = estimate_cost_from_tokens(stage_b_model, stage_b_in, stage_b_out)

    try:
        ActivityLogger.log_receipt_scan(
            receipt_id=None,
            success=True,
            model_used=stage_b_model,
            input_tokens=stage_b_in or None,
            output_tokens=stage_b_out or None,
            estimated_cost_usd=stage_b_cost if (stage_b_in or stage_b_out) else None,
            extra={"provider": "glm", "stage": "B", "scan_complete": True},
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
        result.setdefault("extraction_model", "gemini-3-flash-preview")
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
