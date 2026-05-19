"""fiesta.property.sanity — rental-rate + home-office % sanity checks.

Two yellow warnings (NOT blocks):
    rental_rate_band(monthly_rent_lkr, total_sqft, city)
        Rs/sqft outside the city's reasonable band → warn.

    home_office_percentage_band(home_office_percentage)
        >40% → warn the customer they'll likely need IRA §6 evidence.

Both cite Inland Revenue Act §6 "wholly + exclusively + necessarily"
language so the customer understands WHY the warning is firing.

Source-of-truth table v0.1 — Colombo metro Rs/sqft band 50-300, with a
permissive "other" fallback for non-metro. Replace with CBSL / UDA /
LankaPropertyWeb survey data in v1.1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Rs/sqft per month bands keyed by city casefold.
# v0.1 placeholders — calibration source notes inline.
_CITY_BANDS: dict[str, tuple[float, float]] = {
    # Colombo metro — anchored against 2025-26 LankaPropertyWeb listings
    # and CBSL rent-index dispersion. Wide band reflects neighbourhood
    # variance (Cinnamon Gardens vs. Wellawatte vs. Battaramulla).
    "colombo": (50.0, 300.0),
    "dehiwala": (50.0, 280.0),
    "mount lavinia": (60.0, 280.0),
    "rajagiriya": (60.0, 280.0),
    "battaramulla": (60.0, 280.0),
    "nugegoda": (55.0, 280.0),
    "kotte": (55.0, 280.0),
    "kohuwala": (50.0, 250.0),

    # Other major cities — narrower bands per regional listings
    "kandy": (35.0, 200.0),
    "galle": (30.0, 200.0),
    "negombo": (30.0, 180.0),
    "kurunegala": (25.0, 150.0),
    "matara": (25.0, 150.0),
    "jaffna": (25.0, 150.0),
}

# Permissive fallback band for cities not yet in the table. Errs wide so
# we don't false-warn on smaller towns we have no data for.
_DEFAULT_BAND: tuple[float, float] = (20.0, 350.0)

_HOME_OFFICE_PCT_WARN_THRESHOLD = 40.0


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SanityWarning:
    """One yellow warning. severity = info | warning."""
    code: str
    severity: str
    message: str
    citation: str = ""


@dataclass(frozen=True)
class SanityReport:
    """Container for all sanity checks run on a property+rental."""
    warnings: list[SanityWarning] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _resolve_band(city: Optional[str]) -> tuple[float, float]:
    if not isinstance(city, str):
        return _DEFAULT_BAND
    return _CITY_BANDS.get(city.strip().casefold(), _DEFAULT_BAND)


def rental_rate_band(
    monthly_rent_lkr: float,
    total_sqft: int,
    city: Optional[str],
) -> Optional[SanityWarning]:
    """Yellow warning if Rs/sqft falls outside city band. None otherwise."""
    if monthly_rent_lkr is None or total_sqft is None or total_sqft <= 0:
        return None
    try:
        rs_per_sqft = float(monthly_rent_lkr) / float(total_sqft)
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    lo, hi = _resolve_band(city)
    if rs_per_sqft < lo:
        return SanityWarning(
            code="rate_below_band",
            severity="warning",
            message=(
                f"Rs {rs_per_sqft:,.0f}/sqft is below the typical band for "
                f"{city or 'this area'} (Rs {lo:.0f}-{hi:.0f}/sqft). If the "
                f"landlord is a related party, IRD may treat the under-rent "
                f"as undeclared in-kind compensation."
            ),
            citation=(
                "Inland Revenue Act §195 (related-party transactions) + §6 "
                "(business-expense substance test)"
            ),
        )
    if rs_per_sqft > hi:
        return SanityWarning(
            code="rate_above_band",
            severity="warning",
            message=(
                f"Rs {rs_per_sqft:,.0f}/sqft is above the typical band for "
                f"{city or 'this area'} (Rs {lo:.0f}-{hi:.0f}/sqft). IRD may "
                f"ask for justification; keep evidence of comparable listings "
                f"or a registered-valuer report."
            ),
            citation=(
                "Inland Revenue Act §6 (rental must be wholly, exclusively, "
                "and necessarily incurred to qualify as deductible)"
            ),
        )
    return None


def home_office_percentage_band(
    home_office_percentage: Optional[float],
) -> Optional[SanityWarning]:
    """Yellow warning if home_office_percentage > 40%."""
    if home_office_percentage is None:
        return None
    try:
        pct = float(home_office_percentage)
    except (TypeError, ValueError):
        return None
    if pct <= _HOME_OFFICE_PCT_WARN_THRESHOLD:
        return None
    return SanityWarning(
        code="home_office_pct_high",
        severity="warning",
        message=(
            f"You're claiming {pct:.0f}% of this property as home-office. "
            f"That's high — be ready to show IRD how the space is used "
            f"wholly + exclusively + necessarily for business (photos, "
            f"floor-plan with marked rooms, client-meeting log)."
        ),
        citation=(
            "Inland Revenue Act §6 — wholly, exclusively, and necessarily "
            "test for business deductions"
        ),
    )


def run_sanity_checks(
    monthly_rent_lkr: Optional[float],
    total_sqft: Optional[int],
    city: Optional[str],
    home_office_percentage: Optional[float],
) -> SanityReport:
    """Compose all S7 sanity checks into a single report."""
    warnings: list[SanityWarning] = []

    if monthly_rent_lkr is not None and total_sqft is not None:
        w = rental_rate_band(monthly_rent_lkr, total_sqft, city)
        if w is not None:
            warnings.append(w)

    w2 = home_office_percentage_band(home_office_percentage)
    if w2 is not None:
        warnings.append(w2)

    return SanityReport(warnings=warnings)
