"""fiesta.assets_liabilities.fa_push — Optional FA 5192455 push for Lanka.tax customers.

Feature 9 D9 (PLAN_X9_COMPLETION §5).

Workflow:
  1. Detect SF Customer__c match by email using an existing SF helper if available.
     Greps fiesta/integrations/ and fiesta/persona/ for sf_customer_by_email
     or equivalent. If none found, logs a skip and returns {"skipped": True}.
  2. If a match is found, POST pre-filled A&L data to the FA 5192455 API
     endpoint, reusing existing FA API wiring if present.
  3. On success, the caller is responsible for writing fa_submission_id
     back to AssetEntry rows so the result is persisted (done in routes.py).

Acceptance (D9 spec):
  - For Lanka.tax-linked customer: A&L data pushes to FA 5192455.
  - For non-linked customer: function returns {"skipped": True, "reason": "no_sf_match"}.
  - If SF match helper not found: returns {"skipped": True, "reason": "no_helper"}.

FA endpoint:
  POST https://app.tfaforms.com/api_v2/responses/5192455
  Auth: Bearer token from env FA_API_TOKEN (same env var used by other FA wiring).
  Content-Type: application/json
  Body: {tfa_<fieldId>: value, ...}  — field IDs TBD per FA form inspection.

NOTE: The exact FA field IDs for form 5192455 are not available via static
analysis (they live in the FA form definition). On first push for a real
Lanka.tax customer, inspect the FA API response or use the FA MCP tool
(mcp__FormAssembly__fa_get_form) to retrieve field IDs and update
FA_FORM_5192455_FIELD_MAP below.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FA endpoint config
# ---------------------------------------------------------------------------
FA_FORM_ID = "5192455"
FA_BASE_URL = "https://app.tfaforms.com/api_v2/responses"

# Placeholder field map — update when FA form field IDs are inspected.
# Keys are FA field IDs (e.g. "tfa_123"); values are callables that extract
# the relevant data from the bundled al_data dict.
FA_FORM_5192455_FIELD_MAP: dict[str, Any] = {
    # "tfa_REPLACE_ME_TAXPAYER_NAME": lambda d: d.get("user_name", ""),
    # "tfa_REPLACE_ME_NIC":           lambda d: d.get("user_nic", ""),
    # "tfa_REPLACE_ME_TAX_YEAR":      lambda d: d.get("tax_year", ""),
    # "tfa_REPLACE_ME_TOTAL_ASSETS":  lambda d: str(d.get("total_assets_lkr", "")),
    # "tfa_REPLACE_ME_TOTAL_LIAB":    lambda d: str(d.get("total_liabilities_lkr", "")),
    # "tfa_REPLACE_ME_NET_WORTH":     lambda d: str(d.get("net_worth_lkr", "")),
}
_FIELD_MAP_POPULATED = bool(
    [k for k in FA_FORM_5192455_FIELD_MAP if "REPLACE_ME" not in k]
)


# ---------------------------------------------------------------------------
# SF Customer match — try known helpers, fall back to stub
# ---------------------------------------------------------------------------
def _find_sf_customer_by_email(email: str) -> dict | None:
    """Return SF Customer__c record dict {Id, Name, Email__c} or None.

    Tries to import existing SF helpers in priority order:
      1. fiesta.integrations.sf_client (if it has sf_customer_by_email)
      2. fiesta.persona.sf_utils       (common location in this codebase)
      3. Stub (logs and returns None)
    """
    # --- attempt 1: fiesta.integrations ---
    try:
        from fiesta.integrations import sf_client as _sf  # type: ignore[import]
        if hasattr(_sf, "sf_customer_by_email"):
            result = _sf.sf_customer_by_email(email)
            if result:
                log.info("fa_push: SF match found via fiesta.integrations.sf_client")
            return result  # may be None
    except ImportError:
        pass
    except Exception as exc:
        log.warning("fa_push: fiesta.integrations.sf_client raised: %s", exc)

    # --- attempt 2: fiesta.persona ---
    try:
        from fiesta.persona import sf_utils as _su  # type: ignore[import]
        if hasattr(_su, "sf_customer_by_email"):
            result = _su.sf_customer_by_email(email)
            if result:
                log.info("fa_push: SF match found via fiesta.persona.sf_utils")
            return result
    except ImportError:
        pass
    except Exception as exc:
        log.warning("fa_push: fiesta.persona.sf_utils raised: %s", exc)

    # --- stub ---
    log.info(
        "fa_push: SF match helper not found — searched fiesta.integrations.sf_client "
        "and fiesta.persona.sf_utils. Push skipped."
    )
    return None


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------
def push_to_fa_5192455(
    user,                    # Flask-Login user object (has .email, .id)
    al_data: dict,           # bundled A&L data (see _build_al_data below)
) -> dict:
    """Push A&L data to FA form 5192455 for Lanka.tax-linked customers.

    Parameters
    ----------
    user    : current_user (must have .email attribute)
    al_data : dict with keys:
                user_name, user_nic, tax_year,
                total_assets_lkr, total_liabilities_lkr, net_worth_lkr,
                assets  (list of dicts)
                liabilities (list of dicts)

    Returns
    -------
    dict with keys:
      success         : bool
      submission_id   : str | None
      skipped         : bool
      reason          : str (human-readable)
    """
    email = getattr(user, "email", None) or ""
    if not email:
        return {"success": False, "skipped": True, "reason": "no_email", "submission_id": None}

    # Step 1 — SF match
    sf_customer = _find_sf_customer_by_email(email)
    if sf_customer is None:
        return {
            "success": False,
            "skipped": True,
            "reason": "no_sf_match",
            "submission_id": None,
        }

    sf_id = sf_customer.get("Id", "")
    log.info("fa_push: SF match Id=%s for email=%s", sf_id, email)

    # Step 2 — field map check
    if not _FIELD_MAP_POPULATED:
        log.warning(
            "fa_push: FA_FORM_5192455_FIELD_MAP has only placeholder keys. "
            "Inspect form 5192455 to populate real field IDs. Push skipped."
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "field_map_not_configured",
            "submission_id": None,
        }

    # Step 3 — build FA payload
    payload: dict[str, Any] = {
        "tfa_dbControl": {"id": FA_FORM_ID},
    }
    for fa_key, extractor in FA_FORM_5192455_FIELD_MAP.items():
        try:
            payload[fa_key] = extractor(al_data)
        except Exception as exc:
            log.warning("fa_push: field extractor %s failed: %s", fa_key, exc)

    # Step 4 — POST to FA
    fa_token = os.environ.get("FA_API_TOKEN", "")
    if not fa_token:
        log.warning("fa_push: FA_API_TOKEN not set in env. Push skipped.")
        return {
            "success": False,
            "skipped": True,
            "reason": "fa_token_missing",
            "submission_id": None,
        }

    try:
        import requests  # type: ignore[import]
        resp = requests.post(
            f"{FA_BASE_URL}/{FA_FORM_ID}",
            json=payload,
            headers={
                "Authorization": f"Bearer {fa_token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        resp_json = resp.json()
        submission_id = str(resp_json.get("id", resp_json.get("submissionId", "")))
        log.info("fa_push: FA 5192455 submission_id=%s for SF customer=%s", submission_id, sf_id)
        return {
            "success": True,
            "skipped": False,
            "reason": "pushed",
            "submission_id": submission_id,
            "sf_customer_id": sf_id,
        }
    except Exception as exc:
        log.error("fa_push: POST to FA 5192455 failed: %s", exc)
        return {
            "success": False,
            "skipped": False,
            "reason": f"fa_post_failed: {exc}",
            "submission_id": None,
        }


# ---------------------------------------------------------------------------
# Helper: build al_data dict from model instances
# ---------------------------------------------------------------------------
def build_al_data(
    user_name: str,
    user_nic: str,
    tax_year: str,
    assets: Sequence[Any],
    liabilities: Sequence[Any],
) -> dict:
    """Build the al_data dict expected by push_to_fa_5192455 and the PDF generator."""
    total_assets = sum(
        int(getattr(a, "value_lkr_cents", 0) or 0) for a in assets
    )
    total_liab = sum(
        int(getattr(lb, "balance_lkr_cents", 0) or 0) for lb in liabilities
    )
    net_worth = total_assets - total_liab

    def _cents_to_lkr(c: int) -> str:
        return str((Decimal(c) / Decimal(100)).quantize(Decimal("0.01")))

    return {
        "user_name": user_name,
        "user_nic": user_nic,
        "tax_year": tax_year,
        "total_assets_lkr": _cents_to_lkr(total_assets),
        "total_liabilities_lkr": _cents_to_lkr(total_liab),
        "net_worth_lkr": _cents_to_lkr(net_worth),
        "assets": [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in assets],
        "liabilities": [lb.to_dict() if hasattr(lb, "to_dict") else dict(lb) for lb in liabilities],
    }


__all__ = ["push_to_fa_5192455", "build_al_data", "FA_FORM_ID"]
