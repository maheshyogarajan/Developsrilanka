"""
GLM-OCR client (Z.ai / Zhipu) — Stage A of the two-stage OCR pipeline.

Sends a receipt image to Z.ai's OpenAI-compatible chat-completions endpoint
and asks for the receipt's factual fields (vendor, date, items, totals,
taxes) as JSON. Tax-deductibility / IFRS category reasoning is intentionally
left to Stage B (gemini_reasoner.py).

Reliability:
  - Wrapped with the same circuit-breaker pattern used for Gemini calls.
  - Per-attempt retry with exponential backoff on transient HTTP errors.
  - Falls back through a short list of GLM model names if one is unavailable.
  - On schema-validation failure, retries once with a stricter prompt.
"""

import os
import io
import json
import base64
import logging
import time
from typing import Optional, Dict, Any

import requests
from PIL import Image

from gemini_error_handler import GeminiCircuitBreaker
from receipt_schema import StageARawReceipt

logger = logging.getLogger(__name__)

ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
ZAI_CHAT_ENDPOINT = f"{ZAI_BASE_URL}/chat/completions"

GLM_OCR_MODELS = [
    os.environ.get("GLM_OCR_MODEL", "glm-4.5v"),
    "glm-4v-plus",
    "glm-4v",
]

REQUEST_TIMEOUT_S = float(os.environ.get("GLM_OCR_TIMEOUT", "60"))
MAX_RETRIES = int(os.environ.get("GLM_OCR_MAX_RETRIES", "3"))

glm_circuit_breaker = GeminiCircuitBreaker(
    failure_threshold=5, timeout=300, window=300
)


EXTRACTION_PROMPT = """You are a receipt OCR engine. Extract the factual contents of this receipt or bank-transfer screenshot.

DOCUMENT HANDLING:
- Detect document type: physical receipt vs bank transfer screenshot.
- Bank transfers: use Beneficiary/Recipient/Payee as vendor_name; create a single line item describing the transfer purpose.
- Multilingual: convert all extracted text to English/Latin script. Dates as YYYY-MM-DD.
- Numbers: parse any format (1,234.56 or 1.234,56) as plain decimals.
- Missing values: use "" for strings, 0 for numerics, [] for items.

DO NOT classify expense categories or tax deductibility. A separate reasoning model handles that. Leave those fields out.

Return ONLY valid JSON, no markdown fences, matching exactly this shape:
{
  "vendor_name": "string",
  "vendor_address": "string",
  "vendor_contact": "string",
  "date": "YYYY-MM-DD",
  "items": [
    {"name": "string", "quantity": number, "price": number}
  ],
  "total_amount": number,
  "service_charge": number,
  "vat_tax": number,
  "sscl_tax": number,
  "vat_registration_number": "string"
}
"""

STRICT_RETRY_SUFFIX = """

IMPORTANT: Your previous response was rejected because it did not match the
required JSON shape. Return ONLY a single valid JSON object with EXACTLY the
keys shown above — no explanations, no markdown fences, no extra fields.
"""


class GLMOCRError(Exception):
    """Raised when the GLM-OCR call fails irrecoverably."""


def _image_to_data_url(image: Image.Image) -> str:
    buf = io.BytesIO()
    img = image.convert("RGB") if image.mode not in ("RGB", "L") else image
    img.save(buf, format="JPEG", quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _build_payload(model: str, image_data_url: str, prompt: str) -> Dict[str, Any]:
    return {
        "model": model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
    }


def _parse_json_loose(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        return None


def _validate_with_pydantic(parsed: dict) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Run the StageARawReceipt Pydantic validator on the parsed JSON. Returns
    (validated_dict, None) on success, or (None, error_message) on failure.
    """
    if not isinstance(parsed, dict):
        return None, "response is not a JSON object"
    try:
        model = StageARawReceipt.model_validate(parsed)
        return model.model_dump(), None
    except Exception as e:
        return None, str(e)


def _call_once(model: str, image_data_url: str, prompt: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """Single Z.ai call with per-attempt retry + transient error handling."""
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            t0 = time.time()
            logger.info(f"GLM-OCR call: model={model} attempt={attempt}")
            resp = requests.post(
                ZAI_CHAT_ENDPOINT,
                headers=headers,
                json=_build_payload(model, image_data_url, prompt),
                timeout=REQUEST_TIMEOUT_S,
            )
            elapsed_ms = int((time.time() - t0) * 1000)

            if resp.status_code == 401:
                raise GLMOCRError("ZHIPU_API_KEY rejected (401)")
            if resp.status_code == 404:
                raise GLMOCRError(f"model {model} not found")
            if resp.status_code in (429, 500, 502, 503, 504):
                backoff = min(2 ** attempt, 8)
                logger.warning(
                    f"GLM-OCR transient {resp.status_code}, retrying in {backoff}s"
                )
                last_error = GLMOCRError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                time.sleep(backoff)
                continue

            resp.raise_for_status()
            body = resp.json()
            content = (
                body.get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            parsed = _parse_json_loose(content if isinstance(content, str) else json.dumps(content))
            if parsed is None:
                raise GLMOCRError("GLM-OCR returned non-JSON content")
            parsed["_model_used"] = model
            parsed["_elapsed_ms"] = elapsed_ms
            return parsed

        except requests.Timeout as e:
            logger.warning(f"GLM-OCR timeout (attempt {attempt}): {e}")
            last_error = e
            continue
        except requests.RequestException as e:
            logger.warning(f"GLM-OCR network error (attempt {attempt}): {e}")
            last_error = e
            time.sleep(min(2 ** attempt, 8))
            continue
        except GLMOCRError:
            raise

    raise GLMOCRError(f"GLM-OCR failed after retries on {model}: {last_error}")


def _extract_inner(image: Image.Image) -> Dict[str, Any]:
    api_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not api_key:
        raise GLMOCRError("ZHIPU_API_KEY is not configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    image_data_url = _image_to_data_url(image)
    last_error: Optional[Exception] = None

    for model in GLM_OCR_MODELS:
        try:
            parsed = _call_once(model, image_data_url, EXTRACTION_PROMPT, headers)
        except GLMOCRError as e:
            msg = str(e)
            if "model" in msg and "not found" in msg:
                logger.warning(f"GLM model {model} unavailable, trying next")
                last_error = e
                continue
            raise

        meta = {
            "_model_used": parsed.get("_model_used", model),
            "_elapsed_ms": parsed.get("_elapsed_ms", 0),
        }
        validated, shape_error = _validate_with_pydantic(parsed)
        if validated is not None:
            validated.update(meta)
            logger.info(
                f"GLM-OCR ok: model={model} vendor={validated.get('vendor_name')!r} "
                f"items={len(validated.get('items', []))} total={validated.get('total_amount')}"
            )
            return validated

        logger.warning(
            f"GLM-OCR Pydantic validation failed ({shape_error}); "
            f"retrying once with strict prompt"
        )
        try:
            parsed = _call_once(
                model, image_data_url, EXTRACTION_PROMPT + STRICT_RETRY_SUFFIX, headers
            )
        except GLMOCRError as e:
            last_error = e
            continue

        meta = {
            "_model_used": parsed.get("_model_used", model),
            "_elapsed_ms": parsed.get("_elapsed_ms", 0),
        }
        validated, shape_error = _validate_with_pydantic(parsed)
        if validated is not None:
            validated.update(meta)
            logger.info(f"GLM-OCR ok after strict retry: model={model}")
            return validated
        last_error = GLMOCRError(f"Pydantic validation still failed: {shape_error}")
        continue

    raise GLMOCRError(f"GLM-OCR failed across all models: {last_error}")


def extract_receipt_fields(image: Image.Image) -> Dict[str, Any]:
    """
    Call GLM-OCR (Z.ai) on the given receipt image and return the raw
    extracted fields. Wrapped with circuit-breaker protection so persistent
    Z.ai outages do not cascade into per-request waits.
    """
    return glm_circuit_breaker.call(_extract_inner, image)
