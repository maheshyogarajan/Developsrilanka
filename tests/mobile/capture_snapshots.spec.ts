/**
 * Tier C-5 — Mobile responsiveness audit snapshot capture.
 *
 * Captures full-page PNGs of 4 surfaces × 4 viewports = 16 images
 * to either `_tier_c_mobile_audit/before/` or `_tier_c_mobile_audit/after/`
 * controlled by env MOBILE_AUDIT_PHASE=before|after (default: before).
 *
 * Also records observed issues (no horizontal scroll, all tap targets ≥ 44px)
 * into `_tier_c_mobile_audit/issues_<phase>.json`.
 *
 * Run from tests/playwright:
 *   BASE_URL=https://fiesta-mvp.fly.dev MOBILE_AUDIT_PHASE=before \
 *     npx playwright test ../mobile/capture_snapshots.spec.ts --config ./playwright.config.ts
 */
import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { loginAs } from '../playwright/smoke/helpers';

const PHASE = (process.env.MOBILE_AUDIT_PHASE || 'before').toLowerCase();
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const OUT_DIR = path.join(REPO_ROOT, '_tier_c_mobile_audit', PHASE);
const ISSUES_FILE = path.join(REPO_ROOT, '_tier_c_mobile_audit', `issues_${PHASE}.json`);

if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

const VIEWPORTS = [
  { name: '320', width: 320, height: 568 },   // iPhone SE 1st gen
  { name: '375', width: 375, height: 667 },   // iPhone SE 2/3
  { name: '414', width: 414, height: 896 },   // iPhone 11 / 14 Pro Max-ish
  { name: '768', width: 768, height: 1024 },  // iPad portrait / small tablet
];

const SURFACES: Array<{ name: string; url: string; requiresAuth: boolean }> = [
  { name: 's0_landing',         url: '/',                  requiresAuth: false },
  { name: 's5_hub',             url: '/',                  requiresAuth: true  },
  { name: 'tax_bill',           url: '/tax-bill/25-26',    requiresAuth: true  },
  { name: 'agreements_service', url: '/agreements/service', requiresAuth: true  },
];

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
      // Ignore hidden / display:none / zero-size (the page's not actually showing them)
      if (cs.display === 'none' || cs.visibility === 'hidden' || (r.width === 0 && r.height === 0)) continue;
      // If the element is an input wrapped by a <label> ancestor, the LABEL is
      // the real tap target — measure the label instead. Apple HIG considers
      // the label's bounding box because tapping anywhere on it activates the input.
      if (el.tagName.toLowerCase() === 'input') {
        const lbl = el.closest('label');
        if (lbl) {
          const lr = lbl.getBoundingClientRect();
          if (lr.width >= 44 && lr.height >= 44) continue;  // OK via label
        }
      }
      // type="hidden" inputs aren't tap targets
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
    surface,
    viewport: vp,
    horizontalScroll: metrics.scrollWidth > metrics.innerWidth + 1,  // tolerate 1px rounding
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
        // Settle: wait for fonts + late layout
        await page.waitForTimeout(1500);

        const row = await assess(page, surface.name, vp.name);
        issues.push(row);

        const outPath = path.join(OUT_DIR, `${surface.name}_${vp.name}.png`);
        await page.screenshot({ path: outPath, fullPage: true });
        // eslint-disable-next-line no-console
        console.log(`captured ${outPath} (scroll=${row.scrollWidth}/${row.innerWidth} tap_targets_small=${row.smallTapTargets}/${row.totalInteractive})`);
      } finally {
        await ctx.close();
      }
    });
  }
}
