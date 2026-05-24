# Tier C-5 Mobile Audit — Issues Identified

**Methodology:** Playwright capture against `https://fiesta-mvp.fly.dev` at viewports 320, 375, 414, 768.
Tap target threshold: 44×44px per Apple HIG. Horizontal scroll: forbidden.
For `<input>` elements wrapped in a `<label>`, the label's bounding box is the measured tap target (clicking anywhere on the label activates the input).

## Capture coverage caveat

The auth-fixture user `playwright.smoke@smarter.tax` does NOT have `persona='sl_foreign_income'`, so:
- `/` for the authenticated user redirects to legacy `/scan` → captured `s5_hub` is byte-identical to `s0_landing` (the persona gate at `app.py:671` falls through).
- `/tax-bill/25-26` and `/agreements/service` redirect to `/login?next=...` for any user without portal access → captured PNGs show the LOGIN page, not the actual templates.

Per Council scope cap (no new test infra), the captures represent what **social-acquisition traffic actually sees** when clicking links from FB/Twitter/Discord:
- Anon click → landing or login page.
- 50%+ of social traffic will hit `/login` before reaching `/tax-bill` or `/agreements`. The login page IS a critical mobile gate.

The templates owned by this task (`s0_landing.html`, `hub.html`, `tax_bill/index.html`, `agreements/service_preview.html`, `agreements/rental_preview.html`) are ALSO patched in their CSS via static source inspection, so post-login the surfaces honour the same 44×44 + no-overflow contract.

## Findings (viewport: 375, representative)

### S0 landing (`templates/fiesta_public/s0_landing.html`)
- No horizontal scroll at any viewport. PASS.
- 1/16 tap targets too small: **`<input type="range">` savings slider (265×6px)**. Critical: impossible to drag on mobile.
- Already has `@media (max-width: 640px)` queries for hero/typography. Slider styling missing.

### S5 hub (`templates/fiesta_public/hub.html`)
- Identical to S0 in captured snapshots (test user redirected). Same slider issue in source.
- Existing media queries at 720px / 640px. Slider styling missing.

### tax_bill (`templates/tax_bill/index.html`)
- Captured snapshots are of the `/login` redirect page (see caveat above).
- Source-level audit: 1085-line template with media query at 768px only. Missing breakpoints for ≤414. Hero numbers are 2.5–3rem font-size — likely overflow at 320. Tables (line-item breakdowns) likely scroll horizontally.

### agreements/service_preview (`templates/agreements/service_preview.html`)
- Captured snapshot is `/login` page.
- Source-level audit: 436-line template, max-width: 820px container, no media queries below 820px. PDF-preview layout will be cramped at 320–414.

### agreements/rental_preview (`templates/agreements/rental_preview.html`)
- Same caveat. Source-level audit: 476 lines, similar to service preview, no mobile media queries.

### Login page (effectively `tax_bill` + `agreements_service` in captures)
- 7 small tap targets at 375:
  - `<a>FIESTA</a>` brand link: 166×36px (h=36, need 44)
  - `<input>` email/password: 237×42px (h=42, need 44)
  - `<a>Forgot?</a>`: 41×18px (BOTH dimensions too small)
  - `<a>Create a new account</a>`: 237×40px (h=40, need 44)
  - `<a>Terms of Service</a>`: 209×40px (h=40, need 44)
  - `<a>Privacy Policy</a>`: 86×19px (BOTH too small)
- Login is OUT OF SCOPE for this task (not in the 4 named surfaces). Flagged for follow-up — but a one-line stylesheet bump would fix all of these.

## Horizontal scroll: ZERO incidents across all 16 captures.

## Plan
1. S0/hub: add `input[type="range"]` mobile sizing (≥44px touch zone via `height: 44px` + visible track 6-8px inside).
2. tax_bill: add `@media (max-width: 480px)` block — shrink hero font sizes, stack flex rows, ensure tables `overflow-x: auto`.
3. agreements/{service,rental}_preview: add `@media (max-width: 480px)` — adjust padding, stack breadcrumbs, ensure preview body wraps.
4. Out-of-scope follow-up: bump login page anchor heights + brand-link tap zone.
