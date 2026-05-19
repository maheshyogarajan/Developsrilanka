"""
CBSL Daily Indicative Exchange Rates scraper — upgrades fx_rate_service from
'ecb_proxy' (approximate) to 'cbsl' (IRD-defensible) for SL foreign-income
filing under PN/IT/2025-01.

DATA SOURCE
-----------
The CBSL public page at /en/rates-and-indicators/exchange-rates/daily-indicative-exchange-rates
embeds an iframe pointing to /cbsl_custom/exrates/exrates.php. That form POSTs
to /cbsl_custom/exrates/exrates_results.php with:

    lookupPage = lookup_daily_exchange_rates.php
    startRange = 2006-11-11   (historical floor; 20 years of data accessible)
    rangeType  = dates
    txtStart   = YYYY-MM-DD
    txtEnd     = YYYY-MM-DD
    chk_cur[]  = USD~US Dollar (repeatable for multi-currency)
    submit_button = Submit

The response is HTML with a section per currency, each containing a table:

    <h2 id="LOOKUPS_IEXE0101"> US Dollar </h2>
    ... <td> 2026-05-15 </td><td> 324.7184 </td><td> 0.0031 </td>

We parse the `<h2>` headings + the date/value cells with regex (no external
HTML parser dependency — keeps the deploy slim).

HONEST LIMITS
-------------
- Date format is yyyy-mm-dd. CBSL historical floor: 2006-11-11.
- Weekends/public holidays return no rate row for that date — caller must
  handle "no rate" by looking back to the previous trading day (TODO Wave B1.2).
- The endpoint has no documented rate limit. We cache aggressively to be polite.
- If CBSL website is down, this returns an empty dict; fx_rate_service then
  falls through to ecb_proxy as before. Failure mode is degraded, not broken.

PUBLIC API
----------
    from cbsl_scraper import fetch_cbsl_rates
    rates = fetch_cbsl_rates(date(2026, 5, 15), [date(2026, 5, 15)], ['USD','GBP'])
    # → {date(2026,5,15): {'USD': Decimal('324.7184'), 'GBP': Decimal('434.1160')}}
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


# ISO code → CBSL form-value "CODE~Display Name" (exact text from the form HTML
# captured 2026-05-17; do NOT alter spacing — note the double space in "UAE  Dirham").
CBSL_CURRENCY_MAP: Dict[str, str] = {
    "USD": "USD~US Dollar",
    "GBP": "GBP~Sterling Pound",
    "EUR": "EUR~Euro",
    "AUD": "AUD~Australian Dollar",
    "CAD": "CAD~Canadian Dollar",
    "CHF": "CHF~Swiss Franc",
    "SGD": "SGD~Singapore Dollar",
    "NZD": "NZD~New Zealand Dollar",
    "JPY": "JPY~Japanese Yen",
    "AED": "AED~UAE  Dirham",  # double space is intentional — CBSL's HTML has it
    "SEK": "SEK~Swedish Kroner",
    "HKD": "HKD~Hong Kong Dollar",
    "INR": "INR~Indian Rupee",
    "CNY": "CNY~Chinese Yuan (Renminbi)",
    "KRW": "KRW~Korean Won",
    "MYR": "MYR~Malaysia  Ringgit",  # also has the double space
    "THB": "THB~Thailand Baht",
    "ZAR": "ZAR~South African Rand",
    "NOK": "NOK~Norwegian Kroner",
    "DKK": "DKK~Danish Kroner",
    "PKR": "PKR~Pakistan Rupee",
    "SAR": "SAR~Saudi Arabian Riyal",
}

# Reverse: display-name fragment (from the <h2> in the response) → ISO code.
# We match case-insensitive against the heading text.
_HEADING_TO_ISO = {
    "us dollar":              "USD",
    "sterling pound":         "GBP",
    "euro":                   "EUR",
    "australian dollar":      "AUD",
    "canadian dollar":        "CAD",
    "swiss franc":            "CHF",
    "singapore dollar":       "SGD",
    "new zealand dollar":     "NZD",
    "japanese yen":           "JPY",
    "uae  dirham":            "AED",
    "uae dirham":             "AED",
    "swedish kroner":         "SEK",
    "hong kong dollar":       "HKD",
    "indian rupee":           "INR",
    "chinese yuan":           "CNY",
    "korean won":             "KRW",
    "malaysia  ringgit":      "MYR",
    "malaysia ringgit":       "MYR",
    "thailand baht":          "THB",
    "south african rand":     "ZAR",
    "norwegian kroner":       "NOK",
    "danish kroner":          "DKK",
    "pakistan rupee":         "PKR",
    "saudi arabian riyal":    "SAR",
}

CBSL_POST_URL = "https://www.cbsl.gov.lk/cbsl_custom/exrates/exrates_results.php"
CBSL_REFERER  = "https://www.cbsl.gov.lk/cbsl_custom/exrates/exrates.php"
HISTORICAL_FLOOR = date(2006, 11, 11)


def fetch_cbsl_rates(
    start_date: date,
    end_date: date,
    currencies: List[str],
    timeout: int = 15,
) -> Dict[date, Dict[str, Decimal]]:
    """Fetch CBSL daily indicative rates for a date range and currency list.

    Returns {date: {iso_code: rate_lkr}}. Empty dict on any failure (HTTP error,
    parse error, unknown currency). Callers should fall through to a backup
    source (e.g. ecb_proxy) on empty.

    For a single-date lookup pass start_date == end_date.
    """
    if start_date > end_date:
        return {}
    if start_date < HISTORICAL_FLOOR:
        log.warning("CBSL scraper: start_date %s before historical floor %s",
                    start_date, HISTORICAL_FLOOR)
        return {}

    requested_isos = []
    cbsl_values = []
    for iso in currencies:
        iso = iso.upper().strip()
        if iso == "LKR":
            continue
        v = CBSL_CURRENCY_MAP.get(iso)
        if not v:
            log.info("CBSL scraper: currency %s not in CBSL_CURRENCY_MAP, skipping", iso)
            continue
        requested_isos.append(iso)
        cbsl_values.append(v)

    if not cbsl_values:
        return {}

    body_pairs = [
        ("lookupPage",     "lookup_daily_exchange_rates.php"),
        ("startRange",     HISTORICAL_FLOOR.isoformat()),
        ("rangeType",      "dates"),
        ("txtStart",       start_date.isoformat()),
        ("txtEnd",         end_date.isoformat()),
        ("submit_button",  "Submit"),
    ] + [("chk_cur[]", v) for v in cbsl_values]

    body = urllib.parse.urlencode(body_pairs, encoding="utf-8")

    req = urllib.request.Request(
        CBSL_POST_URL,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer":      CBSL_REFERER,
            "User-Agent":   "FIESTA/1.0 (lanka.tax foreign-income filing helper)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("CBSL scraper HTTP failed: %s", e)
        return {}

    return _parse_response(html, requested_isos)


# --------------------------------------------------------------------------- #
# Response parser
# --------------------------------------------------------------------------- #

# Each currency section in the response looks like:
#   <h2 id="LOOKUPS_IEXE0101"> US Dollar </h2>
#   ... <table ...> ... <tbody>
#     <tr ...><td> 2026-05-15 </td><td> 324.7184 </td><td> 0.0031 </td></tr>
#     ... possibly more rows for a date range ...
#   </tbody></table>
#
# We split on <h2 id="LOOKUPS_..."> headings, identify the currency, then pull
# the date/value pairs from the first two <td> cells of each row.
_HEADING_RE = re.compile(r'<h2[^>]*id="LOOKUPS_[^"]*"[^>]*>\s*([^<]+?)\s*</h2>', re.I)
_ROW_RE     = re.compile(r'<tr[^>]*>\s*<td[^>]*>\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*</td>\s*<td[^>]*>\s*([0-9]+(?:\.[0-9]+)?)\s*</td>', re.I)


def _parse_response(html: str, requested_isos: List[str]) -> Dict[date, Dict[str, Decimal]]:
    result: Dict[date, Dict[str, Decimal]] = {}

    # Split the HTML at currency headings. headings[i] = display name; bodies[i] = HTML between this heading and the next.
    headings_iter = list(_HEADING_RE.finditer(html))
    if not headings_iter:
        log.warning("CBSL scraper: no <h2 LOOKUPS_..> sections in response (page structure changed?)")
        return result

    for i, m in enumerate(headings_iter):
        display = m.group(1).strip()
        iso = _HEADING_TO_ISO.get(display.lower())
        if not iso:
            log.info("CBSL scraper: unmapped heading %r — skipping", display)
            continue
        if iso not in requested_isos:
            # Defensive: only process currencies we asked for
            continue

        # Body of this section = from end of this heading to start of next heading (or EOF)
        body_start = m.end()
        body_end = headings_iter[i + 1].start() if i + 1 < len(headings_iter) else len(html)
        body = html[body_start:body_end]

        for row in _ROW_RE.finditer(body):
            d_str, v_str = row.group(1), row.group(2)
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
                v = Decimal(v_str)
            except (ValueError, InvalidOperation):
                log.warning("CBSL scraper: unparseable row %r %r", d_str, v_str)
                continue
            result.setdefault(d, {})[iso] = v

    return result


# --------------------------------------------------------------------------- #
# Convenience: single-day fetch
# --------------------------------------------------------------------------- #

def fetch_single_day(on_date: date, currencies: List[str]) -> Dict[str, Decimal]:
    """Single-day convenience wrapper. Returns {iso: rate} or empty dict."""
    data = fetch_cbsl_rates(on_date, on_date, currencies)
    return data.get(on_date, {})
