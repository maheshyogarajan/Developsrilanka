"""fiesta.agreements.disclosure -- §195 related-party disclosure orchestration for S8.

Wave 3 (2026-05-20). Per G.1.3 v0.1 + the Wave 4 related_party detector.

What this module does
---------------------
Given a (customer, service_provider, [payments], market_rate_table) input,
compute whether the §195 disclosure clause must default-ON in the generated
Service Agreement PDF, and produce the disclosure-clause text + audit
metadata that the renderer needs.

Per the G.1.3 finding (line 484 of the proposal):
    "Customer-facing override UX: customer can mark 'this is genuinely
     arm's-length, here's why' with required text input (logged to audit,
     NOT hidden disclosure)."

So the contract here is deliberately asymmetric:
- detector says default-ON -> disclosure clause RENDERS, regardless of any
  customer override. The override is captured (audit log) but does NOT
  remove the clause from the PDF. This protects FIESTA from being mis-used
  by a customer who wants the clause silenced.
- detector says default-OFF AND customer has not explicitly toggled the
  clause ON -> disclosure clause DOES NOT render.
- customer explicitly toggles disclosure ON, regardless of detector -> it
  renders. (CEO-side flag: opt-in always allowed.)

Inputs
------
DisclosureDecisionInput is a thin pydantic model so callers can build it
from request bodies, snake-cased dicts, ORM objects -- whatever. The
detector itself remains a pure function (fiesta.compliance.related_party.detect_related_party).

Outputs
-------
DisclosureDecision contains: the rendered clause text (or empty string),
the boolean flag the renderer reads, the audit metadata (signals,
confidence, reasoning, override reason), and an evidence-prompt string
the wizard UI uses to ask the customer for commercial-substance
justification.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fiesta.compliance.related_party import (
    RelatedPartyResult,
    RelatedPartySignal,
    detect_related_party,
)


# The exact clause text. Must be kept in sync with the Jinja2 template
# (service_agreement.j2). Sourced verbatim from G.1.3 v0.1 §7 checklist
# row #15 + the recommended language in §9 open question 6.
SEC195_DISCLOSURE_CLAUSE_TEXT = (
    "**14. RELATED-PARTY DISCLOSURE (Inland Revenue Act No. 24 of 2017, "
    "section 195)**\n\n"
    "**14.1** The Parties acknowledge and disclose that they are connected "
    "persons within the meaning of section 195 of the Inland Revenue Act "
    "(\"associated persons\"). The relationship is: `{relationship}`.\n\n"
    "**14.2** The Parties confirm that the consideration payable under "
    "this Agreement has been benchmarked against arm's-length market rates "
    "for comparable services. Evidence of such benchmarking is retained by "
    "the Contractor and shall be made available to the Inland Revenue "
    "Department on reasonable request.\n\n"
    "**14.3** Market-rate benchmark applied: `{market_rate_benchmark}`.\n\n"
    "**14.4** Commercial-substance justification provided by the "
    "Contractor: `{commercial_substance_justification}`.\n\n"
    "**14.5** This disclosure is made in good faith to enable proper "
    "characterisation of the transaction under section 195 IRA. Failure to "
    "disclose related-party arrangements is a separate breach with its own "
    "penalty regime; FIESTA recommends seeking professional advice if any "
    "of the above statements would be inaccurate."
)


# Customer-facing UI prompt for commercial-substance justification. This
# text is what the wizard shows when the disclosure clause is default-ON
# and the customer is asked "do you accept the disclosure / do you want to
# justify why this is genuinely arm's-length?".
EVIDENCE_PROMPT_DEFAULT = (
    "We detected signals that this engagement may be a related-party "
    "transaction (the Inland Revenue Act calls this 'associated persons' "
    "under section 195). When the parties are related, the law expects a "
    "brief disclosure on the agreement, plus evidence that the fee is in "
    "line with what an unrelated party would charge.\n\n"
    "You can: (a) accept the disclosure clause -- recommended, costs "
    "nothing -- or (b) tell us in 2-3 sentences why this is genuinely "
    "arm's-length and how you priced it. We keep your justification on "
    "file but the disclosure clause still appears in the PDF (the law "
    "requires it; the override is for our audit log, not for the IRD)."
)


class DisclosureDecisionInput(BaseModel):
    """Aggregated inputs the disclosure decision logic needs."""

    model_config = ConfigDict(extra="ignore")

    customer: dict[str, Any] = Field(default_factory=dict)
    service_provider: dict[str, Any] = Field(default_factory=dict)
    payments: list[dict[str, Any]] | None = None
    market_rate_table: dict[str, dict[str, float]] | None = None
    # Customer-supplied flags from the wizard:
    customer_override_reason: str | None = None
    customer_opt_in_disclosure: bool = False
    # Optional pre-filled market-rate benchmark string for §14.3.
    market_rate_benchmark_text: str | None = None
    relationship_label: str | None = None


class DisclosureDecision(BaseModel):
    """What the PDF renderer + audit trail receive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    should_render: bool
    detector_default_on: bool
    customer_opted_in: bool = False
    customer_override_reason: str | None = None
    relationship_label: str | None = None
    market_rate_benchmark: str | None = None
    confidence: float
    audit_substance_risk: str
    signals: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    rendered_clause_text: str
    evidence_prompt: str


def decide_disclosure(
    inputs: DisclosureDecisionInput | dict[str, Any],
) -> DisclosureDecision:
    """Compute whether the §195 disclosure clause should render.

    Pure function. No I/O, no DB writes. Idempotent for identical inputs.

    Polarity (binding, per Wave 4 related_party.py docstring):
      overdetection FINE (customer can opt-out via override-but-not-suppress)
      underdetection FORBIDDEN (Lanka.tax license risk).
    """
    if isinstance(inputs, dict):
        inputs = DisclosureDecisionInput(**inputs)

    detector: RelatedPartyResult = detect_related_party(
        customer=inputs.customer,
        service_provider=inputs.service_provider,
        payments=inputs.payments,
        market_rate_table=inputs.market_rate_table,
    )

    detector_default_on = bool(detector.should_default_on_disclosure)

    # Render if EITHER (a) detector says default ON, OR (b) customer opted in.
    should_render = detector_default_on or bool(inputs.customer_opt_in_disclosure)

    relationship_label = inputs.relationship_label or _derive_relationship_label(
        inputs.customer, detector.signals
    )
    market_rate_benchmark = (
        inputs.market_rate_benchmark_text
        or _derive_market_rate_benchmark_text(detector)
    )

    if should_render:
        clause_text = SEC195_DISCLOSURE_CLAUSE_TEXT.format(
            relationship=relationship_label or "[not stated]",
            market_rate_benchmark=market_rate_benchmark or "[to be supplied]",
            commercial_substance_justification=(
                inputs.customer_override_reason or "[to be supplied]"
            ),
        )
    else:
        clause_text = ""

    return DisclosureDecision(
        should_render=should_render,
        detector_default_on=detector_default_on,
        customer_opted_in=bool(inputs.customer_opt_in_disclosure),
        customer_override_reason=inputs.customer_override_reason,
        relationship_label=relationship_label,
        market_rate_benchmark=market_rate_benchmark,
        confidence=float(detector.confidence),
        audit_substance_risk=detector.audit_substance_risk,
        signals=[s.value for s in detector.signals],
        reasoning=list(detector.reasoning),
        rendered_clause_text=clause_text,
        evidence_prompt=EVIDENCE_PROMPT_DEFAULT,
    )


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _derive_relationship_label(
    customer: dict[str, Any],
    signals: list[RelatedPartySignal],
) -> str | None:
    """Best-effort label of how the parties relate, for §14.1 of the clause.

    Priority order:
      1. customer.stated_relationship_to_service_provider (verbatim)
      2. signal-implied phrase (e.g. SAME_BANK_ACCOUNT -> 'shared bank account')
      3. None -> renderer falls back to "[not stated]".
    """
    if not isinstance(customer, dict):
        return None
    stated = customer.get("stated_relationship_to_service_provider")
    if isinstance(stated, str) and stated.strip():
        return stated.strip().lower()

    # Signal-implied fallback.
    if RelatedPartySignal.SAME_BANK_ACCOUNT in signals:
        return "shared bank account between parties"
    if RelatedPartySignal.SAME_ADDRESS in signals:
        return "shared registered address"
    if RelatedPartySignal.SAME_NIC_PREFIX in signals:
        return "same NIC issuance cohort (likely family/locality match)"
    if RelatedPartySignal.SAME_SURNAME in signals:
        return "shared surname (likely family connection)"
    return None


def _derive_market_rate_benchmark_text(detector: RelatedPartyResult) -> str | None:
    """If the detector ran a market-rate band, surface the band as
    plain-English benchmark text for §14.3."""
    for signal in detector.signals:
        if signal == RelatedPartySignal.ABOVE_MARKET_RATE:
            return (
                "Fee is ABOVE the FIESTA market-rate median band -- "
                "additional commercial-substance evidence required."
            )
        if signal == RelatedPartySignal.BELOW_MARKET_RATE:
            return (
                "Fee is BELOW the FIESTA market-rate median band -- "
                "client may wish to confirm with an arm's-length comparator."
            )
    return None


__all__ = [
    "DisclosureDecision",
    "DisclosureDecisionInput",
    "decide_disclosure",
    "EVIDENCE_PROMPT_DEFAULT",
    "SEC195_DISCLOSURE_CLAUSE_TEXT",
]
