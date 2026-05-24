# Tier D1 / E2 — Telegram Ops Bot SETUP

One-way Telegram alerts to the CEO when the FIESTA backend hits trouble.

## What ships in this branch
- `ops_alerts.py` — `send_alert(severity, title, body, data=None)` public API.
- `tasks/ops_probes.py` — three Celery tasks: `healthz_probe`, `latency_probe`, `signup_drop_probe`.
- `celery_config.py` — registers the three probes on the Celery beat schedule.
- `tests/ops_alerts/` — pytest coverage for format, dedup, and missing-token no-op.

## Alert sources (4 total)

| # | Source | Frequency | Trigger | Severity |
|---|---|---|---|---|
| 1 | `healthz_probe` | every 60s | 2 consecutive non-200 from `/healthz` | HIGH |
| 2 | `latency_probe` | every 5min | p95 over last 12 samples > 5.0s (window must be full) | MEDIUM |
| 3 | `signup_drop_probe` | daily 09:00 IST (03:30 UTC) | last 24h signups dropped >30% vs prior 24h (skipped if prior <5) | HIGH |
| 4 | Stripe `payment_failed` webhook | event-driven | wired by C1 in the Stripe webhook branch — see "Wire the 4th source" below | HIGH |

## CEO actions (one-time setup)

### 1. Create the bot
Open Telegram, DM `@BotFather`:
```
/newbot
```
- Bot name suggestion: `FIESTA Ops Alerts`
- Bot username suggestion: `fiesta_ops_alerts_bot` (must end in `_bot` and be globally unique)

BotFather replies with a token shaped like `1234567890:AAH...`. Copy it.

### 2. DM the bot once from the CEO account
Send `/start` to the new bot from the CEO's Telegram (chat_id `1813046950`). Telegram bots cannot DM a user who has never opened a conversation with them — this step is what flips the channel from "blocked" to "ready".

### 3. Push the secrets to Fly
From any shell that has `flyctl` authenticated:
```bash
flyctl secrets set \
  TELEGRAM_BOT_TOKEN="<paste BotFather token here>" \
  TELEGRAM_OPS_CHAT_ID=1813046950 \
  -a fiesta-mvp
```

Setting Fly secrets triggers a rolling restart automatically — no `flyctl deploy` needed.

### 4. Verify the channel
After the restart, watch the worker logs for the first beat tick:
```bash
flyctl logs -a fiesta-mvp | grep -i ops_probes
```
Force a smoke alert by exec'ing into the worker:
```bash
flyctl ssh console -a fiesta-mvp -C "python -c \"from ops_alerts import send_alert; print(send_alert('INFO', 'Setup smoke', 'Telegram ops bot wired'))\""
```
Expect: a Telegram DM to chat_id 1813046950 within ~5 seconds AND stdout `{'sent': True, 'deduped': False, 'reason': None}`.

## Wire the 4th source (Stripe payment_failed)

Once C1's Stripe webhook branch lands, drop this single block inside the `payment_failed` handler:

```python
from ops_alerts import send_alert
send_alert(
    severity="HIGH",
    title="Stripe payment_failed",
    body=(
        f"Customer {event['data']['object'].get('customer')} payment "
        f"failed. Charge id: {event['data']['object'].get('id')}."
    ),
    data={
        "stripe_event_id": event.get("id"),
        "amount": event["data"]["object"].get("amount"),
        "failure_code": event["data"]["object"].get("failure_code"),
    },
)
```

That's the entire integration — `send_alert` is one-way and never raises, so it cannot break the webhook path.

## Operational notes

- **Dedup window:** 10 minutes per `(title, severity)` tuple, process-local. A worker restart resets the dedup state — acceptable trade-off for "no Redis" scope cap.
- **Token resolution order:** `TELEGRAM_BOT_TOKEN` env > `/etc/secrets/TELEGRAM_BOT_TOKEN` file > `~/.claude/channels/telegram/.env` (local dev only).
- **Latency probe target:** hits `/healthz` (not `/tax-bill/<year>`) — see `tasks/ops_probes.py` module docstring for the scope rationale.
- **No interactive commands:** this is a one-way alert channel. There is no `/status`, no polling, no command dispatcher — that surface area is out of scope for E2.

## Rollback

If alerts go rogue (false positives, spam):
```bash
flyctl secrets unset TELEGRAM_BOT_TOKEN -a fiesta-mvp
```
The probes will continue running but `send_alert` will return `{"sent": False, "reason": "token_missing"}` and log a warning. No Telegram traffic until the secret is restored.

To stop the probes entirely, comment out the three entries in `celery_config.py` under the `Tier D1 / E2 — Telegram ops alerts probes` block and redeploy.
