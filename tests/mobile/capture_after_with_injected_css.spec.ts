/**
 * Tier C-5 — AFTER snapshots with locally-applied mobile CSS.
 *
 * Captures `after/` snapshots against PROD (fiesta-mvp.fly.dev) but injects
 * the new mobile CSS via page.addStyleTag() so the snapshot reflects what
 * users WILL see post-deploy of this branch's template changes.
 *
 * This is a pragmatic workaround: the local repo's template changes haven't
 * been deployed yet (and per scope cap won't be from this branch). Injecting
 * the same CSS string the templates now contain produces a faithful preview.
 *
 * Source-truth: the CSS strings below are byte-equivalent to what we appended
 * to:
 *   templates/fiesta_public/s0_landing.html
 *   templates/fiesta_public/hub.html
 *   templates/tax_bill/index.html
 *   templates/agreements/service_preview.html
 *   templates/agreements/rental_preview.html
 *
 * Run from tests/playwright:
 *   BASE_URL=https://fiesta-mvp.fly.dev \
 *     npx playwright test ../mobile/capture_after_with_injected_css.spec.ts \
 *     --config ../mobile/playwright.config.ts
 *
 * Output dir: _tier_c_mobile_audit/after/
 * Issues log: _tier_c_mobile_audit/issues_after.json
 */
import { test, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { loginAs } from '../playwright/smoke/helpers';

const PHASE = 'after';
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const OUT_DIR = path.join(REPO_ROOT, '_tier_c_mobile_audit', PHASE);
const ISSUES_FILE = path.join(REPO_ROOT, '_tier_c_mobile_audit', `issues_${PHASE}.json`);

if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

const VIEWPORTS = [
  { name: '320', width: 320, height: 568 },
  { name: '375', width: 375, height: 667 },
  { name: '414', width: 414, height: 896 },
  { name: '768', width: 768, height: 1024 },
];

const SURFACES = [
  { name: 's0_landing',         url: '/',                   requiresAuth: false },
  { name: 's5_hub',             url: '/',                   requiresAuth: true  },
  { name: 'tax_bill',           url: '/tax-bill/25-26',     requiresAuth: true  },
  { name: 'agreements_service', url: '/agreements/service', requiresAuth: true  },
];

/* Aggregate of all mobile CSS added in this branch. Applied to every page so
 * the user-on-mobile preview is accurate regardless of which surface lands. */
const INJECTED_MOBILE_CSS = `
/* s0_landing + hub slider tap target */
@media (max-width: 768px) {
  .x8a-landing input[type=range],
  .hub-page input[type=range] {
    height: 44px; background: transparent; padding: 0; cursor: pointer;
  }
  .x8a-landing input[type=range]::-webkit-slider-runnable-track,
  .hub-page input[type=range]::-webkit-slider-runnable-track {
    height: 8px; background: #d8cab4; border-radius: 4px;
  }
  .x8a-landing input[type=range]::-moz-range-track,
  .hub-page input[type=range]::-moz-range-track {
    height: 8px; background: #d8cab4; border-radius: 4px;
  }
  .x8a-landing input[type=range]::-webkit-slider-thumb,
  .hub-page input[type=range]::-webkit-slider-thumb {
    width: 28px; height: 28px; margin-top: -10px;
  }
  .x8a-landing input[type=range]::-moz-range-thumb,
  .hub-page input[type=range]::-moz-range-thumb {
    width: 28px; height: 28px;
  }
}
@media (max-width: 414px) {
  .x8a-landing { padding: 18px 12px 70px; }
  .x8a-landing h1.display, .hub-page h1.display { font-size: 26px; line-height: 1.1; }
  .x8a-landing .slider-row, .hub-page .slider-row { padding: 16px; }
  .x8a-landing .btn, .hub-page .cta-row .btn, .hub-page .cta-row .btn.ghost {
    padding: 14px 18px; min-height: 44px;
  }
  .x8a-landing .btn.ghost { padding: 12px 16px; min-height: 44px; }
  .x8a-landing .expense-grid { grid-template-columns: 1fr; }
  .x8a-landing .expense-chip { min-height: 48px; padding: 12px; }
  .x8a-landing .expense-chip .name { font-size: 14px; }
}
/* tax_bill 480px breakpoint */
@media (max-width: 480px) {
  .tb-hero { padding: 1.5rem 1rem; }
  .tb-hero h1 { font-size: 1rem; }
  .tb-hero-proto__amount { font-size: 1.25rem; }
  .tb-hero-saved__amount { font-size: 2.5rem; line-height: 1.05; }
  .tb-hero-bill__amount { font-size: 1.15rem; }
  .tb-hero .tb-final { font-size: 1.75rem; }
  .tb-section { padding: 0.85rem; }
  .tb-section summary { font-size: 1rem; }
  .tb-section .tb-section-total { display: block; margin-left: 0; margin-top: 0.25rem; }
  .tb-section-content { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .tb-table { font-size: 0.8rem; min-width: 100%; }
  .tb-table th, .tb-table td { padding: 0.4rem 0.3rem; }
  .tb-defensibility { flex-direction: column; align-items: flex-start; padding: 1rem; gap: 0.5rem; }
  .tb-defensibility .tb-def-score { font-size: 2rem; min-width: 0; }
  .tb-projected-savings { padding: 0.75rem 1rem; }
  .tb-projected-savings__amount { font-size: 1.1rem; }
  .tb-projected-savings__note { margin-left: 0; flex-basis: 100%; }
  .tb-ira-panel summary { padding: 1rem 1rem; font-size: 1rem; }
  .tb-ira-panel__body { padding: 0 1rem 1rem; }
  .tb-ctas { flex-direction: column; gap: 0.5rem; }
  .tb-cta-primary, .tb-cta-secondary {
    min-height: 44px; padding: 0.85rem 1.25rem;
    justify-content: center; width: 100%;
  }
}
@media (max-width: 414px) {
  .tb-container { padding: 0.75rem 0.75rem !important; }
  .tb-finalized-banner, .tb-engine-error, .tb-gate-block, .tb-gate-warn {
    padding: 0.75rem 0.85rem; font-size: 0.85rem;
  }
}
/* agreements service + rental */
@media (max-width: 768px) {
  .s8-page, .s9-page { padding: 0.75rem 0.75rem 3rem; }
  .s8-pane, .s9-pane { padding: 1.25rem 1rem 1rem; border-radius: 12px; }
  .s9-doc { padding: 1.25rem 1rem 1rem; }
  .s9-doc dl { grid-template-columns: 1fr; gap: 0.15rem 0; }
  .s9-doc dt { font-weight: 600; margin-top: 0.5rem; }
  .s9-doc dt:first-of-type { margin-top: 0; }
  .s8-date-grid, .s9-pair-grid { grid-template-columns: 1fr !important; }
  .s8-form-group input, .s8-form-group select, .s8-form-group textarea,
  .s9-form-group input, .s9-form-group select, .s9-form-group textarea {
    min-height: 44px; font-size: 16px;
  }
  .s8-btn-generate, .s9-btn-generate {
    min-height: 44px; padding: 0.85rem 1.5rem; width: 100%;
  }
  .s8-action-row, .s9-action-row { gap: 0.75rem; }
  .s8-breadcrumb, .s9-breadcrumb { flex-wrap: wrap; row-gap: 0.25rem; }
  .s8-checkbox-row input[type="checkbox"], .s8-radio-group input[type="radio"] {
    width: 22px; height: 22px;
  }
}
@media (max-width: 414px) {
  .s8-page, .s9-page { padding: 0.5rem 0.5rem 3rem; }
  .s8-pane, .s9-pane { padding: 1rem 0.85rem 0.85rem; }
  .s9-doc { padding: 1rem 0.85rem 0.85rem; }
  .s8-fieldset-legend, .s9-fieldset-legend { font-size: 0.95rem; }
  .s8-draft-banner, .s8-gate-strip, .s8-disclosure-strip, .s8-tax-savings-strip,
  .s9-draft-banner, .s9-gate-strip, .s9-tax-savings-strip {
    padding: 0.75rem 0.85rem; font-size: 0.85rem;
  }
}
`;

type IssueRow = {
  surface: string;
  viewport: string;
  horizontalScroll: boolean;
  scrollWidth: number;
  innerWidth: number;
  smallTapTargets: number;
  totalInteractive: number;
  smallSamples?: Array<{ tag: string; w: number; h: number; text: string }>;
};
const issues: IssueRow[] = [];

test.afterAll(async () => {
  fs.writeFileSync(ISSUES_FILE, JSON.stringify(issues, null, 2));
});

async function assess(page: Page, surface: string, vp: string): Promise<IssueRow> {
  const metrics = await page.evaluate(() => {
    const scrollWidth = document.documentElement.scrollWidth;
    const innerWidth = window.innerWidth;
    const selectors = 'a, button, input, select, textarea, [role="button"], [onclick]';
    const interactive = Array.from(document.querySelectorAll(selectors)) as HTMLElement[];
    let smallCount = 0;
    const small: Array<{ tag: string; w: number; h: number; text: string }> = [];
    for (const el of interactive) {
      const r = el.getBoundingClientRect();
      const cs = window.getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || (r.width === 0 && r.height === 0)) continue;
      if (el.tagName.toLowerCase() === 'input') {
        const lbl = el.closest('label');
        if (lbl) {
          const lr = lbl.getBoundingClientRect();
          if (lr.width >= 44 && lr.height >= 44) continue;
        }
      }
      if (el.tagName.toLowerCase() === 'input' && (el as HTMLInputElement).type === 'hidden') continue;
      if (r.width < 44 || r.height < 44) {
        smallCount++;
        if (small.length < 15) {
          small.push({
            tag: el.tagName.toLowerCase(),
            w: Math.round(r.width),
            h: Math.round(r.height),
            text: (el.textContent || (el as HTMLInputElement).value || (el as HTMLInputElement).type || '').trim().slice(0, 50),
          });
        }
      }
    }
    return { scrollWidth, innerWidth, smallCount, totalInteractive: interactive.length, small };
  });
  return {
    surface, viewport: vp,
    horizontalScroll: metrics.scrollWidth > metrics.innerWidth + 1,
    scrollWidth: metrics.scrollWidth,
    innerWidth: metrics.innerWidth,
    smallTapTargets: metrics.smallCount,
    totalInteractive: metrics.totalInteractive,
    smallSamples: metrics.small,
  };
}

for (const surface of SURFACES) {
  for (const vp of VIEWPORTS) {
    test(`${PHASE} ${surface.name} @ ${vp.name}`, async ({ browser }) => {
      const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
      const page = await ctx.newPage();
      try {
        if (surface.requiresAuth) {
          await loginAs(page);
        }
        await page.goto(surface.url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
        // Inject the post-deploy CSS as the LAST stylesheet so it wins specificity ties.
        await page.addStyleTag({ content: INJECTED_MOBILE_CSS });
        await page.waitForTimeout(1500);

        const row = await assess(page, surface.name, vp.name);
        issues.push(row);

        const outPath = path.join(OUT_DIR, `${surface.name}_${vp.name}.png`);
        await page.screenshot({ path: outPath, fullPage: true });
        // eslint-disable-next-line no-console
        console.log(`captured ${outPath} (scroll=${row.scrollWidth}/${row.innerWidth} small=${row.smallTapTargets}/${row.totalInteractive})`);
      } finally {
        await ctx.close();
      }
    });
  }
}
