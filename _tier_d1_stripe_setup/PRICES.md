# Stripe price IDs - Tier D1

Fill these in after the CEO creates the recurring price in the Stripe
dashboard (see `README.md` step 1).

| Tier      | Billing    | Amount     | Stripe price id |
| --------- | ---------- | ---------- | --------------- |
| Self-File | Yearly     | LKR 2,500  | `price_TODO`    |
| Self-File | One-time   | LKR 2,500  | (legacy X1 - inline price_data, no Price object) |

## Usage in code

The yearly recurring price id will be needed when we wire a `mode='subscription'`
Stripe Checkout Session for new customers (out of scope for this commit;
the C1 scope is webhooks + billing portal only). When that follow-up commit
lands, read the id from an env var (`STRIPE_PRICE_SELF_FILE_YEARLY`) rather
than hard-coding here.
