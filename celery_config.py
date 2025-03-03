import os
from celery import Celery
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

# If Redis is not available, use SQLite as a broker instead
broker_url = os.environ.get('REDIS_URL', 'sqla+sqlite:///celery.db')
result_backend = os.environ.get('REDIS_URL', 'db+sqlite:///celery-results.db')

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

if __name__ == '__main__':
    app.start()