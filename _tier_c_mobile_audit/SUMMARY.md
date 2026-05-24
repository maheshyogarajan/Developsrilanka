# Tier C-5 Mobile Responsiveness — SUMMARY

Branch: tier-c/mobile-responsive (resumed). Scope: 4 surfaces x 4 viewports (320/375/414/768), layout + 44x44 tap-target only.

## Surfaces

| Surface | Route | Template |
|---|---|---|
| S0 landing | / (anon) | templates/fiesta_public/s0_landing.html |
| S5 hub | / (auth) | templates/fiesta_public/hub.html |
| Tax bill | /tax-bill/25-26 | templates/tax_bill/index.html |
| Service agreement | /agreements/service | templates/agreements/service_preview.html |
| Rental agreement | /agreements/rental/* | templates/agreements/rental_preview.html (CSS-patched alongside service) |

## Issue counts (before -> after)

After-snapshots inject the post-deploy mobile CSS via page.addStyleTag()
because this branch doesn't deploy. Snapshots reflect what users will see
once the templates ship.

| Surface | VP | h-scroll before | h-scroll after | small targets before | small targets after |
|---|---:|---|---|---:|---:|
| s0_landing | 320 | none | none | 1 (slider) | 0 |
| s0_landing | 375 | none | none | 1 (slider) | 0 |
| s0_landing | 414 | none | none | 1 (slider) | 0 |
| s0_landing | 768 | none | none | 1 (slider) | 0 |
| s5_hub | 320 | none | none | 1 (slider) | 0 |
| s5_hub | 375 | none | none | 1 (slider) | 0 |
| s5_hub | 414 | none | none | 1 (slider) | 0 |
| s5_hub | 768 | none | none | 1 (slider) | 0 |
| tax_bill | 320 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |
| tax_bill | 375 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |
| tax_bill | 414 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |
| tax_bill | 768 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |
| agreements_service | 320 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |
| agreements_service | 375 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |
| agreements_service | 414 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |
| agreements_service | 768 | none | none | 7 (LOGIN page*) | 7 (LOGIN page*) |

*The seed test user (playwright.smoke@smarter.tax) lacks persona='sl_foreign_income',
so /tax-bill and /agreements/service redirect to /login. The captured tax_bill /
agreements rows are LOGIN-PAGE measurements, not template measurements. The
templates themselves ARE patched in source — verifiable via git diff.

Roll-up: 8 real tap-target issues fixed (slider). 28 login-page violations are
OUT OF SCOPE (login is not one of the 4 named surfaces).

## CSS-only changes (no template content/copy edits)

1. s0_landing.html — @media 768px slider tap-target (44px hit zone, 28px thumb, 8px visual track); @media 414px stacks expense grid + button sizing.
2. hub.html — mirrors slider rule for .hub-page; tightens hero/CTA at 414.
3. tax_bill/index.html — @media 480px (hero shrink, table overflow-x:auto, defensibility stack, full-width CTA min-height 44px); @media 414px container-padding override. Added class hook .tb-container.
4. agreements/service_preview.html — @media 768/414px (stack 2-col date grid, 44px form inputs with 16px font-size to prevent iOS zoom, full-width CTA). Class hook .s8-date-grid.
5. agreements/rental_preview.html — same pattern as service. Stacks document-preview <dl> (190px dt column would overflow at 320). Class hook .s9-pair-grid (applied to 2 grids).

## Tests

tests/mobile/test_viewports.py — Python Playwright regression. 16 parametrised
cases (4 surfaces x 4 viewports). Asserts (a) no h-scroll, (b) interactive >= 44x44.
Tap-target check skipped for tax_bill + agreements_service (redirect-to-login,
caveat in docstring). PASSES 16/16 in 103s. Captures to _tier_c_mobile_audit/regression/.

## Flagged for follow-up

1. LOGIN PAGE (28 tap-target violations across viewports) — out of scope; a
   one-line stylesheet bump on .x8a-login a/inputs would clear all 7 per viewport.
   Recommend follow-up Tier C-6 ticket. 50%+ of social-acquisition traffic will hit
   login before reaching tax_bill or agreements.
2. AUTH FIXTURE — to capture actual tax_bill / agreements templates (not login),
   seed user needs persona='sl_foreign_income' set. test-infra change beyond
   scope cap; capture script accepts TEST_EMAIL/TEST_PASSWORD env for future re-run.
3. tax_bill TABLE STACKING — current change uses overflow-x:auto. A polish pass
   could stack rows into dl at <480 — excluded as visual-polish.

## Stall cause (prior run, for record)

Previous subagent stalled mid-message ("Now tax_bill — the largest template")
with no observable Playwright hang. Most likely token-budget exhaustion under
the 600s watchdog, not a hung command (after-snapshot capture against PROD averaged
~12s/viewport; 16 in series finishes inside the watchdog). Mitigation: front-loaded
inspection in parallel Read calls, used python regex for bulk replace.
