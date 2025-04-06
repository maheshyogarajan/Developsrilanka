import os
import logging
import threading
import time
from app import app, db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import models to ensure they're registered with SQLAlchemy
import models

# Import and initialize template filters
try:
    import template_filters
    
    # Make sure Flask's Jinja environment has all our filters
    with app.app_context():
        for filter_name in ['currency', 'percent', 'datetime', 'truncate_text', 'nl2br']:
            if filter_name not in app.jinja_env.filters:
                logger.warning(f"Filter '{filter_name}' not properly registered, manually adding")
                # Add a simple fallback filter if needed
                app.jinja_env.filters[filter_name] = lambda x, *args, **kwargs: str(x)
    
    logger.info("Template filters loaded successfully")
except Exception as e:
    logger.error(f"Error loading template filters: {str(e)}")
    # Create emergency fallback filters
    with app.app_context():
        app.jinja_env.filters['currency'] = lambda x, *args, **kwargs: str(x)
        app.jinja_env.filters['percent'] = lambda x, *args, **kwargs: str(x)
        app.jinja_env.filters['datetime'] = lambda x, *args, **kwargs: str(x)
        app.jinja_env.filters['truncate_text'] = lambda x, *args, **kwargs: str(x)
        app.jinja_env.filters['nl2br'] = lambda x, *args, **kwargs: str(x).replace('\n', '<br>') if x else ''
        logger.info("Registered emergency fallback filters")

# Import admin routes
try:
    import admin_routes
    logger.info("Admin routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading admin routes: {str(e)}")

# Import enhanced admin routes (v2)
try:
    import admin_routes_v2
    logger.info("Enhanced admin routes (v2) loaded successfully")
except Exception as e:
    logger.error(f"Error loading enhanced admin routes (v2): {str(e)}")

# Import invoice management routes
try:
    import invoice_routes
    logger.info("Invoice management routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading invoice management routes: {str(e)}")

# Import bank account management routes
try:
    import bank_account_routes
    # Register bank account routes
    bank_account_routes.register_routes(app)
    logger.info("Bank account management routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading bank account management routes: {str(e)}")

# Import organization management routes
try:
    import organization_routes
    # Register organization routes
    organization_routes.register_routes(app)
    logger.info("Organization management routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading organization management routes: {str(e)}")

# Import expense management routes
try:
    import expense_routes
    # Register expense routes
    expense_routes.register_routes(app)
    logger.info("Expense management routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading expense management routes: {str(e)}")

# Import client expense management routes
try:
    import client_expense_routes
    # Register client expense routes
    client_expense_routes.register_routes(app)
    logger.info("Client expense management routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading client expense management routes: {str(e)}")

# Import client management blueprint
try:
    from blueprints.clients import register_blueprint as register_clients_blueprint
    # Register client blueprint
    register_clients_blueprint(app)
    logger.info("Client management blueprint registered successfully")
except Exception as e:
    logger.error(f"Error registering client management blueprint: {str(e)}")

# Import receipt classification routes
try:
    import classify_routes
    # Register receipt classification routes
    classify_routes.register_routes(app)
    logger.info("Receipt classification routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading receipt classification routes: {str(e)}")

# Import enhanced receipt classification routes
try:
    import enhanced_classify_routes
    # Register enhanced receipt classification routes
    enhanced_classify_routes.register_routes(app)
    logger.info("Enhanced receipt classification routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading enhanced receipt classification routes: {str(e)}")

# Import unified receipt and expense views
try:
    import unified_receipt_expense_routes
    # Register unified receipt and expense view routes
    unified_receipt_expense_routes.register_routes(app)
    logger.info("Unified receipt and expense view routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading unified receipt and expense view routes: {str(e)}")

# Import API routes
try:
    import api_routes
    logger.info("API routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading API routes: {str(e)}")

# Create database tables when the application starts
with app.app_context():
    db.create_all()

# Function to start Celery worker in a background thread
def start_background_worker():
    try:
        from start_worker import start_celery_worker
        logger.info("Starting Celery worker thread...")
        worker_pid = start_celery_worker()
        logger.info(f"Celery worker started with PID: {worker_pid}")
    except Exception as e:
        logger.error(f"Error starting background worker: {str(e)}")

if __name__ == "__main__":
    # Start Celery worker in a background thread if ENABLE_ASYNC_PROCESSING is True
    if os.environ.get('ENABLE_ASYNC_PROCESSING', 'True').lower() in ('true', '1', 'yes'):
        worker_thread = threading.Thread(target=start_background_worker)
        worker_thread.daemon = True  # Ensure the thread terminates when the main process does
        worker_thread.start()
        logger.info("Worker thread started")
        
        # Give the worker a moment to start up
        time.sleep(2)
    
    # Get environment
    env = os.environ.get('FLASK_ENV', 'production')
    debug_mode = env == 'development'
    
    # Start the Flask application
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
