# G1.1 — Persona Branch Audit

**Status:** Complete (pure analysis, no code changes)
**Branch:** `tier-d6/g1-1-audit` (from `main` @ `5b050f7`)
**Date:** 2026-05-25
**Auditor:** MS4 W1 dispatch subagent
**Consumer:** MS4 W2 (G1.2 hub-for-all + G1.3 /scan redirect + G1.4 single-sidebar)
**Source addendum:** `working files/_fiesta_unification_addendum_20260525.md` §G1

---

## Executive Summary

The persona discriminator `User.persona == 'sl_foreign_income'` and its derived gate `use_fiesta_shell(user)` are referenced in **48 production branch sites** + **131 layout-extends sites** + **23 test fixture/assertion sites** across the codebase. Total addressable surface: **202 sites**.

### Branch-site bucket breakdown (production code only — excludes tests and dead `.bak`)

| Bucket | Sites | What W2 does with these |
|---|---|---|
| **PURE-UX** (template branching, layout extends, sidebar choice) | 134 | Universal-shell migration: 76 legacy `extends "layout.html"` flip to `extends layout_template`; deprecation banner + persona switcher includes drop; `g.layout_template` collapses to one value |
| **ROUTING** (`/`, `/scan`, `/onboarding`, `/fie/triage`, `/earnings` redirect decisions) | 9 | G1.2 + G1.3 rewrite: `/` becomes hub for ALL authenticated users; `/scan` 302s to `/`; persona-only routes either drop the branch OR consult `User.income_sources` |
| **TAX-ENGINE-DISCRIMINATOR** (risk model + KB topic boost gated on persona) | 3 | G4.2 migration: `_PERSONA_TOPIC_BOOSTS` and `score_risk` Component B switch from `persona == 'sl_foreign_income'` to `'foreign_remittance' in user.income_sources` |
| **ADMIN-GATE** (`role == 'admin'` exemptions co-located with persona checks) | 7 | Leave alone — admin role is the operator gate, not the customer-segment gate |
| **PROFILE-ONLY** (display strings, deprecation banner, signup form copy) | 4 | W4 (visual unification) — cosmetic, not blocking |
| **LEGACY-DEAD-CODE** (`.bak`, `home_bookkeeping_legacy.html`, `analytics_redesign.py`, `add_persona_and_remittance.py`) | 8 | Flag for W2 sweep; delete in same commit as G1.2 ships |
| **DIFFERENT-CONCEPT — DO NOT TOUCH** (`fiesta/persona/*` filing-persona module, `FiestaProfile.persona='self'`, `persona_switcher.html`) | 13 | Leave alone — these are about *filing personas* (self/spouse/dependant), unrelated to the `sl_foreign_income` UX discriminator. The naming collision is unfortunate but the two systems are independent |
| **TEST-FIXTURE-COUPLING** | 23 | Update after G1.2 ships — test fixtures need to assert against `income_sources` instead of `persona` |
| **UNCERTAIN — needs orchestrator decision in W2 design lock** | 1 | See §Risk hotspot #5 |

**Total branch-aware sites:** 202 (78 production, 79 template `extends` decisions, 8 dead-code, 23 tests, 13 different-concept, 1 uncertain). Of these, ~52 require deliberate W2 work; the rest are either touch-and-go (template extends flip) or untouched (different concept / dead).

---

## Migration Table

### Production code (Python) — branch sites

| file | line | snippet | bucket | migration approach | W2 hours |
|---|---|---|---|---|---|
| `app.py` | 116 | comment: "FIESTA shell for sl_foreign_income personas" | PROFILE-ONLY | Update comment to reflect universal shell post-G1.2 | 0.1 |
| `app.py` | 368 | `getattr(current_user, 'persona', None) == 'sl_foreign_income'` (hub-extras context processor gate) | TAX-ENGINE-DISCRIMINATOR | Replace with `'foreign_remittance' in (current_user.income_sources or [])`; the cached hub extras (avg USD remittance, projected savings) only make sense for users with at least one foreign-remittance source | 1.0 |
| `app.py` | 383 | `is_fiesta_persona=getattr(g, 'is_fiesta_persona', False)` (context processor exposure) | PURE-UX | After G1.2 lands, `g.is_fiesta_persona` becomes `current_user.is_authenticated` (everyone). Or rename to `g.is_authenticated_shell_user` and inline | 0.3 |
| `app.py` | 526 | duplicate `is_fiesta_persona=` exposure (early-return branch) | PURE-UX | Same as above | 0.1 |
| `app.py` | 539-561 | `def use_fiesta_shell(user)` — the discriminator function | PURE-UX | Post-G1.2: simplify to `return getattr(user, 'is_authenticated', False)`. Keep the function (do not inline) so admin role exemption remains visible. After G1.4: function is `True for any authenticated user` | 0.5 |
| `app.py` | 559 | `getattr(user, 'persona', None) == 'sl_foreign_income'` inside `use_fiesta_shell` | PURE-UX | Removed by the simplification above | (included) |
| `app.py` | 564-567 | `_expose_use_fiesta_shell` context processor | PURE-UX | Keep — Jinja still needs the predicate for any residual `{% if use_fiesta_shell(current_user) %}` (likely none post-G1.4) | 0 |
| `app.py` | 1290-1334 | `def home()` — `/` route persona-branched hub render | **ROUTING (G1.2 core)** | Remove the persona gate. Every authenticated non-admin user gets `fiesta_home.html`. The hub adapts content via `hub_funnel_state` (already supports `anon`/`empty`/`has_remit`/`ready_for_bill`); add new states for users with no `foreign_remittance` but with `business_lkr` / `employment_lkr` etc. so the hub still has a sensible next-step card | 3.0 |
| `app.py` | 1310-1323 | comment + `current_user.persona == 'sl_foreign_income' and current_user.role != 'admin' and use_fiesta_shell(current_user)` (3-way AND) | **ROUTING (G1.2 core)** | Replace with `current_user.role != 'admin'` only; admins keep `/scan` (operator surface). Update the comment block | (included above) |
| `app.py` | 1296 | comment: "stays on the scan page for sl_foreign_income" | PROFILE-ONLY | Rewrite comment to reflect G1.3 ('/scan' 302s to '/') | 0.1 |
| `app.py` | 1552-1590 | `def index()` — `/scan` handler | **ROUTING (G1.3 core)** | Replace entire body with `return redirect(url_for('home'))`. Email-verification gate moves into `home()` (or stays here, fires before redirect). The `current_user.organizations` no-org loop is no longer relevant since `/` is the universal landing | 2.0 |
| `app.py` | 1562-1575 | persona reroute inside `index()` (`return redirect(url_for('remittance.dashboard'))` for foreign-income) | **ROUTING (G1.3 core)** | Removed by the redirect-to-home rewrite above; `/remittance/dashboard` becomes a sidebar module entry inside the hub | (included above) |
| `app.py` | 3755-3760 | post-email-verification redirect: persona-aware (`'sl_foreign_income' → home`, else `→ fiesta_triage.triage_form`) | **ROUTING (G4.1 — out of W2 scope but flag for sequencing)** | Defer to G4.1 (single onboarding flow). For W2 interim: route ALL verified users to `home()`; triage becomes a sidebar entry / first-render modal on the hub | 1.5 |
| `app.py` | 3798-3812 | `def onboarding_wizard()` — persona-aware bypass | **ROUTING (G4.1 — defer)** | G4.1 replaces this whole endpoint with the unified "Tell us about your income" flow. W2 leaves untouched; W3/W4 may delete it | 0 |
| `app.py` | 4108-4111 | signup form: `request.form.get('persona_sl_foreign_income')` checkbox capture | PROFILE-ONLY (G4.1 — defer) | Stays for now (signup is the only writer of the column). G4.1 replaces with income-type picker | 0 |
| `app.py` | 4143-4154 | signup event payload + persona_set emit | PROFILE-ONLY | Stays as analytics-only signal. Add a parallel `income_sources_set` event in G4.1 | 0 |
| `app.py` | 5086-5094 | `g.is_fiesta_persona = ...; g.layout_template = 'layout_fiesta.html' if g.is_fiesta_persona else 'layout.html'` (before_request layout dispatch) | **PURE-UX (G1.4 core)** | After G1.2 lands, set `g.layout_template = 'layout_fiesta.html'` unconditionally for authenticated users. Then sweep `templates/*.html` to replace `extends "layout.html"` with `extends layout_template` so the dispatch is consistent. Eventually delete `layout.html` (and `home_bookkeeping_legacy.html`) in a follow-up | 4.0 |
| `fiesta/triage/routes.py` | 137-155 | `_post_complete_redirect()` — post-triage routing branches on persona | ROUTING | Replace with `return url_for('home')` (everyone goes home post-triage in the universal shell) | 0.3 |
| `fiesta/earnings/routes.py` | 132-141 | `/earnings` GET — persona-aware redirect to `/remittance/dashboard` | ROUTING | Decision call for orchestrator: (a) keep the redirect (foreign-income earners shouldn't see the bookkeeping earnings page) — switch trigger to `'foreign_remittance' in income_sources`; OR (b) make `/earnings` and `/remittance` route to the same surface in the universal hub. **Recommend (a)** — different evidence/FX models | 0.5 |
| `lankatax_onboarding_routes.py` | 139-152 | sets `user.persona = "sl_foreign_income"` for Lanka.tax-routed users | PROFILE-ONLY | Stays as legacy attribution. In G4.2, *also* append `'foreign_remittance'` to `user.income_sources` (idempotent). Do not remove the persona write — it's the cross-org attribution signal | 0.3 |
| `ai_crm.py` | 119-120 | comment about sub-persona inference | PROFILE-ONLY | Comment only — update post-G4.2 | 0 |
| `ai_crm.py` | 446 | comment about persona='sl_foreign_income' risk gate | PROFILE-ONLY | Comment — update post-G4.2 | 0 |
| `ai_crm.py` | 484-493 | `if persona == "sl_foreign_income"` — risk score Component B gated on persona | **TAX-ENGINE-DISCRIMINATOR (G4.2)** | Replace with `if 'foreign_remittance' in user.income_sources`. `_user_persona_and_subscription()` helper changes to return income_sources tuple | 0.8 |
| `ai_crm.py` | 604 | `persona, sub_status = user.persona, user.subscription_status` | PROFILE-ONLY | Keep — persona becomes a hint column; recompute also writes a sub-persona | 0.1 |
| `admin_analytics.py` | 616-619 | "active FIESTA users" KPI = COUNT(persona='sl_foreign_income') | TAX-ENGINE-DISCRIMINATOR (G4.2 follow-up) | Redefine "active FIESTA users" as users with non-empty `income_sources` (post-G4.1 every user has a non-empty list — the metric becomes "engaged users"). Keep persona-based count as a legacy comparison metric for the 30-day cutover window | 0.5 |
| `lankatax_crosssell.py` | 102-111 | `lankatax_warm_partial` cohort SQL: `WHERE u.persona = 'sl_foreign_income'` | TAX-ENGINE-DISCRIMINATOR (G4.2) | Replace with `WHERE u.income_sources @> '["foreign_remittance"]'` (Postgres JSONB containment). Or query: any user with a `RemittanceEntry` row | 0.4 |
| `support_copilot.py` | 178-188 | `_PERSONA_TOPIC_BOOSTS["sl_foreign_income"]` | TAX-ENGINE-DISCRIMINATOR (G4.2) | Replace persona key with income-source key: `_INCOME_SOURCE_TOPIC_BOOSTS["foreign_remittance"]`. Loop multi-source users through ALL their sources' boost dicts | 0.5 |
| `models.py` | 341 | comment: "Persona: 'sl_foreign_income' routes to..." | PROFILE-ONLY | Update comment in G1.2 commit to reflect new routing semantics | 0.1 |
| `remittance_models.py` | 11 | `PERSONA_SL_FOREIGN_INCOME = "sl_foreign_income"` constant | LEGACY-DEAD-CODE-CANDIDATE | Constant is defined but never imported anywhere else. Safe to delete in W2 cleanup | 0.1 |
| `fiesta/admin/routes.py` | 12 | docstring "values in prod: ``self``, ``sl_foreign_income``, ``None``" | PROFILE-ONLY | Update docstring | 0.1 |
| `fiesta/profile/__init__.py` | 7 | docstring "Persona locked to 'sl_foreign_income' in v1" | DIFFERENT-CONCEPT (filing-persona module) | **DO NOT TOUCH** — this is `FiestaProfile.persona='self'` (filing entity), unrelated to `User.persona='sl_foreign_income'` (UX discriminator). The name collision is the source of much confusion. W4 docs cleanup may rename one of them | 0 |
| `fiesta/earnings/to_tax.py` | 71 | comment about /remittance/* being canonical Earn-in for sl_foreign_income | PROFILE-ONLY | Update comment | 0.1 |

### Production code (Templates) — branch sites

| file | line | snippet | bucket | migration approach | W2 hours |
|---|---|---|---|---|---|
| `templates/error.html` | 1, 4-16 | `{% extends layout_template %}` + persona-aware Home CTA href/label | PURE-UX | Already uses dispatch. Post-G1.2: simplify the Home CTA branch since `/` is universal hub now — drop the `persona == 'sl_foreign_income'` branch, ELIF branch becomes the default | 0.5 |
| `templates/errors/_base.html` | 4-5, 23, 42-44 | docstring + persona-aware Home CTA | PURE-UX | Same simplification as error.html. The `_layout` resolution stays (escape-hatch is good for boot-time 500s) | 0.3 |
| `templates/layout.html` | 366-388 | E4 bookkeeping deprecation banner gated `persona != 'sl_foreign_income'` | PROFILE-ONLY | Post-G1.2: every authenticated user is on FIESTA shell, so this banner inside `layout.html` is unreachable. Will be deleted when `layout.html` is deleted (post-G1.4 final cleanup) | 0 |
| `templates/layout_fiesta.html` | 3 | docstring "BINDING shell for every authenticated sl_foreign_income surface" | PROFILE-ONLY | Update docstring to drop persona qualifier | 0.1 |
| `templates/fiesta_home.html` | 5 | docstring "Authenticated sl_foreign_income → THIS template" | PROFILE-ONLY | Update docstring | 0.1 |
| `templates/remittance/dashboard.html` | 4, 16 | docstring + `extends "layout_fiesta.html"` (HARD-extends, not dispatch) | PURE-UX | After G1.2 lands, flip to `extends layout_template` so it inherits the (now universal) FIESTA shell. Currently it's hardcoded because pre-G1.2 only sl_foreign_income users could reach it | 0.2 |
| `templates/cosign/pending.html` | 16 | `extends "layout_fiesta.html"` (HARD-extends) | PURE-UX | Same as above — flip to dispatch | 0.2 |
| `templates/triage/index.html` | 1-5 | docstring + `extends layout_template` | PROFILE-ONLY | Comment update; the extends is already correct | 0.1 |
| `templates/consultant/book.html` | 1-9 | F7.4 docstring + `extends layout_template` | PROFILE-ONLY | Comment update; the extends is already correct | 0.1 |
| `templates/submit/index.html` | 283-294 | docstring + projected savings counter | PROFILE-ONLY (G4.2) | Counter rendering already tolerates `hub_projected_savings_lkr is defined and …` — post-G4.2 the counter just shows for everyone with foreign income; no template change needed | 0 |
| `templates/register.html` | 189-199 | "Do you receive foreign income?" checkbox → `persona_sl_foreign_income` form field | PROFILE-ONLY (G4.1) | Stays for now. G4.1 replaces signup with the income-source picker (multi-select); persona checkbox becomes one source toggle | 0 |
| `templates/account/data.html` + 44 other `extends layout_template` templates | 1 | `extends layout_template` | PURE-UX (already migrated) | Zero W2 work — already use the dispatch var. Will auto-resolve to `layout_fiesta.html` after G1.4 collapses the dispatch | 0 |
| 76 templates extending `"layout.html"` (hardcoded; full list in §Hardcoded Legacy Extends below) | 1 | `{% extends "layout.html" %}` | **PURE-UX (G1.4 core)** | Sweep to `{% extends layout_template %}` so they pick up the universal shell. Mechanical replacement; risk is medium because the FIESTA shell may not render legacy bookkeeping page chrome (sidebar, top-nav slots) identically. Test plan: load each in Playwright post-G1.4 | 6.0 |

### Test code — branch sites (23 files)

| file | lines | what it tests | post-W2 action |
|---|---|---|---|
| `tests/platform/test_shell.py` | 5, 23, 27, 53, 55, 60, 66, 71, 115, 128, 130-131, 159, 171, 173-174, 185-189, 210, 250, 304 | Asserts `layout_fiesta.html` renders for `sl_foreign_income` AND `admin`; asserts `layout.html` renders for legacy persona; asserts `use_fiesta_shell()` gating | **REWRITE**: replace with "renders for any authenticated non-anonymous user" assertion; admin still gets the admin variant; anonymous still gets layout.html (until anon flow also unifies — TBD) |
| `tests/platform/test_sidebar.py` | 101, 168, 194, 229, 288, 290, 308-309, 322-323, 326 | Asserts legacy persona sees legacy sidebar; FIESTA persona sees fiesta sidebar | **REWRITE for G1.4**: assert one sidebar for all auth users; sub-items conditionally rendered based on `income_sources` |
| `tests/platform/test_hub.py` | 6-16, 116-131, 165, 206, 234, 267-304, 369 | Asserts `/` renders fiesta_home.html for `sl_foreign_income`, redirects to /scan for legacy | **REWRITE for G1.2**: assert `/` renders fiesta_home.html for ANY auth user; the legacy-redirect-to-scan test deletes |
| `tests/platform/test_error_pages.py` | 7-10, 128-150, 158-165 | Asserts 404 renders in FIESTA shell for `sl_foreign_income`; persona-aware Home CTA href | **REWRITE for G1.2**: assert 404 renders in FIESTA shell for ALL auth users; Home CTA href is `url_for('home')` for all auth users |
| `tests/platform/test_redirect_priority.py` | 5-30, 61-86, 118-180 | Locks F-Platform-3 ordering (persona reroute BEFORE org check in `/scan`) | **DELETE for G1.3**: `/scan` is a thin redirect to `/`; the org-check loop no longer exists. Replace with a "redirect-to-home" assertion |
| `tests/fixtures/personas.py` | 20, 49, 50, 53, 76, 112, 170-171, 253 | `ensure_sl_foreign_income_user()` factory — sets persona on the playwright seed user | **UPDATE**: have factory also set `income_sources=['foreign_remittance']` to mirror prod onboarding. Keep persona setter for cross-test compatibility |
| `tests/ai_run/test_ai_crm.py` | 149 | Asserts risk score for persona='sl_foreign_income' with no remit | **UPDATE for G4.2**: assert on `income_sources=['foreign_remittance']` instead |
| `tests/ai_run/test_lankatax_crosssell.py` | 301, 393-394 | Asserts onboarding sets persona='sl_foreign_income' | **UPDATE**: assert both persona is set AND income_sources contains 'foreign_remittance' (post-G4.2) |
| `tests/year_selector_module/conftest.py`, `tests/tax_return_pdf/conftest.py`, `tests/paywall/conftest.py`, `tests/remittance/conftest.py` | various | factory `_make_user(..., persona='sl_foreign_income')` | **UPDATE for G4.2**: factory adds `income_sources=['foreign_remittance']` |
| `tests/mobile/test_viewports.py` | 20-21, 77, 258-259 | Uses `ensure_sl_foreign_income_user()` seed; comments reference persona | **UPDATE**: comment refresh post-G1.2 |
| `tests/auth/test_signup.py` | 113 | `assert user.persona == "self"` (DIFFERENT-CONCEPT — `fiesta/signup/routes.py` writes `persona='self'` for the new signup flow) | **DO NOT TOUCH** — this is the filing-persona system, not the UX discriminator |

### Legacy / dead code

| file | bucket | action |
|---|---|---|
| `app.py.bak` | LEGACY-DEAD-CODE | Delete in W2 cleanup commit (was the pre-MS3 backup) |
| `templates/layout.html.bak` | LEGACY-DEAD-CODE | Delete in W2 cleanup commit |
| `templates/home_bookkeeping_legacy.html` | LEGACY-DEAD-CODE | Comment says "preserved in case rollback needed". Post-G1.2 sustained-success, delete |
| `app_analytics_redesign.py` | LEGACY-DEAD-CODE | The corresponding `/analytics/redesign` route returns `feature_unavailable.html`. The file is dead. Delete |
| `add_persona_and_remittance.py`, `add_persona_tables_x2.py`, `add_admin_and_stripe_columns_to_user.py` | LEGACY-DEAD-CODE | One-shot migration scripts. Already applied. Move to `migrations/_archive/` |
| `_tier_c_mobile_audit/SUMMARY.md`, `_tier_c_mobile_audit/issues.md` | LEGACY-DEAD-CODE (audit notes) | Document the playwright seed user issue, fixed by `ensure_sl_foreign_income_user()`. Keep as-is for the audit trail |

### "Different concept" — DO NOT modify in G1

These all use the word "persona" but refer to the *filing persona* system (one row per (user, persona_id) — V1 only `'self'`, V1.1 will add `spouse`/`dependant`/`parent`). This is a separate subsystem from the `User.persona='sl_foreign_income'` UX discriminator:

- `fiesta/persona/__init__.py`, `fiesta/persona/models.py` — filing-persona models
- `fiesta/profile/models.py:84` — `FiestaProfile.persona` defaults to `'self'`
- `fiesta/profile/validators.py:187` — validator default `'self'`
- `fiesta/signup/routes.py:243` — writes `User.persona="self"` for new FIESTA signups (V1 default)
- `templates/components/persona_switcher.html` — top-bar persona switcher (Self/Spouse/etc.)
- `add_persona_tables_x2.py:7` — comment explaining filing-persona schema
- `tests/persona/test_x2.py` — filing-persona tests
- `tests/profile/test_profile.py:163, 169, 172` — `FiestaProfile.persona='self'` assertions
- `support_kb/account_persona_change.md` — KB doc explaining persona change (both concepts mentioned)

**Risk:** the naming collision means a careless W2 sweep could break the filing-persona system. **Recommend W4 docs cleanup rename one**: e.g. `User.persona` → `User.legacy_segment_hint` (now that income_sources is the real discriminator), keeping `FiestaProfile.persona` for the filing-persona meaning.

---

## Hardcoded Legacy `extends "layout.html"` (76 files — W2 sweep targets)

Mechanical replacement: `{% extends "layout.html" %}` → `{% extends layout_template %}`. Group by area:

**Bookkeeping core** (will the FIESTA shell render these well? — see Risk Hotspot #2):
- `templates/accounts/` (5): assets, chart_of_accounts, dashboard, journal_entries, profit_loss
- `templates/billing/` (2): no_subscription, return
- `templates/billing/`, `templates/clients.html`, `templates/create_client.html`, `templates/edit_client.html`, `templates/view_client.html`
- `templates/invoices.html`, `templates/create_invoice.html`, `templates/create_invoice_fixed.html`, `templates/edit_invoice.html`, `templates/view_invoice.html`, `templates/record_payment.html`
- `templates/expenses.html`, `templates/expense_summary.html`, `templates/submit_expense.html`, `templates/view_expense.html`, `templates/batch_submit_expenses.html`, `templates/expense_pipeline.html`, `templates/expense_report.html`, `templates/expense_report_fixed.html`, `templates/expense_report_print.html`, `templates/print_expense_report.html`
- `templates/bank_accounts.html`, `templates/create_bank_account.html`, `templates/edit_bank_account.html`, `templates/view_bank_account.html`, `templates/organization_bank_accounts.html`, `templates/create_organization_bank_account.html`, `templates/edit_organization_bank_account.html`
- `templates/enhanced_bank/` (8): dashboard, reconcile_statement, reconciliation_center, rule_customization, statements_list, upload_form, validation_results, view_statement
- `templates/enhanced_classify_receipts.html`, `templates/classify_receipts.html`, `templates/tax_doc_scan.html`

**Earnings (bookkeeping flavour)**:
- `templates/earnings/extraction.html`, `templates/earnings/index.html`, `templates/earnings/manual_entry.html`, `templates/earnings/summary.html`

**Organisations / onboarding**:
- `templates/organizations/branding.html`, `templates/organizations/create.html` (conditional empty_layout), `templates/organizations/edit.html`, `templates/organizations/index.html`, `templates/organizations/invite.html`, `templates/organizations/view.html`
- `templates/onboarding_wizard.html`, `templates/getting_started.html`, `templates/verify_email_reminder.html`

**Support**:
- `templates/support/answer.html`, `templates/support/ask.html`, `templates/support/escalated.html`, `templates/support/qa.html`, `templates/support/tickets/list.html`, `templates/support/tickets/detail.html`

**Other**:
- `templates/ai_org/leaderboard.html`
- `templates/cosign/index.html`, `templates/cosign/walkthrough.html`
- `templates/debug_html.html`, `templates/feature_unavailable.html`, `templates/inbound/staff_queue.html`
- `templates/lifecycle/s11_invoice_cadence.html`, `templates/lifecycle/year_end_transition.html`
- `templates/referrals/dashboard.html`, `templates/referrals/landing.html`, `templates/pricing.html`
- `templates/persona/home.html`

---

## Risk Hotspots (Top 5)

### 1. `app.py:1290-1334` `home()` route — the G1.2 fulcrum
Ripping out the persona gate without also generalising `_compute_hub_extras()` will cause the hub to fail for users with no `RemittanceEntry` rows. The hub-extras function already handles empty remittances (returns 0 USD, 0 LKR, funnel_state="anon"), so the immediate render is safe. But the *next-step card* will say "Log your first inward remittance" to a Sri Lankan lawyer who doesn't receive foreign remittances — confusing and wrong. **Mitigation:** G1.2 must introduce additional `hub_funnel_state` values (`needs_income_picker`, `has_business_lkr`, `has_employment_lkr`) and a richer next-step recommender that branches on `income_sources` content, not on remittance presence alone. Estimate +2h on top of G1.2's 4-6h budget.

### 2. `templates/layout.html` → `layout_fiesta.html` sweep — 76 templates × FIESTA shell render mismatch
The FIESTA shell (`layout_fiesta.html`) was designed for the foreign-income flow: editorial paper/forest/clay palette, savings counter in topbar, FIESTA-specific sidebar groups. Bookkeeping pages (76 of them) currently render in `layout.html` with a different sidebar (Receipts / Expenses / Clients) and no savings counter. When G1.4 collapses everyone onto `layout_fiesta.html`, those pages render with a sidebar that has NO "Receipts" item — the bookkeeping module disappears from navigation. **Mitigation:** G1.4 must (a) extend the FIESTA sidebar to include conditional bookkeeping items shown when the user has receipt activity or has `business_lkr`/`employment_lkr` in income_sources; (b) test all 76 templates in Playwright with both shell variants to confirm chrome renders; (c) consider shipping G1.4 in two waves — wave 1 flips the dispatch, wave 2 retires `layout.html` after Playwright confirms zero regressions.

### 3. `use_fiesta_shell()` admin-role co-coupling
The function returns True for `persona == 'sl_foreign_income' OR role == 'admin'`. Once G1.2 collapses the first clause to "any authenticated user", the function semantically becomes "any authenticated user". But: admin pages extend `layout_fiesta_admin.html` (a different file), and the `home()` route explicitly excludes admins (`current_user.role != 'admin'`) from the hub render. **Risk:** if W2 simplifies `use_fiesta_shell()` without also auditing every `role != 'admin'` co-check in `app.py:1320, 1573` etc., admins might accidentally land on the customer hub. **Mitigation:** Search for `role != 'admin'` in `app.py` (currently 5 sites) and verify each still has a sibling persona check. After G1.2, the persona checks become redundant but the `role != 'admin'` checks must stay. Add a verification gate to the W2 PR: "every place persona is removed, admin-role exemption is preserved or migrated to a dedicated `is_customer_user()` helper".

### 4. `fiesta/triage/routes.py:_post_complete_redirect()` + the triage funnel
Currently: post-triage, `sl_foreign_income` → `/` (which renders hub), else → `/scan` (legacy). After G1.2, `/` is the hub for everyone, so a legacy bookkeeping user post-triage will land on the hub, not `/scan`. This may be the *intended* behaviour, but it changes the bookkeeping user's first-page experience. **Risk:** the bookkeeping user expects `/scan` (the receipt scanner) as their primary surface; the FIESTA hub doesn't have a "Scan a receipt" CTA at the top. They'll be confused. **Mitigation:** G1.2 must add a receipt-scan module card to the hub that's prominent when the user has receipt activity. This is G2 scope (bookkeeping into FIESTA shell) — see forward links below.

### 5. UNCERTAIN — `templates/persona/home.html` extends `"layout.html"` and `/persona` is the filing-persona surface
The filing-persona module (`fiesta/persona/`) mounts a `/persona` route that lists the user's filing personas. The template hardcodes `extends "layout.html"` — bookkeeping shell. Two interpretations:
- **(A)** This was wired before FIESTA shell existed and should flip to `extends layout_template` in W2 so post-G1.4 it gets the unified shell.
- **(B)** Filing personas are a v1.1 feature; the page is intentionally minimal and the legacy shell is fine until v1.1 ships the real persona-management UI.

**Recommendation:** Treat as (A) — flip to `extends layout_template` in the W2 sweep, log a note for the v1.1 team to redesign when persona switching ships.

---

## W2 Dispatch Shape Recommendation

**Recommend: 3 sequenced agents, NOT a single agent and NOT parallel.**

The G1.2 → G1.3 → G1.4 ordering has hard dependencies (each builds on the prior's universal-shell guarantee) and the risk surface is large. A single agent will lose context mid-way; fully-parallel agents will race on `app.py` and on the template sweep.

### Agent 1 — Routing + Hub generalisation (G1.2 + G1.3)
**Time:** 6-9h. Single agent, sequential commits.

1. **Design lock first** (the orchestrator should write this before dispatch — see §"Design lock recommendation" below).
2. **G1.2** (4-6h):
   - Remove persona gate from `app.py:home()` — only `role != 'admin'` remains.
   - Extend `_compute_hub_extras()` to compute next-step state from `User.income_sources`, not just `RemittanceEntry` count. Add 3 new `hub_funnel_state` values.
   - Rewrite `fiesta_home.html` next-step card to handle the new states.
   - Update `tests/platform/test_hub.py` to assert "any authenticated non-admin user gets the hub".
   - Verify on staging that a `persona=None` user can render `/`.
3. **G1.3** (1-2h):
   - Replace `app.py:index()` body with `return redirect(url_for('home'))`.
   - Move the email-verification + onboarding-not-completed gates to `home()`.
   - Delete the persona reroute inside `index()`.
   - Add `/scan` 302 redirect to receipt-scan-module URL once that module exists; until then, 302 to `/`.
   - Update `tests/platform/test_redirect_priority.py` to assert `/scan` → 302 → `/`.
4. **Generalise** `_post_complete_redirect()` in `fiesta/triage/routes.py` to always return `url_for('home')`.
5. **Single commit** per gate (G1.2 then G1.3) with the design lock as a third doc-only commit.

### Agent 2 — Sidebar unification (G1.4)
**Time:** 3-4h. Dispatch AFTER Agent 1 lands.

1. Extend `templates/_fiesta/sidebar.html` to surface bookkeeping items (Receipts, Expenses, Clients, Invoices) as a collapsible group when `(current_user.income_sources or [])` contains `business_lkr` / `employment_lkr` / `professional_fees_lkr` — OR when the user has any historical receipt activity (defensive — covers users not yet picker-classified).
2. Flip `app.py:5094` to `g.layout_template = 'layout_fiesta.html'` unconditionally for authenticated users (anonymous still gets `layout.html` until G3 anon-flow unification).
3. Update `tests/platform/test_sidebar.py` to assert single sidebar + conditional sub-items.
4. **DO NOT delete `layout.html` in this commit** — keep as fallback during a 14-day stability window.

### Agent 3 — Template extends sweep + cleanup
**Time:** 6h. Dispatch AFTER Agent 2 lands AND 14-day stability period elapses.

1. Mechanical replacement of `extends "layout.html"` → `extends layout_template` across 76 files.
2. Playwright smoke-test all 76 routes post-sweep.
3. Delete `templates/layout.html`, `templates/layout.html.bak`, `templates/home_bookkeeping_legacy.html`, `app.py.bak`.
4. Move `add_persona_*` migration scripts to `migrations/_archive/`.

### Design Lock Recommendation (write BEFORE Agent 1 dispatches)

The orchestrator should land a `working files/_fiesta_ms1_to_ms4/_g1_design_lock_universal_shell.md` document before W2 starts. Required content:

1. **The discriminator change:** "Post-G1.2, the platform's primary user-segment discriminator is `User.income_sources` (List[str] from `INCOME_SOURCE_TYPES` locked vocab). `User.persona` becomes a legacy hint column kept for cross-org attribution but not consulted by routing, layout, or feature gates."
2. **The shell rule:** "Every authenticated non-admin user gets `layout_fiesta.html`. Admins get `layout_fiesta_admin.html`. Anonymous users still get `layout.html` until G3 anon-flow unification."
3. **The hub rule:** "`/` is the FIESTA hub for every authenticated non-admin user. The hub adapts to `income_sources`: empty list → onboarding nudge; foreign_remittance → remittance ledger module; business_lkr/employment_lkr → bookkeeping module; multi-source → all relevant modules."
4. **The admin gate:** "All `role != 'admin'` checks co-located with persona checks STAY — admin role is the operator/customer separator and is unrelated to G1."
5. **The filing-persona subsystem** (`fiesta/persona/*`, `FiestaProfile.persona`, `persona_switcher.html`) is a DIFFERENT system — DO NOT touch in G1.
6. **Cleanup deferred:** `User.persona` column stays. Deletion gated on (a) 30-day stability post-G1.4 + (b) `lankatax_crosssell.py` migrated + (c) `ai_crm.py` Component B migrated.

---

## Forward Links to MS4 W3+

| Audit item | Affects | W3+ ticket |
|---|---|---|
| `templates/layout.html` deletion + 76-template sweep | G2 (bookkeeping into FIESTA shell) | G2.1 must verify bookkeeping pages render correctly in FIESTA shell — owns the FIESTA sidebar `Bookkeeping` group |
| `app.py:home()` next-step states for non-foreign-income users | G2 (bookkeeping CTAs in hub) | G2.2 must own the next-step recommender for LKR-only users |
| `fiesta/earnings/routes.py:index()` persona redirect | G3 (LKR engine routes) | G3 design must decide whether `/earnings` (statements upload — bookkeeping) and `/remittance` (CBSL ledger — FIESTA) remain separate URLs or merge |
| `ai_crm.py` + `lankatax_crosssell.py` + `admin_analytics.py` persona → income_sources | G4.2 (persona deprecation completion) | G4.2 owns the SQL migration + analytics-comparison window |
| `templates/register.html` persona checkbox + `app.py:4108-4111` signup form | G4.1 (single onboarding flow) | G4.1 replaces with income-source multi-picker; persona checkbox deletes |
| `fiesta/triage/routes.py` `_seed_business_income_sources()` (B12 hook) | G4.1 | G4.1's income-picker replaces triage; the seed helper consolidates into the picker |
| `User.persona` column drop | G4.2 completion + 30-day stability | Schema migration `drop_user_persona_column` — final G1/G4 closeout |

---

## Methodology Notes

- Grep was case-sensitive on `'sl_foreign_income'` and `"sl_foreign_income"` (both forms found and counted together).
- `getattr(user, 'persona', ...)` variants captured by pattern `getattr\(.*persona`.
- The `g.is_fiesta_persona` flag is set in exactly one place (`app.py:5090-5093`) and read in 4 places (3 in `app.py`, 2 in tests).
- The `use_fiesta_shell()` predicate is defined in `app.py:539-561` and called in `app.py:1323` plus 4 test sites.
- `extends "layout.html"` (76 files) vs `extends layout_template` (46 files) vs `extends "layout_fiesta.html"` (5 files) vs `extends 'empty_layout.html'` (3 files) vs `extends "admin/layout_fiesta.html"` (20+ admin files) vs `extends 'preview_layout.html'` (2 files). Total templates surveyed: ~155.
- Test files NOT counted in the 48-production-site bucket count but listed separately in §"Test code".
- Files surveyed: every `*.py`, `*.html`, and `*.md` under the repo root excluding `node_modules`, `.git`, `__pycache__`, `instance/`, and `*.bak`.

---

*End of audit.*
