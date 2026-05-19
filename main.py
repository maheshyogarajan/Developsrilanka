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

# Wave 1 EVENT SPINE 2026-05-17 (council #2): import event_models so the
# `events` table is registered with SQLAlchemy metadata for db.create_all().
# The table is ALSO created via raw SQL in app._ensure_additive_schema() for
# belt-and-braces — that path covers every entry point (gunicorn, wsgi, celery).
try:
    import event_models  # noqa: F401
    logger.info("Event spine models loaded (events table)")
except Exception as e:
    logger.error(f"Error loading event_models: {str(e)}")

# Import model event listeners (for auto-creating Personal Finances organization)
try:
    import models_event_listener
    logger.info("Model event listeners loaded successfully")
except Exception as e:
    logger.error(f"Error loading model event listeners: {str(e)}")

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

# Note: Admin routes v2 has been consolidated into admin_routes.py

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

# Import team management routes
try:
    from blueprints.team import register_routes as register_team_routes
    # Register team management routes
    register_team_routes(app)
    logger.info("Team management routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading team management routes: {str(e)}")

# Import Getting Started wizard routes
try:
    import getting_started
    # Register Getting Started routes
    getting_started.register_routes(app)
    logger.info("Getting Started wizard routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading Getting Started wizard routes: {str(e)}")

# Register Expense Reports routes
try:
    import expense_reports
    expense_reports.register_routes(app)
    logger.info("Expense Reporting routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading Expense Reporting routes: {str(e)}")

# Register Expense Pipeline blueprint
try:
    from expense_pipeline import pipeline_bp
    app.register_blueprint(pipeline_bp)
    logger.info("Expense Pipeline blueprint registered successfully")
except Exception as e:
    logger.error(f"Error registering Expense Pipeline blueprint: {str(e)}")

# Import accounting routes
try:
    import accounts_routes
    # Register accounting routes
    accounts_routes.register_routes(app)
    logger.info("Accounting routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading accounting routes: {str(e)}")

# Import enhanced bank statement routes
try:
    from enhanced_bank_statement_routes import enhanced_bank
    app.register_blueprint(enhanced_bank)
    logger.info("Enhanced bank statement routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading enhanced bank statement routes: {str(e)}")

# Import PDF lineage API routes
try:
    from lineage_api_routes import lineage_api
    app.register_blueprint(lineage_api)
    logger.info("PDF lineage API routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading PDF lineage API routes: {str(e)}")

# Import foreign-income remittance routes (Wave A 2026-05-16)
try:
    import remittance_routes
    remittance_routes.register_routes(app)
    logger.info("Remittance (foreign-income) routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading remittance routes: {str(e)}")

# X2 Persona switch (Wave 3, 2026-05-19) — top-bar cross-screen pill, v1 self-locked.
try:
    from fiesta.persona import routes as persona_routes
    from fiesta.persona.models import current_persona as _cp
    persona_routes.register_routes(app)

    @app.context_processor
    def _inject_persona_switcher_label():
        """Make persona_switcher_current_label available on every page."""
        try:
            from flask_login import current_user
            if not getattr(current_user, "is_authenticated", False):
                return {}
            p = _cp(current_user)
            return {"persona_switcher_current_label": p.display_label if p else "Self (you)"}
        except Exception:
            return {"persona_switcher_current_label": "Self (you)"}

    logger.info("X2 Persona routes + context processor registered at /persona/*")
except Exception as e:
    logger.error(f"Error loading X2 persona module: {str(e)}")

# Wave 2.1 — Revenue Intelligence Dashboard (2026-05-17)
try:
    import revenue_intel
    revenue_intel.register_routes(app)
    logger.info("Revenue Intelligence dashboard registered at /admin/revenue")
except Exception as e:
    logger.error(f"Error loading Revenue Intel: {str(e)}")

# Wave 2.2 — Pricing Engine + Stripe webhook (2026-05-17)
try:
    import stripe_routes
    stripe_routes.register_routes(app)
    logger.info("Pricing + Stripe webhook registered")
except Exception as e:
    logger.error(f"Error loading Pricing/Stripe: {str(e)}")

# Wave 2.3 — AI CRM / Customer Memory (2026-05-17)
try:
    import customer_brain_routes
    customer_brain_routes.register_routes(app)
    logger.info("Customer Brain (AI CRM) registered at /admin/customer")
except Exception as e:
    logger.error(f"Error loading Customer Brain: {str(e)}")

# Wave 2.4 — Ops Sentinel (2026-05-17)
try:
    import ops_routes
    ops_routes.register_routes(app)
    logger.info("Ops Sentinel registered at /internal/ops")
except Exception as e:
    logger.error(f"Error loading Ops Sentinel: {str(e)}")

# Also import the modules so their Celery tasks register (decorators run on import)
try:
    import ai_crm  # noqa: F401  (registers ai_crm.recompute_all_active_profiles task)
    import ops_sentinel  # noqa: F401  (registers ops_sentinel.run_and_alert task)
    import gemini_cost_log_model  # noqa: F401  (registers GeminiCostLog model)
    logger.info("AI-run Celery tasks + cost-log model loaded")
except Exception as e:
    logger.error(f"Error loading AI-run module imports: {str(e)}")

# Wave 3.1 — Proactive Engagement Engine (2026-05-17/18)
try:
    import engagement_models  # noqa: F401  (registers InAppBanner table)
    import engagement_engine  # noqa: F401  (registers Celery task)
    import in_app_nudge_routes
    in_app_nudge_routes.register_routes(app)
    logger.info("Engagement Engine + in-app nudge routes registered")
except Exception as e:
    logger.error(f"Error loading Engagement Engine: {str(e)}")

# Wave 3.2 — AI Support Copilot (2026-05-18)
try:
    import support_copilot_models  # noqa: F401
    import support_copilot  # noqa: F401
    import support_routes
    support_routes.register_routes(app)
    logger.info("AI Support Copilot registered at /support + /admin/support")
except Exception as e:
    logger.error(f"Error loading Support Copilot: {str(e)}")

# Wave 3.3 — Lanka.tax Cross-Sell (2026-05-18)
try:
    import lankatax_models  # noqa: F401
    import lankatax_crosssell  # noqa: F401  (registers Celery task)
    import lankatax_onboarding_routes
    lankatax_onboarding_routes.register_routes(app)
    logger.info("Lanka.tax Cross-Sell + /onboarding/lankatax registered")
except Exception as e:
    logger.error(f"Error loading Lanka.tax Cross-Sell: {str(e)}")

# AI-Org Subagent A — Data Substrate (2026-05-18)
try:
    import ai_org_models  # noqa: F401  (8 tables incl APPEND-ONLY reputation_event)
    import ai_org_substrate  # noqa: F401  (helpers + EVENT_AXIS_MAP)
    logger.info('AI-Org substrate loaded (8 tables, APPEND-ONLY ledger)')
except Exception as e:
    logger.error(f'AI-Org substrate load failed: {e}')

# AI-Org Subagent B — Attribution Writer + Audit (2026-05-18)
try:
    import ai_org_attribution_writer  # noqa: F401  (Celery task)
    import ai_org_audit_harness  # noqa: F401
    import ai_org_audit_routes
    ai_org_audit_routes.register_routes(app)
    logger.info('AI-Org attribution writer + audit harness registered')
except Exception as e:
    logger.error(f'AI-Org attribution load failed: {e}')

# AI-Org Subagent C — Score Engine (2026-05-18)
try:
    import ai_org_score_engine  # noqa: F401  (Celery task)
    import ai_org_score_routes
    ai_org_score_routes.register_routes(app)
    logger.info('AI-Org Score Engine + dashboards registered')
except Exception as e:
    logger.error(f'AI-Org Score Engine load failed: {e}')

# AI-Org Subagent D — Acquisition Studio (2026-05-18)
try:
    import acquisition_studio_org  # noqa: F401  (Celery task: run_pass)
    import acquisition_studio_proposals  # noqa: F401
    import acquisition_studio_routes
    acquisition_studio_routes.register_routes(app)
    logger.info('AI-Org Acquisition Studio (Subagent D) registered')
except Exception as e:
    logger.error(f'AI-Org Acquisition Studio load failed: {e}')

# AI-Org Subagent E — Delivery Ops Command (2026-05-18)
try:
    import delivery_ops_command_org  # noqa: F401  (Celery task: run_pass)
    import delivery_ops_command_proposals  # noqa: F401
    import delivery_ops_command_routes
    delivery_ops_command_routes.register_routes(app)
    logger.info('AI-Org Delivery Ops Command (Subagent E) registered')
except Exception as e:
    logger.error(f'AI-Org Delivery Ops Command load failed: {e}')

# Create database tables when the application starts.
# Note: additive schema fixes (e.g. organization.ocr_provider) are applied
# inside app.py at app-init time so every entry point — gunicorn, wsgi.py,
# the Celery worker — runs them, not just `python main.py`.
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
