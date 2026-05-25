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

# Tier D4 2026-05-24: import feedback_models so the `feedback` table is
# registered with SQLAlchemy metadata for db.create_all(). The table is
# ALSO created via raw SQL in app._ensure_additive_schema() for belt-and-
# braces — that path covers every entry point (gunicorn, wsgi, celery).
try:
    import feedback_models  # noqa: F401
    logger.info("Feedback models loaded (feedback table)")
except Exception as e:
    logger.error(f"Error loading feedback_models: {str(e)}")

# Tier D5 / E6 2026-05-24: A/B testing harness. Import ab_test_models so
# ab_experiment + ab_assignment tables register with SQLAlchemy metadata
# for db.create_all(). Register the {{ ab_variant('experiment_key') }}
# Jinja helper so templates can branch on variant without touching Python.
# Raw DDL backup lives in migrations/add_ab_tests.py.
try:
    import ab_test_models  # noqa: F401
    from ab_test import register_template_helper as register_ab_template_helper
    register_ab_template_helper(app)
    logger.info("A/B testing harness loaded (ab_experiment + ab_assignment + ab_variant helper)")
except Exception as e:
    logger.error(f"Error loading ab_test harness: {str(e)}")

# MS2 E.0 / Design Lock 2 — register canonical tax models (incomes,
# asset_disposals, parsed_bank_statements, rsu_vesting_events) with
# SQLAlchemy metadata so db.create_all() creates them on a fresh DB.
# The same tables are ALSO created via raw DDL in the migration
# (migrations/20260525_130100_e_b8_schema.py) for prod (Postgres on Fly).
try:
    import fiesta.tax.models  # noqa: F401
    logger.info("Canonical tax models loaded (incomes, asset_disposals, parsed_bank_statements, rsu_vesting_events)")
except Exception as e:
    logger.error(f"Error loading fiesta.tax.models: {str(e)}")

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

# S16 — PCSE Markov Inspector routes (/admin/pcse). Kept in its own module
# so it can ship independently of the parallel admin-middleware refactor.
try:
    import pcse_inspector_routes  # noqa: F401
    logger.info("PCSE inspector routes loaded successfully (S16)")
except Exception as e:
    logger.error(f"Error loading PCSE inspector routes: {str(e)}")

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

# Sprint 4 Tier B — client-side analytics beacons (/api/event + session_anon_id cookie)
try:
    from analytics_beacon_routes import register_routes as register_beacon_routes
    register_beacon_routes(app)
    logger.info("Analytics beacon routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading analytics beacon routes: {str(e)}")

# Tier D6 / A2 — UTM capture middleware (first-touch sticky, last-touch overwrite).
# Must register BEFORE the pixels context processor so utm_first_touch is in scope
# when pixels.html renders. See utm_capture.py for the data-flow contract.
try:
    import utm_capture
    utm_capture.register(app)
    logger.info("UTM capture loaded successfully")
except Exception as e:
    logger.error(f"Error loading UTM capture: {str(e)}")

# Tier D6 / A2 — Paid-acquisition pixels (Meta + LinkedIn + Twitter).
# Context processor only — actual pixel JS lives in templates/components/pixels.html.
# Default-OFF behind PIXELS_ENABLED env var; per-network IDs gate each pixel.
try:
    import pixels as _pixels
    _pixels.register(app)
    logger.info("Pixels context processor loaded successfully")
except Exception as e:
    logger.error(f"Error loading pixels: {str(e)}")

# Sprint 4 Tier D4 — in-app feedback widget endpoint (POST /api/feedback)
try:
    from feedback_routes import register_routes as register_feedback_routes
    register_feedback_routes(app)
    logger.info("Feedback routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading feedback routes: {str(e)}")

# Sprint 4 Tier D3 — FAQ / Knowledge Base auto-gen pages.
# Public: /help, /help/<slug>, /sitemap.xml (SEO + LLM citation surface).
# Admin: /admin/faq, /admin/faq/<id>/publish, /admin/faq/<id>/delete
# (admin_required gate applied inside register_routes).
try:
    from faq_routes import register_routes as register_faq_routes
    register_faq_routes(app)
    logger.info("FAQ routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading FAQ routes: {str(e)}")

# Tier D6 / A4 — SEO + Article engine (2026-05-24)
# /articles, /articles/<slug>, /robots.txt + canonical /sitemap.xml that
# extends the FAQ sitemap with article + landing entries. Registered
# AFTER faq_routes so the sitemap override mechanic resolves correctly
# (seo_routes.register_routes() overrides app.view_functions["faq_bp.sitemap_xml"]).
try:
    from seo_routes import register_routes as register_seo_routes
    register_seo_routes(app)
    logger.info(
        "Tier D6 A4 SEO routes registered "
        "(/articles, /articles/<slug>, /robots.txt, /sitemap.xml)"
    )
except Exception as e:
    logger.error(f"Error loading SEO routes: {str(e)}")

# Sprint 4 Tier D3 / D1 — AI Q&A RAG over 41-entry FAQ corpus
# (POST /api/qa + GET /support/qa). TF-IDF retrieval, no LLM calls.
try:
    from qa_routes import register_routes as register_qa_routes
    register_qa_routes(app)
    logger.info("AI Q&A routes loaded successfully")
except Exception as e:
    logger.error(f"Error loading AI Q&A routes: {str(e)}")

# Tier C Wave A — admin-only analytics dashboard (/admin/analytics +
# /admin/analytics/export). Reads from the same `events` table the beacon
# above writes to. Admin-gated via fiesta.auth.decorators.admin_required.
try:
    from analytics_dashboard_routes import register_routes as register_analytics_dashboard_routes
    register_analytics_dashboard_routes(app)
    logger.info("Tier-C analytics dashboard loaded successfully")
except Exception as e:
    logger.error(f"Error loading analytics dashboard routes: {str(e)}")

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

# Tier D4 / C2 — YoY retention nudges (2026-05-24)
# Registers the yoy_nudge ORM model so db.create_all() in this same module
# creates the table on first boot. Beat schedule + Celery tasks live in
# celery_config.py + tasks/yoy_nudges_run.py.
try:
    from yoy_models import register_models as register_yoy_models
    register_yoy_models()
    logger.info("YoY retention nudge model registered (yoy_nudge)")
except Exception as e:
    logger.error(f"Error registering YoY nudges model: {str(e)}")

# Tier D1 C1 — Stripe subscription auto-renew + customer billing portal (2026-05-24)
# Recurring webhooks (invoice.paid / invoice.payment_failed /
# customer.subscription.updated / customer.subscription.deleted) + /billing
# portal redirect. Distinct from the one-time X1 webhook above.
try:
    from webhooks.stripe_subscription import register_routes as register_stripe_subscription
    register_stripe_subscription(app)
    logger.info(
        "Stripe subscription auto-renew registered "
        "(/webhooks/stripe/subscription, /billing)"
    )
except Exception as e:
    logger.error(f"Error loading Stripe subscription: {str(e)}")

# Tier D3 C5 — Dunning recovery (2026-05-24)
# Records Stripe invoice.payment_failed events into paywall_dunning, fires
# Telegram alert to CEO, and injects should_show_dunning_banner into every
# template render so layouts can show a yellow "update your card" banner.
try:
    from dunning_sequence import (
        register_dunning_model, register_context_processor,
    )
    register_dunning_model()
    register_context_processor(app)
    logger.info(
        "Dunning recovery registered (paywall_dunning + banner context proc)"
    )
except Exception as e:
    logger.error(f"Error loading Dunning recovery: {str(e)}")

# Tier D4 / A5 — Lifecycle email drip (2026-05-24)
try:
    from lifecycle_drip_models import register_lifecycle_drip_model
    register_lifecycle_drip_model()
    logger.info("Lifecycle email drip registered (lifecycle_email model)")
except Exception as e:
    logger.error(f"Error loading Lifecycle drip: {str(e)}")

# Tier D4 A3 — One-sided referral loop (2026-05-24)
try:
    from referral_models import register_models as register_referral_models
    register_referral_models()
    from referral_routes import register_routes as register_referral_routes
    register_referral_routes(app)
    logger.info("Referral loop registered (/referrals, /r/<code>)")
except Exception as e:
    logger.error(f"Error loading Referral loop: {str(e)}")

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

# Tier D2 — Lightweight support ticketing (2026-05-24)
# Distinct from Wave 3.2: D2 is a conversation thread (customer <-> staff/AI);
# Wave 3.2 is single-shot Q&A. Coexist on /support prefix with different paths.
try:
    import support_models  # noqa: F401  (registers tables on import)
    import support_tickets_routes
    support_tickets_routes.register_routes(app)
    logger.info(
        "D2 Support Tickets registered at POST /api/support/ticket + "
        "/support/tickets/*"
    )
except Exception as e:
    logger.error(f"Error loading D2 Support Tickets: {str(e)}")

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

# E2 F1.8 — Legal pages (2026-05-22)
# /legal/tos and /legal/privacy rendered in the FIESTA hub shell.
# Placeholder content; counsel review async and non-blocking.
try:
    from fiesta.legal import register_routes as register_legal_routes
    register_legal_routes(app)
    logger.info("Legal blueprint registered: /legal/tos, /legal/privacy")
except Exception as e:
    logger.error(f"Legal blueprint load failed: {e}")

# Tier D5 F5 — Data subject rights (2026-05-24)
# /account/data UI + /api/me/data-export + /api/me/delete.
# Backs PDPA (No. 9 of 2022) + GDPR rights surfaced in privacy_policy.html §5.
try:
    import data_rights_routes
    data_rights_routes.register_routes(app)
    logger.info(
        "data_rights blueprint registered: /account/data, "
        "/api/me/data-export, /api/me/delete"
    )
except Exception as e:
    logger.error(f"data_rights blueprint load failed: {e}")

# Wave 1 — S1 Triage (2026-05-20)
# 3 neutral post-signup fact-finds at /fie/triage; answers persist to
# User.triage_answers (JSON column added by add_triage_answers_to_user.py).
try:
    from fiesta.triage import register_routes as register_triage_routes
    register_triage_routes(app)
    logger.info("S1 Triage blueprint registered: /fie/triage")
except Exception as e:
    logger.error(f"S1 Triage blueprint load failed: {e}")

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

# FIESTA Tier D2-bpdf — IRD-ready tax return PDF download (2026-05-24)
# Sellable without IRD-portal automation (B4/B5/B7 external gates bypassed).
try:
    from fiesta.tax_bill.tax_return_pdf_routes import (
        register_blueprint as register_tax_return_pdf,
    )
    register_tax_return_pdf(app)
    logger.info(
        "FIESTA Tier-D2-bpdf IRD return PDF route registered at "
        "/tax-bill/<tax_year>/return.pdf"
    )
except Exception as e:
    logger.error(f"FIESTA Tier-D2-bpdf IRD return PDF load failed: {e}")

# FIESTA Wave 3 Week 5 — S14 Submit (final gate + IRD-ready export pack)
try:
    from fiesta.submit.routes import register_routes as register_submit
    register_submit(app)
    logger.info('FIESTA S14 Submit registered at /submit')
except Exception as e:
    logger.error(f'FIESTA S14 Submit load failed: {e}')

# X9 F5.1 — S8 Service Agreement + S9 Rental Agreement blueprints (the
# document factory). These were built in Wave 3 but never mounted, so the
# `Generate agreement` CTA's on S6 / S7 fell to 404. Mount them now so the
# entire Generate feature is reachable; F5.6 + F5.7 will add the CTAs.
try:
    from fiesta.agreements.service_routes import register_routes as register_agreements_service
    register_agreements_service(app)
    logger.info('FIESTA S8 Service Agreement registered at /agreements/service')
except Exception as e:
    logger.error(f'FIESTA S8 Service Agreement load failed: {e}')

try:
    # rental_routes.py exposes a module-level Blueprint named `bp` without a
    # register_routes() wrapper, so mount it directly.
    from fiesta.agreements.rental_routes import bp as fiesta_agreements_rental_bp
    app.register_blueprint(fiesta_agreements_rental_bp)
    logger.info('FIESTA S9 Rental Agreement registered at /agreements/rental')
except Exception as e:
    logger.error(f'FIESTA S9 Rental Agreement load failed: {e}')

# X9 F5.1 — S10 Co-sign workflow (Service Provider counter-signing).
try:
    from fiesta.cosign.routes import register_routes as register_cosign
    register_cosign(app)
    logger.info('FIESTA S10 Co-sign workflow registered at /cosign')
except Exception as e:
    logger.error(f'FIESTA S10 Co-sign workflow load failed: {e}')

# FIESTA Feature 9 — Assets & Liabilities declaration tracker (Wave 2, 2026-05-22)
# D6 blueprint + D7 list/edit routes + D8 PDF + D9 FA 5192455 push at /fie/al
try:
    from fiesta.assets_liabilities import models as fiesta_al_models  # noqa: F401
    from fiesta.assets_liabilities import register_routes as register_al_routes
    register_al_routes(app)
    logger.info('FIESTA Feature 9 A&L declaration registered at /fie/al')
except Exception as e:
    logger.error(f'FIESTA Feature 9 A&L load failed: {e}')

# FIESTA Wave 6 — S15 Admin Users list (admin-gated, /admin/fie/users)
#                + S17 Admin Autoreply Queue (/admin/fie/autoreply)
try:
    from fiesta.admin import register_routes as register_fiesta_admin
    register_fiesta_admin(app)
    logger.info('FIESTA S15+S17 Admin (Users + Autoreply Queue) registered at /admin/fie/*')
except Exception as e:
    logger.error(f'FIESTA S15+S17 Admin load failed: {e}')

# FIESTA Wave 6 — X4 Consultant booking (/consultant/book)
try:
    from fiesta.consultant import register_routes as register_consultant
    register_consultant(app)
    logger.info('FIESTA X4 Consultant booking registered at /consultant/book')
except Exception as e:
    logger.error(f'FIESTA X4 Consultant booking load failed: {e}')

# Tier D1 / E1 — Sentry verification route (/sentry-test, admin-only).
# Sentry SDK itself is init'd inside app.py at import time (sentry_init.py);
# this blueprint only exposes the deliberate-exception endpoint used to verify
# Sentry ingestion in production after `flyctl secrets set SENTRY_DSN=...`.
try:
    from sentry_routes import sentry_bp
    app.register_blueprint(sentry_bp)
    logger.info("Tier D1/E1 Sentry verification route registered at /sentry-test")
except Exception as e:
    logger.error(f"Tier D1/E1 Sentry route load failed: {e}")

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
    # v1.0 Auto-File recovery additive migration — idempotent, safe on every boot.
    try:
        from add_autofile_recovery_columns_to_submissions import run as _run_autofile_recovery_migration
        _run_autofile_recovery_migration()
    except Exception as e:
        logger.error(f"Auto-File recovery migration failed (non-fatal — model has ORM-level columns): {e}")

    # S1 triage additive migration — idempotent, safe to run on every boot.
    try:
        from add_triage_answers_to_user import run as _run_triage_migration
        _run_triage_migration()
    except Exception as e:
        logger.error(f"S1 triage migration failed (non-fatal — model has ORM-level column): {e}")

    # Wave 6 admin surface — additive columns for S15+ (is_admin, stripe_customer_id).
    # Idempotent. Safe to re-run on every boot.
    try:
        from add_admin_and_stripe_columns_to_user import run as _run_admin_migration
        _run_admin_migration()
    except Exception as e:
        logger.error(
            f"Wave 6 admin migration failed (non-fatal — decorator + ORM gracefully "
            f"degrade): {e}"
        )

    # Tier D6 / A2 UTM columns — additive ALTER TABLE for user.utm_source +
    # utm_medium + utm_campaign + utm_term + utm_content + partial index.
    # Idempotent. Safe to re-run on every boot. Follows the same auto-apply
    # pattern as the other additive migrations above so a fresh dev DB or
    # test DB gets the columns without manual flyctl invocation.
    try:
        from migrations.add_utm_columns_to_user import upgrade as _run_utm_migration
        _run_utm_migration()
    except Exception as e:
        logger.error(
            f"Tier D6/A2 UTM migration failed (non-fatal — ORM-level columns "
            f"would still surface, just without DB persistence): {e}"
        )

    # Tier D6 / A2 — partial expression index on events.payload->>'utm_source'.
    # Lets the channel-breakdown analytics query the events table without a
    # JSON probe per row. Idempotent CREATE INDEX IF NOT EXISTS.
    try:
        from migrations.add_utm_source_partial_index import upgrade as _run_utm_index_migration
        _run_utm_index_migration()
    except Exception as e:
        logger.error(
            f"Tier D6/A2 UTM events-index migration failed (non-fatal — "
            f"queries still work, just without the partial index): {e}"
        )

    # MS2 E.0 — B8 schema-first / Design Lock 2.
    # Creates incomes, asset_disposals, parsed_bank_statements,
    # rsu_vesting_events tables + adds user.residency_status +
    # user.income_sources + remittance_entries.income_id +
    # backfills one Income row per existing RemittanceEntry.
    # Idempotent + dialect-aware (Postgres prod, SQLite test).
    # NB: file name starts with a digit so we load it by path.
    try:
        import importlib.util as _importlib_util
        from pathlib import Path as _Path
        _b8_spec_path = _Path(__file__).resolve().parent / "migrations" / "20260525_130100_e_b8_schema.py"
        _b8_spec = _importlib_util.spec_from_file_location(
            "_b8_schema_migration_loader", str(_b8_spec_path)
        )
        if _b8_spec is not None and _b8_spec.loader is not None:
            _b8_mod = _importlib_util.module_from_spec(_b8_spec)
            _b8_spec.loader.exec_module(_b8_mod)
            _b8_mod.upgrade()
    except Exception as e:
        logger.error(
            f"MS2 E.0 B8 schema migration failed (non-fatal — ORM-level "
            f"columns + tables still work via db.create_all on fresh DBs): {e}"
        )

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
