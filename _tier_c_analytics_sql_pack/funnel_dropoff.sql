-- funnel_dropoff.sql
-- Question: For each consecutive pair of funnel steps, how many distinct
--           visitors reached the first step IN ORDER and then reached the
--           second step strictly AFTER the first?
--           Window: last 30 days. Anchored on anon_id so logged-out -> signup
--           transitions are captured.
-- Reads:    public.events
-- Indexes:  ix_events_anon_created_at + ix_events_type_created_at.
--
-- Funnel order (5 transitions):
--   landing_view      -> signup_started
--   signup_started    -> signup_completed
--   signup_completed  -> tax_bill_view
--   tax_bill_view     -> payment_started
--   payment_started   -> payment_completed
--
-- v2 SEQUENCING (was: set-membership v1)
-- ---------------------------------------------------------------------
-- v1 answered "ever-reached": a visitor counted as having reached step
-- N+1 if they fired that event ANYWHERE in the 30-day window, even if
-- it came BEFORE step N. That mis-classified out-of-order behaviour
-- (e.g. guest checkout firing payment_started before signup_completed)
-- as a successful transition.
--
-- v2 answers "reached in order": a visitor counts as having transitioned
-- from step N to step N+1 if their FIRST occurrence of step N+1 is
-- strictly AFTER their FIRST occurrence of step N. Per-anon ordering is
-- computed with ROW_NUMBER() OVER (PARTITION BY anon_id, event_type
-- ORDER BY created_at), keeping rn=1 to materialise each visitor's
-- earliest timestamp per step. Visitors who never fired step N are
-- excluded from the denominator for that transition.

WITH funnel_events AS (
    SELECT
        event_type,
        created_at,
        COALESCE(
            events.session_anon_id,
            events.payload->>'session_anon_id'
        ) AS anon_id
    FROM events
    WHERE created_at >= NOW() - INTERVAL '30 days'
      AND event_type IN (
          'landing_view',
          'signup_started',
          'signup_completed',
          'tax_bill_view',
          'payment_started',
          'payment_completed'
      )
),
first_event_per_anon AS (
    -- Earliest timestamp per (anon_id, event_type). Null anon_ids are
    -- excluded — they can't be sequenced across events.
    SELECT
        anon_id,
        event_type,
        created_at AS first_at
    FROM (
        SELECT
            anon_id,
            event_type,
            created_at,
            ROW_NUMBER() OVER (
                PARTITION BY anon_id, event_type
                ORDER BY created_at
            ) AS rn
        FROM funnel_events
        WHERE anon_id IS NOT NULL
    ) ranked
    WHERE rn = 1
),
step_pairs(from_step, to_step, step_order) AS (
    VALUES
        ('landing_view',     'signup_started',   1),
        ('signup_started',   'signup_completed', 2),
        ('signup_completed', 'tax_bill_view',    3),
        ('tax_bill_view',    'payment_started',  4),
        ('payment_started',  'payment_completed',5)
),
transition_counts AS (
    SELECT
        sp.from_step,
        sp.to_step,
        sp.step_order,
        COUNT(DISTINCT f_from.anon_id) AS entered_from_step,
        COUNT(DISTINCT f_from.anon_id) FILTER (
            WHERE f_to.first_at IS NOT NULL
              AND f_to.first_at > f_from.first_at
        ) AS reached_to_step
    FROM       step_pairs sp
    LEFT JOIN  first_event_per_anon f_from
        ON f_from.event_type = sp.from_step
    LEFT JOIN  first_event_per_anon f_to
        ON f_to.anon_id   = f_from.anon_id
       AND f_to.event_type = sp.to_step
    GROUP BY sp.from_step, sp.to_step, sp.step_order
)
SELECT
    from_step,
    to_step,
    entered_from_step,
    reached_to_step,
    entered_from_step - reached_to_step AS dropped,
    ROUND(
        100.0 * reached_to_step / NULLIF(entered_from_step, 0),
        2
    ) AS pct_reached_to_step
FROM   transition_counts
ORDER  BY step_order;
