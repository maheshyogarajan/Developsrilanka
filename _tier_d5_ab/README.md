# Tier D5 / E6 — A/B testing harness

**Date:** 2026-05-24
**Branch:** `tier-d5/e6-ab`

Self-improving conversion needs experiments. This is the minimal, config-driven harness — no code deploy per experiment.

## What ships in this slice

| File | Purpose |
|---|---|
| `ab_test_models.py` | `ABExperiment` + `ABAssignment` ORM models |
| `ab_test.py` | `get_variant(key)` + `register_template_helper(app)` |
| `migrations/add_ab_tests.py` | Idempotent `CREATE TABLE IF NOT EXISTS` |
| `tests/ab_test_module/test_ab_test.py` | 3 pytest cases (determinism, persist, fallback) |
| `main.py` (light edit) | Imports `ab_test_models` for `db.create_all()` + calls `register_template_helper(app)` |

## Out of scope (deferred)

- Admin UI for creating experiments — insert experiments via SQL (see below).
- Bayesian / sequential analysis — CEO eyeballs lift from `/admin/analytics`.
- Mutually-exclusive experiment groups — each experiment is independent.
- Sample / live experiments — none start ticking on deploy. Insert manually.

## How an experiment runs

1. **Insert** the experiment via SQL (see "First experiment" below).
2. **Branch** in a template:
   ```jinja
   {% if ab_variant('s0_hero_color') == 'green' %}
       <button class="btn-green">Start now</button>
   {% elif ab_variant('s0_hero_color') == 'orange' %}
       <button class="btn-orange">Start now</button>
   {% else %}
       <button class="btn-control">Start now</button>
   {% endif %}
   ```
3. **Read** the lift from `events` + `ab_assignment` (analytics SQL pack — see `_tier_c_analytics_sql_pack/`).
4. **Conclude** by setting `status='concluded'` + `winner_variant='green'` on the experiment row. The helper now returns `'control'` for all visitors; existing `ab_assignment` rows remain for audit but stop influencing render.

## CEO action — insert the first experiment

```sql
-- Example: 3-variant hero CTA colour test on screen S0 (landing).
INSERT INTO ab_experiment (key, name, description, variants, status, primary_metric, started_at)
VALUES (
    's0_hero_color',
    'Landing hero CTA colour',
    'Hypothesis: orange outperforms green on first-click rate.',
    '["control", "green", "orange"]'::json,
    'active',
    'calculator_started',
    now() AT TIME ZONE 'utc'
);
```

To conclude:
```sql
UPDATE ab_experiment
SET status = 'concluded',
    winner_variant = 'orange',
    concluded_at = now() AT TIME ZONE 'utc'
WHERE key = 's0_hero_color';
```

## Assignment determinism

`SHA-256(experiment_key + ":" + anon_id) % len(variants)` — same visitor lands in the same bucket on every visit, even before the `ab_assignment` row is persisted. The persisted row exists for analytics + audit, not stickiness.

Visitor identity:
- Authenticated → `"u{user.id}"` (sticky across devices once logged in)
- Anonymous → `session_anon_id` cookie (set by `analytics_beacon_routes`)
- Pre-cookie (rare) → literal `"anon"` (all collide into one bucket — acceptable for the trace volume that lands here pre-cookie)

## Best-effort writes

`get_variant` NEVER raises into a render path. A failed INSERT (UNIQUE race, transient DB issue) is logged at DEBUG and silently swallowed — the deterministic hash guarantees the next request will land in the same bucket, so the row will be written then.

## Tests

```bash
pytest tests/ab_test_module/ -v
```

3 cases:
1. Deterministic assignment — same (key, anon_id) → same variant, 1000 repeats.
2. New visitor + active experiment → declared variant + persisted row.
3. No active experiment → fallback `'control'` + no row written.
