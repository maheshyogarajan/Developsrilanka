"""
Side-by-side OCR provider accuracy + cost comparison.

For each sampled receipt we:
  1. Re-run Gemini single-call OCR and GLM-OCR (Stage A only) on the stored
     image.
  2. Score each provider's factual extraction against the receipt row in the
     database, treating the saved values as ground truth (these are the
     fields the user kept after any manual correction).
  3. Estimate per-sample token-cost using rough public price points for each
     provider so the savings claim is grounded.

This script measures *OCR extraction quality*. Tax-deductibility / IFRS
classification (Stage B) is intentionally NOT compared here; that is a
text-only reasoning step driven by the same Gemini reasoner regardless of
which Stage A provider ran, so comparing it would not isolate OCR quality.

Usage:
    python compare_ocr_providers.py --limit 10
    python compare_ocr_providers.py --receipt-id 123 --receipt-id 456
"""

import argparse
import logging
import os
import sys
from io import BytesIO
from typing import Optional, Dict, Any

from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("compare_ocr")


# Public list-prices (USD per 1M tokens) — update if the providers change.
COST_PER_1M_TOKENS = {
    "gemini": {"input": 0.30, "output": 2.50},   # Gemini 3 Flash (preview)
    "glm": {"input": 0.03, "output": 0.03},      # GLM-OCR (Z.ai)
    "reasoner": {"input": 0.30, "output": 2.50}, # Stage B Gemini 3 Flash reasoner
}

# Conservative per-call token estimates. Gemini single-call OCR = OCR-only.
# GLM end-to-end = Stage A (vision OCR) + Stage B (text-only reasoning).
EST_TOKENS = {
    "gemini_ocr": {"input": 1500, "output": 600},
    "glm_stage_a": {"input": 1500, "output": 600},
    "reasoner_stage_b": {"input": 800, "output": 600},
}


def _load_image_for_receipt(receipt) -> Optional[Image.Image]:
    """Load a receipt image from S3 (preferred) or the legacy local upload dir."""
    if getattr(receipt, "s3_key", None):
        try:
            from s3_storage import s3_download_file_to_memory
            data = s3_download_file_to_memory(receipt.s3_key)
            if data:
                if isinstance(data, (bytes, bytearray)):
                    return Image.open(BytesIO(data))
                return Image.open(data)
        except Exception as e:
            log.warning(f"Receipt {receipt.id}: S3 fetch failed: {e}")

    candidates = []
    if receipt.image_path:
        candidates.append(receipt.image_path)
        candidates.append(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "static",
                receipt.image_path.lstrip("/"),
            )
        )
        candidates.append(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "static",
                "uploads",
                os.path.basename(receipt.image_path),
            )
        )
    for p in candidates:
        if p and os.path.exists(p):
            try:
                return Image.open(p)
            except Exception as e:
                log.warning(f"Receipt {receipt.id}: open {p} failed: {e}")
    log.warning(f"Receipt {receipt.id}: no usable image source")
    return None


def _extract_ground_truth(receipt) -> Dict[str, Any]:
    """The DB row is the corrected ground truth the user accepted."""
    return {
        "vendor_name": (receipt.vendor_name or "").strip(),
        "date": receipt.date.isoformat() if receipt.date else "",
        "total_amount": float(receipt.total_amount or 0),
        "vat_tax": float(receipt.vat_tax or 0),
        "service_charge": float(receipt.service_charge or 0),
        "vat_registration_number": (receipt.vat_registration_number or "").strip(),
        "items_count": len(receipt.items or []),
    }


def _score_one(predicted: Dict[str, Any], truth: Dict[str, Any]) -> Dict[str, bool]:
    """Per-field accuracy. Numerics use a small tolerance; strings are case-folded."""

    def _str_eq(a, b):
        return (a or "").strip().casefold() == (b or "").strip().casefold()

    def _num_eq(a, b, tol=0.01):
        try:
            return abs(float(a or 0) - float(b or 0)) <= tol * max(1.0, abs(float(b or 0)))
        except (TypeError, ValueError):
            return False

    return {
        "vendor_name": _str_eq(predicted.get("vendor_name"), truth["vendor_name"]),
        "date": _str_eq(predicted.get("date"), truth["date"]),
        "total_amount": _num_eq(predicted.get("total_amount"), truth["total_amount"]),
        "vat_tax": _num_eq(predicted.get("vat_tax"), truth["vat_tax"]),
        "service_charge": _num_eq(predicted.get("service_charge"), truth["service_charge"]),
        "vat_registration_number": _str_eq(
            predicted.get("vat_registration_number"), truth["vat_registration_number"]
        ),
        "items_count": (
            len((predicted.get("items") or [])) == truth["items_count"]
        ),
    }


def _cost_for(rates_key: str, tokens_key: str) -> float:
    rates = COST_PER_1M_TOKENS[rates_key]
    toks = EST_TOKENS[tokens_key]
    return (
        toks["input"] / 1_000_000 * rates["input"]
        + toks["output"] / 1_000_000 * rates["output"]
    )


def _estimate_cost(provider: str) -> float:
    """End-to-end per-call cost for the entire provider chain."""
    if provider == "gemini":
        # Single-call OCR + classification.
        return _cost_for("gemini", "gemini_ocr")
    if provider == "glm":
        # Stage A vision OCR + Stage B Gemini reasoner.
        return _cost_for("glm", "glm_stage_a") + _cost_for("reasoner", "reasoner_stage_b")
    return 0.0


def _run_gemini_ocr(image: Image.Image) -> Optional[Dict[str, Any]]:
    """Gemini single-call OCR (the existing legacy path)."""
    try:
        import app as app_module
        return app_module._process_receipt_with_gemini_legacy(image)
    except Exception as e:
        log.warning(f"Gemini OCR call failed: {e}")
        return None


def _run_glm_ocr(image: Image.Image) -> Optional[Dict[str, Any]]:
    """GLM-OCR Stage A only (no Stage B reasoning, so we isolate OCR quality)."""
    from glm_ocr_client import extract_receipt_fields, GLMOCRError
    try:
        return extract_receipt_fields(image)
    except GLMOCRError as e:
        log.warning(f"GLM-OCR call failed: {e}")
        return None


def run(receipt_ids: list[int]) -> None:
    from app import app
    from models import Receipt

    with app.app_context():
        receipts = Receipt.query.filter(Receipt.id.in_(receipt_ids)).all()
        if not receipts:
            log.error("No receipts matched")
            sys.exit(1)

        totals = {
            "gemini": {"correct": 0, "fields": 0, "cost": 0.0, "calls": 0, "fails": 0},
            "glm": {"correct": 0, "fields": 0, "cost": 0.0, "calls": 0, "fails": 0},
        }

        for r in receipts:
            print(f"\n=== Receipt {r.id} (truth vendor={r.vendor_name!r}) ===")
            img = _load_image_for_receipt(r)
            if img is None:
                continue

            truth = _extract_ground_truth(r)
            results = {
                "gemini": _run_gemini_ocr(img),
                "glm": _run_glm_ocr(img),
            }

            for prov, predicted in results.items():
                totals[prov]["calls"] += 1
                if predicted is None:
                    totals[prov]["fails"] += 1
                    print(f"  [{prov:6s}] FAILED")
                    continue
                scores = _score_one(predicted, truth)
                correct = sum(1 for v in scores.values() if v)
                totals[prov]["correct"] += correct
                totals[prov]["fields"] += len(scores)
                totals[prov]["cost"] += _estimate_cost(prov)
                missed = [k for k, v in scores.items() if not v]
                print(
                    f"  [{prov:6s}] {correct}/{len(scores)} fields correct"
                    + (f"  miss={missed}" if missed else "")
                )

        print("\n--- Summary ---")
        for prov, t in totals.items():
            acc = (100 * t["correct"] / t["fields"]) if t["fields"] else 0.0
            print(
                f"  {prov:6s}  field-accuracy={acc:5.1f}%  "
                f"calls={t['calls']}  failures={t['fails']}  "
                f"est_cost=${t['cost']:.6f}"
            )
        if totals["gemini"]["cost"] > 0:
            ratio = totals["glm"]["cost"] / totals["gemini"]["cost"]
            print(f"\n  GLM cost vs Gemini: {ratio*100:.1f}% (lower is cheaper)")
        print(
            "\nNote: cost is end-to-end per provider chain (Gemini = single OCR call; "
            "GLM = Stage A vision OCR + Stage B Gemini reasoner) using public list "
            "prices and the EST_TOKENS budget at the top of this file. Replace "
            "EST_TOKENS with measured token counts for an exact figure."
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--receipt-id", type=int, action="append", default=[])
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args()

    if args.receipt_id:
        ids = args.receipt_id
    else:
        from app import app
        from models import Receipt

        with app.app_context():
            ids = [
                r.id
                for r in Receipt.query.order_by(Receipt.id.desc()).limit(args.limit).all()
            ]
    run(ids)


if __name__ == "__main__":
    main()
