"""
Stage B reasoner: takes already-extracted receipt fields (from any Stage A
OCR provider) and assigns Sri Lankan Inland Revenue Act 2017 tax
deductibility plus an IFRS expense category.

Output shape matches the `Receipt` Pydantic schema so downstream storage,
reporting, and audit logging are unchanged. Falls back to the local rules
engine in `sri_lanka_tax_rules.py` if the Gemini call fails so a Stage B
outage never blocks a receipt save.
"""

import os
import json
import logging
import random
import time
from typing import Dict, Any, List

import google.generativeai as genai

from receipt_schema import Receipt
from gemini_error_handler import (
    GeminiErrorLogger,
    gemini_circuit_breaker,
)

logger = logging.getLogger(__name__)

# Reliability budget for the Stage B reasoner. Stage B is text-only and
# small (a few KB in / out), so we use a tighter budget than the OCR path.
STAGE_B_GLOBAL_TIMEOUT_S = float(os.environ.get("REASONER_GLOBAL_TIMEOUT", "60"))
STAGE_B_REQUEST_TIMEOUT_S = float(os.environ.get("REASONER_REQUEST_TIMEOUT", "30"))
STAGE_B_MAX_RETRIES = int(os.environ.get("REASONER_MAX_RETRIES", "4"))

REASONER_MODELS = [
    os.environ.get("REASONER_MODEL", "gemini-3-flash-preview"),
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


REASONING_PROMPT = """You are a Sri Lankan tax classification expert.

You will be given the already-extracted contents of a single receipt or bank
transfer (vendor, date, line items, totals). Your job is to:

1. Assign each line item the correct tax-deductibility classification under
   the Sri Lankan Inland Revenue Act No. 24 of 2017 (with amendments up to
   Act No. 2 of 2025).
2. Assign the receipt as a whole an IFRS expense_major_category and
   expense_minor_category from the allowed enums.
3. Echo the factual fields back unchanged.

TAX DEDUCTIBILITY RULES:

FULLY DEDUCTIBLE (100%):
- Operating expenses: salaries, rent, utilities, office supplies, maintenance (Section 25)
- Professional services: legal, accounting, consulting fees (Section 25)
- Marketing & advertising: ALL marketing costs even if capital in nature (Section 26A)
- Research & Development: business upgrading, innovation, product development (Section 26)
- Business travel: flights, hotels, transport for business purposes (Section 25)
- Software & SaaS: subscriptions, cloud services, business software (Section 25)
- Training: employee education, workshops, certifications (Section 25)

PARTIALLY DEDUCTIBLE (50%):
- Meals & entertainment: limited unless directly client-related and documented

NON-DEDUCTIBLE (0%):
- Penalties & fines: all violations and late fees (Section 25(2))
- Personal expenses: gym, beauty, personal clothing, entertainment
- Capital expenditure: equipment, vehicles, property (use depreciation instead)
- Provisions & reserves: only actual bad debts written off are deductible
- Donations: only to IRD-approved charities (max 1/3 income or Rs. 75,000)

For each item set:
- tax_deductible: true if deductibility_percentage > 0
- deductibility_percentage: 0, 50, or 100
- tax_law_reference: cite specific section (e.g., "Section 25 - IRA 2017")
- deduction_notes: brief explanation

Return JSON matching the provided response schema exactly.
"""


def _generate_text_with_retry(
    model,
    prompt: str,
    request_options=None,
    max_retries: int = 4,
    initial_delay: float = 1.0,
    global_timeout: float = 60.0,
):
    """
    Text-only equivalent of `gemini_error_handler.generate_with_retry` for
    Stage B. Applies the same retry/backoff/timeout-budget semantics and
    error categorization as the OCR path so the new provider chain has
    reliability parity with the legacy single-call pipeline.

    Returns the GenerateContentResponse, or None if the model signals
    overload (503) so the caller can fall back to the next model.
    """
    start_time = time.time()
    for attempt in range(max_retries):
        elapsed = time.time() - start_time
        if elapsed >= global_timeout:
            raise TimeoutError(
                f"Stage B global timeout exceeded ({global_timeout}s) after "
                f"{attempt} attempts"
            )
        try:
            if request_options is not None:
                return model.generate_content(prompt, request_options=request_options)
            return model.generate_content(prompt)
        except Exception as e:
            category = GeminiErrorLogger.categorize_error(e)
            error_type = type(e).__name__
            logger.warning(
                f"Stage B reasoner error attempt {attempt + 1}/{max_retries}: "
                f"{error_type} - {category}"
            )
            if category == "RATE_LIMIT":
                if attempt < max_retries - 1:
                    wait = (initial_delay * (2 ** attempt)) + random.uniform(0, 1)
                    remaining = global_timeout - (time.time() - start_time)
                    wait = max(0, min(wait, max(0, remaining - 2)))
                    if wait > 0:
                        time.sleep(wait)
                    continue
                raise
            if category == "MODEL_OVERLOAD":
                # Signal caller to try the next model in the fallback chain.
                return None
            if category == "TIMEOUT":
                raise
            if category == "INVALID_REQUEST":
                raise
            # Unknown / transient server error: limited retries.
            if attempt < min(2, max_retries - 1):
                wait = (initial_delay * (2 ** attempt)) + random.uniform(0, 1)
                time.sleep(wait)
                continue
            raise
    raise Exception(f"Stage B failed after {max_retries} attempts")


def _build_context_block(extracted: Dict[str, Any]) -> str:
    items_lines = []
    for it in extracted.get("items") or []:
        items_lines.append(
            f"  - {it.get('name', '')!r} qty={it.get('quantity', 0)} price={it.get('price', 0)}"
        )
    items_text = "\n".join(items_lines) if items_lines else "  (no line items)"

    return (
        "EXTRACTED RECEIPT CONTENTS:\n"
        f"  vendor_name: {extracted.get('vendor_name', '')!r}\n"
        f"  vendor_address: {extracted.get('vendor_address', '')!r}\n"
        f"  vendor_contact: {extracted.get('vendor_contact', '')!r}\n"
        f"  date: {extracted.get('date', '')!r}\n"
        f"  vat_registration_number: {extracted.get('vat_registration_number', '')!r}\n"
        f"  total_amount: {extracted.get('total_amount', 0)}\n"
        f"  service_charge: {extracted.get('service_charge', 0)}\n"
        f"  vat_tax: {extracted.get('vat_tax', 0)}\n"
        f"  sscl_tax: {extracted.get('sscl_tax', 0)}\n"
        f"  items:\n{items_text}\n"
    )


def _rule_based_fallback(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Use the existing local rules engine when the reasoner is unavailable."""
    from sri_lanka_tax_rules import get_classifier

    classifier = get_classifier()
    enriched_items: List[Dict[str, Any]] = []

    receipt_dict = {
        "vendor_name": extracted.get("vendor_name", ""),
        "expense_major_category": "",
        "expense_minor_category": "",
    }

    for it in extracted.get("items") or []:
        cls = classifier.classify_item(
            item_name=it.get("name", ""),
            item_price=it.get("price", 0) or 0,
            vendor_name=extracted.get("vendor_name", ""),
            major_category="",
            minor_category="",
        )
        enriched_items.append(
            {
                "name": it.get("name", ""),
                "quantity": float(it.get("quantity", 0) or 0),
                "price": float(it.get("price", 0) or 0),
                "tax_deductible": bool(cls["tax_deductible"]),
                "deductibility_percentage": float(cls["deductibility_percentage"]),
                "tax_law_reference": cls["tax_law_reference"],
                "deduction_notes": cls["deduction_notes"],
            }
        )

    return {
        "vendor_name": extracted.get("vendor_name", "") or "",
        "vendor_address": extracted.get("vendor_address", "") or "",
        "vendor_contact": extracted.get("vendor_contact", "") or "",
        "date": extracted.get("date", "") or "",
        "items": enriched_items,
        "total_amount": float(extracted.get("total_amount", 0) or 0),
        "service_charge": float(extracted.get("service_charge", 0) or 0),
        "vat_tax": float(extracted.get("vat_tax", 0) or 0),
        "sscl_tax": float(extracted.get("sscl_tax", 0) or 0),
        "vat_registration_number": extracted.get("vat_registration_number", "") or "",
        "expense_major_category": "Operating Expenses",
        "expense_minor_category": "Administrative and General",
        "_reasoner_model": "rule-based-fallback",
    }


def reason_receipt(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run Stage B reasoning over already-extracted receipt fields.

    Args:
        extracted: dict from a Stage A OCR provider (e.g. glm_ocr_client).

    Returns:
        Dict matching the `Receipt` Pydantic schema, with `_reasoner_model`
        annotated for telemetry. Never raises — falls back to the local
        rules engine on any error.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.warning("GEMINI_API_KEY missing — using rule-based fallback for Stage B")
        return _rule_based_fallback(extracted)

    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        logger.warning(f"Could not configure Gemini for Stage B: {e}")
        return _rule_based_fallback(extracted)

    generation_config = {
        "response_mime_type": "application/json",
        "response_schema": Receipt,
        "temperature": 0.1,
    }

    prompt = REASONING_PROMPT + "\n\n" + _build_context_block(extracted)

    try:
        from google.generativeai.types import RequestOptions
        request_options = RequestOptions(timeout=STAGE_B_REQUEST_TIMEOUT_S)
    except Exception:
        request_options = None

    last_err: Exception | None = None
    for model_name in REASONER_MODELS:
        try:
            logger.info(f"Stage B reasoner: trying model {model_name}")
            model = genai.GenerativeModel(
                model_name, generation_config=generation_config
            )

            def _call() -> Any:
                return _generate_text_with_retry(
                    model=model,
                    prompt=prompt,
                    request_options=request_options,
                    max_retries=STAGE_B_MAX_RETRIES,
                    global_timeout=STAGE_B_GLOBAL_TIMEOUT_S,
                )

            response = gemini_circuit_breaker.call(_call)

            if response is None:
                logger.warning(
                    f"Stage B model {model_name} signaled overload; trying next model"
                )
                last_err = Exception(f"{model_name} overloaded")
                continue

            text = getattr(response, "text", None)
            if not text:
                cand = (response.candidates or [None])[0]
                if cand and getattr(cand, "content", None) and cand.content.parts:
                    text = cand.content.parts[0].text

            if not text:
                raise ValueError("empty response from reasoner")

            data = json.loads(text)

            try:
                validated = Receipt.model_validate(data).model_dump()
            except Exception as ve:
                logger.warning(
                    f"Stage B output from {model_name} failed Receipt schema "
                    f"validation: {ve}; trying next reasoner model"
                )
                last_err = ve
                continue

            validated["_reasoner_model"] = model_name
            logger.info(
                f"Stage B ok: model={model_name} items={len(validated.get('items', []))}"
            )
            return validated

        except Exception as e:
            logger.warning(f"Stage B model {model_name} failed: {e}")
            last_err = e
            continue

    logger.error(f"All Stage B reasoner models failed ({last_err}) — falling back to rules")
    return _rule_based_fallback(extracted)
