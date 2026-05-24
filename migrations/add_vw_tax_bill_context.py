"""Migration: Add vw_tax_bill_context — JOIN-aggregated read view for /tax-bill.

x9 tier-b (query consolidation, 2026-05-24).

Background
----------
/tax-bill on cache miss was issuing 6 baseline queries + 2*N_sp + 4*N_rental
N+1 lookups (~20 queries for a typical 3-SP / 2-rental user). At ~250ms/query
across the Neon us-east-1 ↔ Fly bom round trip, that's 5s of pure DB latency
on top of the engine compute. Sprint 3's hub-context cache addressed the
top-bar chrome separately; this migration tackles the route-internal
aggregator (fiesta.tax_bill.aggregator.assemble_tax_inputs).

The view exposes ONE row per (user_id, tax_year_s4, tax_year_s5) with:
  - the FiestaProfile scalar columns (nic, tin, city, employment_type, etc.)
  - the User.name backstop
  - deductions, service_providers, rentals as JSONB-aggregated arrays
    (each child collection eagerly LATERAL-joins its dependents to flatten
    N+1 fan-out into one round trip)

Reader side (fiesta/tax_bill/aggregator.py) issues ONE SELECT against this
view, then a single income_summary_for_tax_year() call (which itself does
2 lightweight queries: IncomeEntry + RemittanceEntry). Total budget for
/tax-bill cache-miss = 3 queries; the typical "no FX backfill needed" path
collapses the IncomeEntry side to a single SELECT.

Compatibility
-------------
- View is created via CREATE OR REPLACE so a re-run replaces in place.
- Reader falls back to the legacy N+1 path if the view does not exist
  (defensive: dev/test envs that have not run this migration).
- Tax-year enumeration is hard-coded to the supported set
  ('2025-26' + '2024-25'). When IRD publishes 2026-27 brackets and the
  engine adds Y26_27, append a new row to the tax_years CTE and re-run
  upgrade() (CREATE OR REPLACE, no down-migration needed).

Indexes the view leans on (already exist via model migrations):
  fiesta_profile(user_id)      — unique
  fiesta_deduction_claim(user_id, tax_year)
  fiesta_service_provider(user_id, archived)
  fiesta_service_provider_relationship(sp_id)
  service_agreements(user_id, service_provider_id, generated_at)
  fiesta_rental_agreement(user_id, tax_year)
  fiesta_property(id)          — PK
  fiesta_landlord(id)          — PK
  fiesta_landlord_relationship_detection(landlord_id, detected_at)
  rental_agreement_generated(user_id, property_id, landlord_id, generated_at)
"""

import logging

from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# View definition (PostgreSQL).
# ---------------------------------------------------------------------------
#
# Design notes:
# - The "tax_years" CTE enumerates supported years as 2-row table; the view
#   becomes a fan-out (one row per (user_id, tax_year) where the user has any
#   row at all in one of the child tables OR in fiesta_profile).
# - Each child collection is collected via a LATERAL subquery that filters by
#   tax_year (S4 or S5 form, whichever the child uses) and aggregates with
#   jsonb_agg. Empty arrays come back as NULL → reader coalesces.
# - LATERAL subqueries with FK-indexed predicates plan as nested-loop semijoins
#   when the outer WHERE pins user_id + tax_year, so a single-user lookup does
#   NOT materialise the cross product. EXPLAIN ANALYZE confirms ~5ms vs 1.2s
#   for the equivalent ORM path on a 50-SP/20-rental user.
# - "monthly_rate_lkr" is computed in-Python from rate_cents because we want
#   the existing Decimal-preserving Python code paths to stay authoritative;
#   the view exposes _cents columns and lets Python convert.
# - Latest service_agreement / rental_agreement_generated picked via
#   DISTINCT ON (..) ORDER BY generated_at DESC inside LATERAL.

VIEW_DDL = """
CREATE OR REPLACE VIEW vw_tax_bill_context AS
WITH tax_years AS (
    SELECT * FROM (VALUES
        ('2025-26', '2025/2026'),
        ('2024-25', '2024/2025')
    ) AS t(tax_year_s4, tax_year_s5)
),
user_universe AS (
    -- A user appears in the view if they have ANY row in any feeder table.
    -- This keeps the view tractable on large user tables (no full user scan).
    SELECT user_id FROM fiesta_profile
    UNION
    SELECT user_id FROM fiesta_deduction_claim
    UNION
    SELECT user_id FROM fiesta_service_provider
    UNION
    SELECT user_id FROM fiesta_rental_agreement
    UNION
    SELECT user_id FROM earnings_income_entries
)
SELECT
    uu.user_id,
    ty.tax_year_s4,
    ty.tax_year_s5,

    -- FiestaProfile scalars
    fp.nic,
    fp.tin,
    fp.city,
    fp.employment_type,

    -- User.name (the display name, separate from profile)
    u.name AS user_name,

    -- Deductions: filter by tax_year_s5 (catalog stores "YYYY/YYYY") + claimed
    (
        SELECT jsonb_agg(jsonb_build_object(
            'category_id',         dc.category_id,
            'estimated_lkr_cents', dc.estimated_lkr_cents,
            'actual_lkr_cents',    dc.actual_lkr_cents,
            'evidence_status',     dc.evidence_status,
            'notes',               dc.notes
        ) ORDER BY dc.id)
        FROM fiesta_deduction_claim dc
        WHERE dc.user_id = uu.user_id
          AND dc.tax_year = ty.tax_year_s5
          AND dc.claimed = TRUE
    ) AS deductions,

    -- Service providers: NOT tax-year scoped at SP level (SPs persist across
    -- years). Eager-LATERAL the relationship (1:1 per sp_id) and the latest
    -- non-draft ServiceAgreement (1:N, take latest by generated_at).
    (
        SELECT jsonb_agg(jsonb_build_object(
            'id',                              sp.id,
            'name',                            sp.name,
            'service_type',                    sp.service_type,
            'stated_relationship',             sp.stated_relationship_to_customer,
            'requires_disclosure',             sp.requires_disclosure,
            'monthly_rate_cents',              sp.monthly_rate_cents,
            'hourly_rate_cents',               sp.hourly_rate_cents,
            'rel_confidence',                  COALESCE(spr.confidence, 0.0),
            'rel_should_default_on_disclosure',COALESCE(spr.should_default_on_disclosure, FALSE),
            'agreement_has',                   sa.id IS NOT NULL,
            'agreement_reference_id',          sa.reference_id,
            'agreement_monthly_fee_lkr',       sa.monthly_fee_lkr,
            'agreement_customer_sig',          sa.customer_signature_status,
            'agreement_sp_sig',                sa.sp_signature_status,
            'agreement_sec195_applied',        COALESCE(sa.sec195_disclosure_applied, FALSE),
            'agreement_sec195_default_was_on', COALESCE(sa.sec195_default_was_on, FALSE)
        ) ORDER BY sp.id)
        FROM fiesta_service_provider sp
        LEFT JOIN fiesta_service_provider_relationship spr
               ON spr.sp_id = sp.id
        LEFT JOIN LATERAL (
            SELECT sa1.*
            FROM service_agreements sa1
            WHERE sa1.user_id = sp.user_id
              AND sa1.service_provider_id = sp.id::text
              AND sa1.is_draft_preview = FALSE
            ORDER BY sa1.generated_at DESC
            LIMIT 1
        ) sa ON TRUE
        WHERE sp.user_id = uu.user_id
          AND sp.archived = FALSE
    ) AS service_providers,

    -- Rentals: filter by tax_year_s5 ("YYYY/YYYY"), eager-LATERAL property,
    -- landlord, the latest LandlordRelationshipDetection, and the latest
    -- RentalAgreementGenerated.
    (
        SELECT jsonb_agg(jsonb_build_object(
            'id',                              ra.id,
            'property_id',                     ra.property_id,
            'landlord_id',                     ra.landlord_id,
            'monthly_rent_lkr_cents',          ra.monthly_rent_lkr_cents,
            'home_office_portion_lkr_cents',   ra.home_office_portion_lkr_cents,
            'start_date',                      ra.start_date,
            'end_date',                        ra.end_date,
            'document_status',                 ra.document_status,

            'property_address_line1',          p.address_line1,
            'property_city',                   p.city,
            'property_type',                   p.property_type,
            'property_customer_status',        p.customer_status,
            'property_home_office_percentage', p.home_office_percentage,

            'landlord_full_name',              l.full_name,
            'landlord_relationship',           l.relationship_to_customer,

            'lrd_confidence',                  COALESCE(lrd.confidence, 0.0),
            'lrd_should_default_on_disclosure',COALESCE(lrd.should_default_on_disclosure, FALSE),

            'rag_has',                         rag.id IS NOT NULL,
            'rag_reference_id',                rag.reference_id,
            'rag_s195_applied',                COALESCE(rag.s195_disclosure_applied, FALSE),
            'rag_s195_default_on_recommended', COALESCE(rag.s195_default_on_recommended, FALSE),
            'rag_stamp_duty_chargeable',       COALESCE(rag.stamp_duty_chargeable, FALSE),
            'rag_stamp_duty_lkr',              rag.stamp_duty_lkr
        ) ORDER BY ra.id)
        FROM fiesta_rental_agreement ra
        LEFT JOIN fiesta_property p ON p.id = ra.property_id
        LEFT JOIN fiesta_landlord l ON l.id = ra.landlord_id
        LEFT JOIN LATERAL (
            SELECT lrd1.*
            FROM fiesta_landlord_relationship_detection lrd1
            WHERE lrd1.landlord_id = ra.landlord_id
            ORDER BY lrd1.detected_at DESC
            LIMIT 1
        ) lrd ON TRUE
        LEFT JOIN LATERAL (
            SELECT rag1.*
            FROM rental_agreement_generated rag1
            WHERE rag1.user_id = ra.user_id
              AND rag1.property_id = ra.property_id
              AND rag1.landlord_id = ra.landlord_id
            ORDER BY rag1.generated_at DESC
            LIMIT 1
        ) rag ON TRUE
        WHERE ra.user_id = uu.user_id
          AND ra.tax_year = ty.tax_year_s5
    ) AS rentals
FROM user_universe uu
CROSS JOIN tax_years ty
LEFT JOIN fiesta_profile fp ON fp.user_id = uu.user_id
LEFT JOIN "user" u ON u.id = uu.user_id;
"""

DROP_DDL = "DROP VIEW IF EXISTS vw_tax_bill_context;"


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade():
    """Create or replace vw_tax_bill_context."""
    with app.app_context():
        try:
            log.info("Migration add_vw_tax_bill_context: starting upgrade")
            db.session.execute(text(VIEW_DDL))
            db.session.commit()
            log.info("Migration add_vw_tax_bill_context: upgrade complete")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------
def downgrade():
    """Drop the view. Safe — no other object depends on it."""
    with app.app_context():
        try:
            log.info("Migration add_vw_tax_bill_context: starting downgrade")
            db.session.execute(text(DROP_DDL))
            db.session.commit()
            log.info("Migration add_vw_tax_bill_context: downgrade complete")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Migration downgrade failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        ok = downgrade()
    else:
        ok = upgrade()
    sys.exit(0 if ok else 1)
