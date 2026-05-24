-- conversion_per_channel.sql
-- Question: For each acquisition channel (utm_source / referer-hostname
--           fallback), how many DISTINCT visitors hit each funnel milestone,
--           and what's the channel-level conversion rate from landing-view
--           -> signup-completed -> payment-completed? Window: last 30d.
-- Reads:    public.events
-- Indexes:  ix_events_anon_created_at + ix_events_type_created_at help; the
--           JSON probe is bounded by the explicit event_type IN (...) filter.
--
-- "Distinct visitor" = the effective anon id:
--   COALESCE(events.session_anon_id, payload->>'session_anon_id')
-- This is the same dual-read every Tier-C query uses (top-level column
-- post-Tier-C2, payload JSON for pre-Tier-C2 rows).

WITH event_with_channel AS (
    SELECT
        event_type,
        created_at,
        COALESCE(
            events.session_anon_id,
            events.payload->>'session_anon_id'
        ) AS anon_id,
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
    WHERE created_at >= NOW() - INTERVAL '30 days'
      AND event_type IN (
          'landing_view',
          'signup_started',
          'signup_completed',
          'payment_started',
          'payment_completed'
      )
)
SELECT
    channel,
    COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'landing_view')      AS landed,
    COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'signup_started')    AS signup_started,
    COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'signup_completed')  AS signup_completed,
    COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'payment_started')   AS payment_started,
    COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'payment_completed') AS payment_completed,
    -- Conversion %s (rounded to 2dp; NULL if denominator is 0)
    ROUND(
        100.0 * COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'signup_completed')
              / NULLIF(COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'landing_view'), 0),
        2
    ) AS pct_landing_to_signup,
    ROUND(
        100.0 * COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'payment_completed')
              / NULLIF(COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'signup_completed'), 0),
        2
    ) AS pct_signup_to_payment,
    ROUND(
        100.0 * COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'payment_completed')
              / NULLIF(COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'landing_view'), 0),
        2
    ) AS pct_landing_to_payment
FROM   event_with_channel
GROUP  BY channel
ORDER  BY landed DESC NULLS LAST, channel;
