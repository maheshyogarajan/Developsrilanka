# Test Fixtures

## sample_receipt.jpg
A minimal JPEG used as the receipt upload fixture in `07-receipt-ocr.spec.ts`.

**Source:** Copied from `test_image.jpg` in the project root (an existing test image
already used by the Python test suite — confirmed to be a valid JPEG that the
Flask `/scan` endpoint can accept).

**To replace:** Drop any real receipt JPEG here named `sample_receipt.jpg`. The OCR
test only checks that the upload POST doesn't return a 5xx error — it does NOT
assert on the extracted data (Celery / Gemini dependency).
