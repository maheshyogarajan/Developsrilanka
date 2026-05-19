# fiesta.delivery_ops.autoreply — Commaut2.0 Inbound → F-code Template Auto-Reply (Tier 1 gated)

**Wave 2c port** of the Lanka.tax inbound-email routing + F-code template auto-reply pattern into FIESTA. Built from CEO direct ask 2026-05-19 (Telegram 3744/3746).

**Branch:** `wave2c/autoreply` (NOT merged)
**Files:**
- `fiesta/delivery_ops/autoreply.py` — module
- `fiesta/delivery_ops/tests/test_autoreply.py` — pytest suite (19 tests, all green)

---

## What it does

1. **Reads** the inbound email body from `Case.Incoming_email__c` (+ AI summary from `Case.Incoming_Email_Summary__c`). Both fields written by **Commaut2.0** outside SF flow metadata.
2. **Builds a per-client case file** (Step 3c, MANDATORY). Uses `client_comm_preflight.check_active_engagement()` from CEO-OS when reachable; degrades to a documented best-effort path with caller warning when unreachable.
3. **Step 3e active-engagement guard:** if another staff-in-progress Email Case exists for the same Contact in the last 14 days → `defer_to_staff=True`, no draft produced.
4. **Classifies** the inbound against the F-code catalog at `lanka.tax/evidence/W2D12_action_catalog.json` (55 entries). Matching combines subject + body keyword scoring with precondition boosts (payment status, profile completion, etc.). Conflict resolution uses the dependency-chain precedence (`Compliance > Payment > Registration > Critical Docs > Routing > Staff Follow-up`).
5. **Renders** the F-code template with full-name greeting + subject, deadline-status sentence merge field, CEO signature block, and `lanka.tax/login` link. **Refuses to render** if any mandatory element is missing.
6. **NEVER auto-sends.** Tier 1 approval only — `submit_for_tier1_approval()` writes the `approval_queue` row + `Resolver_Change__c` (status=`pending`) + Telegram CEO message with the full draft. The actual send is a **separate handler** (see "Handoff point" below).

---

## Public API

```python
from fiesta.delivery_ops.autoreply import classify_and_draft, submit_for_tier1_approval

# Step 1: classify + draft (no writes)
draft = classify_and_draft(
    case_id="500fake0000caseAAAA",
    dry_run=True,
    # Optional dependency injection (tests / non-CEO-OS environments):
    sf_client=...,         # default: fiesta.integrations.sf_auth.SFRestClient
    preflight_fn=...,      # default: client_comm_preflight.check_active_engagement
    catalog_path=...,      # default: G:/.../W2D12_action_catalog.json
    vip_email_set=...,     # default: empty fallback (load from Supabase in prod)
)

# Step 2: if draft.ok, submit for Tier 1 CEO approval
if draft["ok"]:
    submission = submit_for_tier1_approval(
        case_id="500fake0000caseAAAA",
        draft=draft,
        dry_run=False,
        sf_client=...,
        supabase_writer=...,   # default: Supabase REST POST
        telegram_sender=...,   # default: Telegram Bot API
    )
```

### `classify_and_draft()` return shape

| Key | Type | Notes |
|---|---|---|
| `ok` | bool | True iff a draft was rendered and all gates passed |
| `case_id` | str | Echoed |
| `customer_id` | str / None | From Case → Customer__c |
| `contact_id` | str / None | From Case.ContactId |
| `tax_file_id` | str / None | Current-year Tax_File__c |
| `client_name` | str / None | Full name from Customer__r.Full_Name_of_Applicant_English__c |
| `inbound_subject_received` | str | Verbatim Case.Subject |
| `inbound_body_chars` | int | Length of Incoming_email__c |
| `classified_f_code` | str / None | e.g. `"F3.0"`; None when no match or ambiguous |
| `classified_action_name` | str / None | e.g. `"missing_documents_composite"` |
| `draft_subject`, `draft_body` | str / None | Rendered template (None when render refused) |
| `gates_passed` | list[str] | e.g. `["client_full_name_in_greeting", "ceo_signature_block", ...]` |
| `gates_failed` | list[str] | Mandatory elements missing — render refused if non-empty |
| `defer_to_staff` | bool | Step 3e active-engagement guard fired |
| `cooldown_active` | bool | Same F-code template sent within cooldown window |
| `vip_override` | bool | VIP contact — force human review |
| `approval_required` | bool | Always `True` for any draft (Tier 1) |
| `sent` | None | **Always None** — this module NEVER sends |
| `reasoning`, `candidates`, `errors`, `warnings` | various | Diagnostics |

### `submit_for_tier1_approval()` return shape

| Key | Type | Notes |
|---|---|---|
| `ok` | bool | True iff RC row created AND (approval_queue row OR Telegram msg) succeeded |
| `approval_queue_row_id` | str / None | Supabase row id |
| `resolver_change_id` | str / None | SF Resolver_Change__c id (created FIRST per Rule P1) |
| `telegram_msg_id` | int / None | Telegram message id sent to CEO chat 1813046950 |
| `dry_run` | bool | Echoed |
| `would_write` | dict | All three would-write payloads (always populated, even in dry_run) |
| `errors` | list[str] | |

---

## F-code coverage

The classifier matches against all 55 actions in the canonical `action_catalog.json`. Of those, the keyword-driven classifier directly covers **21 F-codes** with curated subject+body keywords (`F_CODE_KEYWORDS` in `autoreply.py`):

| F-code | Trigger | F-code | Trigger |
|---|---|---|---|
| F0.1 | welcome, signed up, package | F3.0 | documents, docs, t10, what to upload |
| F0.3 | are you still, haven't heard | F-AL-REQUEST | asset, liability, a&l |
| F1.1 | tin, no tin | F4 | payment, invoice, how much, fee |
| F1.4 | pin, ird pin | F5 / F5.2 | confirm, computation, calculation |
| F1.6 | pin issue, pin failed, pin reset | F6 | a&l declaration, final step |
| F1.8 | tax type, iit | F6.1 | confirm, haven't confirmed |
| F2.1 | profile, financial checklist | F8.1 | not filed, filing stuck |
| F-RESCUE-001 | forgot, lost, haven't filled | F8.2 / F8.3 | ird timeout, ird 401 |
| F-W2-LOCAL | deadline, due date | F-W2-FOREIGN | foreign, abroad |
| F-W3-RENEWAL | last year, renew | | |

Remaining catalog actions (F0.2 monitor, F1.2/F1.3/F1.5 status flips, F2.x field updates, F3.x.* per-counterparty reminders, F4.x routing, F5.1 monitor, FC.x composites, F-W2-* nudges not in keyword list, F-26-FI.* proactive 26/27, F-W4 estimator, retired F-W2-EXPENSE) **fall through to `classified_f_code=None`** when keywords don't match — the safe default. They need either:
  - More keyword coverage in `F_CODE_KEYWORDS` (v1.1), OR
  - An upstream upstream resolver (i.e. the inbound classifier doesn't need to fire them — they're driven by client state machines, not inbound emails)

---

## Gates (in order of check)

1. **Step 3c case file** — case file build must succeed and produce at least one of `inbound_body` or `inbound_summary`. Otherwise error, no draft.
2. **Step 3e active engagement** — if `preflight_fn` returns `defer_to_staff=True`, abort with `defer_to_staff=True` in result.
3. **VIP override** — if `client_email.lower()` is in `vip_email_set`, abort with `vip_override=True`.
4. **Classification** — score-based, ambiguity-aware. Tied top scores resolved by `STAGE_PRIORITY`; same-tier ties bail to no-match (human classification).
5. **Cooldown** — `Document_Follow_up_Reminder__c` query within the F-code's `cooldown_days` window. If a prior reminder exists, abort with `cooldown_active=True`.
6. **Render mandatory-element gates** — `client_full_name_in_greeting`, `client_full_name_in_subject`, `lanka_tax_login_link`, `ceo_signature_block`. Any failure → render refused, no draft.
7. **Tier 1 approval** — built into the API surface. There is NO `dry_run=False` path that auto-sends. The only way an email goes out is the CEO Y-reply handler (next section).

---

## Tier 1 approval flow

```
[client sends email]
        │
        ▼
[Commaut2.0 writes Case.Incoming_email__c]
        │
        ▼
[autoreply.classify_and_draft(case_id)]   ← reads SF, no writes
        │
   draft.ok = True
        │
        ▼
[autoreply.submit_for_tier1_approval(case_id, draft)]   ← writes:
        │   1. Resolver_Change__c (Status='pending', Reversible=false)  [Rule P1, FIRST]
        │   2. Supabase approval_queue row (status='pending', priority=2)
        │   3. Telegram CEO chat 1813046950 with full draft + "Reply Y/N/edit"
        ▼
[CEO replies Y on Telegram]
        │
        ▼
*** SEPARATE HANDLER (NOT in this module — see Handoff section) ***
   - Reads pending approval_queue rows
   - On Y: pulls draft from the row's sf_value, calls CeoOsSendEmail Apex REST
     (per memory/reference_sf_email_send.md), updates Resolver_Change__c
     Status='executed', sets approval_queue.status='approved_and_sent'
   - On N: marks both rows 'cancelled_by_ceo'
   - On 'edit': prompts CEO for edited body, re-submits
```

---

## Handoff point — what the CEO Y-reply handler needs to do (NOT built)

The CEO Y-reply handler is a **separate module** for the next wave. It must:

1. **Listen for CEO Telegram replies** on chat 1813046950. The Y/N/edit pattern is per `feedback_explicit_approval_for_client_emails.md` — only execute on explicit YES / send / approved, NEVER on bare "ok" / "done" / "ready".
2. **Match the reply to a pending approval_queue row** (by recency or by quote-reply linkage). Supabase REST: `GET /rest/v1/approval_queue?status=eq.pending&action_type=eq.email_send&order=created_at.desc&limit=10`.
3. **Pull the draft payload** from the matched row's `sf_value` (JSON-encoded `{subject, body, to_email, f_code}`). For `to_email`, query SF Contact at send time (don't cache from minutes ago — VIP / opt-out / bounce status can shift).
4. **Re-run the kill-switch check** before sending. If `HALT` or `kill_switch_check.is_active("client_send")`, abort and update the row `status='blocked_by_kill_switch'`.
5. **Call `CeoOsSendEmail` Apex REST** (POST `/services/apexrest/ceo-os/sendEmail`). Per `memory/reference_sf_email_send.md`, this sends AND logs to Contact Activity in one call — never use `emailSimple`.
6. **Update both rows on success:**
   - SF `Resolver_Change__c`: `Status__c='executed'`, set `Sent_At__c` (per `reference_resolver_action_extensions_v2.md`).
   - Supabase `approval_queue`: `status='approved_and_sent'`, `responded_at=now()`.
7. **On failure** (SF HTTP error, kill switch trip, send rejection): write to `working files/pending_actions/autoreply_send_failed_*.json` per RECOVER-ON-FAILURE rule, alert CEO via Telegram, leave the approval_queue row in `pending_send_retry` for next attempt.
8. **Cooldown register** — on successful send, create a `Document_Follow_up_Reminder__c` row with `Tax_File__c` + `Reminder_Template__c='<f_code template name>'` so the next `classify_and_draft` call respects the cooldown. (Otherwise the next inbound from the same client could re-draft the same F-code immediately.)

**Suggested module name:** `fiesta/delivery_ops/autoreply_sender.py` — pair with this module so the import is `from fiesta.delivery_ops.autoreply import classify_and_draft, submit_for_tier1_approval; from fiesta.delivery_ops.autoreply_sender import send_on_ceo_approval`.

---

## Cooldown rules

Per F-code `cooldown_days` in `action_catalog.json`:

| F-code | Cooldown |
|---|---|
| F0.1 prospect_welcome | 21 days |
| F3.0 doc composite | 7 days |
| F-AL-REQUEST | 7 days |
| F-RESCUE-001 | 21 days |
| F4 payment_due | 5 days |
| F5/F5.2/F6.1 confirm | 7 days |
| F6 al_declaration | 7 days |
| F1.1 tin_required | 14 days |

Cooldown check is performed on `Document_Follow_up_Reminder__c` rows scoped to the client's current Tax_File. **Fail-OPEN** on query failure: a network glitch should not block a legitimate reply — the Tier 1 CEO approval is the ultimate safety net.

---

## VIP override

VIP contacts (from Supabase `system_config.vip_contacts`) bypass classification entirely and route to human review. The current module accepts a `vip_email_set` parameter (lowercase emails); production callers should load the live VIP list at startup. Static fallback is empty by design — failing closed (no VIPs known) means the classifier proceeds normally, which is the conservative default given that VIP detection is a *safety* layer over already-Tier-1-gated drafts.

---

## Returning-client personalization

If the case file shows ≥3 prior outbound emails to the client in the last 365 days, the draft body is rewritten with a "Welcome back" opener that references the prior-year relationship. Implements the returning-client rule from CLAUDE.md Step 3c #13.

---

## Constraints (binding from CLAUDE.md)

- **Step 3c** — `client_comm_preflight` must be reachable OR caller must inject `preflight_fn`. No outbound without a case file.
- **Step 3e** — active-engagement guard mandatory. Staff-in-progress wins; CEO-OS defers.
- **Resolver Rule P1** — every email send proposal writes `Resolver_Change__c` FIRST (status=`pending`), then the approval_queue + Telegram. If RC write fails, abort everything (no email queued without a ledger row).
- **`feedback_client_email_full_name.md`** — full name in BOTH greeting and subject. First-name-only fails the render gate.
- **`feedback_explicit_approval_for_client_emails.md`** — only the CEO Y-reply handler (separate module) is allowed to send. Bare "ok" / "done" / "ready" are NOT acceptance signals.
- **`feedback_never_name_other_clients.md`** — only the current Case's client name appears in their draft. The classifier reads ONLY the target case; no cross-client reference path.
- **CEO signature block** (`reference_ceo_signature_block.md`): Hotline +94 71 314 0000, Mobile +94 71 460 0000, Email tax@lanka.tax. Authoritative. Render gate enforces both phone numbers + email.

---

## Tests

19 tests in `fiesta/delivery_ops/tests/test_autoreply.py`. Run:

```bash
cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
python -m pytest fiesta/delivery_ops/tests/test_autoreply.py -v
```

Coverage:
- **Happy path F3.0** — inbound "what documents do I need" → draft rendered, all gates passed, never sent.
- **Step 3e defer to staff** — active email Case in 14d → defer, no draft.
- **Cooldown active** — prior send within window → blocked, no draft.
- **No match** — random inbound text → no F-code, no draft.
- **Dry-run isolation** — no SF/Supabase/Telegram calls in dry_run.
- **VIP override** — VIP email forces human review.
- **Returning client** — ≥3 prior outbound emails → "Welcome back" personalization.
- **Full-name gate** — single-token name fails render.
- **Payment routing** — "how much do I owe" on unpaid client → F4.
- **Missing inbound body** — empty body and empty summary → error.
- **Tier 1 dry-run** — no writes; would_write payloads populated.
- **Tier 1 live** — RC written FIRST (Rule P1), then approval_queue + Telegram, all three IDs returned.
- **Tier 1 refuses failed draft** — never submits a `draft.ok=False`.
- **Tier 1 refuses case_id mismatch** — defensive guard.
- **Tier 1 RC failure aborts** — Rule P1 compliance: no approval_queue write if ledger fails.
- **Pure helpers** — `_validate_full_name`, `_deadline_status_sentence`, `_norm`.

---

## Known limitations (v1)

1. **Keyword classifier only** — no LLM, no sentiment model. ~21 F-codes have curated keywords; the other ~34 fall through to no-match. v1.1 expands the keyword list; v2 swaps in a small classifier behind the same API.
2. **No WhatsApp branch** — `ks_sn__MessageHistory__c` inbound is out of scope. v1.1.
3. **No SF EmailTemplate fetch** — when an F-code references `sf_template_developer_name` but has no `inline_template_md`, the renderer uses the structured fallback body. v1.1 will fetch the HTML EmailTemplate from `sf_email_templates.json` and unwrap merge fields.
4. **Cooldown is fail-open on query error** — explicit choice (Tier 1 approval is the safety net). If you want fail-closed, override the cooldown function.
5. **VIP list static fallback is empty** — production must load `system_config.vip_contacts` at startup.
6. **No retry on Telegram failure** — Telegram is best-effort; if it fails, the approval_queue row still gives CEO visibility via `/pending`. v1.1 adds retry-with-backoff.
7. **No celery/scheduling integration** — caller must invoke `classify_and_draft` per Case. v1.1 wires it to `celery_app.py` to fire on new Commaut Case creation.

---

## Future work (v1.1+)

- Expand `F_CODE_KEYWORDS` to all 55 catalog entries via real Commaut-traffic sampling.
- Add WhatsApp inbound source from `ks_sn__MessageHistory__c`.
- Wire to `celery_app.py` so new Commaut Cases trigger this module automatically.
- Build the CEO Y-reply handler (`autoreply_sender.py`) — see Handoff section.
- Pull VIP set from Supabase `system_config.vip_contacts` at startup.
- Add structured `_funnel_stage` writes to `Resolver_Action__c` per `reference_resolver_action_extensions_v2.md`.
- Anthropic Haiku classifier behind the same API (Phase B) for ambiguous cases.
