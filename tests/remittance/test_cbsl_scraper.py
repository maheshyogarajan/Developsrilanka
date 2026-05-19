"""
Wave B1.1 — CBSL scraper tests.

Pure-function tests for the HTML parser (no network). The live-network smoke
runs separately via fx_rate_service end-to-end in the container.
"""
from datetime import date
from decimal import Decimal

import pytest

from cbsl_scraper import _parse_response, fetch_single_day, CBSL_CURRENCY_MAP


SAMPLE_HTML = """
<html><body>
<h2 id="LOOKUPS_IEXE0101"> US Dollar </h2>
<div class="table-responsive"><table class="table"><thead><tr>
<th>Date</th><th>1 USD -> LKR</th><th>1 LKR -> USD</th></tr></thead><tbody>
<tr><td> 2026-05-15 </td><td> 324.7184 </td><td> 0.0031 </td></tr>
<tr><td> 2026-05-14 </td><td> 323.9586 </td><td> 0.0031 </td></tr>
</tbody></table></div>
<h2 id="LOOKUPS_IEXE0101"> Sterling Pound </h2>
<div class="table-responsive"><table class="table"><thead><tr>
<th>Date</th><th>1 GBP -> LKR</th><th>1 LKR -> GBP</th></tr></thead><tbody>
<tr><td> 2026-05-15 </td><td> 434.1160 </td><td> 0.0023 </td></tr>
</tbody></table></div>
<h2 id="LOOKUPS_IEXE0101"> UAE  Dirham </h2>
<div class="table-responsive"><table class="table"><thead><tr>
<th>Date</th><th>1 AED -> LKR</th><th>1 LKR -> AED</th></tr></thead><tbody>
<tr><td> 2026-05-15 </td><td> 88.4045 </td><td> 0.0113 </td></tr>
</tbody></table></div>
</body></html>
"""


def test_parser_extracts_usd():
    result = _parse_response(SAMPLE_HTML, ["USD", "GBP", "AED"])
    d15 = date(2026, 5, 15)
    assert d15 in result
    assert result[d15]["USD"] == Decimal("324.7184")
    assert result[d15]["GBP"] == Decimal("434.1160")
    assert result[d15]["AED"] == Decimal("88.4045")


def test_parser_handles_range():
    result = _parse_response(SAMPLE_HTML, ["USD"])
    assert result[date(2026, 5, 15)]["USD"] == Decimal("324.7184")
    assert result[date(2026, 5, 14)]["USD"] == Decimal("323.9586")


def test_parser_respects_double_space_currency_name():
    """UAE Dirham appears as 'UAE  Dirham' (two spaces) in the CBSL HTML.
    The heading→ISO map must tolerate both single and double space."""
    result = _parse_response(SAMPLE_HTML, ["AED"])
    assert result[date(2026, 5, 15)]["AED"] == Decimal("88.4045")


def test_parser_returns_empty_on_garbage():
    result = _parse_response("<html>no exchange data here</html>", ["USD"])
    assert result == {}


def test_parser_skips_unrequested_currencies():
    """Defensive: even if CBSL returns more sections than we asked for,
    we only fill the ones the caller wanted."""
    result = _parse_response(SAMPLE_HTML, ["USD"])
    # GBP and AED are present in the HTML but not in requested_isos
    assert "GBP" not in result.get(date(2026, 5, 15), {})
    assert "AED" not in result.get(date(2026, 5, 15), {})


def test_currency_map_covers_top_10():
    """Sanity: the top-10 currencies for SL foreign-income earners are mapped."""
    for iso in ["USD", "GBP", "EUR", "AUD", "AED", "CAD", "SGD", "JPY", "CHF", "NZD"]:
        assert iso in CBSL_CURRENCY_MAP, f"{iso} missing from CBSL_CURRENCY_MAP"


def test_currency_map_format():
    """Each value must be 'CODE~Display Name' — the CBSL form-value format."""
    for iso, v in CBSL_CURRENCY_MAP.items():
        assert v.startswith(iso + "~"), f"{iso}: bad value format {v!r}"


def test_fetch_invalid_date_range():
    # start > end → empty
    assert fetch_single_day(date(1999, 1, 1), ["USD"]) == {}  # before historical floor
