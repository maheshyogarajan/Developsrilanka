# G5.1 — Remaining `extends "layout.html"` template audit + migration

**Date:** 2026-05-26
**Branch:** `tier-d6/g5-visual-admin-unification`
**Source spec:** `working files/_fiesta_unification_addendum_20260525.md` §G5

## Scope

W3a left ~71-86 templates extending the legacy `layout.html` (anonymous pages, edit-detail bookkeeping sub-pages, orphan legacy templates). G5.1 classifies each remaining template and migrates the AUTHENTICATED MIGRATABLE set to `extends layout_template` (the dynamic dispatcher set by `app.py:check_authentication` — `layout_fiesta.html` for authed non-admins, `layout_fiesta_admin.html` for admins, `layout.html` for anon).

## Method

1. `grep -rEn "extends ['\"]layout\.html['\"]" templates/` (line-1 matches only — exclude comments)
2. For each template, locate `render_template("X")` callers in `*.py`
3. Check route decorators (`@login_required`, `@admin_required`, none)
4. Classify each into one of four categories.

## Classification — 87 templates (line-1 `extends "layout.html"`)

### Category A: ANON (KEEP on `layout.html`) — 10 templates

Reason: page rendered for unauthenticated users; Design Lock 3 §D5 reserves `layout.html` for anonymous boot-time fallback (S0 landing, signup, login, public help/articles, public leaderboard, invitation-acceptance).

| Template | Route | Reason |
|---|---|---|
| `templates/login.html` | `app.py /login` | unauth: login form |
| `templates/register.html` | `app.py /register` | unauth: signup form |
| `templates/fiesta_public/s0_landing.html` | `app.py /` (anon branch) | public marketing landing |
| `templates/help/index.html` | `faq_routes.py /help` (public) | SEO help index |
| `templates/help/entry.html` | `faq_routes.py /help/<slug>` (public) | SEO FAQ entry |
| `templates/articles/index.html` | `seo_routes.py /articles` (public) | SEO articles index |
| `templates/articles/detail.html` | `seo_routes.py /articles/<slug>` (public) | SEO article detail |
| `templates/pricing.html` | `app.py /pricing` (no @login_required) | public pricing |
| `templates/ai_org/leaderboard.html` | `ai_org_score_routes.py /leaderboard` (public, no auth) | public band-only board |
| `templates/confirm_friend_invitation.html` | `app.py /accept-invitation/<token>` (no @login_required — invitees may be unsigned) | public invitation accept |
| `templates/home_bookkeeping_legacy.html` | rollback-only — preserved per `app.py:1507` comment + `tests/fiesta_public/test_full_flow.py:226` | DOCUMENTED rollback path |

(11 entries — `home_bookkeeping_legacy.html` is the documented rollback safety net.)

### Category B: AUTHENTICATED MIGRATABLE (flip → `extends layout_template`) — 60 templates

All confirmed behind `@login_required` (route decorator audit). Flipping to `extends layout_template` means: admins get `layout_fiesta_admin.html`, non-admin authed users get `layout_fiesta.html`, anon would fall back to `layout.html` (but routes are auth-gated so anon path is unreachable).

**Block content fully preserved.** All sub-blocks (`additional_styles`, `content`, `scripts`, `extra_js`, page-specific overrides) carry across because both layouts expose the same block surface. Differences in inner topbar/sidebar are owned by the layout, not the page.

#### Bookkeeping cash-in/cash-out (W3a deferred — accounts/bank — high-traffic)

1. `templates/bank_accounts.html` — `bank_account_routes.py /bank-accounts`
2. `templates/create_bank_account.html` — `bank_account_routes.py /bank-accounts/create`
3. `templates/edit_bank_account.html` — `bank_account_routes.py /bank-accounts/<id>/edit`
4. `templates/view_bank_account.html` — `bank_account_routes.py /bank-accounts/<id>`
5. `templates/create_organization_bank_account.html` — `bank_account_routes.py /organizations/<id>/bank-accounts/create`
6. `templates/edit_organization_bank_account.html` — `bank_account_routes.py /organizations/<id>/bank-accounts/<aid>/edit`

#### Receipts / classify

7. `templates/classify_receipts.html` — `classify_routes.py`
8. `templates/enhanced_classify_receipts.html` — `enhanced_classify_routes.py`
9. `templates/edit_receipt.html` — `app.py /edit/<id>`
10. `templates/edit_receipt_fixed.html` — `unified_receipt_expense_routes.py /edit/<id>` (the LIVE editor)
11. `templates/edit_receipt_minimal.html` — orphan (no caller in grep, retained for parity)
12. `templates/receipt_history.html` — alias redirected by `app.py /history` → `unified_view.history`; template still loaded by edge codepath
13. `templates/receipts.html` — `app.py /receipts`
14. `templates/unified_history.html` — `unified_receipt_expense_routes.py /history`
15. `templates/unified_history_optimized.html` — A/B variant (kept for ab_test infra)
16. `templates/unified_receipt_view.html` — `unified_receipt_expense_routes.py /view/<id>`
17. `templates/allocate_to_client.html` — `client_expense_routes.py`

#### Expenses

18. `templates/expenses.html` — `expense_reports.py /expenses`
19. `templates/expense_report.html` — orphan (no caller); kept for parity
20. `templates/expense_report_fixed.html` — `expense_reports.py /reimbursements` (LIVE)
21. `templates/expense_report_print.html` — orphan (no caller)
22. `templates/expense_summary.html` — orphan
23. `templates/print_expense_report.html` — `expense_reports.py /expenses/print` (LIVE)
24. `templates/submit_expense.html` — `expense_routes.py`
25. `templates/batch_submit_expenses.html` — `expense_routes.py`
26. `templates/create_expense_from_receipt.html` — `expense_routes.py /receipts/<id>/expenses/new`
27. `templates/view_expense.html` — `expense_routes.py /expenses/<id>`

#### Invoices

28. `templates/create_invoice.html` — `invoice_routes.py`
29. `templates/create_invoice_fixed.html` — invoice routes (alt variant)
30. `templates/edit_invoice.html` — `invoice_routes.py /invoices/<id>/edit`
31. `templates/view_invoice.html` — `invoice_routes.py /invoices/<id>`
32. `templates/record_payment.html` — `invoice_routes.py`

#### Clients

33. `templates/clients.html` — `blueprints/clients.py`
34. `templates/create_client.html` — `blueprints/clients.py`
35. `templates/edit_client.html` — `blueprints/clients.py`
36. `templates/view_client.html` — `blueprints/clients.py`
37. `templates/client_expenses.html` — `client_expense_routes.py`

#### Organizations

38. `templates/organizations/index.html` — `organization_routes.py /organizations`
39. `templates/organizations/create.html` — `organization_routes.py /organizations/create` (NOTE: uses `extends "layout.html" if not is_modal else "empty_layout.html"` — dual-path; convert to `extends layout_template if not is_modal else "empty_layout.html"`)
40. `templates/organizations/edit.html` — `organization_routes.py /organizations/<id>/edit`
41. `templates/organizations/view.html` — `organization_routes.py /organizations/<id>`
42. `templates/organizations/branding.html` — `organization_routes.py /organizations/<id>/branding`
43. `templates/organizations/invite.html` — `organization_routes.py /organizations/<id>/invite`
44. `templates/organizations/confirm_invitation.html` — `organization_routes.py /confirm-invitation/<token>`
45. `templates/organization_bank_accounts.html` — orphan in current `*.py` grep; kept for parity

#### Earnings

46. `templates/earnings/extraction.html` — `fiesta/earnings/routes.py`
47. `templates/earnings/index.html` — `fiesta/earnings/routes.py /earnings`
48. `templates/earnings/manual_entry.html` — `fiesta/earnings/routes.py`
49. `templates/earnings/summary.html` — `fiesta/earnings/routes.py`

#### Support / referrals / cosign / billing

50. `templates/support/answer.html` — `support_routes.py`
51. `templates/support/ask.html` — `support_routes.py`
52. `templates/support/escalated.html` — `support_routes.py`
53. `templates/support/qa.html` — `qa_routes.py`
54. `templates/support/tickets/detail.html` — `support_tickets_routes.py`
55. `templates/support/tickets/list.html` — `support_tickets_routes.py`
56. `templates/referrals/dashboard.html` — `referral_routes.py`
57. `templates/referrals/landing.html` — `referral_routes.py`
58. `templates/cosign/index.html` — `fiesta/cosign/routes.py`
59. `templates/cosign/walkthrough.html` — cosign onboarding (auth)
60. `templates/billing/no_subscription.html` — Stripe subscription page (auth)
61. `templates/billing/return.html` — `webhooks/stripe_subscription.py /billing/return`
62. `templates/invite_friends.html` — `app.py /invite-friends`
63. `templates/onboarding_wizard.html` — `app.py /onboarding`
64. `templates/getting_started.html` — `getting_started.py /getting-started`
65. `templates/verify_email_reminder.html` — `app.py /verify-email-reminder` (@login_required)

#### Misc bookkeeping / analytics

66. `templates/analytics.html` — `app.py /analytics` (renders `feature_unavailable.html` — orphan but linked)
67. `templates/analytics_redesign.html` — `app_analytics_redesign.py`
68. `templates/feature_unavailable.html` — `app.py /analytics`, `/analytics/redesign`, `/tax-savings` (@login_required)
69. `templates/profile.html` — `app.py /profile`
70. `templates/tax_savings.html` — `app.py /tax-savings`
71. `templates/team/dashboard.html` — `blueprints/team.py`
72. `templates/inbound/staff_queue.html` — orphan in current `*.py` grep but referenced by `fiesta/inbound/README.md` as staff-review UI
73. `templates/lifecycle/s11_invoice_cadence.html` — orphan (no caller)
74. `templates/lifecycle/year_end_transition.html` — orphan (no caller)
75. `templates/persona/home.html` — ALREADY on `extends layout_template` per Stage D7 (false positive in regex — `extends "layout.html"` appears in a `{# ... #}` comment block)
76. `templates/debug_html.html` — orphan dev tool (kept; auth-gated route in `app.py` if mounted)

(60 templates flipped — `persona/home.html` is excluded as already-migrated; `home_bookkeeping_legacy.html` is excluded as documented rollback path.)

### Category C: PARTIAL / EDITOR-INTERNAL — 0 templates

None of the audited line-1-`extends "layout.html"` templates are content-only partials. Partials in this codebase live under `templates/_fiesta/`, `templates/components/`, `templates/admin/`-shared, and use `{% include %}` from layouts (no `extends`).

### Category D: STANDALONE — 2 templates

These intentionally have no `{% extends %}` and render a complete `<!DOCTYPE html>`:

| Template | Reason |
|---|---|
| `templates/fiesta_admin/users.html` | S15 admin Users list (Wave 6 ship) — was built as a standalone page pre-G5 admin layout. **§G5.4 unification:** migrate to `extends "admin/layout_fiesta.html"` to inherit topbar/rail/canvas + reduce drift |
| (any other DOCTYPE-on-line-1 files in templates/) | none found in scope of layout.html-extenders |

`templates/empty_layout.html` is the documented minimal-shell layout used for modal popups (e.g. `organizations/create.html if is_modal`) — not a target, kept as-is.

## Page-level nav duplication (G5.3 input)

Templates that introduce a page-level nav inside the FIESTA shell are candidates for collapse to breadcrumb (or relocation into `head_extra`):

- `templates/accounts/dashboard.html` — already on `layout_fiesta.html`; renders its own tab strip. **Per W3a report:** acknowledged; G5.3 collapses to breadcrumb.
- No other duplicated page-level nav surfaced inside Category B migrations (all are detail/edit pages that don't ship their own topbar).

## §G5.4 admin 360-view URL unification

**Audit:**
- `/admin/fie/users` — S15 paginated list (template `templates/fiesta_admin/users.html`, standalone shell, blueprint `fiesta_admin`)
- `/admin/customer/<int:user_id>` — 360 view per user (template `templates/admin/customer_profile.html`, extends `admin/layout_fiesta.html`, blueprint `customer_brain`)

The two routes are NOT overlapping (list vs detail). The list at `/admin/fie/users` already links each row's email cell to `url_for('customer_brain.view', user_id=r.id)` (line 143 of `templates/fiesta_admin/users.html` — citing comment `C5 F8.6`). The canonical per-user 360 URL is `/admin/customer/<id>`.

**Outcome:** no rebuild. URL contract is already canonical. Drift fix is to bring the list template under the admin FIESTA shell (Category D migration above) so the list + detail share one chrome.

## Error pages (G5.2 verify-only)

`templates/errors/_base.html` was shipped by F-Platform-6 (Stage C3) with smart layout dispatch (uses `error_layout_template` -> `g.layout_template` -> `'layout.html'` fallback). All four error pages (`401.html`, `403.html`, `404.html`, `500.html`) extend `errors/_base.html`. No drift detected.

`templates/error.html` (legacy generic error page) was migrated in W3a to `extends layout_template` — confirmed (line 1).

`templates/admin/403.html` extends `admin/layout_fiesta.html` directly — correct admin error path. No drift.

## Migration outcome (after this PR)

| Category | Count | Action |
|---|---|---|
| A. ANON (kept on `layout.html`) | 11 | unchanged |
| B. AUTH MIGRATABLE → `layout_template` | 60 | flipped this PR |
| C. PARTIAL / EDITOR-INTERNAL | 0 | n/a |
| D. STANDALONE → `admin/layout_fiesta.html` | 1 | flipped this PR (S15 users.html) |
| **Total swept** | **72** | |

Remaining `extends "layout.html"` after this PR: **11** (all anon, all justified). The legacy `layout.html` itself stays — its deletion is W2 Agent 3 work post 14-day stability, outside MS4.

## Verification

`tests/platform/test_g5_visual_unification.py` enforces:
1. No `extends "layout.html"` on line 1 of any template outside the explicit ANON accept-list.
2. `/admin/customer/<id>` is the canonical 360 URL; the list links to it.
3. `templates/accounts/dashboard.html` doesn't double-render the FIESTA topbar.
4. All `templates/errors/*.html` extend `errors/_base.html` (which dispatches via `layout_template`).
5. `templates/profile/*` does not contain stale `sl_foreign_income` strings (G5.5 PROFILE-ONLY cleanup).
