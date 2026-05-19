# fiesta.delivery_ops.automation_runner

**Wave 2b SL adapter.** Inserts a `Processing_task__c` row into the Lanka.tax Salesforce org with the exact dispatcher payload contract; the existing `DataSciLT/IRD-System-hosting` Lambda + Dimuth Docker poller pick it up and execute the matching Playwright automation.

This is the architecturally-correct v1 of automation_runner. It supersedes the v0.1 Python port at `G:/My Drive/CEO OS/working files/automation_runner/runner.py`, which classified PIN states off-platform — the v0.1 module remains as an offline-fallback decision helper and is not deleted.

## API

```python
from fiesta.delivery_ops.automation_runner import invoke_sl_automation

result = invoke_sl_automation(
    customer_id="a0F2w000001abcdEAA",      # 15 or 18 char SF Customer__c.Id
    automation_type="PIN_REQUEST",         # see allowlist below
    dry_run=True,                          # default; live mode requires Phase Gate Y
    trace_id=None,                         # auto "ar-<12hex>" if not supplied
    sf_client=None,                        # DI seam for tests
    phase_gate_reader=None,                # DI seam for tests
)
```

### Return shape

```python
{
    "ok": True,
    "processing_task_id": "a3F2w...xxx" | None,
    "resolver_change_id": "a4F2w...yyy" | None,
    "dry_run": True,
    "would_insert": { ...PT payload... },
    "trace_id": "ar-abc123def456",
    "dispatched_to": "aws_lambda" | "docker_poller" | "docker_poller_unverified",
    "expected_completion_minutes": 5 | 2 | 9 | None,
    "errors": [],
    "actions_taken": ["read_customer:...", "dry_run_no_write"],
}
```

## automation_type allowlist + payload contract

Source: **PCSE Strategist D §3.1** (`working files/_cockpit_fiesta/PCSE_STRATEGIST_D_FIESTA_PARITY_20260519.md`) + **`memory/reference_run_automation_screenflow.md`** §"Worked example" + **`working files/lanka_tax_repos_source/IRD-System-hosting/lambda_function.py`** (the routing values `PinRequest` / `Paymentinfo*` / etc).

| automation_type | Processing_task_type__c | Routing | Typical cycle |
|---|---|---|---|
| `PIN_REQUEST` | `PIN Creation` | AWS Lambda (ECS Fargate `ird-pin-request-worker`) | ~3-5 min |
| `TEMP_PIN_RESET` | `Permanent PIN Activation` | Docker poller (Dimuth host) | ~1 min |
| `LOGIN_CHECK` | `IRD Credential Verification` | Docker poller | ~1-1.5 min |
| `TAX_YEARS_CHECK` | `Tax Filing Requirement Validation` | Docker poller | ~1-2 min |
| `PAYMENT_INFO` | `Tax Payment Validation` | AWS Lambda (ECS Fargate `ird-payment-info-worker`) | ~9 min (1 sample) |
| `DIN_COLLECTION` | `DIN Collection` | Docker poller — **listener may be paused** (no closed runs observed) | UNKNOWN |

### Processing_task__c insert payload

```json
{
  "Subject__c":                    "<PT type> - <Customer Name>",
  "Status__c":                     "Open",
  "Processing_task_type__c":       "<one of the 6 values above>",
  "Client_name__c":                "<Customer__c.Id>",
  "Contact__c":                    "<Customer__c.Contact__c, optional>",
  "Relationship_Manager__c":       "<Customer__c.Assigned_Relationship_Manager__c, optional>",
  "Primary_processsing_person__c": "a17OX00003Cc3mQYAR",
  "Due_date__c":                   "<today+1 ISO date>"
}
```

`Primary_processsing_person__c = a17OX00003Cc3mQYAR` is the system-bot Tax_System_Employee ID — it tells the listener "AI-originated, run automation now" (vs human-Tax_System_Employee IDs which route differently). Source: `memory/reference_tax_system_employee_ids.md`.

All field writes have **PROVED writer attribution** via `working files/knowledge/flow_writer_map.json` (the 6 ALF flows that the `Scr_IRD_automation_handler` ScreenFlow dispatches to — `ALF_to_create_PIN_Task`, `ALF_IRD_credential_verification_task_creation`, `ALF_Permanent_PIN_activation_task_creation`, `ALF_Tax_filing_requirement_validation_task_creation`, `ALF_payment_details_task_creation`, `ALF_DIN_extraction_task_creation`).

## Phase Gate Y guard (live mode only)

`dry_run=False` requires Phase Gate Y to be active. Default reader hits `G:/My Drive/CEO OS/working files/_audit/phase_gate_y_state.json` with rules mirrored from v0.1 runner (only `CEO via Telegram*` stamps honored per B-004; max age 7 days). For FIESTA-only deployments without the CEO-OS filesystem mount, inject a custom `phase_gate_reader` callable returning `(active: bool, reason: str)`.

## Resolver Rule P1 (live mode)

Per CLAUDE.md Resolver P1, every SF write CEO-OS performs creates a `Resolver_Change__c` row **BEFORE** the actual write. This adapter:

1. Creates `Resolver_Change__c` with `Change_Type__c='automation_invoke'`, `Reversible__c=false` (IRD-side effects per D §3.3 cannot be rolled back from SF), `Status__c='pending'`.
2. If the RC insert fails → aborts the PT insert, logs to `pending_actions/automation_runner_<trace_id>.json` per RECOVER-ON-FAILURE.
3. Inserts the `Processing_task__c` row.
4. If the PT insert fails → returns the orphan `resolver_change_id` so the caller can investigate.

## Why SL-only, no jurisdiction abstraction

Per council #2 §3 (2026-05-19): the 6 IRD automations only exist as Lanka.tax SF flows + AWS Lambda containers + Dimuth's Docker host. FIESTA cannot rebuild that pipeline cheaply — the right move is **delegate via this adapter**, not duplicate. AU/ATO has no equivalent automations to invoke yet; an `au_ato_adapter` stub is deferred until ATO equivalents are scoped. PCSE Strategist D §9 calls this "the single most important design call" — the SL adapter is what makes the dual-target FIESTA-plus-Lanka.tax architecture coherent.

## Relationship to v0.1

| Aspect | v0.1 (CEO-OS working files) | v1 (this module) |
|---|---|---|
| Location | `working files/automation_runner/runner.py` | `fiesta/delivery_ops/automation_runner.py` |
| LOC | ~320 | ~310 (module) + ~210 (sf_auth) |
| What it does | Decides "should we propose PIN reset?" locally and applies the SF field write directly | Inserts `Processing_task__c` so the existing IRD-System-hosting pipeline runs the actual IRD Playwright automation |
| When to use | Offline classification / dry-run decision support / batch scanning without invoking IRD | Invoke an IRD automation (PIN creation, credential check, etc.) |
| SF schema touched | `Customer__c.PIN_Valid__c` (single picklist update) | `Processing_task__c` insert (8 fields) |
| Listener dependency | None — pure SF write | Lambda + Docker poller in IRD-System-hosting |
| Side effects | SF picklist transition only | Full IRD interaction (PIN, login, doc pull, etc.) |

## Tests

```bash
cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
python -m pytest fiesta/delivery_ops/tests/ -v
```

34 tests cover: payload shape for all 6 automation types, dry-run no-write contract, Phase Gate Y refusal, Resolver_Change failure abort, PT insert failure logging, customer_id shape validation (15+18 chars, malformed reject), customer-not-found, SF query exceptions, routing-hint metadata, trace_id auto-gen / preserve. All SF interactions are mocked via the `sf_client` DI seam — no live SF calls.

## Pointers

- Council #2 synthesis: `G:/My Drive/CEO OS/working files/_cockpit_fiesta/COUNCIL_SYNTHESIS_REPO_PORTING_20260519.md` (§1 repo table, §3 effort, §9 design call)
- PCSE Strategist D parity doc: `G:/My Drive/CEO OS/working files/_cockpit_fiesta/PCSE_STRATEGIST_D_FIESTA_PARITY_20260519.md` (§3.1 payload contract is the spec)
- IRD ScreenFlow reference: `G:/My Drive/CEO OS/memory/reference_run_automation_screenflow.md`
- v0.1 offline fallback: `G:/My Drive/CEO OS/working files/automation_runner/runner.py`
- SF auth helper: `fiesta/integrations/sf_auth.py`

## Out of scope (per dispatch brief)

- Compliance Brigade `regulatory_pre_check` integration (after both this AND OCR v2 land)
- AU/ATO jurisdiction adapter
- Live-mode rollout (`dry_run=True` stays default)
- Editing `celery_app.py` (Wave 2b telemetry + pcse_deploy own that)
- Modifying `/tmp/ird-system-hosting` clone (read-only study)
