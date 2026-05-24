"""
ops_alerts.py — Telegram ops alerts bot (Tier D1 / E2, 2026-05-24).

PURPOSE
-------
One-way alert channel from the self-running FIESTA backend to the CEO's
Telegram (chat_id 1813046950). When healthz fails, payments fail, request
latency degrades, or signup volume drops, this module is how the system
shouts for help.

  - Single public function: send_alert(severity, title, body, data=None)
  - HTTPS POST to https://api.telegram.org/bot{TOKEN}/sendMessage
  - Token from TELEGRAM_BOT_TOKEN env var (Fly secret), with fallback to
    the canonical local plugin store (~/.claude/channels/telegram/.env)
    and the Fly secrets file mount (/etc/secrets/TELEGRAM_BOT_TOKEN)
  - Chat ID from TELEGRAM_OPS_CHAT_ID env var (defaults to CEO chat id)
  - In-memory dedup: max 1 alert per (title, severity) per 10 minutes via
    a process-local dict (no Redis dependency — per scope cap)
  - Never raises. Failure to send is logged and reported in the return dict.

DESIGN NOTES
------------
- Token resolution + raw urllib POST patterns mirror worker_heartbeat.py
  (v18.3) — proven shape, no new dependency footprint in the worker.
- One-way only: this is not an interactive bot. No /commands, no polling.
- Per-process dedup is intentional. The Celery worker is long-lived (200
  tasks per child) and the in-process probes share state. If multiple
  workers fire the same alert simultaneously we accept up to N copies as
  the worst case — the alternative (Redis / DB row) breaks the scope cap
  and adds a failure domain to the alerting path itself.

USAGE
-----
    from ops_alerts import send_alert
    result = send_alert(
        severity="HIGH",
        title="Healthz failing",
        body="GET /healthz returned 503 for 3 consecutive checks.",
        data={"last_status": 503, "consecutive_failures": 3},
    )
    # result == {"sent": True, "deduped": False, "reason": None}

INTEGRATION POINTS (other modules call send_alert):
- Stripe webhook handler (payment_failed): C1 owns webhook files; this
  module is the alert path. Once the webhook lands, one call from inside
  the payment_failed branch is the wiring. See SETUP.md.
- Celery probes (healthz / latency / signup): tasks/ops_probes.py.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

CEO_CHAT_ID_DEFAULT = "1813046950"  # CEO's Telegram chat id
DEDUP_WINDOW_SECONDS = 600          # 10 minutes per (title, severity)
TELEGRAM_TIMEOUT_SECONDS = 10
TELEGRAM_MESSAGE_MAX_CHARS = 3900   # safe limit under Telegram's 4096 cap

VALID_SEVERITIES = {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


# --------------------------------------------------------------------------- #
# Dedup state (process-local)
# --------------------------------------------------------------------------- #

_dedup_lock = threading.Lock()
_dedup_state: dict[tuple[str, str], float] = {}  # (title, severity) -> last_send_ts


def _dedup_check_and_record(title: str, severity: str, now: Optional[float] = None) -> bool:
    """Return True if this (title, severity) is within the dedup window
    (i.e. CALLER SHOULD SKIP). Otherwise record the send timestamp and
    return False (caller sends).
    """
    now = now if now is not None else time.time()
    key = (title, severity)
    with _dedup_lock:
        last = _dedup_state.get(key)
        if last is not None and (now - last) < DEDUP_WINDOW_SECONDS:
            return True
        _dedup_state[key] = now
        # Opportunistic garbage collection — drop entries older than 2x
        # the window to keep the dict bounded under steady-state load.
        cutoff = now - (2 * DEDUP_WINDOW_SECONDS)
        stale = [k for k, ts in _dedup_state.items() if ts < cutoff]
        for k in stale:
            _dedup_state.pop(k, None)
        return False


def _reset_dedup_state_for_tests() -> None:
    """Test-only hook. Not exported in __all__."""
    with _dedup_lock:
        _dedup_state.clear()


# --------------------------------------------------------------------------- #
# Token + chat-id resolution
# --------------------------------------------------------------------------- #

def _resolve_telegram_token() -> Optional[str]:
    """Resolve the Telegram bot token. Returns None if nothing resolves —
    caller logs + returns sent=False.

    Order:
      1. TELEGRAM_BOT_TOKEN env var (Fly secret in prod)
      2. /etc/secrets/TELEGRAM_BOT_TOKEN file (Fly secrets file mount)
      3. ~/.claude/channels/telegram/.env (local dev, canonical plugin store)
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip()

    fly_path = Path("/etc/secrets/TELEGRAM_BOT_TOKEN")
    try:
        if fly_path.exists():
            content = fly_path.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception as e:
        log.debug(f"_resolve_telegram_token: /etc/secrets read failed: {e}")

    home_env = Path.home() / ".claude" / "channels" / "telegram" / ".env"
    try:
        if home_env.exists():
            for line in home_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "TELEGRAM_BOT_TOKEN":
                    return v.strip().strip('"').strip("'")
    except Exception as e:
        log.debug(f"_resolve_telegram_token: home .env read failed: {e}")

    return None


def _resolve_chat_id() -> str:
    """Resolve the ops chat id. Defaults to CEO when unset."""
    return os.environ.get("TELEGRAM_OPS_CHAT_ID", CEO_CHAT_ID_DEFAULT).strip()


# --------------------------------------------------------------------------- #
# Telegram raw send (carved out so tests can mock it)
# --------------------------------------------------------------------------- #

def _post_to_telegram(token: str, chat_id: str, text: str) -> bool:
    """POST to sendMessage. Returns True on HTTP 200, False otherwise.
    Never raises.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT_SECONDS) as resp:
            ok = (resp.status == 200)
            if not ok:
                log.warning(f"ops_alerts: Telegram returned status={resp.status}")
            return ok
    except Exception as e:
        log.error(f"ops_alerts: Telegram POST failed: {e}")
        return False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def send_alert(
    severity: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> dict:
    """Send a one-way ops alert to the configured Telegram chat.

    Args:
        severity: One of INFO / LOW / MEDIUM / HIGH / CRITICAL. Anything
                  else is coerced to "UNKNOWN" and logged as a misuse.
        title:    Short headline (used as dedup key alongside severity).
        body:     Human-readable detail (multi-line OK).
        data:     Optional dict — serialised as compact JSON in the message.

    Returns:
        {"sent": bool, "deduped": bool, "reason": str | None}
            sent    — True iff Telegram returned 200
            deduped — True iff suppressed by the 10-minute window
            reason  — populated when sent=False (token_missing /
                      deduped / http_error / exception_*)

    Never raises. Best-effort send.
    """
    sev = (severity or "").upper().strip()
    if sev not in VALID_SEVERITIES:
        log.warning(
            f"ops_alerts.send_alert: invalid severity={severity!r} — "
            "coercing to UNKNOWN"
        )
        sev = "UNKNOWN"

    # Dedup gate — skip if we sent the same (title, sev) within window.
    if _dedup_check_and_record(title, sev):
        log.info(
            f"ops_alerts: deduped (title={title!r}, severity={sev}) — "
            f"another alert sent within last {DEDUP_WINDOW_SECONDS}s"
        )
        return {"sent": False, "deduped": True, "reason": "deduped"}

    # Token resolution
    token = _resolve_telegram_token()
    if not token:
        log.error(
            "ops_alerts: TELEGRAM_BOT_TOKEN not resolvable — alert dropped. "
            "Set the Fly secret: `flyctl secrets set TELEGRAM_BOT_TOKEN=... "
            "--app fiesta-mvp`"
        )
        return {"sent": False, "deduped": False, "reason": "token_missing"}

    chat_id = _resolve_chat_id()

    # Format
    try:
        data_str = json.dumps(data, default=str) if data else "none"
    except Exception as e:
        log.warning(f"ops_alerts: data JSON-serialise failed: {e}")
        data_str = f"<unserialisable: {e}>"

    text = (
        f"[SEV {sev}] {title}\n\n"
        f"{body}\n\n"
        f"Data: {data_str}\n\n"
        f"ts: {datetime.utcnow().isoformat()}Z"
    )
    if len(text) > TELEGRAM_MESSAGE_MAX_CHARS:
        text = text[:TELEGRAM_MESSAGE_MAX_CHARS - 20] + "\n…[truncated]"

    ok = _post_to_telegram(token, chat_id, text)
    if ok:
        return {"sent": True, "deduped": False, "reason": None}
    return {"sent": False, "deduped": False, "reason": "http_error"}


__all__ = [
    "send_alert",
    "DEDUP_WINDOW_SECONDS",
    "VALID_SEVERITIES",
]
