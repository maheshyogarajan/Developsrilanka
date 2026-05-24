# Tier C6 — Latency Measurements (Neon vs Fly bom)

Captured 2026-05-24 during prep. All samples from this worktree (Mumbai-ish
network). Live prod is Neon us-east-1; new target is Fly Postgres in `bom`.

## Current state (Neon us-east-1, live prod)

### `/healthz` — no DB roundtrip, just app liveness probe
Base TTFB (network + Gunicorn route resolution):

| sample | TTFB (s) |
|---|---|
| 1 | 0.897 |
| 2 | 0.894 |
| 3 | 0.884 |
| 4 | 0.937 |
| 5 | 0.901 |

Median: **~0.90s**

### `/health` — one `SELECT 1` against Neon (representative DB roundtrip)

| sample | TTFB (s) |
|---|---|
| 1 | 3.039 |
| 2 | 3.079 |
| 3 | 1.978 |
| 4 | 2.006 |
| 5 | 3.104 |

Median: **~3.04s** (range 2.0-3.1s)

### DB-only contribution (Neon roundtrip)
`/health` − `/healthz` ≈ **1.1 to 2.2 seconds per single-query request**.

This matches the task brief's "2.5s latency floor" — a request that hits
the DB once already burns most of the response budget on Neon round-trips.

## Target state (Fly Postgres in bom — dry-run, no app cutover yet)

Measured by SSHing into the fiesta-pg-bom machine and running psql against
itself over Flycast (the same network path app→pg uses post-cutover).

### Per-connection cost (no pool — each `psql` spawns a fresh handshake)
Connect + auth + query + disconnect:

| sample | total (ms) |
|---|---|
| 1 | 1234 |
| 2 |  999 |
| 3 | 1001 |
| 4 |  991 |
| 5 | 1011 |

Median: ~1.0s — dominated by TLS handshake. Not representative; real
fiesta-mvp uses SQLAlchemy connection pooling.

### Per-query cost over Flycast (persistent connection, realistic)

| sample | Time (ms) |
|---|---|
| 1 | 0.565 |
| 2 | 0.351 |
| 3 | 0.349 |
| 4 | 0.262 |
| 5 | 0.281 |

Median: **~0.35ms per query** (range 0.26-0.57ms)

## Expected post-cutover delta for `/health`

| route | Neon (live now) | Fly bom (projected) | delta |
|---|---|---|---|
| `/healthz` (no DB) | ~0.90s | ~0.90s | 0 (no DB) |
| `/health` (1 SELECT 1) | ~3.04s | ~0.90s + ~0.001s ≈ 0.90s | **−2.1s (−70%)** |
| `/tax-bill` (Tier B, ~4 queries via vw_tax_bill_context cache) | est. 4-6s (CEO-reported) | est. ~1.0s | **−3-5s (−75%)** |

**Conclusion:** target of <0.5s for DB roundtrip portion is comfortably met;
the 2.5s latency floor on DB-touching routes drops to ~0ms (DB) + base
network TTFB (~0.9s) post-cutover.

## Caveats

1. /tax-bill projected number is an extrapolation — Tier B's
   vw_tax_bill_context caches the heavy query, so it's likely already
   "1-2 DB hits per request" rather than dozens. CEO/Tier B should
   measure actual /tax-bill TTFB in a logged-in session post-cutover.
2. Connection-pool warmup matters: cold workers (first request after a
   fresh deploy) pay the per-connection ~1s cost ONCE per Gunicorn
   worker; subsequent requests in the same worker reuse the pooled
   connection. With `gunicorn -w 4 --preload` (per fly.toml), 4 workers
   × 1s = 4s of cumulative cold cost, amortised across thousands of
   requests.
3. Numbers above are point estimates with n=5; not statistical proof.
   Cutover runbook must re-measure 10+ samples post-cutover.
4. Network jitter between my worktree (Sri Lanka residential) and Fly
   bom adds 200-400ms variance to all "live prod" numbers — Mumbai is
   geographically close so the floor here is real-world realistic, but
   Neon us-east-1 traffic does cross continents.
