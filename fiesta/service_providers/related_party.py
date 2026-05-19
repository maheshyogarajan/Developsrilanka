"""fiesta.service_providers.related_party — S6 ↔ §195 detector glue.

This module is the THIN integration layer between S6 (Service Provider
CRUD) and the detector that lives at fiesta.compliance.related_party.

Responsibilities:
    - Load + cache the market_rates_table.yaml (once per process).
    - Build the (customer, sp, payments) triple the detector expects from
      a (user, ServiceProvider) pair.
    - Call detect_related_party() and return the typed RelatedPartyResult.
    - Persist (upsert) the result onto ServiceProviderRelationship rows.
    - Sync ServiceProvider.requires_disclosure from the result.

This module deliberately does NOT:
    - Mutate the detector's behaviour (its polarity contract is binding).
    - Author the disclosure clause itself (that lives in S8 generator).
    - Wrap the detector in a try/except that hides exceptions — bugs
      should surface, since underdetection is a Lanka.tax operating-licence
      risk per the detector's own design doc.
"""
from __future__ import annotations

import logging
import os
import pathlib
from datetime import datetime
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detector imports (defensive — module must be importable even if the
# detector is missing in a stripped-down test env).
# ---------------------------------------------------------------------------
try:
    from fiesta.compliance.related_party import (
        RelatedPartyResult,
        RelatedPartySignal,
        detect_related_party,
    )
    _HAS_DETECTOR = True
except Exception as exc:  # pragma: no cover
    logger.error(
        "fiesta.compliance.related_party unavailable — S6 §195 integration "
        "WILL FAIL CLOSED: %s", exc
    )
    _HAS_DETECTOR = False
    RelatedPartyResult = None  # type: ignore[misc,assignment]
    RelatedPartySignal = None  # type: ignore[misc,assignment]

    def detect_related_party(*args: Any, **kwargs: Any):  # type: ignore[misc]
        raise RuntimeError(
            "fiesta.compliance.related_party.detect_related_party not "
            "importable — refusing to silently treat as 'no signals fired'. "
            "Underdetection is a hard constraint per §195 polarity contract."
        )

from fiesta.service_providers.models import (
    ServiceProvider,
    ServiceProviderRelationship,
    STATED_RELATIONSHIP_TO_DETECTOR,
)

# ---------------------------------------------------------------------------
# Market-rate table loader (lazy, cached, fail-soft).
#
# The table is a v0.1 placeholder shipped at fiesta/compliance/market_rates_table.yaml.
# If it can't be loaded the detector simply does not fire the
# ABOVE_MARKET_RATE / BELOW_MARKET_RATE signals — the relational signals
# (NIC / address / bank / surname / stated) still work. This is the correct
# graceful degradation: a missing market-rate table reduces precision but
# does NOT compromise underdetection on definitive signals.
# ---------------------------------------------------------------------------
_MARKET_RATES_CACHE: Optional[dict[str, dict[str, float]]] = None


def _load_market_rates() -> dict[str, dict[str, float]]:
    """Load market_rates_table.yaml -> dict for detector. Cached after first call."""
    global _MARKET_RATES_CACHE
    if _MARKET_RATES_CACHE is not None:
        return _MARKET_RATES_CACHE

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "compliance"
        / "market_rates_table.yaml"
    )
    if not path.exists():
        logger.warning(
            "market_rates_table.yaml missing at %s — ABOVE/BELOW market "
            "signals will not fire. Relational signals unaffected.", path
        )
        _MARKET_RATES_CACHE = {}
        return _MARKET_RATES_CACHE

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        logger.warning(
            "pyyaml not available — skipping market-rate table load. "
            "Relational signals unaffected."
        )
        _MARKET_RATES_CACHE = {}
        return _MARKET_RATES_CACHE

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "market_rates_table.yaml load failed (%s) — skipping. "
            "Relational signals unaffected.", exc
        )
        _MARKET_RATES_CACHE = {}
        return _MARKET_RATES_CACHE

    # The table file is a top-level mapping; we want service_type -> {median_monthly_lkr}.
    # File format: { service_type: {median_monthly_lkr: N, ...}, ... } OR
    # under a top-level "rates" key. Handle both gracefully.
    out: dict[str, dict[str, float]] = {}
    rates_section = raw.get("rates") if isinstance(raw, dict) else None
    if isinstance(rates_section, dict):
        source = rates_section
    elif isinstance(raw, dict):
        source = raw
    else:
        source = {}

    for k, v in source.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        med = v.get("median_monthly_lkr")
        if isinstance(med, (int, float)) and med > 0:
            out[k.strip().casefold()] = {"median_monthly_lkr": float(med)}

    _MARKET_RATES_CACHE = out
    logger.info(
        "Loaded market-rates table: %d service-types calibrated.", len(out)
    )
    return out


def reset_market_rates_cache() -> None:
    """Test helper: drop the cached table so tests can re-load it cleanly."""
    global _MARKET_RATES_CACHE
    _MARKET_RATES_CACHE = None


# ---------------------------------------------------------------------------
# Customer-profile loader.
#
# We need the customer's own (name, nic, address, bank_account) to compute
# the relational signals. In production these come from the FIESTA User
# / Profile model (S3). For unit-test isolation we accept a `customer_dict`
# kwarg that bypasses DB lookup.
# ---------------------------------------------------------------------------
def _load_customer_profile(user_id: int) -> dict[str, Any]:
    """Load the customer's profile fields for the detector.

    Falls back to empty-string fields if the User / Profile model can't be
    queried. That's safe: missing fields make the detector emit FEWER
    signals, never more — but since we're operating against the user's
    OWN data, this only means a NIC/address/bank coincidence will be
    missed, NOT a stated_relationship signal (the latter comes from the
    SP record, not the customer profile).
    """
    try:
        from app import db  # noqa: F401 (only need to ensure session)
        from models import User  # type: ignore[import-not-found]
    except Exception:
        return {"name": "", "nic": "", "address": None, "bank_account": ""}

    try:
        user = User.query.filter_by(id=user_id).first()
    except Exception as exc:  # pragma: no cover
        logger.warning("user lookup failed for id=%s: %s", user_id, exc)
        return {"name": "", "nic": "", "address": None, "bank_account": ""}
    if user is None:
        return {"name": "", "nic": "", "address": None, "bank_account": ""}

    # Field-name mapping is best-effort — the FIESTA User model has
    # evolved through several waves. We use getattr with sensible
    # defaults so we never raise on an absent column.
    name = (
        getattr(user, "full_name", None)
        or getattr(user, "name", None)
        or getattr(user, "display_name", None)
        or ""
    )
    nic = (
        getattr(user, "nic", None)
        or getattr(user, "nic_number", None)
        or getattr(user, "national_id", None)
        or ""
    )
    bank = (
        getattr(user, "bank_account_number", None)
        or getattr(user, "primary_bank_account", None)
        or ""
    )

    addr: Optional[dict[str, Any]] = None
    street = getattr(user, "address_line1", None) or getattr(user, "address", None)
    if street:
        addr = {
            "street": street,
            "locality": getattr(user, "city", None) or "",
            "postcode": getattr(user, "postcode", None) or "",
        }

    return {
        "name": name or "",
        "nic": nic or "",
        "address": addr,
        "bank_account": bank or "",
    }


# ---------------------------------------------------------------------------
# Detection runner.
# ---------------------------------------------------------------------------
def run_detection_for_sp(
    sp: ServiceProvider,
    payments: Optional[Iterable[dict[str, Any]]] = None,
    customer_dict: Optional[dict[str, Any]] = None,
):  # -> RelatedPartyResult (typed once detector import succeeds)
    """Run the §195 detector for one ServiceProvider.

    Parameters
    ----------
    sp : ServiceProvider
        The SP whose relationship to the customer we're assessing.
    payments : iterable of dicts (optional)
        Payment ledger entries for IRREGULAR_CADENCE signal. Pass None
        if not yet collected — cadence simply won't fire.
    customer_dict : dict (optional)
        Pre-built customer profile. When None, we'll load it from the
        User model via sp.user_id.

    Returns
    -------
    RelatedPartyResult — the typed, frozen result. Caller persists it
    via persist_detection_result().
    """
    if customer_dict is None:
        customer_dict = _load_customer_profile(sp.user_id)

    # Map the SP's stated_relationship_to_customer (UI value) to the
    # detector's free-text vocabulary, and inject it into customer_dict
    # under the detector's expected key.
    stated_ui = sp.stated_relationship_to_customer or ""
    stated_detector = STATED_RELATIONSHIP_TO_DETECTOR.get(stated_ui, "")
    customer_dict = {
        **customer_dict,
        "stated_relationship_to_service_provider": stated_detector,
    }

    sp_dict = sp.to_detector_dict()
    market_rates = _load_market_rates()

    result = detect_related_party(
        customer=customer_dict,
        service_provider=sp_dict,
        payments=list(payments) if payments is not None else None,
        market_rate_table=market_rates or None,
    )
    return result


def persist_detection_result(sp: ServiceProvider, result, db_session=None):
    """Upsert ServiceProviderRelationship from a RelatedPartyResult.

    Also syncs sp.requires_disclosure from the result. Caller is
    responsible for the commit (we don't commit here so the routes layer
    can wrap multiple writes in a single transaction).

    Parameters
    ----------
    sp : ServiceProvider
        The SP whose detection result we're caching. Must have a primary
        key (i.e. must have been flushed at least once).
    result : RelatedPartyResult
        Detector output.
    db_session : SQLAlchemy session (optional)
        When None, falls back to `from app import db`.
    """
    if db_session is None:
        from app import db as _db
        db_session = _db.session

    # Detect existing row.
    rel: Optional[ServiceProviderRelationship] = (
        db_session.query(ServiceProviderRelationship)
        .filter_by(sp_id=sp.id)
        .first()
    )

    # Serialize signals as list of enum-values (string) — JSON safe.
    signal_values: list[str] = []
    for s in (result.signals or []):
        try:
            signal_values.append(s.value if hasattr(s, "value") else str(s))
        except Exception:  # pragma: no cover
            signal_values.append(str(s))

    payload = dict(
        sp_id=sp.id,
        user_id=sp.user_id,
        signals=signal_values,
        confidence=float(result.confidence),
        should_default_on_disclosure=bool(result.should_default_on_disclosure),
        audit_substance_risk=str(result.audit_substance_risk),
        reasoning=list(result.reasoning or []),
        last_detected_at=datetime.utcnow(),
    )

    if rel is None:
        rel = ServiceProviderRelationship(**payload)
        db_session.add(rel)
    else:
        for k, v in payload.items():
            setattr(rel, k, v)

    # Sync the SP's denormalized disclosure flag honouring the customer
    # override if one is set; otherwise the detector's default-on flag.
    if rel.customer_disclosure_override is not None:
        sp.requires_disclosure = bool(rel.customer_disclosure_override)
    else:
        sp.requires_disclosure = bool(result.should_default_on_disclosure)

    db_session.add(sp)
    return rel
