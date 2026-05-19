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

# Wave 3 / S4 — Connect-earnings 'drop in statements' screen (2026-05-20)
try:
    import fiesta.earnings.models  # noqa: F401  (registers Statement + IncomeEntry tables)
    from fiesta.earnings.routes import register_routes as register_earnings_routes
    register_earnings_routes(app)
    logger.info("Earnings (S4) routes registered at /earnings/*")
except Exception as e:
    logger.error(f"Error loading earnings routes: {str(e)}")

# FIESTA S6 — "Your support team — Service Providers" (Wave 3, 2026-05-20)
try:
    from fiesta.service_providers import models as fiesta_sp_models  # noqa: F401
    from fiesta.service_providers import routes as fiesta_sp_routes
    fiesta_sp_routes.register_blueprint(app)
    logger.info("FIESTA S6 service-providers screen registered at /service-providers")
except Exception as e:
    logger.error(f"FIESTA S6 service-providers load failed: {e}")

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

# Wave 2 X1 — Paywall trigger (cross-cutting, 2026-05-20)
# Decorator + Self-File product + idempotent Stripe webhook for S6-S14 gating.
try:
    from fiesta.paywall import register_routes as register_paywall_routes
    register_paywall_routes(app)
    logger.info("Paywall X1 trigger registered (/pricing/x1, /webhooks/stripe/paywall)")
except Exception as e:
    logger.error(f"Error loading Paywall X1: {str(e)}")

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

# Wave 1 — S2 Signup (2026-05-20)
# FIESTA-branded zero-friction signup at /signup, with ToS/Privacy gate.
# Lives alongside the existing /register flow in app.py.
try:
    from fiesta.signup import register_routes as register_signup_routes
    register_signup_routes(app)
    logger.info("S2 Signup blueprint registered: /signup, /terms, /privacy")
except Exception as e:
    logger.error(f"S2 Signup blueprint load failed: {e}")

# Wave 3 S3 — Progressive customer profile (2026-05-20)
try:
    from fiesta.profile.routes import register_blueprint as register_fiesta_profile
    register_fiesta_profile(app)
    logger.info("Wave 3 S3 — fiesta_profile blueprint registered at /fiesta/profile")
except Exception as e:
    logger.error(f"Error loading fiesta_profile (S3): {str(e)}")

# FIESTA S5 — "Reduce your tax — 10 ways" (Wave 3, 2026-05-20)
try:
    from fiesta.deductions import models as fiesta_deductions_models  # noqa: F401
    from fiesta.deductions import routes as fiesta_deductions_routes
    fiesta_deductions_routes.register_blueprint(app)
    logger.info("FIESTA S5 deductions screen registered at /reduce-tax")
except Exception as e:
    logger.error(f"FIESTA S5 deductions load failed: {e}")

# Wave 3 S7 — Property Owner (2026-05-20)
try:
    from fiesta.property import models as fiesta_property_models  # noqa: F401
    from fiesta.property import routes as fiesta_property_routes
    fiesta_property_routes.register_blueprint(app)
    logger.info("FIESTA S7 property screen registered at /property")
except Exception as e:
    logger.error(f"FIESTA S7 property load failed: {e}")

# FIESTA S12 — Your tax bill (outcome + audit trail) — 2026-05-20
try:
    from fiesta.tax_bill.routes import register_blueprint as register_tax_bill
    register_tax_bill(app)
    logger.info("FIESTA S12 tax-bill screen registered at /tax-bill")
except Exception as e:
    logger.error(f"FIESTA S12 tax-bill load failed: {e}")

# FIESTA Wave 3 Week 5 — S14 Submit (final gate + IRD-ready export pack)
try:
    from fiesta.submit.routes import register_routes as register_submit
    register_submit(app)
    logger.info('FIESTA S14 Submit registered at /submit')
except Exception as e:
    logger.error(f'FIESTA S14 Submit load failed: {e}')

# Create database tables when the application starts.
# Note: additive schema fixes (e.g. organization.ocr_provider) are applied
# inside app.py at app-init time so every entry point — gunicorn, wsgi.py,
# the Celery worker — runs them, not just `python main.py`.
with app.app_context():
    db.create_all()
    # S2 signup additive migration — idempotent, safe to run on every boot.
    try:
        from add_tos_privacy_acceptance_to_user import run as _run_tos_migration
        _run_tos_migration()
    except Exception as e:
        logger.error(f"S2 signup migration failed (non-fatal — model has ORM-level columns): {e}")

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
