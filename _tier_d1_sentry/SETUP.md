# Tier D1 / E1 — Sentry error tracking — SETUP

Wires Sentry into the FIESTA Flask app so unhandled exceptions and 10% of
request perf-traces flow to a Sentry project automatically. Local dev is a
silent no-op (no DSN, no SDK init). Production turns on the moment the
`SENTRY_DSN` secret is set on Fly.

## What ships in code

- `sentry-sdk[flask]>=2.18.0` added to `pyproject.toml`.
- `sentry_init.py` — `init_sentry()` reads `SENTRY_DSN` from env; no-op if unset.
  Configured: `traces_sample_rate=0.1`, `profiles_sample_rate=0.0`,
  `send_default_pii=False`, `environment=$FLASK_ENV` (default `production`),
  `release=$FLY_RELEASE_VERSION` (default `dev`).
- `app.py` calls `init_sentry()` once, immediately after logging is configured
  and before Flask app creation, so `FlaskIntegration` + `SqlalchemyIntegration`
  hook the WSGI lifecycle before any route runs.
- `sentry_routes.py` exposes `GET /sentry-test`, admin-gated via
  `fiesta.auth.decorators.admin_required`. It raises `SentryVerificationError`
  deliberately so Sentry has something to ingest.
- `main.py` registers the `sentry_bp` blueprint inside a try/except, matching
  the existing blueprint-registration pattern.

## What the CEO needs to do (one-time, ~3 minutes)

### 1. Create the Sentry project

Open https://sentry.io/organizations/ — pick the FIESTA / Smarter Tax org
(create a free org if none exists). Then:

    Projects -> Create Project -> Platform: Python -> Framework: Flask
    Project name: fiesta-mvp
    Alert frequency: default

Copy the DSN from the "Configure SDK" screen. It looks like:

    https://abc123def@o0000000.ingest.sentry.io/0000000

### 2. Set the DSN as a Fly secret

    flyctl secrets set SENTRY_DSN="https://abc123def@o0000000.ingest.sentry.io/0000000" --app fiesta-mvp

(Fly auto-restarts the app on secret change. Wait ~30s for the new boot.)

### 3. Trigger the verification route

Log into FIESTA as an admin user, then visit:

    https://<fiesta-prod-host>/sentry-test

You'll see a 500 page. That's intentional — the route raises on purpose.
Within ~5 seconds the matching event appears in the Sentry project inbox as
`SentryVerificationError: Deliberate /sentry-test exception ...`.

Resolve / archive the event in Sentry once you've confirmed ingestion.

### 4. Optional — mute periodic verification noise

If you want to trigger `/sentry-test` on a schedule (smoke check) without
flooding the inbox, add a Sentry alert filter excluding events where
`error.type = SentryVerificationError`.

## How to disable Sentry temporarily

    flyctl secrets unset SENTRY_DSN --app fiesta-mvp

The init is gated on DSN presence, so the SDK falls back to a no-op. No code
change required.

## What's intentionally NOT in scope

- Custom breadcrumb instrumentation (`FlaskIntegration` + `SqlalchemyIntegration`
  cover ~90% of useful trail data on their own).
- Profile sampling (`profiles_sample_rate=0.0`) — kept off for cost control.
- User PII (`send_default_pii=False`) — emails, IPs, cookies never leave the
  process. Re-enable per release if you ever need user-keyed grouping.
- Higher `traces_sample_rate` — 10% is the recommended sweet spot for
  low-volume apps; raise only if perf-trace coverage becomes thin.
