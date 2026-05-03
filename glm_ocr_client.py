"""
GLM-OCR client for Z.ai (Zhipu) vision-based receipt extraction.

Stage A of the two-stage OCR pipeline. Sends a receipt image to Z.ai's
OpenAI-compatible chat-completions endpoint and asks for the receipt's
factual fields (vendor, date, items, totals, taxes) as JSON.

Tax-deductibility / IFRS category reasoning is intentionally NOT done here —
that runs in Stage B (gemini_reasoner.py) on a stronger reasoning model.

Pricing reference: GLM-OCR is ~$0.03 per 1M tokens (input & output) and
beats Gemini-3-Pro on receipt benchmarks (94.5 vs ~84 OmniDocBench V1.5).
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


EXTRACTION_PROMPT = """You are a receipt OCR engine. Extract the factual contents of this receipt or bank-transfer screenshot.

DOCUMENT HANDLING:
- Detect document type: physical receipt vs bank transfer screenshot.
- Bank transfers: use the Beneficiary/Recipient/Payee as vendor_name; create a single line item describing the transfer purpose.
- Multilingual: convert all extracted text to English/Latin script. Dates as YYYY-MM-DD.
- Numbers: parse any format (1,234.56 or 1.234,56) as plain decimals.
- Missing values: use "" for strings, 0 for numerics, [] for items.

DO NOT classify expense categories or tax deductibility. A separate reasoning model will do that. Leave those fields empty / 0 / false.

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


class GLMOCRError(Exception):
    """Raised when the GLM-OCR call fails irrecoverably."""


def _image_to_data_url(image: Image.Image) -> str:
    """Encode a PIL image as a base64 data URL suitable for the Z.ai API."""
    buf = io.BytesIO()
    fmt = "JPEG"
    img = image.convert("RGB") if image.mode not in ("RGB", "L") else image
    img.save(buf, format=fmt, quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _build_payload(model: str, image_data_url: str) -> Dict[str, Any]:
    return {
        "model": model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
    }


def _parse_json_loose(text: str) -> Optional[dict]:
    """Best-effort JSON parse: strips markdown fences if the model added them."""
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
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        return None


def extract_receipt_fields(image: Image.Image) -> Dict[str, Any]:
    """
    Call GLM-OCR (Z.ai) on the given receipt image and return the raw extracted
    fields. Does NOT populate tax-deductibility or expense category — Stage B
    handles that.

    Args:
        image: PIL Image of the receipt.

    Returns:
        Dict with keys: vendor_name, vendor_address, vendor_contact, date,
        items (list of {name, quantity, price}), total_amount, service_charge,
        vat_tax, sscl_tax, vat_registration_number, _model_used.

    Raises:
        GLMOCRError: if the API call fails or returns unparseable output after
        all retries and model fallbacks.
    """
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
        payload = _build_payload(model, image_data_url)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                t0 = time.time()
                logger.info(f"GLM-OCR call: model={model} attempt={attempt}")
                resp = requests.post(
                    ZAI_CHAT_ENDPOINT,
                    headers=headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT_S,
                )
                elapsed_ms = int((time.time() - t0) * 1000)

                if resp.status_code == 401:
                    raise GLMOCRError("ZHIPU_API_KEY rejected (401)")

                if resp.status_code == 404:
                    logger.warning(f"GLM-OCR model {model} not available (404), trying next")
                    last_error = GLMOCRError(f"model {model} not found")
                    break

                if resp.status_code in (429, 500, 502, 503, 504):
                    backoff = min(2 ** attempt, 8)
                    logger.warning(
                        f"GLM-OCR transient {resp.status_code}, retrying in {backoff}s"
                    )
                    last_error = GLMOCRError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    time.sleep(backoff)
                    continue

                resp.raise_for_status()
                body = resp.json()

                content = (
                    body.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                logger.debug(f"GLM-OCR raw content (first 300): {str(content)[:300]}")

                parsed = _parse_json_loose(content if isinstance(content, str) else json.dumps(content))
                if not parsed:
                    raise GLMOCRError("GLM-OCR returned non-JSON content")

                parsed.setdefault("vendor_name", "")
                parsed.setdefault("vendor_address", "")
                parsed.setdefault("vendor_contact", "")
                parsed.setdefault("date", "")
                parsed.setdefault("items", [])
                parsed.setdefault("total_amount", 0)
                parsed.setdefault("service_charge", 0)
                parsed.setdefault("vat_tax", 0)
                parsed.setdefault("sscl_tax", 0)
                parsed.setdefault("vat_registration_number", "")
                parsed["_model_used"] = model
                parsed["_elapsed_ms"] = elapsed_ms

                logger.info(
                    f"GLM-OCR ok: model={model} vendor={parsed.get('vendor_name')!r} "
                    f"items={len(parsed.get('items', []))} total={parsed.get('total_amount')} "
                    f"elapsed={elapsed_ms}ms"
                )
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
            except Exception as e:
                logger.exception(f"GLM-OCR unexpected error (attempt {attempt}): {e}")
                last_error = e
                continue

    raise GLMOCRError(f"GLM-OCR failed after all retries / models: {last_error}")
