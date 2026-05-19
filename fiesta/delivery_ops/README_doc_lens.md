# fiesta.delivery_ops.doc_lens — FIESTA document extraction + validation

**Status:** v1.0 shipped 2026-05-19 on branch `wave2b/ocr-doclens-port` (NOT merged).
**Council authority:** council #2 §1 (OCR repo table), Strategist D §4 (extraction pattern + accuracy phase gate). CEO approval Telegram msg 3738.
**Supersedes:** `working files/ocr/t10_extractor.py` (synthetic T10-only v1 in CEO-OS — kept as standalone reference, not deleted).

## What this is

A FIESTA-native port of the doclens-v1 + Commaut2.0/dev + LTAiCore extraction
pattern, restructured around a single API the Subagent E consumer hook calls:

```python
from fiesta.delivery_ops import validate_doc, DocType

result = validate_doc(
    client_id="abc123",
    doc_path="/path/to/upload.pdf",
    expected_doc_type=DocType.T10,    # or None to auto-detect
)
```

## Public API

```python
def validate_doc(
    *,
    client_id: str,
    doc_path: str,
    expected_doc_type: str | DocType | None = None,   # see DocType enum
) -> dict:
    """Returns:
    {
        "ok": bool,                          # True if extraction succeeded structurally
        "client_id": str,                    # echoed back
        "doc_type": str,                     # T10 | BANK_INTEREST_WHT | ... | UNKNOWN
        "confidence": float,                 # 0..1 heuristic — NOT calibrated
        "extracted_fields": dict,            # schema-conformant fields
        "fully_valid": bool,                 # True if doc passes validity gate
        "failure_reason": str | None,        # set when fully_valid=False
        "sf_writes_proposed": list[dict],    # PROVED 18-key writer-attribution
        "extraction_method": str,            # "gemini" | "regex" | "none"
        "text_extraction_layer": str,        # "pdfplumber" | "tesseract" | "hybrid"
        "errors": list[str],
        "raw_text_sample": str,              # first 500 chars (debug aid)
    }
    """
```

CLI form for ad-hoc inspection:

```bash
python -m fiesta.delivery_ops.doc_lens <client_id> <doc_path> [doc_type]
```

## Doc types supported

| Doc type | v1.0 status | Field set | PROVED writer (Commaut2.0/dev field_mapping) |
|---|---|---|---|
| `T10` | LIVE (Gemini + regex fallback) | 9 fields | `T10_received__c` (entry #1) |
| `BANK_INTEREST_WHT` | LIVE (Gemini only — no regex layer) | 6 + per-account interest/WHT/cert lists | `Bank_documents_received__c` (entry #2) |
| `BALANCE_CONFIRMATION` | v1.1 stub | 5 fields | `Bank_documents_received__c` (entry #18) |
| `A_AND_L` | v1.1 stub | 5 fields | `Assets_and_Liabilities_form_received__c` (entry #9) |
| `EMPLOYER_LETTER` | v1.1 stub | 4 fields | **UNPROVED** (no entry in 18-key map) |
| `UNKNOWN` | auto-detect fallthrough | n/a | n/a |

The 18-key `field_mapping` in `Commaut2.0/dev:src/dv_up.py:tik_and_upload()` (SHA d0d5cc7) is the canonical PROVED writer for these Tax_File__c boolean fields. `doc_lens` does NOT write to SF — it returns `sf_writes_proposed` (a list of proposals) that Subagent E consumes.

## Layered extraction pattern (ported from doclens-v1)

1. **`pdfplumber`** — fast text extraction. Most modern doc uploads are text PDFs.
2. **`pytesseract`** — OCR fallback if pdfplumber yields <200 chars (image PDFs, scanned older docs). Pages rasterized via pdfplumber's `to_image` (no poppler dependency on Windows). Skips gracefully if Tesseract binary not installed.
3. **Field extraction:**
   - **PRIMARY:** Gemini-prompt-based, mirroring `doclens-v1` Gemini-2.5-flash schema-validated extraction. Uses `GEMINI_API_KEY` env var. Gracefully falls back if absent.
   - **FALLBACK:** regex extraction (carried over from v1 `t10_extractor.py`). Only T10 has a regex layer in v1.0; bank/A&L are Gemini-only.
4. **Pydantic schema validation:** per-doc-type schemas in `fiesta/delivery_ops/schemas/`. The `is_fully_valid()` gate per schema decides whether the doc is consumable for downstream computation.

## Prompts: NOT copied from doclens-v1 (per council #2 §5.1 mitigation)

The risk identified in council synthesis: doclens-v1's SL-bank-specific prompts are tightly tuned to particular bank statement layouts and would NOT generalize cleanly to T10/A&L/employer/CSE/insurance docs. Per the council mitigation: we PORT THE PATTERN (few-shot + Pydantic schema + confidence routing), not the SL-bank prompts themselves.

Fresh prompts written in `doc_lens.py` (`_T10_GEMINI_PROMPT`, `_BANK_GEMINI_PROMPT`) preserve the critical alignment warnings from doclens-v1 (e.g. the `total_tax_deducted` vs `benefits_excluded_for_tax` misalignment trap) while restructuring around FIESTA's schema. Future v1.1 expansion will use Third Eye Cases as the few-shot corpus per Strategist D §4.1-4.2.

## Failure routing (carried from LTAiCore + doclens-v1 case_creation)

When `ok=False` or `fully_valid=False`, the consumer (Subagent E) is expected to create a SF Case mirroring the doclens-v1 `case_creation.py` pattern. `doc_lens` itself does NOT create cases — that's Subagent E's responsibility, downstream of this module.

Returned `failure_reason` is a human-readable string suitable for Case Description.

## Accuracy phase gate (per council #2 §3 + Strategist D §4.4)

Before FIESTA-internal prod activation:

| Metric | Threshold | Status |
|---|---|---|
| `classification_accuracy` (auto-detect doc_type correct) | ≥ 0.95 | NOT YET MEASURED (synthetic samples only) |
| `field_extraction_accuracy` (per-field match against ground truth) | ≥ 0.85 | NOT YET MEASURED |

The phase gate runs against the 25/26 Lanka.tax `Scan_result_employement__c` corpus per Strategist D §4.4. Until thresholds are met, `doc_lens` ships in shadow mode only: outputs logged, no client-visible computation feed.

## Honest-uncertainty contract (CLAUDE.md Step 2b)

- `confidence` is a heuristic blend of required-field hit rate (50%) + numeric plausibility (30%) + sanity check (20%). It is **NOT** a calibrated probability.
- Real-doc confidence has **NOT** been validated as of v1.0 — synthetic samples only. Treat extracted fields as `hypothesis` (per CLAUDE.md Step 2c) until validation against the 25/26 corpus completes.
- `extraction_method` tells you which layer produced the fields: `gemini` (highest trust if Gemini available) > `regex` (high trust on clean text) > `none` (failure).
- `errors` is always populated on partial failure — surface it to callers, do not silently swallow.

## Confidence threshold guidance

(Pending real-corpus validation; tune after first 50 real T10s.)

| Confidence | Recommended action |
|---|---|
| ≥ 0.9 | Auto-consume as computation input |
| 0.7 – 0.9 | Auto-consume, flag for audit spot-check |
| 0.4 – 0.7 | Create Processing Case for staff review |
| < 0.4 | Treat as failed extraction; manual data entry |

## Source-repo lineage

| Repo | SHA at port time | Pattern extracted | Code copied? |
|---|---|---|---|
| `DataSciLT/doclens-v1` | 352df1d | Gemini schema-validated extraction + per-field alignment warnings + 2-extractor consensus pattern | NO (PATTERN ported, prompts rewritten — see council #2 §5.1) |
| `DataSciLT/Commaut2.0` (dev branch) | d0d5cc7 | 18-key `field_mapping` writer-attribution + idempotency pattern (`tik_and_upload`) | Field map verbatim (factual writer attribution); idempotency contract preserved in `sf_writes_proposed[].idempotent=True` |
| `DataSciLT/LTAiCore` | ce5fe4f | Case-creation-on-failure routing | Pattern documented; concrete case creation deferred to Subagent E consumer |

Local source for all three is available at `working files/lanka_tax_repos_source/{doclens-v1,Commaut2.0,LTAiCore}/`.

## Relationship to v1 standalone (`working files/ocr/t10_extractor.py`)

The CEO-OS standalone `t10_extractor.py` (subagent_ocr, shipped 2026-05-19 earlier) is preserved as a reference implementation. It:

- Handles T10 only
- Uses pdfplumber + Tesseract + regex (no Gemini)
- Returns a flatter dict (no SF writes proposed)
- Lives outside the FIESTA repo (CEO-OS working files)

This v2 module supersedes it for FIESTA consumption. The pattern (layered fallback + confidence scoring + graceful degrade) is the same — v2 adds the doclens-v1 schema layer + Commaut2.0/dev writer attribution + multi-doc-type taxonomy.

## Install

The required Python deps (`pdfplumber`, `pytesseract`, `pydantic`, `reportlab` for tests) are already in the FIESTA environment. Optional: `google-generativeai` for the Gemini layer (gracefully degrades if absent).

For Tesseract OCR fallback (optional, recommended for image PDFs):

- **Windows:** install via UB Mannheim binary (https://github.com/UB-Mannheim/tesseract/wiki). Add `tesseract.exe` install dir to PATH.
- **Linux:** `apt install tesseract-ocr`
- **macOS:** `brew install tesseract`

## Run tests

```bash
cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
python -m pytest fiesta/delivery_ops/tests/test_doc_lens.py -v
```

Tests generate synthetic PDFs via reportlab at session setup. No real client documents required. Tesseract + Gemini layers skip gracefully if unavailable (counted as PASS).

Current test count: **14 passed**.

## Files

- `fiesta/delivery_ops/doc_lens.py` — main module + CLI (~700 LOC including prompts)
- `fiesta/delivery_ops/schemas/t10.py` — T10 Pydantic schema + validity gate
- `fiesta/delivery_ops/schemas/bank_interest_wht.py` — bank interest/WHT Pydantic schema
- `fiesta/delivery_ops/schemas/stubs.py` — v1.1 stub schemas (A&L, BALANCE_CONFIRMATION, EMPLOYER_LETTER)
- `fiesta/delivery_ops/schemas/__init__.py` — schema package + PROVED-attribution doc
- `fiesta/delivery_ops/sample_docs/_generate.py` — synthetic PDF generator (reportlab)
- `fiesta/delivery_ops/tests/test_doc_lens.py` — pytest suite (14 tests)
- `fiesta/delivery_ops/README_doc_lens.md` — this file

## Roadmap

- **v1.0 (this ship)** — T10 + BANK_INTEREST_WHT live; A&L / BALANCE / EMPLOYER_LETTER stubs.
- **Phase gate (next)** — accuracy validation on 25/26 corpus per Strategist D §4.4. Until thresholds met, shadow-only.
- **v1.1 (next session)** — flesh out the 3 stub doc types using Third Eye Cases as few-shot corpus. Add regex fallback for BANK_INTEREST_WHT (currently Gemini-only).
- **v1.2** — multi-account bank statements (one PDF, multiple accounts) — doclens-v1 supports this via per-account Pydantic schema; v1.0 returns single-account extractions only.
- **v2.0** — wire into Subagent E `pcse_executor.py` (the deferred consumer hook documented in `working files/ocr/_pcse_e_consumer_hook.md`). Replace the synthetic-T10 v1 entirely.

## Source / lineage

Patterns ported from:

- `working files/lanka_tax_repos_source/doclens-v1/employment_logic.py`
  (Gemini extractor pattern + non-split-year T10 schema)
- `working files/lanka_tax_repos_source/doclens-v1/scan_bank.py`
  (Bank interest + WHT schema with per-account granularity)
- `working files/lanka_tax_repos_source/doclens-v1/identify.py`
  (Doc-type classification via Gemini with structured output)
- `working files/lanka_tax_repos_source/doclens-v1/case_creation.py`
  (Failure-routing pattern — case creation on extraction failure)
- `working files/lanka_tax_repos_source/Commaut2.0/src/dv_up.py`
  (18-key `field_mapping` + `tik_and_upload()` idempotent SF write pattern, dev branch)
- `working files/lanka_tax_repos_source/LTAiCore/`
  (Failure-Case-creation host pattern — documented, consumed by Subagent E)
- `working files/ocr/t10_extractor.py`
  (v1 reference — layered fallback + confidence scoring approach carried forward)
- `memory/lanka_tax/reference_third_eye_ai_scanner.md`
  (975-Case ground truth + 10 case-creation paths)
- `memory/lanka_tax/reference_sf_doc_collection_writers.md`
  (PROVED-writer attribution catalog)
