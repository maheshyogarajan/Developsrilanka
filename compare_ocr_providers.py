"""
Side-by-side OCR provider accuracy & cost comparison.

Re-runs both Gemini and GLM-OCR against a sample of stored receipt images and
prints a field-level diff plus rough cost estimate. Use this before flipping
the OCR_PROVIDER default.

Usage:
    # Compare against the most recent N receipts that still have an image on disk
    python compare_ocr_providers.py --limit 10

    # Compare a specific receipt id
    python compare_ocr_providers.py --receipt-id 123
"""

import argparse
import logging
import os
import sys
from io import BytesIO

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


def _load_image_for_receipt(receipt) -> Image.Image | None:
    """Load the raw receipt image from local disk or S3."""
    try:
        from image_processor import storage
    except Exception as e:
        log.error(f"Cannot import storage: {e}")
        return None

    path = receipt.image_path or receipt.image_filename
    if not path:
        log.warning(f"Receipt {receipt.id} has no image path")
        return None
    try:
        data = storage.read_bytes(path) if hasattr(storage, "read_bytes") else None
        if not data and os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
        if not data:
            log.warning(f"Receipt {receipt.id}: image not found at {path}")
            return None
        return Image.open(BytesIO(data))
    except Exception as e:
        log.warning(f"Receipt {receipt.id}: failed to load image: {e}")
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

        for r in receipts:
            print(f"\n=== Receipt {r.id} (vendor on file: {r.vendor_name!r}) ===")
            img = _load_image_for_receipt(r)
            if img is None:
                continue

            os.environ["OCR_PROVIDER"] = "gemini"
            gemini_result = process_receipt(img)
            os.environ["OCR_PROVIDER"] = "glm"
            glm_result = process_receipt(img)

            if not gemini_result or not glm_result:
                print(f"  ✗ provider failed: gemini={bool(gemini_result)} glm={bool(glm_result)}")
                continue

            diffs = _diff(gemini_result, glm_result)
            total_fields += len(COMPARE_FIELDS)
            differing_fields += len(diffs)
            if not diffs:
                agree += 1
                print("  ✓ all compared fields agree")
            else:
                for f, gv, lv in diffs:
                    print(f"  ~ {f}: gemini={gv!r}  glm={lv!r}")
            print(f"  items: gemini={len(gemini_result.get('items', []))} glm={len(glm_result.get('items', []))}")

        print("\n--- Summary ---")
        print(f"Receipts compared: {len(receipts)}")
        print(f"Full-agreement receipts: {agree}/{len(receipts)}")
        if total_fields:
            print(
                f"Field agreement: {total_fields - differing_fields}/{total_fields} "
                f"({100 * (total_fields - differing_fields) / total_fields:.1f}%)"
            )
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
