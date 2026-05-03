"""
Side-by-side OCR provider accuracy & cost comparison.

Re-runs both Gemini and GLM-OCR against a sample of stored receipt images and
prints a field-level diff plus rough cost estimate. Use this before flipping
the OCR_PROVIDER default for an organization.

Usage:
    # Compare against the most recent N receipts that still have an image
    python compare_ocr_providers.py --limit 10

    # Compare a specific receipt id
    python compare_ocr_providers.py --receipt-id 123
"""

import argparse
import logging
import os
import sys
from io import BytesIO
from typing import Optional

from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("compare_ocr")


COMPARE_FIELDS = [
    "vendor_name",
    "date",
    "total_amount",
    "vat_tax",
    "service_charge",
    "vat_registration_number",
    "expense_major_category",
    "expense_minor_category",
]


def _load_image_for_receipt(receipt) -> Optional[Image.Image]:
    """Load a receipt image from S3 (preferred) or the legacy local upload dir."""
    if getattr(receipt, "s3_key", None):
        try:
            from s3_storage import s3_download_file_to_memory
            data = s3_download_file_to_memory(receipt.s3_key)
            if data:
                return Image.open(BytesIO(data))
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


def _diff(a: dict, b: dict) -> list[tuple[str, object, object]]:
    diffs = []
    for f in COMPARE_FIELDS:
        if (a or {}).get(f) != (b or {}).get(f):
            diffs.append((f, (a or {}).get(f), (b or {}).get(f)))
    return diffs


def run(receipt_ids: list[int]) -> None:
    from app import app
    from models import Receipt
    from ocr_providers import process_receipt

    with app.app_context():
        receipts = Receipt.query.filter(Receipt.id.in_(receipt_ids)).all()
        if not receipts:
            log.error("No receipts matched")
            sys.exit(1)

        agree = 0
        total_fields = 0
        differing_fields = 0
        per_provider_models: dict[str, set[str]] = {"gemini": set(), "glm": set()}

        for r in receipts:
            print(f"\n=== Receipt {r.id} (vendor on file: {r.vendor_name!r}) ===")
            img = _load_image_for_receipt(r)
            if img is None:
                continue

            gemini_result = process_receipt(img, provider="gemini")
            glm_result = process_receipt(img, provider="glm")

            if not gemini_result or not glm_result:
                print(
                    f"  ✗ provider failed: gemini={bool(gemini_result)} glm={bool(glm_result)}"
                )
                continue

            per_provider_models["gemini"].add(str(gemini_result.get("extraction_model")))
            per_provider_models["glm"].add(str(glm_result.get("extraction_model")))

            diffs = _diff(gemini_result, glm_result)
            total_fields += len(COMPARE_FIELDS)
            differing_fields += len(diffs)
            if not diffs:
                agree += 1
                print("  ✓ all compared fields agree")
            else:
                for f, gv, lv in diffs:
                    print(f"  ~ {f}: gemini={gv!r}  glm={lv!r}")
            print(
                f"  items: gemini={len(gemini_result.get('items', []))} "
                f"glm={len(glm_result.get('items', []))}"
            )

        print("\n--- Summary ---")
        print(f"Receipts compared: {len(receipts)}")
        print(f"Full-agreement receipts: {agree}/{len(receipts)}")
        if total_fields:
            print(
                f"Field agreement: {total_fields - differing_fields}/{total_fields} "
                f"({100 * (total_fields - differing_fields) / total_fields:.1f}%)"
            )
        print(f"Models seen — gemini: {sorted(per_provider_models['gemini'])}")
        print(f"Models seen — glm:    {sorted(per_provider_models['glm'])}")
        print(
            "\nCost (rough): Gemini 2.5 Flash ~ $0.30/1M in + $2.50/1M out; "
            "GLM-OCR ~ $0.03/1M both. Stage B reasoner adds a small text-only call."
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
