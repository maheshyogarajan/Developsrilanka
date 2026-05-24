# Tier D1 / C1 - Stripe subscription auto-renew setup

This bundle is the operator runbook for the Stripe subscription auto-renew +
customer billing portal feature delivered on branch
`tier-d1/c1-stripe-auto-renew`.

## What the code does

- `webhooks/stripe_subscription.py` — POST `/webhooks/stripe/subscription`
  receives + verifies + handles four event types:
    - `invoice.paid` -> mark `paywall_subscription` active, advance
      `current_period_end` + `expires_at` + `User.access_expiration_date`.
    - `invoice.payment_failed` -> flip status='dunning' (stub for C5).
    - `customer.subscription.updated` -> mirror `cancel_at_period_end` +
      `current_period_end`.
    - `customer.subscription.deleted` -> status='cancelled', auto_renew=False.
- `webhooks/stripe_subscription.py:billing_portal` — GET `/billing` creates a
  Stripe Customer Portal Session for the logged-in user and redirects.
- `migrations/add_subscription_autorenew.py` — adds 5 columns to
  `paywall_subscription`: `auto_renew`, `stripe_subscription_id`,
  `stripe_customer_id`, `current_period_end`, `cancel_at_period_end`.

## CEO actions required in the Stripe dashboard

These steps require the live Stripe dashboard and an account-owner login.
The code is deployable without them, but no recurring revenue will start
flowing until they are completed.

1. **Create the yearly recurring Price.**
   - Stripe Dashboard -> Products -> FIESTA Self-File -> Add another price.
   - Pricing model: **Standard pricing**.
   - Price: **2500.00 LKR**.
   - Billing period: **Yearly**.
   - Save. Copy the `price_...` id into `PRICES.md`.

2. **Register the webhook endpoint.**
   - Stripe Dashboard -> Developers -> Webhooks -> Add endpoint.
   - URL: `https://<your-prod-host>/webhooks/stripe/subscription`
     (e.g. `https://fiesta.fly.dev/webhooks/stripe/subscription`).
   - Events to send:
       - `invoice.paid`
       - `invoice.payment_failed`
       - `customer.subscription.updated`
       - `customer.subscription.deleted`
   - Save. Click "Reveal" on the signing secret -> copy the
     `whsec_...` value.

3. **Set the webhook secret env var.**
   - `fly secrets set STRIPE_SUBSCRIPTION_WEBHOOK_SECRET=whsec_...`
   - (If unset, the handler falls back to `STRIPE_PAYWALL_WEBHOOK_SECRET`
     then `STRIPE_WEBHOOK_SECRET`, but a dedicated secret means a
     misconfigured one can only take down its own surface.)

4. **Activate the customer billing portal.**
   - Stripe Dashboard -> Settings -> Billing -> Customer portal.
   - Enable: card update, cancellation, subscription pause (optional).
   - Save. (No env var needed — the SDK reads the portal config
     automatically.)

5. **Run the DB migration on prod.**
   - `fly ssh console -C "python migrations/add_subscription_autorenew.py"`
   - Idempotent (uses `ADD COLUMN IF NOT EXISTS`). Safe to re-run.

## How to convert the CEO's existing yearly subscription (id=45)

The CEO's row was created by the legacy X1 one-time-payment flow. To convert
it to auto-renew without re-charging:

1. Manually create a Stripe Subscription against the CEO's existing Stripe
   Customer with the new yearly price, trial-end set to the existing
   `paywall_subscription.expires_at` value (2027-05-22). Stripe will not
   charge until the trial ends, then will auto-renew yearly.
2. The first `customer.subscription.created` event triggers our
   `_get_or_create_subscription` helper to provision the row. Subsequent
   `invoice.paid` events advance `current_period_end`.

Alternative: leave the existing row as-is, ask CEO to subscribe again from
`/pricing/x1` (with a Stripe-side coupon for the prepaid amount). Cleaner
data but requires CEO action.

## Test plan (local)

The unit suite is in `tests/stripe_subscription/`. To run:

```bash
cd "C:/Users/mahes/fiesta_phase_a/worktrees/tier-d1-c1-renew"
python -m pytest tests/stripe_subscription/ -v
```

The tests mock `stripe.Webhook.construct_event` so no live Stripe traffic is
required.
