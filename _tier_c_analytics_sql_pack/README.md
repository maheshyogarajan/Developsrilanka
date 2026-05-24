# Tier C / Analytics SQL Pack

Five saved Postgres queries the CEO (or a future cron) can run against the
prod `events` table to answer the five questions the launch needs:

| # | File                          | Question it answers                                                              |
|---|-------------------------------|----------------------------------------------------------------------------------|
| 1 | `funnel_overall.sql`          | How many events of each type fired in the last 7d / 30d / all-time?              |
| 2 | `funnel_by_channel.sql`       | Same funnel but split by acquisition channel (utm_source / referer hostname).    |
| 3 | `conversion_per_channel.sql`  | Landing-view -> signup-completed -> payment-completed % per channel.             |
| 4 | `new_users_per_day.sql`       | Distinct new visitors (anon_id) per day, last 30d.                               |
| 5 | `funnel_dropoff.sql`          | For each funnel step, how many users entered but didn't reach the next step.     |

All queries use `COALESCE(events.session_anon_id, events.payload->>'session_anon_id')`
for anon-id resolution because the top-level column was only promoted in
Tier C2 (2026-05-24) and pre-promotion rows still carry it inside the JSON
payload only. The dual-read keeps the funnel intact across the cutover.

## How CEO runs one
```bash
flyctl ssh console -a fiesta-mvp -C \
  "bash -lc 'psql \"$DATABASE_URL\" -f /app/_tier_c_analytics_sql_pack/funnel_overall.sql'"
```

If `/app/_tier_c_analytics_sql_pack/` isn't present on the running pod yet
(no deploy since the SQL pack landed), pipe the local file in instead:
```bash
flyctl postgres connect -a fiesta-pg-bom \
  < _tier_c_analytics_sql_pack/funnel_overall.sql
```
