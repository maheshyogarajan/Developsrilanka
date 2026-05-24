-- new_users_per_day.sql
-- Question: How many distinct visitors hit the site each day for the last
--           30 days? "Visitor" = unique session_anon_id from any event (the
--           beacon endpoint mints one on first touch).
-- Reads:    public.events
-- Indexes:  ix_events_anon_created_at (anon_id, created_at DESC); the
--           date_trunc('day') drives a sort but it's bounded by 30 days of
--           data so cost is small.
--
-- Why not "distinct first-time visitors" (cohort analysis)? Because that
-- needs a per-anon FIRST-seen-at lookup which is more expensive and the
-- launch dashboard doesn't yet require it. Day-over-day distinct anon-id
-- is the cheap proxy for "new traffic" CEO needs for the channel test.

WITH per_day AS (
    SELECT
        date_trunc('day', created_at) AS day,
        COALESCE(
            events.session_anon_id,
            events.payload->>'session_anon_id'
        ) AS anon_id
    FROM events
    WHERE created_at >= NOW() - INTERVAL '30 days'
)
SELECT
    day::date              AS day,
    COUNT(DISTINCT anon_id) AS distinct_visitors
FROM   per_day
WHERE  anon_id IS NOT NULL
GROUP  BY day
ORDER  BY day DESC;
