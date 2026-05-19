# AI-Org Subagent Deployment Checklist

Before merging any subagent integration commit (v19+):

1. [ ] Subagent has a concrete `SubagentSmokeTest` subclass at
       `tests/ai_run/smoke/test_<subagent_name>_smoke.py`
2. [ ] `python -m tests.ai_run.smoke.run_all` passes locally
3. [ ] After deploy: `flyctl ssh console --app fiesta-mvp -C "cd /home/runner/workspace && python -m tests.ai_run.smoke.run_all"` returns exit 0
4. [ ] Worker heartbeat is still firing (check Telegram for absence of heartbeat alert in the 30 min after deploy)

If any step fails, the deploy is rolled back via `flyctl releases rollback`.
