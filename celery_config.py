import os
from celery import Celery
from celery.schedules import crontab
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Broker / result-backend selection (in priority order):
#   1. REDIS_URL — preferred when available (lowest latency, best for prod)
#   2. DATABASE_URL — Postgres-backed broker via SQLAlchemy. Works across
#      separate web + worker processes on Reserved VM deployments because
#      both processes already share the project's Postgres database.
#   3. SQLite on local filesystem — dev fallback only. NEVER use this in
#      production: autoscale and multi-process deployments don't share the
#      file, so the worker won't see tasks the web process enqueues.
def _build_broker_urls():
    redis_url = os.environ.get('REDIS_URL')
    if redis_url:
        return redis_url, redis_url
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        # Normalize postgres:// (legacy) to postgresql:// for SQLAlchemy
        if db_url.startswith('postgres://'):
            db_url = 'postgresql://' + db_url[len('postgres://'):]
        return f'sqla+{db_url}', f'db+{db_url}'
    return 'sqla+sqlite:///celery.db', 'db+sqlite:///celery-results.db'

broker_url, result_backend = _build_broker_urls()

app = Celery(
    'develop_sri_lanka',
    broker=broker_url,
    backend=result_backend,
    include=[
        'image_processor',
        # Wave 2 tasks
        'ai_crm',
        'ops_sentinel',
        # Wave 3 tasks
        'engagement_engine',
        'lankatax_crosssell',
        # AI-Org Subagents B + C (2026-05-18)
        'ai_org_attribution_writer',
        'ai_org_score_engine',
        # AI-Org Subagent D — Acquisition Studio (v17)
        'acquisition_studio_org',
        # AI-Org Subagent E — Delivery Ops Command (v18)
        'delivery_ops_command_org',
        # ─────────────────────────────────────────────────────────────
        # v18.1 BOOTSTRAP FIX: model-only modules that have no Celery
        # tasks but MUST be loaded before lazy imports inside tasks try
        # to reach them. Worker never runs main.py, so these don't load
        # otherwise. Bug: 10:55 UTC 2026-05-18 — process_recent_events
        # raised ModuleNotFoundError: event_models.
        # ─────────────────────────────────────────────────────────────
        'event_models',
        'ai_org_models',
        'models',
        'lankatax_models',
        # ─────────────────────────────────────────────────────────────
        # v18.2 BOOTSTRAP FIX: helper / substrate modules lazy-imported
        # by task code. Same root-cause class as v18.1 — modules not in
        # include list aren't loaded into worker process; lazy imports
        # fail with ModuleNotFoundError despite being on filesystem.
        # Surfaced 2026-05-18 12:45 UTC when attribution writer reached
        # event 671 and failed on `from ai_org_substrate import ...`.
        # ─────────────────────────────────────────────────────────────
        # Substrate / business-logic helpers
        'ai_org_substrate',              # attribution_writer L238, acquisition_studio_org L372, delivery_ops_command_org L351
        'ai_org_audit_harness',          # ai_org_score_engine L265 (audit_metrics)
        'acquisition_studio_proposals',  # acquisition_studio_org L376
        'delivery_ops_command_proposals', # delivery_ops_command_org L252, L355
        'events',                        # ops_sentinel L618/L676, engagement_engine L624, ai_org_score_engine L416
        # Additional model modules surfaced during v18.2 sweep
        'remittance_models',             # ai_crm L253/L340, engagement_engine L185
        'engagement_models',             # engagement_engine L542 (InAppBanner)
        'gemini_cost_log_model',         # ops_sentinel L178 (GeminiCostLog)
        # ─────────────────────────────────────────────────────────────
        # v18.3 OPERATIONAL HARDENING: worker heartbeat (silent-failure
        # detector). Telegram alerts CEO if no AI-org task has succeeded
        # in the last 60 min (outside quiet window 22-07 UTC). Catches
        # the 2026-05-15→18 failure mode where the worker was up but
        # every task was silently erroring.
        # ─────────────────────────────────────────────────────────────
        'worker_heartbeat',
        # D5 / F-Feature-3.7 — CBSL daily rate pre-fetch
        'tasks.cbsl_rate_fetch',
        # Tier D1 / F1 — Daily Fly PG backup → Tigris
        'tasks.pg_backup',
        # Tier D1 / E2 — Telegram ops alerts probes (2026-05-24)
        'tasks.ops_probes',
    ]
)

# Configure Celery
app.conf.update(
    result_expires=3600,  # results expire after 1 hour
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max runtime per task
    worker_max_tasks_per_child=200,  # restart workers after 200 tasks
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
    task_acks_late=True,  # ack tasks after they're completed for better reliability
)

# Automatically retry failed tasks
app.conf.task_default_retry_delay = 30  # 30 seconds
app.conf.task_max_retries = 3  # retry 3 times max

# AI-run beat schedule (Wave 2 council #2 — 2026-05-17)
app.conf.beat_schedule = {
    'ai_crm-recompute-nightly': {
        'task': 'ai_crm.recompute_all_active_profiles',
        'schedule': crontab(hour=2, minute=0),  # 02:00 UTC daily
    },
    'ops_sentinel-every-5min': {
        'task': 'ops_sentinel.run_and_alert',
        'schedule': crontab(minute='*/5'),
    },
}

# Wave 3 additions (council #2 — 2026-05-18)
app.conf.beat_schedule.update({
    'engagement_engine-run-pass': {
        'task': 'engagement_engine.run_pass',
        'schedule': crontab(minute=0),  # top of every hour
    },
    'lankatax_crosssell-daily': {
        'task': 'lankatax_crosssell.daily_pulse',
        'schedule': crontab(hour=7, minute=0),  # 07:00 UTC daily
    },
})

# AI-Org Subagents B + C additions (2026-05-18)
app.conf.beat_schedule.update({
    'ai_org_attribution-every-5min': {
        'task': 'ai_org_attribution_writer.process_recent',
        'schedule': crontab(minute='*/5'),
        'kwargs': {'since_minutes': 15},
    },
    'ai_org_score_engine-recompute-nightly': {
        'task': 'ai_org_score_engine.recompute_nightly',
        'schedule': crontab(hour=3, minute=0),  # 03:00 UTC daily, no clash with ai_crm@02:00 or lankatax@07:00
    },
})

# AI-Org Subagent D — Acquisition Studio (v17, 2026-05-18)
app.conf.beat_schedule.update({
    'acquisition_studio-hourly': {
        'task': 'acquisition_studio_org.run_pass',
        'schedule': crontab(minute=17),  # hourly at :17, offset from other AI-org tasks
    },
})

# AI-Org Subagent E — Delivery Ops Command (v18, 2026-05-18)
app.conf.beat_schedule.update({
    'delivery_ops_command-every-10min': {
        'task': 'delivery_ops_command_org.run_pass',
        'schedule': crontab(minute='*/10'),  # every 10 min, offset from acquisition's :17
        'kwargs': {'since_minutes': 15},
    },
})

# v18.3 — worker heartbeat (silent-failure detector)
app.conf.beat_schedule.update({
    'worker-heartbeat-30min': {
        'task': 'worker_heartbeat.check_and_alert',
        'schedule': crontab(minute='*/30'),
    },
})

# D5 / F-Feature-3.7 — CBSL daily rate pre-fetch
# Runs at 07:30 UTC = ~13:00 SL, after CBSL publishes same-day rates.
# Populates cbsl_rates table so /remittance/new can auto-fill the rate field.
# Task wraps itself in app_context (see fetch_today_task in tasks/cbsl_rate_fetch.py).
app.conf.beat_schedule.update({
    'cbsl-rate-daily-prefetch': {
        'task': 'tasks.cbsl_rate_fetch.fetch_today_task',
        'schedule': crontab(hour=7, minute=30),  # 07:30 UTC daily
    },
})

# Tier D1 / F1 (2026-05-24) — Daily Fly Postgres backup → Tigris.
# fiesta-pg-bom is unmanaged: no automatic snapshots. This task dumps
# the cluster nightly via pg_dump --format=custom + uploads to a Tigris
# S3-compatible bucket. Retention 14 daily + 12 monthly, pruned in-task.
# Schedule: 20:30 UTC = 02:00 IST = lowest-traffic window.
# Env requirements + DR runbook: _tier_d1_pg_backup/DR_RUNBOOK.md
app.conf.beat_schedule.update({
    'pg_backup-daily-2030-utc': {
        'task': 'tasks.pg_backup.daily_backup_task',
        'schedule': crontab(hour=20, minute=30),  # 02:00 IST daily
    },
})

# Tier D1 / E2 — Telegram ops alerts probes (2026-05-24)
# Healthz probe runs every 60s, latency probe every 5min, signup-drop
# probe daily at 09:00 IST = 03:30 UTC. All three route to the CEO via
# ops_alerts.send_alert (one-way Telegram). Per-alert dedup window is
# 10 min so cascading failures don't spam.
app.conf.beat_schedule.update({
    'ops-probe-healthz-every-60s': {
        'task': 'tasks.ops_probes.healthz_probe',
        'schedule': 60.0,  # every 60 seconds (float schedule)
    },
    'ops-probe-latency-every-5min': {
        'task': 'tasks.ops_probes.latency_probe',
        'schedule': crontab(minute='*/5'),
    },
    'ops-probe-signup-drop-daily': {
        'task': 'tasks.ops_probes.signup_drop_probe',
        # 09:00 IST = 03:30 UTC (IST is UTC+5:30)
        'schedule': crontab(hour=3, minute=30),
    },
})

# ─────────────────────────────────────────────────────────────────────────────
# v18.1 BOOTSTRAP FIX: push Flask app_context for every Celery task.
#
# Background: the Celery worker process does NOT run main.py — it imports
# celery_config + the modules in `include`. Tasks that touch the database
# need a pushed Flask app_context, but nothing was pushing one. Result:
# every db.session / .query call inside a task raised "Working outside of
# application context" (visible in ops_sentinel logs 2026-05-18 10:55 UTC).
#
# Tasks that already wrap themselves in `with flask_app.app_context():`
# (Subagents C, D, E, and the new compliance_brigade in v19) continue to
# work — the inner push is a no-op because there's already a context
# pushed by this signal handler. Defence in depth.
#
# Subagents B (attribution_writer) and ops_sentinel did NOT self-wrap and
# this signal handler is what makes them work post-v18.1.
# ─────────────────────────────────────────────────────────────────────────────
from celery.signals import task_prerun, task_postrun, worker_process_init

_pushed_contexts = {}  # task_id -> pushed Flask app context (one per concurrent task)


@worker_process_init.connect
def _worker_process_init(**kwargs):
    """Forked worker process boots → import the Flask app so the model
    metadata + extensions are all loaded before tasks fire. The actual
    app_context push happens per-task in task_prerun below.
    """
    try:
        import app as _app_module  # noqa: F401
        logger.info("v18.1 worker bootstrap: Flask app module imported")
    except Exception as e:
        logger.error(f"v18.1 worker bootstrap: failed to import app module: {e}")


@task_prerun.connect
def _push_app_context_for_task(task_id, task, *args, **kwargs):
    """Push a Flask app_context for the duration of a task. Stored per
    task_id so concurrent tasks each get their own push/pop pair.
    """
    try:
        from app import app as flask_app
        ctx = flask_app.app_context()
        ctx.push()
        _pushed_contexts[task_id] = ctx
    except Exception as e:
        logger.warning(
            f"v18.1 task_prerun: failed to push app_context for task "
            f"{task.name if task else '?'} (id={task_id}): {e}"
        )


@task_postrun.connect
def _pop_app_context_for_task(task_id, task, *args, **kwargs):
    """Pop the context pushed by task_prerun. Best-effort — if push
    failed, pop silently no-ops.
    """
    ctx = _pushed_contexts.pop(task_id, None)
    if ctx is not None:
        try:
            ctx.pop()
        except Exception as e:
            logger.warning(
                f"v18.1 task_postrun: failed to pop app_context for task "
                f"{task.name if task else '?'} (id={task_id}): {e}"
            )


if __name__ == '__main__':
    app.start()