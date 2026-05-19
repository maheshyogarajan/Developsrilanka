"""fiesta.compliance.related_party — §195 related-party signal detection.

Wave 4 v1.0 (2026-05-20). Risk B mitigation per THE_PATH_20260520.md
decision pack (G.1.3 answered DEFAULT-ON).

WHAT IT DOES
------------
Given a (customer, service_provider, [payments]) triple, computes which
related-party signals fire, an aggregate confidence, and a binary
`should_default_on_disclosure` flag. The §195 disclosure toggle MUST default
ON in the FIESTA UI when this flag is True. Reasoning trace explains WHY
each signal fired -- this is the audit-defensibility surface IRD examiners
will look for if they ever scrutinise FIESTA-generated S8/S9 agreements.

WHAT IT DOES NOT DO
-------------------
- Does not call out to SF / DB / network -- pure function.
- Does not author the disclosure clause itself (that's the S8/S9 generator).
- Does not learn from outcomes -- rules-based v1; ML candidate for v1.2+.
- Does not handle non-SL ID formats other than degrade-gracefully.

POLARITY (binding)
------------------
Overdetection is FINE (false-positive surface = "you can switch the
disclosure off if it doesn't apply"). Underdetection is the Lanka.tax
operating-license risk and is FORBIDDEN at the architecture level. Target
false-positive rate: <15%. Target false-negative rate: ~0%.

CONSTRAINTS
-----------
- pydantic v2
- pure functions, no I/O
- type-strict (annotated end-to-end, mypy-clean intent)
- explicit reasoning traces for every signal that fires
- gracefully handle missing/partial inputs without raising
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Public enums + models
# --------------------------------------------------------------------------- #


class RelatedPartySignal(str, Enum):
    """Discrete signals indicating a customer + service_provider may not be
    arm's length under §195 Inland Revenue Act No. 24 of 2017."""

    SAME_NIC_PREFIX = "same_nic_prefix"
    SAME_ADDRESS = "same_address"
    SAME_BANK_ACCOUNT = "same_bank_account"
    STATED_RELATIONSHIP = "stated_relationship"
    SAME_SURNAME = "same_surname"
    IRREGULAR_CADENCE = "irregular_cadence"
    ABOVE_MARKET_RATE = "above_market_rate"
    BELOW_MARKET_RATE = "below_market_rate"


# Stated relationships that ALWAYS imply related-party (per Inland Revenue Act
# definitions of "associated persons" -- spouse, ascendants, descendants,
# siblings, in-laws, and self-deal). "None" / "Independent contractor" do not.
RELATED_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "self",
        "spouse",
        "wife",
        "husband",
        "partner",
        "civil partner",
        "parent",
        "father",
        "mother",
        "child",
        "son",
        "daughter",
        "sibling",
        "brother",
        "sister",
        "in-law",
        "in law",
        "mother-in-law",
        "father-in-law",
        "son-in-law",
        "daughter-in-law",
        "brother-in-law",
        "sister-in-law",
        "grandparent",
        "grandfather",
        "grandmother",
        "grandchild",
        "grandson",
        "granddaughter",
        "uncle",
        "aunt",
        "niece",
        "nephew",
        "cousin",
    }
)

NON_RELATED_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "",
        "none",
        "n/a",
        "na",
        "independent contractor",
        "independent",
        "contractor",
        "vendor",
        "supplier",
        "agency",
        "freelancer",
        "consultant",
        "company",
        "firm",
        "unknown",
        "not related",
        "no relationship",
    }
)


class RelatedPartyResult(BaseModel):
    """Output of detect_related_party. See module docstring for polarity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signals: list[RelatedPartySignal] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    should_default_on_disclosure: bool
    reasoning: list[str] = Field(default_factory=list)
    audit_substance_risk: Literal["low", "medium", "high"]


# --------------------------------------------------------------------------- #
# Signal weights (calibrated for FP target <15%, FN ~0%)
# --------------------------------------------------------------------------- #

_SIGNAL_WEIGHTS: dict[RelatedPartySignal, float] = {
    RelatedPartySignal.STATED_RELATIONSHIP: 1.00,
    RelatedPartySignal.SAME_BANK_ACCOUNT: 0.95,
    RelatedPartySignal.SAME_NIC_PREFIX: 0.55,
    RelatedPartySignal.SAME_ADDRESS: 0.40,
    RelatedPartySignal.SAME_SURNAME: 0.25,
    RelatedPartySignal.IRREGULAR_CADENCE: 0.45,
    RelatedPartySignal.ABOVE_MARKET_RATE: 0.50,
    RelatedPartySignal.BELOW_MARKET_RATE: 0.40,
}

_DEFAULT_ON_THRESHOLD: float = 0.25


# --------------------------------------------------------------------------- #
# Internal helpers -- normalisation
# --------------------------------------------------------------------------- #


def _norm_str(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    return unicodedata.normalize("NFKC", s).strip().casefold()


def _strip_punct(s: str) -> str:
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev: list[int] = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr: list[int] = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


# --------------------------------------------------------------------------- #
# NIC matching
# --------------------------------------------------------------------------- #

_OLD_NIC_RE = re.compile(r"^\s*(\d{9})\s*([vVxX])\s*$")
_NEW_NIC_RE = re.compile(r"^\s*(\d{12})\s*$")


def _parse_nic(nic: str) -> tuple[str, str] | None:
    """Return (format, digits) where format is 'old'|'new'. None if invalid."""
    if not isinstance(nic, str):
        return None
    s = nic.strip()
    m_old = _OLD_NIC_RE.match(s)
    if m_old:
        return ("old", m_old.group(1))
    m_new = _NEW_NIC_RE.match(s)
    if m_new:
        return ("new", m_new.group(1))
    return None


def _nic_prefix_old(digits9: str) -> str | None:
    """5-char family-signature: YY (year) + first 3 of registration district serial."""
    if len(digits9) != 9 or not digits9.isdigit():
        return None
    yy = digits9[0:2]
    sss = digits9[5:8]
    return yy + sss


def _nic_prefix_new(digits12: str) -> str | None:
    """5-char family-signature comparable to old-NIC prefix."""
    if len(digits12) != 12 or not digits12.isdigit():
        return None
    yyyy = digits12[0:4]
    sssss = digits12[7:12]
    return yyyy[2:4] + sssss[0:3]


def same_nic_prefix(nic1: str, nic2: str) -> bool:
    """True if two NICs share the SL family-signature prefix."""
    p1 = _parse_nic(nic1)
    p2 = _parse_nic(nic2)
    if p1 is None or p2 is None:
        return False
    fmt1, d1 = p1
    fmt2, d2 = p2
    prefix1 = _nic_prefix_old(d1) if fmt1 == "old" else _nic_prefix_new(d1)
    prefix2 = _nic_prefix_old(d2) if fmt2 == "old" else _nic_prefix_new(d2)
    if prefix1 is None or prefix2 is None:
        return False
    return prefix1 == prefix2


# --------------------------------------------------------------------------- #
# Address matching
# --------------------------------------------------------------------------- #

_ADDR_NOISE = (
    "no.", "no ", "#", "apt.", "apt ", "apartment", "unit", "flat",
    "room", "rm.", "rm ",
)


def _normalise_address_line(line: str) -> str:
    n = _norm_str(line)
    n = _strip_punct(n)
    for noise in _ADDR_NOISE:
        n = n.replace(noise, " ")
    return re.sub(r"\s+", " ", n).strip()


def same_address(addr1: dict[str, Any] | None, addr2: dict[str, Any] | None) -> bool:
    """True if two address dicts likely refer to the same physical location."""
    if not isinstance(addr1, dict) or not isinstance(addr2, dict):
        return False

    s1 = _normalise_address_line(addr1.get("street", "") or "")
    s2 = _normalise_address_line(addr2.get("street", "") or "")
    if not s1 or not s2:
        return False

    l1 = _normalise_address_line(addr1.get("locality", "") or "")
    l2 = _normalise_address_line(addr2.get("locality", "") or "")

    p1 = _norm_str(addr1.get("postcode", ""))
    p2 = _norm_str(addr2.get("postcode", ""))
    if p1 and p2 and p1 != p2:
        return False

    street_match = (s1 == s2) or (_levenshtein(s1, s2) < 3)
    locality_match = (
        (not l1 or not l2) or (l1 == l2) or (_levenshtein(l1, l2) < 3)
    )
    return bool(street_match and locality_match)


# --------------------------------------------------------------------------- #
# Bank account matching
# --------------------------------------------------------------------------- #


def _normalise_bank_account(acct: Any) -> str:
    if not isinstance(acct, str):
        return ""
    return re.sub(r"[\s\-\.]+", "", acct).casefold()


def same_bank_account(acct1: Any, acct2: Any) -> bool:
    """Exact match after normalising whitespace / punctuation."""
    n1 = _normalise_bank_account(acct1)
    n2 = _normalise_bank_account(acct2)
    if not n1 or not n2:
        return False
    return n1 == n2


# --------------------------------------------------------------------------- #
# Surname matching
# --------------------------------------------------------------------------- #


def _tokenise_name(name: Any) -> list[str]:
    n = _norm_str(name)
    n = _strip_punct(n)
    return [t for t in n.split() if t]


def same_surname(name1: Any, name2: Any) -> bool:
    """Last-token OR first-token match (>=3 chars). Returns False on Sinhala-only."""
    t1 = _tokenise_name(name1)
    t2 = _tokenise_name(name2)
    if not t1 or not t2:
        return False

    candidates_1 = {t1[-1]}
    if len(t1) > 1:
        candidates_1.add(t1[0])
    candidates_2 = {t2[-1]}
    if len(t2) > 1:
        candidates_2.add(t2[0])

    candidates_1 = {c for c in candidates_1 if len(c) >= 3}
    candidates_2 = {c for c in candidates_2 if len(c) >= 3}
    if not candidates_1 or not candidates_2:
        return False

    return bool(candidates_1 & candidates_2)


# --------------------------------------------------------------------------- #
# Cadence detection
# --------------------------------------------------------------------------- #


def _parse_payment_date(d: Any) -> date | None:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d.strip()).date()
        except ValueError:
            return None
    return None


def irregular_cadence(payments: Iterable[dict[str, Any]] | None) -> bool:
    """CoV of inter-payment intervals > 0.5 -> irregular. Needs >=3 payments."""
    if not payments:
        return False
    parsed: list[date] = []
    for p in payments:
        if not isinstance(p, dict):
            continue
        d = _parse_payment_date(p.get("date") or p.get("payment_date"))
        if d is not None:
            parsed.append(d)
    if len(parsed) < 3:
        return False
    parsed.sort()
    intervals = [
        (parsed[i] - parsed[i - 1]).days for i in range(1, len(parsed))
    ]
    if not intervals:
        return False
    mean = statistics.mean(intervals)
    if mean <= 0:
        return False
    stdev = statistics.pstdev(intervals)
    cov = stdev / mean
    return cov > 0.5


# --------------------------------------------------------------------------- #
# Market-rate matching
# --------------------------------------------------------------------------- #


def market_rate_band(
    service_type: str,
    monthly_fee_lkr: float,
    rate_table: dict[str, dict[str, float]],
) -> Literal["below", "within", "above", "unknown"]:
    """Compare monthly_fee_lkr against the loaded market-rate table.

    Bands:
        - 'below'   : fee < 0.5 * median
        - 'within'  : 0.5 * median <= fee <= 2.0 * median
        - 'above'   : fee > 2.0 * median
        - 'unknown' : service_type not in table OR fee non-positive
    """
    if not isinstance(service_type, str) or monthly_fee_lkr <= 0:
        return "unknown"
    key = service_type.strip().casefold()
    entry = rate_table.get(key)
    if not isinstance(entry, dict):
        return "unknown"
    median = entry.get("median_monthly_lkr")
    if not isinstance(median, (int, float)) or median <= 0:
        return "unknown"
    if monthly_fee_lkr > 2.0 * median:
        return "above"
    if monthly_fee_lkr < 0.5 * median:
        return "below"
    return "within"


# --------------------------------------------------------------------------- #
# Top-level orchestrator
# --------------------------------------------------------------------------- #


def detect_related_party(
    customer: dict[str, Any],
    service_provider: dict[str, Any],
    payments: list[dict[str, Any]] | None = None,
    market_rate_table: dict[str, dict[str, float]] | None = None,
) -> RelatedPartyResult:
    """Detect §195 related-party signals between customer and service_provider.

    See module docstring. Polarity: overdetection fine, underdetection forbidden.
    """
    signals: list[RelatedPartySignal] = []
    reasoning: list[str] = []

    if not isinstance(customer, dict) or not isinstance(service_provider, dict):
        return RelatedPartyResult(
            signals=[],
            confidence=0.0,
            should_default_on_disclosure=False,
            reasoning=["inputs were not dicts -- skipped all checks"],
            audit_substance_risk="low",
        )

    # ---- STATED_RELATIONSHIP -----------------------------------------------
    stated = _norm_str(
        customer.get("stated_relationship_to_service_provider", "")
    )
    if stated:
        if stated in RELATED_RELATIONSHIPS:
            signals.append(RelatedPartySignal.STATED_RELATIONSHIP)
            reasoning.append(
                f"customer declared relationship '{stated}' -- always "
                "treated as related-party under IRA s.195 associated-person "
                "definitions"
            )
        elif stated in NON_RELATED_RELATIONSHIPS:
            reasoning.append(
                f"customer declared relationship '{stated}' -- treated as "
                "arm's-length; other signals still evaluated"
            )
        else:
            signals.append(RelatedPartySignal.STATED_RELATIONSHIP)
            reasoning.append(
                f"customer declared unrecognised relationship '{stated}' "
                "-- treated conservatively as related-party (manual review "
                "recommended)"
            )

    # ---- SAME_NIC_PREFIX ---------------------------------------------------
    nic_c = customer.get("nic", "")
    nic_sp = service_provider.get("nic", "")
    if nic_c and nic_sp and same_nic_prefix(nic_c, nic_sp):
        signals.append(RelatedPartySignal.SAME_NIC_PREFIX)
        reasoning.append(
            "NIC family-signature prefix matches: same birth-year band + "
            "same registration district. Strong signal of familial relation "
            "in SL (district registration is hereditary at village level)."
        )

    # ---- SAME_ADDRESS ------------------------------------------------------
    addr_c = customer.get("address")
    addr_sp = service_provider.get("address")
    if same_address(addr_c, addr_sp):
        signals.append(RelatedPartySignal.SAME_ADDRESS)
        reasoning.append(
            "customer and service provider share an address. Could be "
            "cohabitation (family) OR shared workspace (roommate); on its "
            "own it is a moderate signal -- corroborate with NIC / name / "
            "bank-account."
        )

    # ---- SAME_BANK_ACCOUNT -------------------------------------------------
    bank_c = customer.get("bank_account", "")
    bank_sp = service_provider.get("bank_account", "")
    if bank_c and bank_sp and same_bank_account(bank_c, bank_sp):
        signals.append(RelatedPartySignal.SAME_BANK_ACCOUNT)
        reasoning.append(
            "customer and service provider use the SAME bank account "
            "number. Near-definitive self-deal or pooled-funds arrangement; "
            "almost always related-party."
        )

    # ---- SAME_SURNAME ------------------------------------------------------
    name_c = customer.get("name", "")
    name_sp = service_provider.get("name", "")
    if name_c and name_sp and same_surname(name_c, name_sp):
        signals.append(RelatedPartySignal.SAME_SURNAME)
        reasoning.append(
            "customer and service provider share a hereditary surname "
            "token. Weak signal alone (SL surnames cluster geographically); "
            "elevates confidence only when combined with NIC / address."
        )

    # ---- IRREGULAR_CADENCE -------------------------------------------------
    if payments:
        if irregular_cadence(payments):
            signals.append(RelatedPartySignal.IRREGULAR_CADENCE)
            reasoning.append(
                "inter-payment intervals have coefficient-of-variation > "
                "0.5 -- timing irregular vs claimed monthly/quarterly "
                "retainer. Possible economic-substance gap."
            )

    # ---- ABOVE / BELOW MARKET RATE -----------------------------------------
    if market_rate_table is not None:
        svc_type = service_provider.get("service_type", "")
        fee_raw = service_provider.get("monthly_fee_lkr")
        if isinstance(svc_type, str) and isinstance(fee_raw, (int, float)):
            band = market_rate_band(svc_type, float(fee_raw), market_rate_table)
            if band == "above":
                signals.append(RelatedPartySignal.ABOVE_MARKET_RATE)
                reasoning.append(
                    f"declared monthly fee LKR {fee_raw:,.0f} for "
                    f"'{svc_type}' is > 2x market median per FIESTA "
                    f"market-rate table v0.1. Above-market rates are a "
                    f"profit-shifting signal."
                )
            elif band == "below":
                signals.append(RelatedPartySignal.BELOW_MARKET_RATE)
                reasoning.append(
                    f"declared monthly fee LKR {fee_raw:,.0f} for "
                    f"'{svc_type}' is < 0.5x market median per FIESTA "
                    f"market-rate table v0.1. Below-market rates may "
                    f"indicate undeclared in-kind compensation."
                )

    # ---- Aggregate confidence (complement-product OR) ----------------------
    if signals:
        complement = 1.0
        for s in signals:
            complement *= (1.0 - _SIGNAL_WEIGHTS[s])
        confidence = 1.0 - complement
    else:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    # ---- Audit substance risk ----------------------------------------------
    has_strong_definitive = bool(
        {
            RelatedPartySignal.STATED_RELATIONSHIP,
            RelatedPartySignal.SAME_BANK_ACCOUNT,
        }
        & set(signals)
    )
    has_relational = bool(
        {
            RelatedPartySignal.SAME_NIC_PREFIX,
            RelatedPartySignal.SAME_ADDRESS,
            RelatedPartySignal.SAME_BANK_ACCOUNT,
            RelatedPartySignal.STATED_RELATIONSHIP,
        }
        & set(signals)
    )
    has_economic = bool(
        {
            RelatedPartySignal.ABOVE_MARKET_RATE,
            RelatedPartySignal.BELOW_MARKET_RATE,
            RelatedPartySignal.IRREGULAR_CADENCE,
        }
        & set(signals)
    )

    audit_substance_risk: Literal["low", "medium", "high"]
    if has_strong_definitive or (has_relational and has_economic):
        audit_substance_risk = "high"
    elif has_relational or has_economic:
        audit_substance_risk = "medium"
    else:
        audit_substance_risk = "low"

    should_default_on = confidence >= _DEFAULT_ON_THRESHOLD

    if not signals:
        reasoning.append(
            "no related-party signals detected -- arrangement appears "
            "arm's-length on available evidence"
        )

    return RelatedPartyResult(
        signals=signals,
        confidence=round(confidence, 4),
        should_default_on_disclosure=should_default_on,
        reasoning=reasoning,
        audit_substance_risk=audit_substance_risk,
    )
