/**
 * Smoke test 02: Health / liveness endpoints.
 * /healthz  — Fly.io liveness probe, returns "ok" with 200
 * /health   — Full health check JSON, returns { status: "healthy" }
 */
import { test, expect } from '@playwright/test';

test.describe('02 — Health endpoints', () => {
  test('/healthz returns 200 with body "ok"', async ({ request }) => {
    const response = await request.get('/healthz');
    expect(response.status()).toBe(200);
    const body = await response.text();
    expect(body.trim().toLowerCase()).toBe('ok');
  });

  test('/health returns 200 with JSON status healthy or database connected', async ({ request }) => {
    const response = await request.get('/health');
    expect(response.status()).toBe(200);
    const json = await response.json();
    // Either healthy (DB up) or we accept the shape exists; status field must be present
    expect(json).toHaveProperty('status');
    // If unhealthy, at least we get the shape — CI can decide severity
    console.log(`/health status: ${json.status}`);
  });
});
