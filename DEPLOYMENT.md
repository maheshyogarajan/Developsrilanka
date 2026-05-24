# Develop Sri Lanka - Receipt Scanner Deployment

This document outlines the steps required to deploy the Develop Sri Lanka Receipt Scanner application to a production server.

## System Requirements

- Python 3.9+
- PostgreSQL 14+
- Redis (optional, for Celery task queue)

## Required Environment Variables

The following environment variables must be set in the production environment:

```
# Flask configuration
FLASK_ENV=production
SESSION_SECRET=your_secure_session_key

# Database configuration
DATABASE_URL=postgresql://username:password@hostname:port/database

# Optional Redis configuration
REDIS_URL=redis://hostname:port/db

# API Keys
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
FACEBOOK_CLIENT_ID=your_facebook_client_id
FACEBOOK_CLIENT_SECRET=your_facebook_client_secret

# Processing configuration
ENABLE_ASYNC_PROCESSING=True

# Stripe / FIESTA X1 paywall (added 2026-05-20 for FIESTA v1 deploy)
#
# v1 ships with test-mode keys; swap to live-mode at deploy time.
#
#   STRIPE_SECRET_KEY                Must start with sk_live_ in production.
#                                    sk_test_ values are accepted for dev/staging.
#   STRIPE_PAYWALL_WEBHOOK_SECRET    X1 webhook signing secret (whsec_...). Falls
#                                    back to STRIPE_WEBHOOK_SECRET if unset.
#   STRIPE_LIVE_WEBHOOK_SECRET       (optional) explicit live-mode webhook secret;
#                                    when both this AND the active webhook secret
#                                    are present and mode=live they MUST match
#                                    or /healthz/stripe reports ready=False.
#   STRIPE_LIVE_KEYS_REQUIRED        Set to "1" in production. When true, missing
#                                    or test-mode keys are reported as ISSUES
#                                    (ready=False) rather than warnings.
#
# Verify mode at deploy time:
#   curl https://YOUR_DOMAIN/healthz/stripe
#   Expected: HTTP 200 with {"mode": "live", "ready": true, ...}
#   HTTP 503 indicates a configuration problem; do not flip DNS until fixed.
#
STRIPE_SECRET_KEY=sk_live_XXXXXXXXXXXXXXXX
STRIPE_PAYWALL_WEBHOOK_SECRET=whsec_XXXXXXXXXXXXXXXX
STRIPE_LIVE_WEBHOOK_SECRET=whsec_XXXXXXXXXXXXXXXX
STRIPE_LIVE_KEYS_REQUIRED=1

# Paid-acquisition pixels (Tier D6 / A2, added 2026-05-24)
#
# All three pixel networks are env-gated. The master switch is
# PIXELS_ENABLED; default-OFF means a deploy without these values set is
# risk-free. See docs/PIXELS.md for the full operational guide.
#
#   PIXELS_ENABLED         Master kill switch. Default: false.
#                          Set to "true" to enable any pixel rendering.
#   META_PIXEL_ID          Meta (Facebook/Instagram) Pixel ID.
#                          Obtain from Meta Business -> Events Manager.
#   LINKEDIN_PARTNER_ID    LinkedIn Insight Tag partner ID.
#                          Obtain from LinkedIn Campaign Manager -> Insight Tag.
#   TWITTER_PIXEL_ID       Twitter/X Pixel ID (uwt).
#                          Obtain from Twitter Ads -> Conversion tracking.
#
# Optional knobs:
#   PIXELS_ALLOW_IN_DEV    Set to "1" to fire pixels in FLASK_ENV=development.
#                          Default: pixels suppressed in dev to avoid polluting
#                          ad-network analytics with developer traffic.
#   PIXELS_DISABLE_IN_TEST Set to "1" to force suppression even outside pytest.
#                          (Pytest is already auto-detected.)
PIXELS_ENABLED=false
META_PIXEL_ID=
LINKEDIN_PARTNER_ID=
TWITTER_PIXEL_ID=
```

## Deployment Steps

1. Clone the repository:
   ```bash
   git clone https://github.com/your-repository/develop-sri-lanka.git
   cd develop-sri-lanka
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up the environment variables (see above).

4. Initialize the database (if not already done):
   ```bash
   flask db upgrade
   ```

5. Start the Gunicorn server:
   ```bash
   gunicorn --bind 0.0.0.0:5000 --workers 4 wsgi:app
   ```

6. (Optional) Start the Celery worker for background tasks:
   ```bash
   celery -A worker.celery worker --loglevel=info
   ```

## Nginx Configuration

If using Nginx as a reverse proxy, use a configuration similar to:

```nginx
server {
    listen 80;
    server_name developsrilanka.com www.developsrilanka.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Static files handling
    location /static {
        alias /path/to/your/app/static;
        expires 30d;
    }
}
```

## SSL/TLS Configuration

For secure HTTPS connections, set up SSL certificates using Let's Encrypt:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d developsrilanka.com -d www.developsrilanka.com
```

## Background Tasks

If using Celery for background tasks:

1. Ensure Redis is installed and running.
2. Configure Supervisor to keep the Celery worker running:

```
[program:develop_sri_lanka_celery]
command=/path/to/venv/bin/celery -A worker.celery worker --loglevel=info
directory=/path/to/develop_sri_lanka
user=www-data
numprocs=1
stdout_logfile=/var/log/celery/worker.log
stderr_logfile=/var/log/celery/worker.log
autostart=true
autorestart=true
startsecs=10
stopwaitsecs=600
```

## Health Checks

Set up a health check endpoint to monitor the application:

```bash
curl https://developsrilanka.com/health
```

The application should return a 200 OK response if everything is functioning correctly.