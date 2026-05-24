-- funnel_dropoff.sql
-- Question: For each consecutive pair of funnel steps, how many distinct
--           visitors entered the first step but never reached the second?
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
-- "Reached" = visitor fired the later event AT ANY POINT in the 30-day
-- window (NOT strictly after the earlier event; sequencing per session is a
-- v2 question. For launch-day measurement, set membership is the right
-- abstraction — it answers "of the people who saw the landing, how many
-- ever started signup?" which is what CEO actually needs.)

WITH visitors_by_step AS (
    SELECT
        event_type,
        ARRAY_AGG(DISTINCT COALESCE(
            events.session_anon_id,
            events.payload->>'session_anon_id'
        )) FILTER (WHERE COALESCE(
            events.session_anon_id,
            events.payload->>'session_anon_id'
        ) IS NOT NULL) AS anon_ids
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
    GROUP BY event_type
),
step_pairs(from_step, to_step) AS (
    VALUES
        ('landing_view',     'signup_started'),
        ('signup_started',   'signup_completed'),
        ('signup_completed', 'tax_bill_view'),
        ('tax_bill_view',    'payment_started'),
        ('payment_started',  'payment_completed')
)
SELECT
    sp.from_step,
    sp.to_step,
    COALESCE(array_length(vf.anon_ids, 1), 0) AS entered_from_step,
    COALESCE(
        array_length(
            ARRAY(SELECT UNNEST(vf.anon_ids) INTERSECT SELECT UNNEST(vt.anon_ids)),
            1
        ),
        0
    ) AS reached_to_step,
    COALESCE(array_length(vf.anon_ids, 1), 0)
        - COALESCE(
            array_length(
                ARRAY(SELECT UNNEST(vf.anon_ids) INTERSECT SELECT UNNEST(vt.anon_ids)),
                1
            ),
            0
        )
    AS dropped,
    ROUND(
        100.0 * COALESCE(
            array_length(
                ARRAY(SELECT UNNEST(vf.anon_ids) INTERSECT SELECT UNNEST(vt.anon_ids)),
                1
            ),
            0
        ) / NULLIF(COALESCE(array_length(vf.anon_ids, 1), 0), 0),
        2
    ) AS pct_reached_to_step
FROM       step_pairs                     sp
LEFT JOIN  visitors_by_step AS vf ON vf.event_type = sp.from_step
LEFT JOIN  visitors_by_step AS vt ON vt.event_type = sp.to_step
ORDER  BY
    CASE sp.from_step
        WHEN 'landing_view'     THEN 1
        WHEN 'signup_started'   THEN 2
        WHEN 'signup_completed' THEN 3
        WHEN 'tax_bill_view'    THEN 4
        WHEN 'payment_started'  THEN 5
    END;
