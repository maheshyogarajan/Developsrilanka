# Paid-acquisition pixels (Meta / LinkedIn / Twitter)

Tier D6 / A2 — added 2026-05-24.

FIESTA renders three ad-network pixels for paid-acquisition attribution:

| Network              | Env var                  | Where to obtain                                             |
| -------------------- | ------------------------ | ----------------------------------------------------------- |
| Meta (Facebook/IG)   | `META_PIXEL_ID`          | Meta Business → Events Manager → Data Sources → Pixel ID    |
| LinkedIn Insight Tag | `LINKEDIN_PARTNER_ID`    | LinkedIn Campaign Manager → Account Assets → Insight Tag    |
| Twitter (X) Pixel    | `TWITTER_PIXEL_ID`       | Twitter Ads → Tools → Conversion tracking → Pixel ID (uwt)  |

All three are **default-OFF**. The master kill switch is `PIXELS_ENABLED`;
even with real per-network IDs set, no pixel JS reaches a browser until
the master switch is flipped on.

## Environment variables

```
# Master kill switch — REQUIRED to be "true" before any pixel renders.
PIXELS_ENABLED=false

# Per-network IDs (each one independently gates its own pixel)
META_PIXEL_ID=                          # e.g. 1234567890123456
LINKEDIN_PARTNER_ID=                    # e.g. 9876543
TWITTER_PIXEL_ID=                       # e.g. abc12

# Suppression knobs (rarely needed)
PIXELS_DISABLE_IN_TEST=1                # default; pytest is auto-detected too
PIXELS_ALLOW_IN_DEV=                    # set to "1" to fire pixels in FLASK_ENV=development
```

## Suppression rules

A pixel only renders when **all** of these are true:

1. `PIXELS_ENABLED` is truthy (`1`, `true`, `yes`, `on`).
2. The network's own ID env var is set and non-placeholder.
3. We are **not** in test mode (`pytest`, `FLASK_ENV=test`).
4. We are **not** in dev mode without explicit opt-in
   (`FLASK_ENV=development` AND `PIXELS_ALLOW_IN_DEV` not `1`).
5. Flask's `app.testing` is not set.

Any one of these failing suppresses **that** pixel; the other two still
evaluate independently. Placeholder strings (`your_pixel_id`, `changeme`,
`placeholder`, `xxxxx`, etc.) are treated as if the env var is unset, so
`.env.example` values can't leak into production.

## Conversion events fired

When a template sets `{% set pixel_event = '<name>' %}`, the pixel
component fires the corresponding network event in addition to the
default page-view event:

| `pixel_event`                  | Meta event                | LinkedIn               | Twitter event              |
| ------------------------------ | ------------------------- | ---------------------- | -------------------------- |
| `signup_started`               | `Lead`                    | (no event)             | `signup_started`           |
| `signup_completed`             | `CompleteRegistration`    | conversion (if mapped) | `signup_completed`         |
| `paid_subscription_started`    | `InitiateCheckout`        | (no event)             | `checkout_started`         |
| `paid_subscription_completed`  | `Purchase` (value + LKR)  | conversion (if mapped) | `purchase` (value + LKR)   |

Surfaces that fire these (today):

- `templates/signup.html` → `signup_started`
- `templates/verify_email_reminder.html` → `signup_completed`
- `templates/paywall/pricing_x1.html` → `paid_subscription_started`
- `templates/paywall/checkout_success.html` → `paid_subscription_completed`

LinkedIn conversion events are only fired when a `pixel_linkedin_signup_conversion_id`
or `pixel_linkedin_purchase_conversion_id` context variable is set (these
are obtained per-campaign from LinkedIn Campaign Manager and are not
required for base attribution).

## Deploy checklist

1. In LinkedIn Campaign Manager / Meta Business / Twitter Ads, create
   one Pixel per network. Copy the IDs.
2. Set Fly secrets:

   ```
   flyctl secrets set -a fiesta-mvp \
     META_PIXEL_ID=<meta_id> \
     LINKEDIN_PARTNER_ID=<linkedin_id> \
     TWITTER_PIXEL_ID=<twitter_id>
   ```

3. Verify the secrets without firing pixels by leaving `PIXELS_ENABLED`
   unset (default-off behaviour). Pixels remain suppressed; no risk.
4. Flip the master switch when ready:

   ```
   flyctl secrets set -a fiesta-mvp PIXELS_ENABLED=true
   ```

5. Visit `/signup` from an anonymous browser. View source — confirm the
   `fbq('init', '<id>')` / `_linkedin_partner_id` / `twq('config', ...)`
   lines render.
6. In each network's Events Manager, confirm the test event arrives
   within ~60s.

## Risk / rollback

Set `PIXELS_ENABLED=false` to suppress every pixel without a redeploy.
Per-network env vars stay set; flipping the master back to `true`
restores all three pixels in one move.

## See also

- `pixels.py` — env reading + context processor + suppression rules
- `templates/components/pixels.html` — actual pixel JS
- `docs/UTM_FLOW.md` — UTM capture flow that feeds attribution to events
