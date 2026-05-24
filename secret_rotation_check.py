"""
secret_rotation_check.py — Tier D5 / F4 secret rotation reminder engine
(2026-05-24).

PURPOSE
-------
Load the YAML inventory at
``_tier_d5_secret_rotation/secret_inventory.yaml``, compute days since
each secret was last rotated, and bucket each entry as OK / WARN / URGENT
against its rotation_days policy.

The public entrypoint ``check_and_alert()`` is what the weekly Celery
beat task (tasks/secret_rotation_probe.py) calls — it fires one
``ops_alerts.send_alert(...)`` per non-OK bucket so the CEO gets a single
WARN summary and a single URGENT summary (not one per secret, which
would spam).

THRESHOLDS
----------
- URGENT (severity=HIGH):  days_since_rotation >= rotation_days
- WARN   (severity=LOW):   days_since_rotation >= rotation_days - warn_lead_days
                           AND less than rotation_days
- OK:                       everything else (no alert)

The warn_lead_days default is 15 (so canonical 90d secrets warn at 75d,
urgent at 90d). Per-secret rotation_days can override this; warn_lead is
a global default for now (kept simple per the task scope cap).

NO AUTO-ROTATION
----------------
This module ONLY tracks + reminds. Each provider's rotation flow is
documented in the YAML's `rotation_steps` block — CEO follows those
manually.

NEVER RAISES
------------
``check_and_alert()`` is best-effort. If the YAML is missing/corrupt or
ops_alerts fails, the function logs and returns a status dict rather
than crashing the worker.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants / defaults
# --------------------------------------------------------------------------- #

DEFAULT_WARN_LEAD_DAYS = 15
DEFAULT_ROTATION_DAYS = 90

INVENTORY_PATH = (
    Path(__file__).resolve().parent
    / "_tier_d5_secret_rotation"
    / "secret_inventory.yaml"
)


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #


@dataclass
class SecretStatus:
    """One row in the rotation report."""
    name: str
    last_rotated: date
    rotation_days: int
    days_since: int
    bucket: str  # "ok" | "warn" | "urgent"
    owner: Optional[str] = None
    description: Optional[str] = None
    store: Optional[str] = None
    rotation_steps: list[str] = field(default_factory=list)


@dataclass
class RotationReport:
    """Aggregated result returned by check()."""
    ok: list[SecretStatus] = field(default_factory=list)
    warn: list[SecretStatus] = field(default_factory=list)
    urgent: list[SecretStatus] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.ok) + len(self.warn) + len(self.urgent)


# --------------------------------------------------------------------------- #
# YAML loading
# --------------------------------------------------------------------------- #


def _load_yaml(path: Path) -> dict:
    """Tiny YAML loader. Uses PyYAML if available; falls back to a hand-
    rolled subset parser that handles the structure of secret_inventory.yaml
    (top-level keys, a `secrets:` list of dicts, scalar fields, and string
    lists under `rotation_steps`).

    The fallback exists so this module can run in test environments
    without the PyYAML dependency loaded (and so the production worker
    doesn't crash if a future dep prune removes it accidentally).
    """
    text = path.read_text(encoding="utf-8")

    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        log.debug("PyYAML not available; using fallback parser for inventory")
        return _fallback_parse(text)


def _fallback_parse(text: str) -> dict:
    """Minimal YAML subset parser for the inventory shape.

    Supports:
      - Top-level scalar keys (schema_version, defaults block)
      - `secrets:` followed by a list of `- name: ...` blocks
      - Per-entry scalar fields
      - `rotation_steps:` followed by `- "step text"` lines
      - Quoted and unquoted scalar values
      - Comments (# ...) and blank lines
    """
    result: dict = {}
    lines = text.splitlines()

    i = 0
    n = len(lines)
    current_secret: Optional[dict] = None
    in_secrets_list = False
    in_rotation_steps = False
    in_defaults = False

    secrets: list[dict] = []
    defaults: dict = {}

    def _coerce(val: str):
        val = val.strip()
        if val.startswith(('"', "'")) and val.endswith(val[0]):
            return val[1:-1]
        if val.lower() in ("true", "false"):
            return val.lower() == "true"
        try:
            if "." not in val:
                return int(val)
        except ValueError:
            pass
        return val

    while i < n:
        raw = lines[i]
        # Strip trailing comment (but only when outside quoted strings,
        # which we don't deeply support — comments at line-end are fine).
        stripped = raw.rstrip()
        # Skip empty + pure-comment lines
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            i += 1
            continue

        indent = len(stripped) - len(stripped.lstrip(" "))
        content = stripped.strip()

        # Inline comments — keep only the part before any unquoted '#'.
        # Cheap heuristic: if a '#' appears with a space before it, treat as comment.
        if " #" in content:
            content = content.split(" #", 1)[0].rstrip()

        # Top-level: indent==0
        if indent == 0:
            in_secrets_list = False
            in_defaults = False
            in_rotation_steps = False
            current_secret = None

            if content.startswith("secrets:"):
                in_secrets_list = True
            elif content.startswith("defaults:"):
                in_defaults = True
            elif ":" in content:
                k, _, v = content.partition(":")
                v = v.strip()
                if v:
                    result[k.strip()] = _coerce(v)
            i += 1
            continue

        # Defaults block (indent 2)
        if in_defaults and indent == 2 and ":" in content:
            k, _, v = content.partition(":")
            defaults[k.strip()] = _coerce(v)
            i += 1
            continue

        # Secrets list entry start: "- name: VALUE" at indent 2
        if in_secrets_list and indent == 2 and content.startswith("- "):
            in_rotation_steps = False
            current_secret = {}
            secrets.append(current_secret)
            after = content[2:].strip()
            if ":" in after:
                k, _, v = after.partition(":")
                v = v.strip()
                if v:
                    current_secret[k.strip()] = _coerce(v)
            i += 1
            continue

        # Field within a secret entry (indent 4, "key: value")
        if in_secrets_list and current_secret is not None and indent == 4:
            in_rotation_steps = False
            if ":" in content:
                k, _, v = content.partition(":")
                key = k.strip()
                v = v.strip()
                if key == "rotation_steps":
                    in_rotation_steps = True
                    current_secret[key] = []
                elif v:
                    current_secret[key] = _coerce(v)
            i += 1
            continue

        # rotation_steps item: indent 6, "- 'text'"
        if (
            in_secrets_list
            and current_secret is not None
            and in_rotation_steps
            and indent == 6
            and content.startswith("- ")
        ):
            step = content[2:].strip()
            if step.startswith(('"', "'")) and step.endswith(step[0]):
                step = step[1:-1]
            current_secret.setdefault("rotation_steps", []).append(step)
            i += 1
            continue

        # Anything else — skip safely (best-effort parser)
        i += 1

    result["secrets"] = secrets
    if defaults:
        result["defaults"] = defaults
    return result


# --------------------------------------------------------------------------- #
# Date parsing
# --------------------------------------------------------------------------- #


def _parse_date(v) -> Optional[date]:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if not v:
        return None
    s = str(v).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        log.warning(f"secret_rotation_check: unparseable date: {v!r}")
        return None


# --------------------------------------------------------------------------- #
# Core: check()
# --------------------------------------------------------------------------- #


def check(
    inventory_path: Optional[Path] = None,
    today: Optional[date] = None,
) -> RotationReport:
    """Load inventory, compute buckets. Pure function — no side effects.

    Args:
        inventory_path: override default INVENTORY_PATH (used by tests).
        today: override "now" for deterministic testing.

    Returns:
        RotationReport with ok/warn/urgent lists.
    """
    today = today or date.today()
    path = inventory_path or INVENTORY_PATH

    report = RotationReport()

    try:
        data = _load_yaml(path)
    except FileNotFoundError:
        msg = f"inventory not found at {path}"
        log.error(f"secret_rotation_check: {msg}")
        report.parse_errors.append(msg)
        return report
    except Exception as e:
        msg = f"inventory load failed: {e}"
        log.exception(f"secret_rotation_check: {msg}")
        report.parse_errors.append(msg)
        return report

    defaults = data.get("defaults", {}) or {}
    default_rotation_days = int(defaults.get("rotation_days", DEFAULT_ROTATION_DAYS))
    warn_lead_days = int(defaults.get("warn_lead_days", DEFAULT_WARN_LEAD_DAYS))

    secrets = data.get("secrets") or []
    if not isinstance(secrets, list):
        report.parse_errors.append("inventory: 'secrets' is not a list")
        return report

    for idx, entry in enumerate(secrets):
        if not isinstance(entry, dict):
            report.parse_errors.append(f"secret[{idx}]: not a dict")
            continue

        name = entry.get("name")
        if not name:
            report.parse_errors.append(f"secret[{idx}]: missing 'name'")
            continue

        last = _parse_date(entry.get("last_rotated"))
        if last is None:
            report.parse_errors.append(f"{name}: missing/unparseable last_rotated")
            continue

        rotation_days = int(entry.get("rotation_days", default_rotation_days))
        if rotation_days <= 0:
            report.parse_errors.append(f"{name}: rotation_days must be > 0")
            continue

        days_since = (today - last).days
        warn_threshold = max(rotation_days - warn_lead_days, 0)

        if days_since >= rotation_days:
            bucket = "urgent"
        elif days_since >= warn_threshold:
            bucket = "warn"
        else:
            bucket = "ok"

        status = SecretStatus(
            name=name,
            last_rotated=last,
            rotation_days=rotation_days,
            days_since=days_since,
            bucket=bucket,
            owner=entry.get("owner"),
            description=entry.get("description"),
            store=entry.get("store"),
            rotation_steps=list(entry.get("rotation_steps") or []),
        )

        getattr(report, bucket).append(status)

    return report


# --------------------------------------------------------------------------- #
# Public: check_and_alert()
# --------------------------------------------------------------------------- #


def _format_summary(statuses: list[SecretStatus]) -> str:
    """Render a multi-line summary of a bucket for the Telegram message."""
    if not statuses:
        return "(none)"
    lines = []
    # Sort URGENT/WARN by days-overdue desc so worst first
    for s in sorted(statuses, key=lambda x: x.days_since, reverse=True):
        overdue = s.days_since - s.rotation_days
        if overdue >= 0:
            tail = f"{s.days_since}d / {s.rotation_days}d cap (+{overdue}d overdue)"
        else:
            tail = f"{s.days_since}d / {s.rotation_days}d cap ({-overdue}d to cap)"
        owner = f" [{s.owner}]" if s.owner else ""
        lines.append(f"  - {s.name}{owner}: {tail}")
    return "\n".join(lines)


def check_and_alert(
    inventory_path: Optional[Path] = None,
    today: Optional[date] = None,
    send_alert_fn=None,
) -> dict:
    """Run check() and fire one Telegram alert per non-empty WARN/URGENT bucket.

    Args:
        inventory_path: override default INVENTORY_PATH (used by tests).
        today: override "now" for deterministic testing.
        send_alert_fn: optional injection point for tests. Defaults to
                       ops_alerts.send_alert. Signature must match
                       (severity, title, body, data=None) -> dict.

    Returns:
        {
          "checked": int,           # total secrets evaluated
          "ok": int,
          "warn": int,
          "urgent": int,
          "parse_errors": [str],
          "alerts_sent": int,
          "alert_results": [dict],  # one per send_alert call
        }
    """
    report = check(inventory_path=inventory_path, today=today)

    if send_alert_fn is None:
        try:
            from ops_alerts import send_alert as send_alert_fn  # type: ignore
        except Exception as e:
            log.error(f"secret_rotation_check: cannot import ops_alerts: {e}")
            return {
                "checked": report.total,
                "ok": len(report.ok),
                "warn": len(report.warn),
                "urgent": len(report.urgent),
                "parse_errors": report.parse_errors,
                "alerts_sent": 0,
                "alert_results": [],
                "reason": f"ops_alerts_import_error: {e}",
            }

    alert_results: list[dict] = []

    if report.urgent:
        body = (
            f"URGENT: {len(report.urgent)} secret(s) past their {report.urgent[0].rotation_days}-day "
            "rotation window (or per-secret override). Rotate ASAP.\n\n"
            f"{_format_summary(report.urgent)}\n\n"
            "Rotation steps: _tier_d5_secret_rotation/secret_inventory.yaml\n"
            "After rotating, update last_rotated + commit."
        )
        try:
            res = send_alert_fn(
                severity="HIGH",
                title="Secret rotation URGENT",
                body=body,
                data={
                    "urgent_count": len(report.urgent),
                    "names": [s.name for s in report.urgent],
                },
            )
            alert_results.append({"bucket": "urgent", **(res or {})})
        except Exception as e:
            log.exception("secret_rotation_check: urgent alert failed")
            alert_results.append({"bucket": "urgent", "sent": False, "reason": str(e)})

    if report.warn:
        body = (
            f"WARN: {len(report.warn)} secret(s) approaching rotation window. "
            "Rotate within the next 15 days.\n\n"
            f"{_format_summary(report.warn)}\n\n"
            "Rotation steps: _tier_d5_secret_rotation/secret_inventory.yaml"
        )
        try:
            res = send_alert_fn(
                severity="LOW",
                title="Secret rotation WARN",
                body=body,
                data={
                    "warn_count": len(report.warn),
                    "names": [s.name for s in report.warn],
                },
            )
            alert_results.append({"bucket": "warn", **(res or {})})
        except Exception as e:
            log.exception("secret_rotation_check: warn alert failed")
            alert_results.append({"bucket": "warn", "sent": False, "reason": str(e)})

    if report.parse_errors:
        # One alert for inventory issues — they shouldn't be silent.
        body = (
            "Secret inventory had parse errors. Some secrets may not be "
            "tracked. Fix the inventory YAML.\n\n"
            + "\n".join(f"  - {e}" for e in report.parse_errors)
        )
        try:
            res = send_alert_fn(
                severity="MEDIUM",
                title="Secret rotation: inventory parse errors",
                body=body,
                data={"errors": report.parse_errors},
            )
            alert_results.append({"bucket": "parse_errors", **(res or {})})
        except Exception as e:
            log.exception("secret_rotation_check: parse-error alert failed")
            alert_results.append(
                {"bucket": "parse_errors", "sent": False, "reason": str(e)}
            )

    return {
        "checked": report.total,
        "ok": len(report.ok),
        "warn": len(report.warn),
        "urgent": len(report.urgent),
        "parse_errors": report.parse_errors,
        "alerts_sent": sum(1 for r in alert_results if r.get("sent")),
        "alert_results": alert_results,
    }


__all__ = [
    "SecretStatus",
    "RotationReport",
    "check",
    "check_and_alert",
    "INVENTORY_PATH",
    "DEFAULT_WARN_LEAD_DAYS",
    "DEFAULT_ROTATION_DAYS",
]


if __name__ == "__main__":
    # CLI: dry-run check, print the report without firing alerts.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rpt = check()
    print(f"Checked: {rpt.total}")
    print(f"OK:      {len(rpt.ok)}")
    print(f"WARN:    {len(rpt.warn)}")
    print(f"URGENT:  {len(rpt.urgent)}")
    if rpt.warn:
        print("\nWARN:")
        print(_format_summary(rpt.warn))
    if rpt.urgent:
        print("\nURGENT:")
        print(_format_summary(rpt.urgent))
    if rpt.parse_errors:
        print("\nPARSE ERRORS:")
        for e in rpt.parse_errors:
            print(f"  - {e}")
