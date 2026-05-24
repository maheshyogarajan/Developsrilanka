-- funnel_overall.sql
-- Question: For each funnel event type, how many fired in the last 7d / 30d /
--           all-time? This is the single most useful page-1 dashboard tile —
--           one row per event_type with three columns of counts.
-- Reads:    public.events
-- Indexes:  hits ix_events_type_created_at on (event_type, created_at DESC).

SELECT
    event_type,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days')   AS count_7d,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days')  AS count_30d,
    COUNT(*)                                                          AS count_all_time
FROM   events
WHERE  event_type IN (
           'landing_view',
           'cta_click',
           'signup_started',
           'signup_completed',
           'tax_bill_view',
           'audit_view',
           'evidence_uploaded',
           'payment_started',
           'payment_completed'
       )
GROUP  BY event_type
ORDER  BY count_30d DESC, event_type;
