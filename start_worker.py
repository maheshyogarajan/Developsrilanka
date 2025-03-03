#!/usr/bin/env python
import os
import subprocess
import logging
import sys
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('worker.log')
    ]
)
logger = logging.getLogger(__name__)

def start_celery_worker():
    """Start a Celery worker as a background process."""
    try:
        logger.info("Starting Celery worker as a background process...")
        
        # Set environment variables
        env = os.environ.copy()
        env['CELERY_CONCURRENCY'] = '1'  # Use 1 worker process for simplicity
        env['ENABLE_ASYNC_PROCESSING'] = 'True'
        
        # Build the command
        command = [
            sys.executable,
            'worker.py'
        ]
        
        # Start the process
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"worker_{timestamp}.log"
        
        with open(log_file, 'w') as f:
            process = subprocess.Popen(
                command,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
                start_new_session=True  # Detach the process
            )
        
        logger.info(f"Celery worker started with PID {process.pid}")
        logger.info(f"Logs available at {log_file}")
        
        # Return immediately, letting the worker run in the background
        return process.pid
        
    except Exception as e:
        logger.error(f"Error starting Celery worker: {str(e)}")
        return None

if __name__ == "__main__":
    pid = start_celery_worker()
    if pid:
        print(f"Worker started successfully with PID {pid}")
    else:
        print("Failed to start worker. Check the logs for details.")