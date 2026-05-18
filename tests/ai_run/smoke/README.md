# AI-Org Subagent Smoke Tests

A reusable end-to-end smoke-test framework for every AI-org subagent.
Built v18.3 (2026-05-18) after a 3-day silent-failure window where
v15→v18.1 thought they were running clean and weren't.

## How to add a smoke test for your new subagent

1. **Create a concrete subclass** at
   `tests/ai_run/smoke/test_<your_subagent>_smoke.py`. Inherit from
   `SubagentSmokeTest` and implement the 4 phases:
   - `emit_synthetic_event(marker)` — drop a recognisable Event row
     (use a unique `smoke_marker` in payload so the assert phase finds
     only this run's rows)
   - `trigger_or_wait(timeout_seconds)` — call the task function
     directly, OR wait for a beat tick (the framework gives you a
     timeout budget)
   - `assert_downstream(marker)` — query the downstream row(s) you
     expected the subagent to produce; raise if absent
   - `cleanup(marker)` — scrub the synthetic rows; respect APPEND-ONLY
     on `reputation_event` (drop/recreate the RULE pattern, see
     `BasicAttributionSmokeTest.cleanup`)

2. **Register the class** with `@register_smoke` so
   `run_all.py` discovers it without manual imports.

3. **Wire it into pytest** by adding `test_<your_subagent>_smoke.py`
   that exercises the class as a pytest unit (see
   `test_attribution_smoke.py` for the 1-test template).

## What this prevents

On 2026-05-15 we shipped v15 with substrate + Subagent B + Subagent C
"working". On 2026-05-18 we discovered the worker process had never
actually executed a single AI-org Celery task — bootstrap was missing
both the model module imports (v18.1 fix) and the Flask app_context
push per task (also v18.1). The substrate was clean, the schema was
clean, every test passed. Nothing was running.

A one-line smoke (`signup with utm_source=lankatax → AttributionLedger
row appears within 6 min`) run as a deploy gate would have caught this
on Day 1 instead of Day 4.

The framework here is that smoke generalised: any subagent that wants
to ship past v19 builds a 4-phase smoke against its own Celery path
and the deploy pipeline runs `python -m tests.ai_run.smoke.run_all`
before promoting the build.

## Running

```bash
# Locally (against live Neon):
python -m tests.ai_run.smoke.run_all

# On Fly post-deploy:
flyctl ssh console --app fiesta-mvp -C \
  "cd /home/runner/workspace && python -m tests.ai_run.smoke.run_all"

# Via pytest (each smoke also runs as a pytest unit):
pytest tests/ai_run/smoke/ -v
```
