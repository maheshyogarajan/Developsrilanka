"""
tasks/cbsl_rate_fetch.py — Daily CBSL middle-rate pre-fetch task (D5 / F-Feature-3.7).

PURPOSE
-------
Proactively fetch and cache today's CBSL middle rates for the currencies
listed in PREFETCH_CURRENCIES so the /remittance/new form can auto-fill
the rate field when the page loads — no per-request latency, no dependency
on CBSL being reachable at form-load time.

Wired into the Celery beat schedule in celery_config.py:

    'cbsl-rate-daily-prefetch': {
        'task': 'tasks.cbsl_rate_fetch.fetch_today_task',
        'schedule': crontab(hour=7, minute=30),  # 07:30 UTC = ~13:00 SL = after CBSL publishes
    },

INVOCATION
----------
Via Celery beat (preferred):
    celery -A celery_config beat --scheduler celery.beat.PersistentScheduler

Via Flask CLI (manual / cron fallback):
    flask cbsl-fetch
    flask cbsl-fetch --date 2026-05-20          # backfill a specific date
    flask cbsl-fetch --currencies USD,GBP,EUR   # override currency list

Via Python module (scripts / debugging):
    python -m tasks.cbsl_rate_fetch

DESIGN NOTES
------------
- Idempotent: cache_write uses ON CONFLICT DO UPDATE with source-hierarchy
  guard, so re-running is safe.
- Failure mode: logged + returns summary; never raises. The form still works
  (falls through to live CBSL fetch or ecb_proxy) when this task hasn't run.
- IRD defensibility: only 'cbsl' and 'cbsl_cached' sources are IRD-defensible.
  This task writes source='cbsl'. The ecb_proxy path in fx_rate_service stays
  as the live-fallback for today; this task is what populates the cache
  proactively so ecb_proxy is almost never needed for same-day entries.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Currencies to pre-fetch every day. These cover >95% of FIESTA users.
# AED/SGD/AUD cover GCC + Singapore + Australia remittances (top corridors).
PREFETCH_CURRENCIES: List[str] = [
    "USD", "GBP", "EUR", "AUD", "CAD",
    "AED", "SGD", "NZD", "CHF", "JPY",
    "HKD", "SEK",
]


# ---------------------------------------------------------------------------
# Core fetch + cache logic (no Flask/Celery dependency — testable standalone)
# ---------------------------------------------------------------------------


def fetch_and_cache(
    on_date: date,
    currencies: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Fetch CBSL rates for *on_date* and write to the fx_rate_service cache.

    Returns a summary dict:
        {
            "date": "YYYY-MM-DD",
            "fetched": ["USD", "GBP", ...],    # rates we got from CBSL
            "skipped": ["KRW", ...],            # CBSL returned no row (non-trading day or unlisted)
            "failed": [],                        # sanity-rejected or parse error
            "cache_written": 3,
        }
    Raises nothing. All errors are logged at WARNING level.
    """
    if currencies is None:
        currencies = PREFETCH_CURRENCIES

    summary: Dict = {
        "date": on_date.isoformat(),
        "fetched": [],
        "skipped": [],
        "failed": [],
        "cache_written": 0,
    }

    try:
        from cbsl_scraper import fetch_cbsl_rates
    except ImportError as exc:
        log.error("cbsl_rate_fetch: cbsl_scraper unavailable: %s", exc)
        summary["skipped"] = currencies
        return summary

    try:
        rates_by_date = fetch_cbsl_rates(on_date, on_date, currencies, timeout=20)
    except Exception as exc:
        log.warning("cbsl_rate_fetch: fetch_cbsl_rates raised: %s", exc)
        summary["skipped"] = currencies
        return summary

    day_rates = rates_by_date.get(on_date, {})

    if not day_rates:
        # CBSL returned no rows — public holiday or weekend; nothing to cache.
        log.info("cbsl_rate_fetch: no rates for %s (non-trading day or CBSL down)", on_date)
        summary["skipped"] = currencies
        return summary

    try:
        from fx_rate_service import FxRate, _cache_write, _passes_sanity, SANITY_RANGES_LKR
        from datetime import datetime
        from decimal import Decimal
    except ImportError as exc:
        log.error("cbsl_rate_fetch: fx_rate_service unavailable: %s", exc)
        summary["skipped"] = currencies
        return summary

    now = datetime.utcnow()
    for iso in currencies:
        if iso not in day_rates:
            summary["skipped"].append(iso)
            continue
        try:
            fx = FxRate(
                currency=iso,
                rate_date=on_date,
                value=day_rates[iso],
                source="cbsl",
                fetched_at=now,
            )
        except Exception as exc:
            log.warning("cbsl_rate_fetch: FxRate construction failed for %s: %s", iso, exc)
            summary["failed"].append(iso)
            continue

        if not _passes_sanity(fx):
            log.warning("cbsl_rate_fetch: sanity rejected %s %s=%s", iso, on_date, fx.value)
            summary["failed"].append(iso)
            continue

        try:
            _cache_write(fx)
            summary["fetched"].append(iso)
            summary["cache_written"] += 1
        except Exception as exc:
            log.warning("cbsl_rate_fetch: cache_write failed for %s: %s", iso, exc)
            summary["failed"].append(iso)

    log.info(
        "cbsl_rate_fetch: date=%s fetched=%d skipped=%d failed=%d",
        on_date, summary["cache_written"], len(summary["skipped"]), len(summary["failed"]),
    )
    return summary


# ---------------------------------------------------------------------------
# get_cbsl_rate — convenience helper for routes / templates
# ---------------------------------------------------------------------------


def get_cbsl_rate(currency: str, on_date: Optional[date] = None) -> Optional[object]:
    """Return the cached CBSL FxRate for *currency* on *on_date*.

    Falls back to the nearest PREVIOUS cached date (up to 7 days back) so
    weekend/holiday gaps don't show as empty — the most-recent trading-day
    rate is IRD-acceptable for entries logged on non-trading days.

    Returns an FxRate (from fx_rate_service) or None if no cached rate
    exists within the lookback window.

    This is a READ-ONLY helper. It does NOT trigger a live CBSL fetch;
    use fx_rate_service.get_rate() for the full tiered path.
    """
    if on_date is None:
        on_date = date.today()

    try:
        from fx_rate_service import _cache_lookup, FxRate
        from sqlalchemy import text as _sql_text
        from app import db
        from decimal import Decimal
        from datetime import datetime as _dt
    except ImportError as exc:
        log.warning("get_cbsl_rate: import failed: %s", exc)
        return None

    currency = (currency or "").upper().strip()[:3]
    if not currency or currency == "LKR":
        return None

    # Try exact date first (hot path).
    exact = _cache_lookup(currency, on_date)
    if exact is not None:
        return exact

    # Fallback: nearest previous cached date within 7 days.
    # Handles weekends (CBSL closed Sat/Sun) and public holidays.
    lookback_floor = on_date - timedelta(days=7)
    try:
        row = db.session.execute(
            _sql_text("""
                SELECT currency, rate_date, rate_lkr, source, fetched_at
                FROM cbsl_rates
                WHERE currency = :ccy
                  AND rate_date < :d
                  AND rate_date >= :floor
                ORDER BY rate_date DESC
                LIMIT 1
            """),
            {"ccy": currency, "d": on_date, "floor": lookback_floor},
        ).fetchone()
    except Exception as exc:
        log.warning("get_cbsl_rate: fallback query failed: %s", exc)
        return None

    if not row:
        return None

    try:
        # Relabel source to indicate this is a carry-forward (one trading day back).
        src = row[3]
        if src == "cbsl":
            src = "cbsl_cached"
        return FxRate(
            currency=row[0],
            rate_date=row[1],
            value=Decimal(str(row[2])),
            source=src,
            fetched_at=row[4],
        )
    except Exception as exc:
        log.warning("get_cbsl_rate: FxRate rebuild failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Celery task — lazy registration so test imports don't require a broker
# ---------------------------------------------------------------------------


def _register_celery_task():
    """Lazily register the Celery task. Returns the task function or None."""
    try:
        from celery_config import app as celery  # type: ignore
    except ImportError:
        return None

    @celery.task(name="tasks.cbsl_rate_fetch.fetch_today_task", bind=False)
    def fetch_today_task():
        """Celery beat task: pre-fetch today's CBSL rates into the cache.

        Runs daily at 07:30 UTC (13:00 SL) — after CBSL publishes rates
        for the day. Beat entry in celery_config.py:
            'cbsl-rate-daily-prefetch': {
                'task': 'tasks.cbsl_rate_fetch.fetch_today_task',
                'schedule': crontab(hour=7, minute=30),
            }
        """
        from app import app as flask_app
        with flask_app.app_context():
            summary = fetch_and_cache(date.today())
            if summary["failed"]:
                log.warning(
                    "cbsl_rate_fetch Celery task: %d currencies failed sanity/write: %s",
                    len(summary["failed"]), summary["failed"],
                )
            return summary

    return fetch_today_task


fetch_today_task = _register_celery_task()


# ---------------------------------------------------------------------------
# Flask CLI command  (`flask cbsl-fetch`)
# ---------------------------------------------------------------------------


def register_cli(app) -> None:
    """Register `flask cbsl-fetch` command on *app*.

    Called from remittance_routes.py (or app.py) via:
        from tasks.cbsl_rate_fetch import register_cli
        register_cli(app)
    """
    import click

    @app.cli.command("cbsl-fetch")
    @click.option("--date", "date_str", default=None,
                  help="YYYY-MM-DD to fetch (default: today)")
    @click.option("--currencies", "ccy_str", default=None,
                  help="Comma-separated ISO codes, e.g. USD,GBP (default: all PREFETCH_CURRENCIES)")
    def cbsl_fetch_cmd(date_str, ccy_str):
        """Fetch and cache today's (or a specific date's) CBSL middle rates."""
        from datetime import datetime as _dt
        if date_str:
            try:
                target_date = _dt.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                click.echo(f"ERROR: invalid date '{date_str}' — expected YYYY-MM-DD", err=True)
                sys.exit(1)
        else:
            target_date = date.today()

        currencies = (
            [c.strip().upper() for c in ccy_str.split(",") if c.strip()]
            if ccy_str else None
        )
        summary = fetch_and_cache(target_date, currencies)
        click.echo(
            f"CBSL fetch {summary['date']}: "
            f"written={summary['cache_written']} "
            f"skipped={len(summary['skipped'])} "
            f"failed={len(summary['failed'])}"
        )
        if summary["fetched"]:
            click.echo("  Cached: " + ", ".join(summary["fetched"]))
        if summary["skipped"]:
            click.echo("  Skipped (no CBSL row): " + ", ".join(summary["skipped"]))
        if summary["failed"]:
            click.echo("  FAILED (sanity/write error): " + ", ".join(summary["failed"]))


# ---------------------------------------------------------------------------
# __main__ entry point  (`python -m tasks.cbsl_rate_fetch`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    # Bootstrap Flask app context so db/ORM calls work.
    # Expects DATABASE_URL (or REDIS_URL) in environment.
    try:
        from app import app as flask_app
    except ImportError:
        print("ERROR: cannot import Flask app. Run from the project root.", file=sys.stderr)
        sys.exit(1)

    _date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    _target: date
    if _date_arg:
        from datetime import datetime as _dt_cls
        try:
            _target = _dt_cls.strptime(_date_arg, "%Y-%m-%d").date()
        except ValueError:
            print(f"ERROR: invalid date '{_date_arg}' — expected YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        _target = date.today()

    with flask_app.app_context():
        _summary = fetch_and_cache(_target)
        print(
            f"CBSL fetch {_summary['date']}: "
            f"written={_summary['cache_written']} "
            f"skipped={len(_summary['skipped'])} "
            f"failed={len(_summary['failed'])}"
        )
        if _summary["fetched"]:
            print("  Cached:", ", ".join(_summary["fetched"]))
        if _summary["skipped"]:
            print("  Skipped:", ", ".join(_summary["skipped"]))
        if _summary["failed"]:
            print("  FAILED:", ", ".join(_summary["failed"]))
