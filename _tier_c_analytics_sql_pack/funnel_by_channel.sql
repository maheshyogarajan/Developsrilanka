-- funnel_by_channel.sql
-- Question: Split the funnel by acquisition channel. For each (channel,
--           event_type) pair return last-7d, last-30d, all-time counts.
--           Channel = utm_source if present in the beacon payload, else the
--           Referer's hostname (the client beacon writes a `client_referrer`
--           into payload), else 'direct'.
-- Reads:    public.events
-- Indexes:  hits ix_events_type_created_at on (event_type, created_at DESC);
--           the JSON probes are unindexed but only run against the 7 funnel
--           event types so the scan stays bounded.
--
-- NB: COALESCE(session_anon_id, payload->>'session_anon_id') is not strictly
--     needed here (we're counting events, not unique users) but other queries
--     in the pack rely on the same pattern, so the comment is included for
--     consistency.

WITH event_with_channel AS (
    SELECT
        event_type,
        created_at,
        COALESCE(
            NULLIF(payload->>'utm_source', ''),
            NULLIF(
                regexp_replace(payload->>'client_referrer',
                               '^https?://([^/]+).*$', '\1'),
                ''
            ),
            'direct'
        ) AS channel
    FROM events
    WHERE event_type IN (
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
)
SELECT
    channel,
    event_type,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days')   AS count_7d,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days')  AS count_30d,
    COUNT(*)                                                          AS count_all_time
FROM   event_with_channel
GROUP  BY channel, event_type
ORDER  BY channel, count_30d DESC, event_type;
