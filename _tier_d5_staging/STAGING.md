# FIESTA Staging Environment

**Tier D5 / F2** — Separate Fly app for pre-prod testing.
**Created:** 2026-05-24 (forked main @ f52158d, branch `tier-d5/f2-staging`)

---

## What it is

- **App:** `fiesta-mvp-staging` (Fly.io, personal org, region `bom`)
- **URL:** https://fiesta-mvp-staging.fly.dev
- **Postgres:** `fiesta-pg-staging` (separate cluster, 5GB volume, 1 machine)
- **DB attach:** `DATABASE_URL` secret already set on staging app
- **Config file:** `fly.staging.toml` (sibling of prod `fly.toml`)
- **VM footprint:** `shared-cpu-1x` / 512MB across all process groups
- **Min machines:** 1 (cold start tolerated; prod uses 2)
- **Gunicorn:** `-w 2` (no `--preload`; prod uses `-w 4 --preload` on 1024MB)

## Cost estimate

Roughly **$10/month** while running:
- App VM (shared-cpu-1x / 512MB) ≈ $5/mo
- Postgres VM (shared-cpu-1x / 512MB) + 5GB volume ≈ $5/mo

`auto_stop_machines = true` lets the staging app suspend when idle (lower than $5 if traffic is sporadic). Postgres stays warm.

To pay $0, tear down (see bottom).

## Deploy procedure

From the worktree root:

```bash
flyctl deploy --remote-only --config fly.staging.toml
```

Watch logs:

```bash
flyctl logs -a fiesta-mvp-staging
```

Smoke test:

```bash
curl -fsS https://fiesta-mvp-staging.fly.dev/healthz
```

Should return 200 OK once the machine is healthy.

## Run a migration on staging

Same pattern as prod, just with `-a fiesta-mvp-staging`:

```bash
flyctl ssh console -a fiesta-mvp-staging -C "bash -c 'cd /app && PYTHONPATH=/app python migrations/<your_migration>.py'"
```

Or, for the unified Alembic runner if present:

```bash
flyctl ssh console -a fiesta-mvp-staging -C "bash -c 'cd /app && PYTHONPATH=/app alembic upgrade head'"
```

## Seed staging with dogfood data

**TODO:** No `scripts/seed_staging.py` exists yet. When built, run:

```bash
flyctl ssh console -a fiesta-mvp-staging -C "bash -c 'cd /app && PYTHONPATH=/app python scripts/seed_staging.py'"
```

Seed should create: 1-2 admin users, 1-2 orgs, a handful of sample receipts/expenses, a sample bank statement. **NO data sync from prod** (privacy + cost).

## Secrets — CEO actions required

Staging needs **test-mode** Stripe keys (NOT live). Set them once:

```bash
flyctl secrets set STRIPE_SECRET_KEY=sk_test_... -a fiesta-mvp-staging
flyctl secrets set STRIPE_PUBLISHABLE_KEY=pk_test_... -a fiesta-mvp-staging
flyctl secrets set STRIPE_WEBHOOK_SECRET=whsec_test_... -a fiesta-mvp-staging
```

Get the test keys from https://dashboard.stripe.com/test/apikeys (toggle "Test mode" on).

Other secrets the app likely needs (audit by running `flyctl secrets list -a fiesta-mvp` and replicating the relevant ones to staging with test/staging values):

- `SENDGRID_API_KEY` — use a separate staging-only key or rate-limited sandbox key
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` — share with prod OK (read-only API calls), or use separate staging keys for cost isolation
- `S3_*` / `AWS_*` — point to a staging bucket, NOT prod bucket (avoid mixing test uploads with real receipts)
- `SENTRY_DSN` — separate staging Sentry project (or omit)
- `SECRET_KEY` / `CSRF_*` — generate fresh values for staging (do NOT reuse prod)

List current prod secrets to inventory:

```bash
flyctl secrets list -a fiesta-mvp
```

Then replicate the needed ones to staging with staging-appropriate values.

## Custom domain

**None.** Staging uses `*.fly.dev` only — no DNS, no certs, no risk of confusion with prod.

## Data sync

**Disallowed.** Staging Postgres is independent. No automated prod→staging clone (PII + cost). If you need realistic data, seed with synthetic via `scripts/seed_staging.py` (TODO above).

## Tear down (to stop billing)

```bash
flyctl apps destroy fiesta-mvp-staging --yes
flyctl apps destroy fiesta-pg-staging --yes
```

This removes the app, the Postgres cluster, and all data on the staging volume. Irreversible.

## Quick reference — created artifacts

| What | Value |
|---|---|
| Staging app | `fiesta-mvp-staging` |
| Staging URL | https://fiesta-mvp-staging.fly.dev |
| Postgres cluster | `fiesta-pg-staging` |
| Postgres internal host | `fiesta-pg-staging.flycast:5432` |
| DB name | `fiesta_mvp_staging` |
| DB user | `fiesta_mvp_staging` |
| DATABASE_URL on app | set (visible via `flyctl secrets list -a fiesta-mvp-staging`) |
| Config file | `fly.staging.toml` |
| Region | `bom` (Mumbai) |
| Org | `personal` |

## Differences from prod (cheat sheet)

| Setting | Prod | Staging |
|---|---|---|
| app name | `fiesta-mvp` | `fiesta-mvp-staging` |
| FLASK_ENV | (unset, defaults to production) | `staging` |
| VM size (app) | `shared-cpu-2x` / 1024MB | `shared-cpu-1x` / 512MB |
| VM size (worker/beat) | `shared-cpu-1x` / 512MB | `shared-cpu-1x` / 512MB |
| min_machines_running | 2 | 1 |
| auto_stop_machines | false | true |
| gunicorn | `-w 4 --preload` | `-w 2` |
| Postgres | `fiesta-pg-bom` | `fiesta-pg-staging` |
| Mounts | (none in current fly.toml) | (none) |
| Domain | TBD | `*.fly.dev` only |
