# UTM capture & attribution flow

Tier D6 / A2 — hardened 2026-05-24.

FIESTA captures the five standard `utm_*` query-string parameters at the
edge and persists them at three layers so the funnel dashboard can
answer "which channel drove this signup / payment?" without joins on
volatile click logs.

## The three persistence layers

```
                                  +-------------------------------+
   Ad click lands on /            |  Layer 1: Flask session       |
   ?utm_source=meta&...           |    utm_first_touch (sticky)   |
        |                         |    utm_last_touch (overwrite) |
        v                         +-------------------------------+
   utm_capture._before_request_capture
        |
        | every page render
        v
   utm_first_touch context var --> templates can read first-touch
        |
        | every event emit (auto via payload merge at the call site)
        v
   +-----------------------------------+
   |  Layer 2: events.payload          |
   |    utm_source / utm_medium / ...  |
   |    indexed via                    |
   |    ix_events_utm_source           |
   +-----------------------------------+
        |
        | user signs up (POST /signup)
        v
   +-----------------------------------+
   |  Layer 3: user.utm_* columns      |
   |    set ONCE at signup             |
   |    indexed via ix_user_utm_source |
   +-----------------------------------+
```

## What each layer is for

| Layer                    | Best for                                                |
| ------------------------ | ------------------------------------------------------- |
| Session (Layer 1)        | Per-visit attribution while the user browses anonymously |
| `events.payload` (Layer 2) | Per-event funnel analysis (any user, any time window)    |
| `user.utm_*` (Layer 3)   | Lifetime attribution joined to revenue / churn analytics |

The two database layers are intentionally redundant. Layer 2 makes "what
% of signups came from Meta in May?" a single index seek. Layer 3 makes
"what's the LTV of users we acquired via LinkedIn?" trivial. Together
they avoid a clicks→sessions→signups window-function chain.

## First-touch vs last-touch

The session stores both. **First-touch** is the source of record
(written to `user.utm_*` at signup, used for primary attribution).
**Last-touch** is captured for diagnostics — if a user's last click
before paying is a different source than their first, we surface that
in `events.payload.utm_last_touch_source` so the funnel dashboard can
flag attribution drift.

## What gets captured

Only the five standard UTM params:

- `utm_source` (e.g. `meta`, `linkedin`, `twitter`, `lankatax`)
- `utm_medium` (e.g. `cpc`, `social`, `email`)
- `utm_campaign` (campaign identifier; free text)
- `utm_term` (paid-search keyword)
- `utm_content` (creative variant identifier)

Each value is:

- Stripped of control / non-printable characters
- Capped at 128 chars
- Stored as plain text in the session cookie (no PII issues)

We never persist the full query string, referrer URL, or any header
that could contain PII.

## When is the User row written?

`utm_capture.persist_to_user()` runs inside `fiesta/signup/routes.signup_submit`
just after the new `User` is committed. It writes only:

- Columns that exist on the User model (defensive against drift)
- Values that are currently null on the user row (idempotent — once set,
  the attribution is permanent for that user; future ad clicks do not
  overwrite the lifetime attribution)

The companion migration `migrations/add_utm_columns_to_user.py` adds
the five columns (`VARCHAR(128) NULL`) plus a partial index on
`utm_source` for the channel breakdown query.

## Cross-flow compatibility

`lankatax_onboarding_routes.lankatax_onboarding` (the Lanka.tax 1-click
deep link) was already writing `utm_source` to `CustomerProfile.acquisition_source`
before this work. That path is **untouched**. The two attribution surfaces
coexist:

- `CustomerProfile.acquisition_source` — Lanka.tax cross-sell users only
- `User.utm_*` — every signup that landed via a UTM-tagged URL

If a user lands via Lanka.tax first and pays later via a paid ad click,
both surfaces record their first-touch and we can compare in the funnel
dashboard.

## Querying for channel breakdown

```sql
-- Signups per channel, last 30 days
SELECT
  COALESCE(utm_source, '(organic)') AS channel,
  COUNT(*) AS signups
FROM "user"
WHERE created_at > now() - interval '30 days'
GROUP BY 1
ORDER BY 2 DESC;

-- Per-event funnel by channel
SELECT
  payload->>'utm_source' AS channel,
  event_type,
  COUNT(*) AS events
FROM events
WHERE payload ? 'utm_source'
  AND created_at > now() - interval '7 days'
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
```

The events query is served by the partial index
`ix_events_utm_source` (added 2026-05-24 in `add_utm_source_partial_index.py`).

## Running the migration

The migration is **not** auto-applied on deploy. Trigger it manually:

```
flyctl ssh console -a fiesta-mvp -C 'python migrations/add_utm_columns_to_user.py upgrade'
```

To roll back:

```
flyctl ssh console -a fiesta-mvp -C 'python migrations/add_utm_columns_to_user.py downgrade'
```

## See also

- `utm_capture.py` — middleware + helpers
- `models.py` — `User.utm_source` / `utm_medium` / `utm_campaign` / `utm_term` / `utm_content` columns
- `migrations/add_utm_columns_to_user.py` — column + index migration
- `migrations/add_utm_source_partial_index.py` — events.payload partial index
- `docs/PIXELS.md` — the ad-network pixels these UTMs attribute to
