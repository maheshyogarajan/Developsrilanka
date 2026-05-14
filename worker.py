#!/usr/bin/env python
import os
import socket
import logging
import threading
from celery_config import app as celery_app
from celery.signals import worker_ready, worker_shutting_down

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# Heartbeat config: write every HEARTBEAT_INTERVAL_SECONDS seconds.
# The /scan and /admin/celery_health endpoints look for rows updated
# within the last 60s, so 15s gives us a comfortable safety margin
# against transient DB latency without hammering Postgres.
HEARTBEAT_INTERVAL_SECONDS = 15
_heartbeat_stop = threading.Event()
_worker_identity = None


def _resolve_worker_name(sender=None) -> str:
    global _worker_identity
    if _worker_identity:
        return _worker_identity
    # `sender` from worker_ready is a celery.apps.worker.Worker instance
    # whose `.hostname` is the canonical "celery@<host>" identifier. Fall
    # back to str(sender) (Consumer repr) and finally to the OS hostname.
    name = getattr(sender, 'hostname', None)
    if not name and sender is not None:
        name = str(sender)
    if not name:
        name = f"celery@{socket.gethostname()}"
    _worker_identity = name
    return _worker_identity


def _write_heartbeat_once(worker_name: str) -> None:
    """Upsert this worker's heartbeat row inside the Flask app context."""
    from app import app
    from models import WorkerHeartbeat
    with app.app_context():
        WorkerHeartbeat.upsert(worker_name)


def _heartbeat_loop(worker_name: str) -> None:
    """Background thread: refresh the heartbeat row on a fixed cadence.

    The Postgres-backed Celery broker does not support broadcast control
    commands (inspect/ping rely on fanout, which kombu's SQL transport
    doesn't implement), so the web process can't ask the broker "are any
    workers alive?". This loop is our liveness signal: each tick upserts
    `worker_heartbeat.last_seen`, and the web layer treats rows newer
    than 60s as a healthy worker.
    """
    while not _heartbeat_stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        try:
            _write_heartbeat_once(worker_name)
        except Exception as exc:
            logger.warning(f"worker heartbeat upsert failed: {exc}")


@worker_ready.connect
def _on_worker_ready(sender=None, **kwargs):
    worker_name = _resolve_worker_name(sender)
    logger.info(f"Worker ready ({worker_name}) — starting heartbeat loop")
    try:
        _write_heartbeat_once(worker_name)
    except Exception as exc:
        logger.warning(f"initial worker heartbeat upsert failed: {exc}")
    t = threading.Thread(
        target=_heartbeat_loop,
        args=(worker_name,),
        name="worker-heartbeat",
        daemon=True,
    )
    t.start()


@worker_shutting_down.connect
def _on_worker_shutting_down(sender=None, **kwargs):
    """Stop the heartbeat thread and clear our row so health checks fail fast."""
    _heartbeat_stop.set()
    try:
        from app import app, db
        from models import WorkerHeartbeat
        worker_name = _resolve_worker_name(sender)
        with app.app_context():
            WorkerHeartbeat.query.filter_by(worker_name=worker_name).delete()
            db.session.commit()
    except Exception as exc:
        logger.warning(f"worker heartbeat cleanup failed: {exc}")


if __name__ == '__main__':
    logger.info("Starting Celery worker...")
    # Set concurrency based on environment or default to 2
    concurrency = os.environ.get('CELERY_CONCURRENCY', 2)
    
    # Start worker
    argv = [
        'worker',
        '--loglevel=INFO',
        f'--concurrency={concurrency}',
        '--without-gossip',  # Disable gossip for better performance in simple setups
        '--without-mingle',  # Disable mingle which is not needed for simple workers
        '--without-heartbeat',  # Disable heartbeat for better performance
        '--pool=prefork',  # Use prefork pool which is more stable
    ]
    
    celery_app.worker_main(argv)