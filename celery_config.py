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
    include=['image_processor']
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

if __name__ == '__main__':
    app.start()