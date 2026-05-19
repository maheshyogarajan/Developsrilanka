"""
FX rate service for the Remittance Ledger (Wave B1 — council #2 unanimous pick).

DESIGN PRINCIPLES (per FIESTA_HARDENING_PLAN + council #2):
- Frozen-at-entry: rates are persisted on the remittance record at create time.
  Subsequent rate fetches NEVER mutate historical records.
- Source-labelled: every cached rate carries a `source` tag so the user knows
  whether it's CBSL-direct (IRD-defensible) or a proxy (approximate, must be
  confirmed by user before filing).
- Sanity-range guarded: per-currency min/max bounds. Anything outside is
  rejected as a likely scrape error — better no rate than a wrong rate.
- Tiered fallback:
    1. Local cache (cbsl_rates table)
    2. CBSL official (TODO — currently unreachable via public API; placeholder for future scraper)
    3. open-er-api.com (latest rates only, free, no key) — for TODAY only
    4. None — historical dates the user must enter manually
- IRD context: filing under PN/IT/2025-01 requires CBSL middle rate on the
  remittance date. A proxy rate is NOT IRD-defensible — UI must flag this.

PUBLIC API:
    from fx_rate_service import get_rate
    rate = get_rate("USD", date(2026, 3, 15))   # returns FxRate | None
    rate.value           # Decimal LKR per 1 unit foreign
    rate.source          # 'cbsl' | 'cbsl_cached' | 'ecb_proxy' | 'manual'
    rate.is_ird_defensible  # True only when source in {'cbsl','cbsl_cached'}
"""
from __future__ import annotations

import logging
import urllib.request
import urllib.error
import json
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict

from app import db

log = logging.getLogger(__name__)


# Sanity ranges in LKR per 1 unit of foreign currency. Anything outside →
# reject as a likely scraping/parsing artefact. Refresh every 6 months or when
# a rate at the edge fires a sanity rejection.
# Calibrated 2026-05-17 against open-er-api current rates (USD = 324.6).
SANITY_RANGES_LKR: Dict[str, tuple[Decimal, Decimal]] = {
    "USD": (Decimal("250"), Decimal("450")),
    "GBP": (Decimal("320"), Decimal("550")),
    "EUR": (Decimal("280"), Decimal("500")),
    "AUD": (Decimal("180"), Decimal("310")),
    "CAD": (Decimal("200"), Decimal("330")),
    "AED": (Decimal("65"), Decimal("130")),
    "SGD": (Decimal("210"), Decimal("340")),
    "JPY": (Decimal("1.5"), Decimal("4")),
    "CHF": (Decimal("310"), Decimal("520")),
    "NZD": (Decimal("170"), Decimal("290")),
    "SEK": (Decimal("25"), Decimal("60")),
    "HKD": (Decimal("30"), Decimal("65")),
}


@dataclass(frozen=True)
class FxRate:
    """A point-in-time FX rate. Immutable; new lookups produce new instances."""
    currency: str
    rate_date: date
    value: Decimal               # LKR per 1 unit foreign
    source: str                  # cbsl | cbsl_cached | ecb_proxy | manual
    fetched_at: datetime

    @property
    def is_ird_defensible(self) -> bool:
        return self.source in {"cbsl", "cbsl_cached"}

    @property
    def label_for_ui(self) -> str:
        return {
            "cbsl":        "Verified CBSL rate",
            "cbsl_cached": "Verified CBSL rate (cached)",
            "ecb_proxy":   "Proxy rate — confirm with CBSL before filing",
            "manual":      "Manual entry",
        }.get(self.source, self.source)


# --------------------------------------------------------------------------- #
# DB cache table (created via _ensure_fx_table)
# --------------------------------------------------------------------------- #

def _ensure_fx_table():
    """Idempotent. Runs on import; cheap."""
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS cbsl_rates (
                    id SERIAL PRIMARY KEY,
                    currency VARCHAR(3) NOT NULL,
                    rate_date DATE NOT NULL,
                    rate_lkr NUMERIC(18, 6) NOT NULL,
                    source VARCHAR(32) NOT NULL,
                    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (currency, rate_date)
                )
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_cbsl_rates_lookup
                    ON cbsl_rates (currency, rate_date DESC)
            """))
            db.session.commit()
    except Exception as e:
        log.warning("Could not ensure cbsl_rates table: %s", e)
        try: db.session.rollback()
        except Exception: pass


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def get_rate(currency: str, on_date: date) -> Optional[FxRate]:
    """Resolve a CBSL middle rate for (currency, date). Tiered fallback.

    Returns None when no rate can be sourced AND no sanity-clean fallback exists.
    Caller is expected to surface this to the user as "manual entry required".
    """
    currency = (currency or "").upper().strip()[:3]
    if not currency or currency == "LKR":
        return None
    if not isinstance(on_date, date):
        return None

    # Tier 1: cache hit (any source — CBSL preferred, returned as cbsl_cached)
    cached = _cache_lookup(currency, on_date)
    if cached is not None:
        return cached

    # Tier 2: CBSL scraper — IRD-defensible source. Works for any date back to
    # 2006-11-11. Returns empty dict if CBSL is down or the date is a non-trading day.
    cbsl_fx = _fetch_cbsl(currency, on_date)
    if cbsl_fx is not None:
        _cache_write(cbsl_fx)
        return cbsl_fx

    # Tier 3: open-er-api.com — only for "today" lookups (no historical).
    # NOT IRD-defensible; UI flags it for manual CBSL confirmation before filing.
    if on_date == date.today():
        proxied = _fetch_ecb_proxy_today(currency)
        if proxied is not None:
            _cache_write(proxied)
            return proxied

    # Tier 4: nothing. Caller must collect manually.
    return None


def _fetch_cbsl(currency: str, on_date: date) -> Optional[FxRate]:
    """Wrap cbsl_scraper.fetch_single_day → FxRate with sanity check."""
    try:
        from cbsl_scraper import fetch_single_day
    except ImportError as e:
        log.warning("CBSL scraper unavailable: %s", e)
        return None
    try:
        rates = fetch_single_day(on_date, [currency])
    except Exception as e:
        log.warning("CBSL scraper raised: %s", e)
        return None
    if currency not in rates:
        return None
    fx = FxRate(
        currency=currency,
        rate_date=on_date,
        value=rates[currency],
        source="cbsl",
        fetched_at=datetime.utcnow(),
    )
    if not _passes_sanity(fx):
        log.warning("CBSL rate failed sanity: %s %s=%s", currency, on_date, fx.value)
        return None
    return fx


def store_manual_rate(currency: str, on_date: date, rate_lkr: Decimal) -> FxRate:
    """Persist a user-supplied rate. Useful when the auto-source fails and the
    user types it themselves — we still want to cache it so the next entry on
    the same date reuses it."""
    fx = FxRate(
        currency=currency.upper()[:3],
        rate_date=on_date,
        value=Decimal(str(rate_lkr)),
        source="manual",
        fetched_at=datetime.utcnow(),
    )
    if _passes_sanity(fx):
        _cache_write(fx)
    return fx


def cache_size() -> int:
    """Diagnostic: how many rates do we have cached?"""
    from sqlalchemy import text as _sql_text
    try:
        r = db.session.execute(_sql_text("SELECT COUNT(*) FROM cbsl_rates")).scalar()
        return int(r or 0)
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #

def _passes_sanity(fx: FxRate) -> bool:
    bounds = SANITY_RANGES_LKR.get(fx.currency)
    if not bounds:
        # Unknown currency — accept conservatively (no bounds to compare against)
        return True
    lo, hi = bounds
    ok = lo <= fx.value <= hi
    if not ok:
        log.warning("FX sanity rejection: %s %s=%s outside [%s,%s]",
                    fx.currency, fx.rate_date, fx.value, lo, hi)
    return ok


def _cache_lookup(currency: str, on_date: date) -> Optional[FxRate]:
    from sqlalchemy import text as _sql_text
    try:
        row = db.session.execute(
            _sql_text("""SELECT currency, rate_date, rate_lkr, source, fetched_at
                         FROM cbsl_rates
                         WHERE currency = :ccy AND rate_date = :d
                         LIMIT 1"""),
            {"ccy": currency, "d": on_date},
        ).fetchone()
    except Exception as e:
        log.warning("FX cache lookup failed: %s", e)
        return None
    if not row:
        return None
    try:
        return FxRate(
            currency=row[0],
            rate_date=row[1],
            value=Decimal(str(row[2])),
            source=row[3] + "_cached" if not row[3].endswith("_cached") and row[3] == "cbsl" else row[3],
            fetched_at=row[4],
        )
    except (InvalidOperation, ValueError) as e:
        log.warning("FX cache row corrupt: %s", e)
        return None


def _cache_write(fx: FxRate) -> None:
    from sqlalchemy import text as _sql_text
    try:
        db.session.execute(
            _sql_text("""INSERT INTO cbsl_rates (currency, rate_date, rate_lkr, source, fetched_at)
                         VALUES (:ccy, :d, :r, :s, :ts)
                         ON CONFLICT (currency, rate_date) DO UPDATE
                         SET rate_lkr = EXCLUDED.rate_lkr,
                             source = EXCLUDED.source,
                             fetched_at = EXCLUDED.fetched_at
                         WHERE cbsl_rates.source NOT IN ('cbsl', 'cbsl_cached')"""),
            {"ccy": fx.currency, "d": fx.rate_date, "r": str(fx.value),
             "s": fx.source, "ts": fx.fetched_at},
        )
        db.session.commit()
    except Exception as e:
        log.warning("FX cache write failed: %s", e)
        try: db.session.rollback()
        except Exception: pass


def _fetch_ecb_proxy_today(currency: str) -> Optional[FxRate]:
    """open-er-api.com — free, no key, current rates only. ECB-sourced for most
    currencies. NOT IRD-defensible — labelled accordingly."""
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FIESTA/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        log.warning("FX proxy fetch failed: %s", e)
        return None

    if payload.get("result") != "success":
        log.warning("FX proxy returned non-success: %s", payload.get("result"))
        return None

    rates = payload.get("rates", {})
    usd_lkr = rates.get("LKR")
    if usd_lkr is None:
        log.warning("FX proxy: LKR not in rates dict")
        return None

    try:
        usd_lkr_d = Decimal(str(usd_lkr))
    except InvalidOperation:
        return None

    # USD case is direct
    if currency == "USD":
        fx_value = usd_lkr_d
    else:
        # Cross-rate via USD: e.g. GBP→LKR = (USD→LKR) / (USD→GBP)
        usd_to_currency = rates.get(currency)
        if usd_to_currency is None or float(usd_to_currency) == 0:
            log.warning("FX proxy: %s not in rates dict or zero", currency)
            return None
        try:
            fx_value = (usd_lkr_d / Decimal(str(usd_to_currency))).quantize(Decimal("0.0001"))
        except (InvalidOperation, ZeroDivisionError):
            return None

    fx = FxRate(
        currency=currency,
        rate_date=date.today(),
        value=fx_value,
        source="ecb_proxy",
        fetched_at=datetime.utcnow(),
    )
    if not _passes_sanity(fx):
        return None
    return fx


# Run schema setup on import
_ensure_fx_table()
