"""fiesta.delivery_ops.autoreply — Commaut2.0 inbound -> F-code template auto-reply (Tier 1 gated).

This module ports the Lanka.tax inbound-email routing + F-code template
auto-reply pattern into FIESTA. CEO direct ask 2026-05-19 (Telegram 3744/3746):
"One more will help to auto reply customers, you can also use the templates
clearing that we have also come up with."

WHAT IT DOES
------------
1. Reads the inbound email body from SF Case.Incoming_email__c (+ the AI summary
   on Case.Incoming_Email_Summary__c). Both fields written by Commaut2.0 outside
   SF flow metadata (per memory/reference_commaut_inbound_schema.md).
2. Builds a per-client case file (Step 3c, MANDATORY). Uses
   client_comm_preflight.check_active_engagement() when reachable (CEO-OS
   filesystem mount); degrades to a documented best-effort path with a caller
   warning when unreachable.
3. Step 3e ACTIVE ENGAGEMENT GUARD — if another staff-in-progress Email Case
   exists for the same Contact in the last 14 days, returns defer_to_staff=True
   and does NOT draft.
4. Classifies the inbound against the 26 (actually 55-entry) F-code catalog in
   lanka.tax/evidence/W2D12_action_catalog.json. Matching uses subject keywords,
   body keywords, and per-F-code preconditions. Conflict resolution follows the
   dependency-chain precedence: Compliance > Payment > Registration > Critical
   Docs > Routing > Staff Follow-up (encoded as stage_priority).
5. If a single F-code matches AND case-file gates pass, renders the F-code's
   template (from action_catalog.json inline_template_md OR SF EmailTemplate
   name when no inline) with: full client name in greeting AND subject,
   deadline-status sentence merge field, CEO signature block. Refuses to render
   if any of those mandatory elements are missing (refusal goes into
   gates_failed; no draft is produced).
6. NEVER auto-sends. Tier 1 approval ONLY. `submit_for_tier1_approval()` writes
   to the approval_queue (Supabase), Resolver_Change__c (SF, status='pending'),
   and posts a Telegram message to chat_id 1813046950 with the full draft body
   for CEO Y/N/edit reply. The actual EmailMessage send happens in a SEPARATE
   handler triggered by the CEO Y reply -- that handler is OUT OF SCOPE for this
   module (documented in README_autoreply.md).

WHAT IT DOES NOT DO
-------------------
- Does not send any client-facing email itself.
- Does not handle WhatsApp inbound from ks_sn__MessageHistory__c (v1.1).
- Does not use LLM classification -- rules + keywords + preconditions only.
- Does not author new F-codes -- only reads the 55 in action_catalog.json.
- Does not edit celery_app.py or capability_registry.json (other waves).

CONSTRAINTS (BINDING)
---------------------
- Step 3c case file MANDATORY before any draft (no outbound without preflight).
- Step 3e active engagement guard MANDATORY (defer to staff if in-progress Case).
- Client emails must use full name (not first-name-only), include lanka.tax/login
  link (Customer Login), include CEO signature block (Hotline +94 71 314 0000,
  Mobile +94 71 460 0000, Email tax@lanka.tax). Refuse to render if any missing.
- Never include another client's name in a draft.
- Tier 1 approval is queue + Telegram. No autonomous send EVER.

Public API:
    classify_and_draft(*, case_id, dry_run=True, **deps) -> dict
    submit_for_tier1_approval(*, case_id, draft, **deps) -> dict
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & config
# ---------------------------------------------------------------------------

# Canonical action catalog path. Caller can override via env or DI for tests
# that don't have the CEO-OS filesystem mount.
_ACTION_CATALOG_CANONICAL = pathlib.Path(
    "G:/My Drive/CEO OS/lanka.tax/evidence/W2D12_action_catalog.json"
)
_ACTION_CATALOG_ENV = "FIESTA_ACTION_CATALOG_PATH"

# CEO signature block (per reference_ceo_signature_block.md + CLAUDE.md
# feedback memories). Authoritative version. Used in EVERY rendered draft.
CEO_SIGNATURE_BLOCK = (
    "Lanka.tax Team\n"
    "Hotline: +94 71 314 0000\n"
    "Mobile: +94 71 460 0000\n"
    "Email: tax@lanka.tax\n"
    "Web: https://www.lanka.tax"
)

# Mandatory rendered-template elements (refuse-to-render if any missing).
# Per feedback_client_email_patterns.md + feedback_client_email_full_name.md.
MANDATORY_ELEMENTS = (
    "client_full_name_in_greeting",
    "client_full_name_in_subject",
    "ceo_signature_block",
    "lanka_tax_login_link",
)

LANKA_TAX_LOGIN_URL = "https://www.lanka.tax/login"

# Telegram CEO chat id (per CLAUDE.md and ceo-os-state.json).
CEO_TELEGRAM_CHAT_ID = 1813046950

# Stage priority for conflict resolution. Higher number = higher precedence.
# Per the 6-level dependency chain in CLAUDE.md Step 3b:
#   Compliance > Payment > Registration > Critical Docs > Routing > Staff Follow-up
STAGE_PRIORITY = {
    "Compliance":           60,
    "Payment":              50,
    "Registration":         40,
    "Critical Docs":        30,
    "Routing":              20,
    "Staff Follow-up":      10,
    # Catalog stage-name approximations to chain levels:
    "Prospect":              50,   # Stage 0 = pre-payment, payment-tier
    "Onboarding":            40,   # Stage 1 = registration
    "Doc Collection":        30,
    "Computation":           20,
    "Confirmation":          15,
    "Filing":                25,
    "Cross-stage":           60,
}

# Active engagement window. Step 3e default: 14 days.
ACTIVE_ENGAGEMENT_LOOKBACK_DAYS = 14

# Returning-client threshold: prior tax year outbound count.
RETURNING_CLIENT_OUTBOUND_THRESHOLD = 3

# VIP override list — these contacts always force human review regardless of
# F-code classification. Pulled from Supabase system_config.vip_contacts when
# available. Static fallback (lowercase email) for offline testing.
DEFAULT_VIP_EMAIL_FALLBACK = {
    # Placeholder — production loads from Supabase via the resolver.
    # Test fixtures can inject a complete list via dependency injection.
}

# Per-F-code keyword maps for classification (additive to F-code preconditions).
# Keyword in inbound subject OR body adds match weight to that F-code.
# Tuned conservatively — false positives push the classifier to no-match which
# is the safe default (human classification).
F_CODE_KEYWORDS = {
    "F0.1":  ("welcome", "signed up", "interested", "package", "pricing"),
    "F0.3":  ("are you still", "long time", "haven't heard"),
    "F1.1":  ("tin", "no tin", "tax identification number", "apply for tin"),
    "F1.4":  ("pin", "no pin", "ird pin"),
    "F1.6":  ("pin issue", "pin failed", "pin not working", "pin error",
              "pin reset", "ird login fail"),
    "F1.8":  ("tax type", "iit", "individual income tax registration"),
    "F2.1":  ("profile", "financial checklist", "fill the form", "questionnaire"),
    "F-RESCUE-001": ("forgot", "lost", "haven't filled", "still not started"),
    "F3.0":  ("documents", "docs", "what do i send", "what to upload",
              "t10", "bank statement", "asset", "liability"),
    "F-AL-REQUEST": ("asset", "liability", "a&l", "al form", "assets and liabilities",
                     "asset and liability"),
    "F4":    ("payment", "invoice", "how much", "amount to pay", "cost", "fee"),
    "F5":    ("confirm", "computation", "calculation", "review", "tax return ready",
              "confirm calculation"),
    "F5.2":  ("confirm calculation", "computation ready", "ready to file"),
    "F6.1":  ("confirm", "haven't confirmed", "still need to confirm"),
    "F6":    ("a&l", "assets and liabilities", "al declaration", "final step"),
    "F8.1":  ("not filed", "filing stuck", "still pending", "why is it taking"),
    "F8.2":  ("ird timeout", "ird down", "ird not responding"),
    "F8.3":  ("ird 401", "ird unauthorized", "login expired"),
    "F-W2-LOCAL":   ("deadline", "due date", "when must i pay", "by when"),
    "F-W2-FOREIGN": ("foreign", "abroad", "overseas", "non resident"),
    "F-W3-RENEWAL": ("last year", "previous year", "renew", "again this year"),
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

_CATALOG_CACHE: dict = {}


def _load_action_catalog(path_override: Optional[str] = None) -> dict:
    """Load + cache the action catalog. Caller can override via path arg."""
    cache_key = path_override or "_default"
    if cache_key in _CATALOG_CACHE:
        return _CATALOG_CACHE[cache_key]

    if path_override:
        p = pathlib.Path(path_override)
    else:
        env_path = os.environ.get(_ACTION_CATALOG_ENV)
        p = pathlib.Path(env_path) if env_path else _ACTION_CATALOG_CANONICAL

    if not p.exists():
        raise FileNotFoundError(
            f"action catalog not found at {p}. "
            f"Set {_ACTION_CATALOG_ENV} env or pass a path override."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    actions = data.get("actions", [])
    # Index by f_code for fast lookup.
    by_code = {a["f_code"]: a for a in actions if a.get("f_code")}
    catalog = {"raw": data, "actions": actions, "by_code": by_code}
    _CATALOG_CACHE[cache_key] = catalog
    return catalog


def _default_sf_client():
    """Default SF client. Lazy import so tests can run without sf_auth."""
    from fiesta.integrations.sf_auth import SFRestClient
    return SFRestClient()


def _default_preflight_fn(contact_id: Optional[str], tax_file_id: Optional[str]) -> dict:
    """Default preflight: call CEO-OS client_comm_preflight if reachable."""
    try:
        import sys
        ceo_os_wf = "G:/My Drive/CEO OS/working files"
        if ceo_os_wf not in sys.path:
            sys.path.insert(0, ceo_os_wf)
        from client_comm_preflight import check_active_engagement  # type: ignore
        return check_active_engagement(contact_id, tax_file_id)
    except ImportError:
        return {
            "defer_to_staff": False,
            "reason": "preflight_unreachable_degraded_path",
            "active_case_ids": [],
            "active_case_count": 0,
            "_warning": (
                "client_comm_preflight not importable; using degraded path. "
                "Inject preflight_fn for production use."
            ),
        }


def _default_supabase_writer(table: str, row: dict) -> dict:
    """Default Supabase writer. Returns {ok, id, error}.

    Lazy import — many test paths won't need this. Production injects via DI.
    """
    try:
        import urllib.request
        import urllib.error
        anon_key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get(
            "SUPABASE_SERVICE_KEY"
        )
        url_base = os.environ.get(
            "SUPABASE_URL",
            "https://afrwkpkhwqgodxaycajt.supabase.co",
        )
        if not anon_key:
            return {"ok": False, "error": "SUPABASE_ANON_KEY missing", "id": None}
        url = f"{url_base}/rest/v1/{table}"
        body = json.dumps(row).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {anon_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list) and data:
            return {"ok": True, "id": data[0].get("id"), "error": None}
        return {"ok": True, "id": None, "error": None}
    except Exception as e:  # pragma: no cover - network path
        return {"ok": False, "error": str(e), "id": None}


def _default_telegram_sender(chat_id: int, text: str) -> dict:
    """Default Telegram sender (best-effort). Production injects.

    Returns {ok, msg_id, error}. No-op stub when BOT_TOKEN missing.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return {
            "ok": False,
            "msg_id": None,
            "error": "TELEGRAM_BOT_TOKEN missing; inject telegram_sender for prod.",
        }
    try:  # pragma: no cover - network path
        import urllib.request
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        body = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if data.get("ok"):
            return {"ok": True, "msg_id": data["result"]["message_id"], "error": None}
        return {"ok": False, "msg_id": None, "error": str(data)}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "msg_id": None, "error": str(e)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s: Optional[str]) -> str:
    """Normalize text for keyword matching: lowercase, collapse whitespace."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.lower()).strip()


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("+0000", "+00:00").replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except (ValueError, AttributeError):
            return None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_ago(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    return max(0, (_now() - dt).days)


def _deadline_status_sentence(tax_year: str) -> str:
    """Render the deadline-status sentence merge field.

    Per reference_deadline_status_sentence.md: never hardcode 'approaching' /
    'passed'. Compute from tax_year (e.g. "2025/2026") and the SL filing
    deadline (Nov 30 of the post-year, fiscal year ends Mar 31).
    """
    if not tax_year or "/" not in tax_year:
        return "Please review the deadline status with your tax officer."
    # Tax year "YY/YY" -> filing deadline = Nov 30 of the second YY's year.
    try:
        parts = tax_year.split("/")
        end_yy = int(parts[1])
        end_year = 2000 + end_yy if end_yy < 100 else end_yy
        # Filing deadline: Nov 30 of the year ending the tax year.
        deadline = date(end_year, 11, 30)
        today = date.today()
        days = (deadline - today).days
        if days < 0:
            return (
                f"The filing deadline for {tax_year} ({deadline.isoformat()}) "
                f"has passed by {abs(days)} days. Late filing penalties apply -- "
                "please contact us urgently."
            )
        elif days <= 14:
            return (
                f"The filing deadline for {tax_year} is "
                f"{deadline.isoformat()} -- only {days} days remain. "
                "Please act now."
            )
        elif days <= 60:
            return (
                f"The filing deadline for {tax_year} is "
                f"{deadline.isoformat()} ({days} days from today)."
            )
        else:
            return (
                f"The filing deadline for {tax_year} is "
                f"{deadline.isoformat()}."
            )
    except (ValueError, IndexError):
        return f"Please review the deadline status for {tax_year} with us."


def _validate_full_name(name: Optional[str]) -> bool:
    """Full name = at least first + last (>=2 tokens, total >=4 chars)."""
    if not name:
        return False
    tokens = name.strip().split()
    if len(tokens) < 2:
        return False
    if sum(len(t) for t in tokens) < 4:
        return False
    return True


# ---------------------------------------------------------------------------
# Inbound classifier
# ---------------------------------------------------------------------------

def _classify_inbound(
    *,
    inbound_subject: str,
    inbound_body: str,
    case_file: dict,
    catalog: dict,
) -> dict:
    """Classify the inbound + case_file context into an F-code (or None).

    Returns:
        {classified_f_code, classified_action, score, candidates, reasoning}
    """
    subj_norm = _norm(inbound_subject)
    body_norm = _norm(inbound_body)
    combined = f"{subj_norm} {body_norm}".strip()

    # Score each F-code in F_CODE_KEYWORDS based on subject + body match.
    scores: dict[str, int] = {}
    matched_keywords: dict[str, list[str]] = {}
    for f_code, keywords in F_CODE_KEYWORDS.items():
        score = 0
        kw_hits = []
        for kw in keywords:
            kw_norm = kw.lower()
            if kw_norm in subj_norm:
                score += 3  # subject hits weighted higher
                kw_hits.append(f"subj:{kw}")
            elif kw_norm in body_norm:
                score += 1
                kw_hits.append(f"body:{kw}")
        if score > 0:
            scores[f_code] = score
            matched_keywords[f_code] = kw_hits

    if not scores:
        return {
            "classified_f_code": None,
            "classified_action": None,
            "score": 0,
            "candidates": [],
            "reasoning": "no_keyword_matches_against_55_f_codes",
            "matched_keywords": {},
        }

    # Add precondition boosts: if case_file signals match F-code preconditions,
    # boost the score.
    by_code = catalog["by_code"]
    for f_code in list(scores.keys()):
        action = by_code.get(f_code)
        if not action:
            continue
        precs = set(action.get("preconditions") or [])
        if "payment_status_paid" in precs and case_file.get("payment_status") == "Paid":
            scores[f_code] += 2
        if "payment_status_not_paid" in precs and case_file.get("payment_status") != "Paid":
            scores[f_code] += 2
        if "profile_complete" in precs and case_file.get("profile_complete"):
            scores[f_code] += 2
        if "profile_not_complete" in precs and not case_file.get("profile_complete"):
            scores[f_code] += 2
        if "has_email" in precs and case_file.get("email"):
            scores[f_code] += 1
        if "at_least_one_doc_missing" in precs and case_file.get("docs_missing"):
            scores[f_code] += 2
        if "tax_computation_draft_ready" in precs and case_file.get("computation_ready"):
            scores[f_code] += 3

    # Build ranked list with stage-priority tie-break (precedence rule).
    candidates = []
    for f_code, score in scores.items():
        action = by_code.get(f_code)
        if not action:
            continue
        stage_name = action.get("stage_name", "")
        stage_pri = STAGE_PRIORITY.get(stage_name, 0)
        candidates.append({
            "f_code": f_code,
            "score": score,
            "stage_priority": stage_pri,
            "f_code_name": action.get("f_code_name"),
            "stage_name": stage_name,
            "matched_keywords": matched_keywords.get(f_code, []),
        })
    # Sort: score DESC, then stage_priority DESC (precedence: earlier-stage wins).
    candidates.sort(key=lambda c: (-c["score"], -c["stage_priority"]))

    top = candidates[0]
    top_score = top["score"]
    # Ambiguity check: if multiple F-codes tied at top score AND not same stage,
    # mark as needs_human_classification (don't auto-draft).
    tied = [c for c in candidates if c["score"] == top_score]
    if len(tied) > 1:
        # Resolve via stage_priority. If still tied, kick to human.
        tied_pris = {c["stage_priority"] for c in tied}
        if len(tied_pris) > 1:
            # Use the highest stage_priority winner (precedence).
            winner = tied[0]  # already sorted by stage_priority desc
            reasoning = (
                f"top_score={top_score} tied across {len(tied)} f_codes; "
                f"resolved by stage_priority precedence -> {winner['f_code']}"
            )
        else:
            return {
                "classified_f_code": None,
                "classified_action": None,
                "score": top_score,
                "candidates": candidates,
                "reasoning": (
                    f"ambiguous_tie_at_score={top_score} across {len(tied)} "
                    f"same-precedence f_codes; needs_human_classification"
                ),
                "matched_keywords": matched_keywords,
            }
    else:
        winner = top
        reasoning = (
            f"single_top f_code={winner['f_code']} score={top_score} "
            f"keywords={winner['matched_keywords']}"
        )

    return {
        "classified_f_code": winner["f_code"],
        "classified_action": by_code[winner["f_code"]],
        "score": winner["score"],
        "candidates": candidates,
        "reasoning": reasoning,
        "matched_keywords": matched_keywords,
    }


# ---------------------------------------------------------------------------
# Case-file builder (lightweight; preflight provides the heavy lift)
# ---------------------------------------------------------------------------

def _build_case_file(
    *,
    case_id: str,
    sf_client: Any,
) -> dict:
    """Pull the inbound + minimal client context for classification.

    Returns dict with:
      - case_id, contact_id, customer_id, tax_file_id
      - client_name, client_email, tax_year
      - inbound_subject, inbound_body, inbound_summary
      - payment_status, profile_complete, docs_missing, computation_ready
      - prior_outbound_count, returning
      - errors (list)
    """
    cf = {
        "case_id": case_id,
        "contact_id": None,
        "customer_id": None,
        "tax_file_id": None,
        "client_name": None,
        "client_email": None,
        "tax_year": None,
        "inbound_subject": "",
        "inbound_body": "",
        "inbound_summary": "",
        "payment_status": None,
        "profile_complete": False,
        "docs_missing": False,
        "computation_ready": False,
        "prior_outbound_count": 0,
        "returning": False,
        "errors": [],
    }

    # Pull Case + Contact + Customer + Tax_File in one SOQL.
    # Commaut2.0 writes Incoming_email__c (body) + Incoming_Email_Summary__c (AI summary).
    soql = (
        "SELECT Id, Subject, ContactId, "
        "Incoming_email__c, Incoming_Email_Summary__c, "
        "Contact.Name, Contact.Email, "
        "Customer__c, Customer__r.Name, Customer__r.Full_Name_of_Applicant_English__c "
        f"FROM Case WHERE Id = '{case_id}'"
    )
    try:
        q = sf_client.query(soql)
    except Exception as e:
        cf["errors"].append(f"case_query_failed: {e}")
        return cf
    if q.get("error"):
        cf["errors"].append(f"case_query_http_error: {q}")
        return cf
    records = q.get("records") or []
    if not records:
        cf["errors"].append(f"no Case found with Id={case_id}")
        return cf

    rec = records[0]
    cf["inbound_subject"] = rec.get("Subject") or ""
    cf["inbound_body"] = rec.get("Incoming_email__c") or ""
    cf["inbound_summary"] = rec.get("Incoming_Email_Summary__c") or ""
    cf["contact_id"] = rec.get("ContactId")
    cf["customer_id"] = rec.get("Customer__c")

    contact = rec.get("Contact") or {}
    cust = rec.get("Customer__r") or {}
    # Prefer Customer.Full_Name_of_Applicant_English__c, then Contact.Name, then Customer.Name.
    cf["client_name"] = (
        cust.get("Full_Name_of_Applicant_English__c")
        or contact.get("Name")
        or cust.get("Name")
    )
    cf["client_email"] = contact.get("Email")

    # Pull most recent Tax_File for the Customer (current tax year preferred).
    if cf["customer_id"]:
        tf_soql = (
            "SELECT Id, Tax_Year__c, Customers_profile_filling_status__c, "
            "Purchased_package_ID__r.Payment_Status__c "
            f"FROM Tax_File__c WHERE Customer__c = '{cf['customer_id']}' "
            "AND Tax_Year__c = '2025/2026' LIMIT 1"
        )
        try:
            tf_q = sf_client.query(tf_soql)
            tf_records = tf_q.get("records") or []
            if tf_records:
                tf = tf_records[0]
                cf["tax_file_id"] = tf.get("Id")
                cf["tax_year"] = tf.get("Tax_Year__c") or "2025/2026"
                # Profile complete: status == 5 (per reference_profile_completion_scale.md).
                pf_status = tf.get("Customers_profile_filling_status__c")
                cf["profile_complete"] = pf_status == 5 or pf_status == "5"
                pkg = tf.get("Purchased_package_ID__r") or {}
                cf["payment_status"] = pkg.get("Payment_Status__c")
        except Exception as e:
            cf["errors"].append(f"taxfile_query_failed: {e}")

    # Prior outbound count (returning-client detection).
    if cf["contact_id"]:
        prior_soql = (
            "SELECT COUNT() FROM EmailMessage "
            f"WHERE ToAddress LIKE '%{cf['client_email']}%' "
            "AND CreatedDate >= LAST_N_DAYS:365 "
            "AND Incoming = false"
        ) if cf["client_email"] else None
        if prior_soql:
            try:
                pq = sf_client.query(prior_soql)
                cf["prior_outbound_count"] = pq.get("totalSize", 0)
                cf["returning"] = (
                    cf["prior_outbound_count"] >= RETURNING_CLIENT_OUTBOUND_THRESHOLD
                )
            except Exception as e:
                cf["errors"].append(f"prior_outbound_query_failed: {e}")

    return cf


def _check_cooldown(
    *,
    f_code: str,
    case_file: dict,
    catalog: dict,
    sf_client: Any,
) -> dict:
    """Check Document_Follow_up_Reminder__c for prior send within cooldown."""
    action = catalog["by_code"].get(f_code)
    cooldown_days = (action or {}).get("cooldown_days") or 0
    if cooldown_days <= 0 or not case_file.get("tax_file_id"):
        return {"cooldown_active": False, "last_sent": None, "cooldown_days": cooldown_days}

    soql = (
        "SELECT Id, CreatedDate, Reminder_Template__c FROM Document_Follow_up_Reminder__c "
        f"WHERE Tax_File__c = '{case_file['tax_file_id']}' "
        f"AND CreatedDate >= LAST_N_DAYS:{cooldown_days} "
        "ORDER BY CreatedDate DESC LIMIT 1"
    )
    try:
        q = sf_client.query(soql)
        records = q.get("records") or []
        if records:
            return {
                "cooldown_active": True,
                "last_sent": records[0].get("CreatedDate"),
                "cooldown_days": cooldown_days,
                "last_template": records[0].get("Reminder_Template__c"),
            }
    except Exception as e:
        log.warning("cooldown check failed: %s", e)
        # On error, fail-OPEN for cooldown (don't block a legit reply on a query glitch);
        # the Tier 1 CEO approval is the ultimate safety net.
        return {
            "cooldown_active": False,
            "last_sent": None,
            "cooldown_days": cooldown_days,
            "warning": f"cooldown_query_failed: {e}",
        }
    return {"cooldown_active": False, "last_sent": None, "cooldown_days": cooldown_days}


# ---------------------------------------------------------------------------
# Template renderer
# ---------------------------------------------------------------------------

def _load_inline_template(action: dict) -> Optional[str]:
    """Load inline_template_md if specified and reachable."""
    rel = action.get("inline_template_md")
    if not rel:
        return None
    # Path is relative to CEO-OS root. Try canonical mount then env override.
    candidates = [
        pathlib.Path("G:/My Drive/CEO OS") / rel,
        pathlib.Path(os.environ.get("CEO_OS_ROOT", ".")) / rel,
    ]
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:  # pragma: no cover
                continue
    return None


def _render_template(
    *,
    f_code: str,
    action: dict,
    case_file: dict,
) -> dict:
    """Render the F-code template against case_file.

    Returns:
        {ok, subject, body, gates_passed, gates_failed}
    gates_failed listing any of MANDATORY_ELEMENTS not present -> ok=False, no draft.
    """
    client_name = case_file.get("client_name")
    tax_year = case_file.get("tax_year") or "2025/2026"

    gates_passed = []
    gates_failed = []

    # GATE: full name (greeting + subject) -- per feedback_client_email_full_name.md.
    if not _validate_full_name(client_name):
        gates_failed.append("client_full_name_in_greeting")
        gates_failed.append("client_full_name_in_subject")
    else:
        gates_passed.append("client_full_name_in_greeting")
        gates_passed.append("client_full_name_in_subject")

    # GATE: client email present.
    if not case_file.get("client_email"):
        gates_failed.append("client_email_present")
    else:
        gates_passed.append("client_email_present")

    # Build subject + body even if gates fail (so caller can see what would be drafted).
    f_code_name = action.get("f_code_name", "Lanka.tax update")
    pretty_name = f_code_name.replace("_", " ").title()
    subject = f"{pretty_name} - {client_name or '[NAME MISSING]'} - {tax_year}"

    # Try inline template first; if absent, build a structured fallback.
    inline = _load_inline_template(action)
    if inline:
        body_template = inline
    else:
        # Structured fallback. Includes mandatory elements (login + signature).
        body_template = (
            "Dear {{client_name}},\n\n"
            "{{deadline_status_sentence}}\n\n"
            "Regarding your tax file for {{tax_year}}, our records indicate the "
            "following action is required: **{{f_code_name}}** "
            "({{f_code_description}}).\n\n"
            "Please log in to your account at {{lanka_tax_login}} to review "
            "your status and complete any pending steps.\n\n"
            "If you have any questions, reply to this email -- we will respond "
            "personally.\n\n"
            "{{ceo_signature}}\n"
        )

    # Merge fields
    merges = {
        "{{client_name}}": client_name or "[NAME MISSING]",
        "{{client_full_name}}": client_name or "[NAME MISSING]",
        "{{tax_year}}": tax_year,
        "{{f_code}}": f_code,
        "{{f_code_name}}": pretty_name,
        "{{f_code_description}}": action.get("description", "")[:200],
        "{{deadline_status_sentence}}": _deadline_status_sentence(tax_year),
        "{{lanka_tax_login}}": LANKA_TAX_LOGIN_URL,
        "{{login_link}}": LANKA_TAX_LOGIN_URL,
        "{{ceo_signature}}": CEO_SIGNATURE_BLOCK,
        "{{signature}}": CEO_SIGNATURE_BLOCK,
    }
    body = body_template
    for k, v in merges.items():
        body = body.replace(k, str(v))

    # GATE: lanka.tax/login link present.
    if LANKA_TAX_LOGIN_URL in body or "lanka.tax/login" in body.lower():
        gates_passed.append("lanka_tax_login_link")
    else:
        gates_failed.append("lanka_tax_login_link")

    # GATE: CEO signature block present (check for hotline as proxy).
    if "+94 71 314 0000" in body and "tax@lanka.tax" in body:
        gates_passed.append("ceo_signature_block")
    else:
        gates_failed.append("ceo_signature_block")

    # Returning-client personalization (if flagged).
    if case_file.get("returning"):
        body = (
            "Dear " + (client_name or "[NAME MISSING]") + ",\n\n"
            "Welcome back -- we are pleased to assist you again this tax year. "
            "Building on the work we did together previously, here is the next step:\n\n"
            + body.split("\n\n", 1)[-1]  # drop original greeting, keep rest
        )

    ok = len(gates_failed) == 0
    return {
        "ok": ok,
        "subject": subject,
        "body": body,
        "gates_passed": gates_passed,
        "gates_failed": gates_failed,
    }


# ---------------------------------------------------------------------------
# Public API: classify_and_draft
# ---------------------------------------------------------------------------

def classify_and_draft(
    *,
    case_id: str,
    dry_run: bool = True,
    sf_client: Any = None,
    preflight_fn: Optional[Callable[[Optional[str], Optional[str]], dict]] = None,
    catalog_path: Optional[str] = None,
    vip_email_set: Optional[set] = None,
) -> dict:
    """Classify inbound + draft an F-code template auto-reply (Tier 1 gated).

    Args:
        case_id: SF Case.Id with Commaut-written Incoming_email__c body.
        dry_run: True (default) — no writes anywhere; returns draft only.
                 False — caller is expected to then call submit_for_tier1_approval().
                 IMPORTANT: dry_run=False NEVER auto-sends. It only signals to
                 the caller that the draft is ready for the Tier 1 submission
                 step. The actual approval queue/Telegram write happens in
                 submit_for_tier1_approval(), and the actual send happens in
                 a SEPARATE handler on CEO Y reply.
        sf_client: DI seam for tests.
        preflight_fn: DI seam for client_comm_preflight.check_active_engagement.
        catalog_path: DI seam for action catalog json path.
        vip_email_set: VIP email override set (lowercase).

    Returns:
        dict with keys:
          ok, case_id, customer_id, contact_id, tax_file_id, client_name,
          inbound_subject_received, inbound_body_chars,
          classified_f_code, classified_action_name,
          draft_subject, draft_body,
          gates_passed, gates_failed,
          defer_to_staff (bool), cooldown_active (bool),
          vip_override (bool),
          approval_required (always True for any draft -- Tier 1),
          sent (always None -- this module NEVER sends),
          reasoning, candidates, errors
    """
    trace_id = f"ar-cl-{uuid.uuid4().hex[:12]}"
    result: dict = {
        "trace_id": trace_id,
        "ok": False,
        "case_id": case_id,
        "customer_id": None,
        "contact_id": None,
        "tax_file_id": None,
        "client_name": None,
        "inbound_subject_received": None,
        "inbound_body_chars": 0,
        "classified_f_code": None,
        "classified_action_name": None,
        "draft_subject": None,
        "draft_body": None,
        "gates_passed": [],
        "gates_failed": [],
        "defer_to_staff": False,
        "cooldown_active": False,
        "vip_override": False,
        "approval_required": True,
        "sent": None,
        "reasoning": None,
        "candidates": [],
        "errors": [],
        "warnings": [],
        "dry_run": dry_run,
    }

    # --- INPUT VALIDATION ---
    if not case_id or not isinstance(case_id, str):
        result["errors"].append("case_id required (non-empty str)")
        return result

    # --- LOAD CATALOG ---
    try:
        catalog = _load_action_catalog(catalog_path)
    except FileNotFoundError as e:
        result["errors"].append(f"catalog_load_failed: {e}")
        return result

    client = sf_client or _default_sf_client()

    # --- BUILD CASE FILE (Step 3c MANDATORY) ---
    case_file = _build_case_file(case_id=case_id, sf_client=client)
    if case_file.get("errors"):
        result["errors"].extend(case_file["errors"])
        return result

    # Stamp case file fields onto result.
    result["customer_id"] = case_file.get("customer_id")
    result["contact_id"] = case_file.get("contact_id")
    result["tax_file_id"] = case_file.get("tax_file_id")
    result["client_name"] = case_file.get("client_name")
    result["inbound_subject_received"] = case_file.get("inbound_subject")
    result["inbound_body_chars"] = len(case_file.get("inbound_body") or "")

    if not (case_file.get("inbound_body") or case_file.get("inbound_summary")):
        result["errors"].append(
            "no inbound body or summary on Case "
            "(Commaut2.0 may not have fired yet)"
        )
        return result

    # --- STEP 3e: ACTIVE ENGAGEMENT GUARD (MANDATORY) ---
    pf = preflight_fn or _default_preflight_fn
    engagement = pf(case_file.get("contact_id"), case_file.get("tax_file_id"))
    if engagement.get("_warning"):
        result["warnings"].append(engagement["_warning"])
    if engagement.get("defer_to_staff"):
        result["defer_to_staff"] = True
        result["reasoning"] = engagement.get("reason", "active engagement detected")
        return result

    # --- VIP OVERRIDE (force human review) ---
    vip_set = vip_email_set if vip_email_set is not None else DEFAULT_VIP_EMAIL_FALLBACK
    if case_file.get("client_email") and case_file["client_email"].lower() in vip_set:
        result["vip_override"] = True
        result["reasoning"] = "vip_contact_force_human_review"
        return result

    # --- CLASSIFY ---
    classification = _classify_inbound(
        inbound_subject=case_file["inbound_subject"],
        inbound_body=case_file["inbound_body"] or case_file["inbound_summary"],
        case_file=case_file,
        catalog=catalog,
    )
    result["classified_f_code"] = classification["classified_f_code"]
    result["candidates"] = classification["candidates"]
    result["reasoning"] = classification["reasoning"]

    if not classification["classified_f_code"]:
        # Ambiguous / no match -> human classification (no draft).
        return result

    action = classification["classified_action"]
    result["classified_action_name"] = action.get("f_code_name")

    # --- COOLDOWN CHECK ---
    cd = _check_cooldown(
        f_code=classification["classified_f_code"],
        case_file=case_file,
        catalog=catalog,
        sf_client=client,
    )
    if cd.get("warning"):
        result["warnings"].append(cd["warning"])
    if cd["cooldown_active"]:
        result["cooldown_active"] = True
        result["reasoning"] = (
            f"cooldown_active: same f_code sent at {cd['last_sent']}, "
            f"cooldown_window={cd['cooldown_days']}d"
        )
        return result

    # --- RENDER TEMPLATE ---
    rendered = _render_template(
        f_code=classification["classified_f_code"],
        action=action,
        case_file=case_file,
    )
    result["draft_subject"] = rendered["subject"]
    result["draft_body"] = rendered["body"]
    result["gates_passed"] = rendered["gates_passed"]
    result["gates_failed"] = rendered["gates_failed"]

    if not rendered["ok"]:
        result["reasoning"] = (
            f"render_refused: gates_failed={rendered['gates_failed']}"
        )
        return result

    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# Public API: submit_for_tier1_approval
# ---------------------------------------------------------------------------

def submit_for_tier1_approval(
    *,
    case_id: str,
    draft: dict,
    sf_client: Any = None,
    supabase_writer: Optional[Callable[[str, dict], dict]] = None,
    telegram_sender: Optional[Callable[[int, str], dict]] = None,
    dry_run: bool = False,
) -> dict:
    """Write the draft into approval_queue + Resolver_Change__c + Telegram CEO.

    Never sends the actual email. The CEO Y reply triggers a SEPARATE handler
    that performs the SF EmailMessage send (out of scope here, documented in
    README_autoreply.md).

    Args:
        case_id: SF Case.Id (must match draft["case_id"]).
        draft: dict from classify_and_draft (must have ok=True).
        sf_client, supabase_writer, telegram_sender: DI seams.
        dry_run: if True, no writes anywhere -- returns the would-write payloads.

    Returns:
        {ok, approval_queue_row_id, resolver_change_id, telegram_msg_id,
         dry_run, would_write, errors}
    """
    result: dict = {
        "ok": False,
        "approval_queue_row_id": None,
        "resolver_change_id": None,
        "telegram_msg_id": None,
        "dry_run": dry_run,
        "would_write": {},
        "errors": [],
    }

    # Validate draft shape.
    if not draft or not draft.get("ok"):
        result["errors"].append("draft.ok=False -- refusing to submit a failed draft")
        return result
    if draft.get("case_id") != case_id:
        result["errors"].append(
            f"case_id mismatch: arg={case_id} draft={draft.get('case_id')}"
        )
        return result
    if not draft.get("draft_subject") or not draft.get("draft_body"):
        result["errors"].append("draft missing subject or body")
        return result

    f_code = draft.get("classified_f_code")
    client_name = draft.get("client_name") or "[unknown]"

    # Build approval_queue row (Supabase) per CLAUDE.md Tier 2 schema reused for Tier 1.
    approval_row = {
        "action_type": "email_send",
        "target_description": (
            f"Auto-reply draft to {client_name} (Case {case_id}, F-code {f_code})"
        ),
        "sf_object": "Case",
        "sf_record_id": case_id,
        "sf_field": "AutoReply_Draft",  # informational only -- send is separate
        "sf_value": json.dumps({
            "subject": draft["draft_subject"],
            "body": draft["draft_body"],
            "to_email": "<from contact lookup at send time>",
            "f_code": f_code,
        }),
        "priority": 2,
        "status": "pending",
    }

    # Build Resolver_Change__c row (Rule P1) -- ledger-first, status=pending.
    rc_row = {
        "Target_Object__c": "EmailMessage",
        "Target_Field__c": "(send)",
        "Old_Value__c": "",
        "New_Value__c": json.dumps({
            "f_code": f_code,
            "subject": draft["draft_subject"][:255],
            "case_id": case_id,
        })[:32000],
        "Old_Value_Type__c": "string",
        "New_Value_Type__c": "string",
        "Change_Type__c": "email_send",
        "Status__c": "pending",  # awaiting CEO Y reply
        "Reversible__c": False,  # email send is irreversible
        "Step_Order__c": 1,
    }

    # Build Telegram message to CEO.
    tg_msg = (
        f"AUTOREPLY DRAFT for CEO review\n"
        f"Client: {client_name}\n"
        f"Case: {case_id}\n"
        f"F-code: {f_code}\n"
        f"---\n"
        f"Subject: {draft['draft_subject']}\n"
        f"---\n"
        f"{draft['draft_body']}\n"
        f"---\n"
        f"Reply Y to send, N to cancel, or paste an edit."
    )
    # Truncate to Telegram 4096 limit (single message).
    if len(tg_msg) > 4000:
        tg_msg = tg_msg[:3990] + "\n[...truncated]"

    result["would_write"] = {
        "approval_queue_row": approval_row,
        "resolver_change_row": rc_row,
        "telegram_message": tg_msg,
    }

    if dry_run:
        result["ok"] = True
        return result

    # --- LIVE: Resolver_Change__c FIRST (Rule P1: ledger before SF write) ---
    client = sf_client or _default_sf_client()
    try:
        rc_resp = client.post("Resolver_Change__c", rc_row)
    except Exception as e:
        result["errors"].append(f"resolver_change_post_raised: {e}")
        return result
    if rc_resp.get("error") or not rc_resp.get("id"):
        result["errors"].append(f"resolver_change_failed: {rc_resp}")
        return result
    result["resolver_change_id"] = rc_resp["id"]

    # --- Supabase approval_queue row ---
    sw = supabase_writer or _default_supabase_writer
    aq_resp = sw("approval_queue", approval_row)
    if not aq_resp.get("ok"):
        result["errors"].append(f"approval_queue_write_failed: {aq_resp.get('error')}")
        # Don't return yet -- still try Telegram so CEO sees the draft.
    result["approval_queue_row_id"] = aq_resp.get("id")

    # --- Telegram CEO ---
    ts = telegram_sender or _default_telegram_sender
    tg_resp = ts(CEO_TELEGRAM_CHAT_ID, tg_msg)
    if not tg_resp.get("ok"):
        result["errors"].append(f"telegram_send_failed: {tg_resp.get('error')}")
    result["telegram_msg_id"] = tg_resp.get("msg_id")

    # ok iff RC row created + at least one of approval_queue or telegram succeeded.
    result["ok"] = bool(
        result["resolver_change_id"]
        and (result["approval_queue_row_id"] or result["telegram_msg_id"])
    )
    return result


__all__ = [
    "classify_and_draft",
    "submit_for_tier1_approval",
    "CEO_SIGNATURE_BLOCK",
    "LANKA_TAX_LOGIN_URL",
    "MANDATORY_ELEMENTS",
    "F_CODE_KEYWORDS",
    "STAGE_PRIORITY",
    "CEO_TELEGRAM_CHAT_ID",
]
