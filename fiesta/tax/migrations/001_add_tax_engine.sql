-- fiesta/tax/migrations/001_add_tax_engine.sql
-- ------------------------------------------------------------------
-- Tax engine Phase 1 is OFFLINE-COMPUTABLE. The engine itself (brackets,
-- relief, engine.py) has NO database dependency — it is a pure function
-- consumed by S0 estimator (no persistence), S12 tax bill (server-side
-- compute, render-time only) and S14 Auto-File (passes engine output to
-- automation_runner; the runner already has its own task table).
--
-- This migration is OPTIONAL. It adds a tax_computation_audit table that
-- callers MAY use if they want a server-side audit row per computation
-- (e.g. for S12 "show me what I computed at signup vs at file-time" diff,
-- or for support to reproduce a customer's number from inputs).
--
-- DECISION (Phase 1): the migration is SHIPPED but NOT WIRED. The engine
-- does not write to it. Wave 3 S12 build will decide whether to persist
-- computations to this table or to recompute from stored inputs on every
-- render. Either is defensible; the question is a UX/perf call, not a
-- correctness call.
--
-- Phase 2 (foreign income) MAY require persistence of remittance audit
-- trails — that decision deferred to Phase 2 spec.
-- ------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tax_computation_audit (
    id                          BIGSERIAL PRIMARY KEY,
    customer_id                 BIGINT NOT NULL,                 -- FK to customer table (no constraint here — FIESTA customer model not in scope of this migration)
    tax_year                    VARCHAR(8)  NOT NULL,            -- e.g. '24_25', '25_26'
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    engine_version              VARCHAR(16) NOT NULL DEFAULT 'phase1',

    -- Inputs (denormalised so audit is replayable)
    income_employment_lkr       NUMERIC(18, 2) NOT NULL DEFAULT 0,
    income_business_lkr         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    income_foreign_lkr          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    income_rental_lkr           NUMERIC(18, 2) NOT NULL DEFAULT 0,
    income_fd_interest_lkr      NUMERIC(18, 2) NOT NULL DEFAULT 0,
    income_investment_lkr       NUMERIC(18, 2) NOT NULL DEFAULT 0,
    income_other_lkr            NUMERIC(18, 2) NOT NULL DEFAULT 0,

    deduction_solar_lkr         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    deduction_rent_lkr          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    deduction_expenditure_lkr   NUMERIC(18, 2) NOT NULL DEFAULT 0,

    senior_citizen              BOOLEAN NOT NULL DEFAULT FALSE,

    -- Computed outputs (Decimal preserved as NUMERIC for replay accuracy)
    gross_income_lkr            NUMERIC(18, 2) NOT NULL,
    relief_total_lkr            NUMERIC(18, 2) NOT NULL,
    taxable_income_lkr          NUMERIC(18, 2) NOT NULL,
    gross_tax_lkr               NUMERIC(18, 2) NOT NULL,
    net_tax_due_lkr             NUMERIC(18, 2) NOT NULL,
    marginal_rate               NUMERIC(6, 4)  NOT NULL,
    effective_rate              NUMERIC(6, 4)  NOT NULL,

    -- Audit-trail JSON (per-band breakdown)
    by_band_json                JSONB NOT NULL,

    -- Optional notes (e.g. "computed at S12 render", "S14 pre-fill")
    context                     VARCHAR(64),

    CONSTRAINT tax_computation_audit_year_chk
        CHECK (tax_year IN ('24_25', '25_26'))
);

CREATE INDEX IF NOT EXISTS idx_tax_computation_audit_customer_year
    ON tax_computation_audit (customer_id, tax_year, computed_at DESC);

-- ------------------------------------------------------------------
-- ROLLBACK
-- ------------------------------------------------------------------
-- DROP INDEX IF EXISTS idx_tax_computation_audit_customer_year;
-- DROP TABLE IF EXISTS tax_computation_audit;
