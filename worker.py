#!/usr/bin/env python
import os
import logging
from celery_config import app as celery_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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