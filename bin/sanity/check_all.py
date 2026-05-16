#!/usr/bin/env python
"""
SUB-E Data Sanity — FIESTA integration verification script.
Re-runnable, read-only (except S3 write+delete self-test).
Run from repo root:  python bin/sanity/check_all.py

Reads credentials from G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env
via python-dotenv.  Falls back to os.environ if dotenv unavailable.
"""

import os
import sys
import json
import time
import datetime
import socket
import traceback
import smtplib

# ── credential loader ────────────────────────────────────────────────────────
ENV_PATH = r"G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env"

def load_env(path):
    """Parse key=value from a .env file; skip # comments and blank lines."""
    if not os.path.exists(path):
        print(f"[WARN] .env not found at {path}, using os.environ only")
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env(ENV_PATH)

def env(key):
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Missing env var: {key}")
    return val


# ── result accumulator ───────────────────────────────────────────────────────
results = {}   # section_key -> dict with status, detail, rows, etc.

def section(key, status, **kwargs):
    results[key] = {"status": status, **kwargs}
    icon = "+" if status == "PASS" else ("!" if status == "FAIL" else "~")
    print(f"  [{icon}] {key}: {status}")
    for k, v in kwargs.items():
        print(f"       {k}: {v}")


# ════════════════════════════════════════════════════════════════════════════
# 1. Neon Postgres
# ════════════════════════════════════════════════════════════════════════════
print("\n=== 1. Neon Postgres ===")

KNOWN_MODEL_TABLES = [
    # from models.py (non-__tablename__ classes use Flask-SQLAlchemy defaults)
    'audit_log', 'organization', 'organization_user', 'friend_invitation',
    'trust_activity', 'trust_ranking', 'organization_invitation', 'user',
    'receipt', 'receipt_item', 'client', 'invoice', 'invoice_item',
    'payment', 'bank_account', 'company_expense', 'client_expense',
    'user_income', 'registration_rate_limit', 'worker_heartbeat',
    # onboarding_models.py (no __tablename__ → default 'onboarding_progress')
    'onboarding_progress',
    # from explicit __tablename__ across all model files
    'account', 'general_ledger_entry', 'journal_entry_line', 'asset_category',
    'fixed_asset', 'depreciation_entry', 'accounting_period',
    'financial_transaction', 'transaction_meta_receipt', 'transaction_meta_bank',
    'transaction_meta_manual', 'transaction_meta_journal', 'bank_statement',
    'bank_statement_page', 'bank_statement_processing_log', 'funding_source',
    'funding_source_transaction', 'reconciliation_exception',
    'reconciliation_training_data', 'smart_matching_rules',
    'split_transaction_group', 'split_transaction_member', 'reconciliation_audit',
    'account_balance_cache', 'document_context', 'extraction_event',
]

try:
    import psycopg2
    db_url = env("DATABASE_URL")
    conn = psycopg2.connect(db_url, connect_timeout=15)
    conn.set_session(readonly=True)
    cur = conn.cursor()

    # Basic diagnostics
    cur.execute("SELECT version();")
    pg_version = cur.fetchone()[0]

    cur.execute("SELECT current_database(), current_user, inet_server_addr();")
    db_name, db_user, server_addr = cur.fetchone()

    cur.execute("""
        SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public';
    """)
    public_table_count = cur.fetchone()[0]

    # Enumerate public tables
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public'
        ORDER BY table_name;
    """)
    public_tables = [r[0] for r in cur.fetchall()]

    # Row counts for known model tables
    row_counts = {}
    for tbl in KNOWN_MODEL_TABLES:
        if tbl in public_tables:
            try:
                cur.execute(f"SELECT count(*) FROM {tbl};")
                row_counts[tbl] = cur.fetchone()[0]
            except Exception as e:
                row_counts[tbl] = f"ERROR: {e}"
                conn.rollback()
        else:
            row_counts[tbl] = "TABLE_NOT_FOUND"

    # Total rows across all counted tables
    numeric_counts = {k: v for k, v in row_counts.items() if isinstance(v, int)}
    total_rows = sum(numeric_counts.values())

    conn.close()

    section("neon_version",      "PASS", value=pg_version[:80])
    section("neon_connect",      "PASS",
            database=db_name, user=db_user, server=str(server_addr))
    section("neon_tables",       "PASS",
            public_table_count=public_table_count,
            tables_list=", ".join(public_tables))
    section("neon_row_counts",   "PASS",
            counts=json.dumps(row_counts, indent=2),
            total_rows_sampled=total_rows)

except Exception as exc:
    section("neon_connect", "FAIL", error=str(exc))
    traceback.print_exc()


# ════════════════════════════════════════════════════════════════════════════
# 2. AWS S3
# ════════════════════════════════════════════════════════════════════════════
print("\n=== 2. AWS S3 ===")

try:
    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.client(
        "s3",
        aws_access_key_id=env("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env("AWS_SECRET_ACCESS_KEY"),
        region_name=env("AWS_REGION"),
    )
    bucket = env("AWS_S3_BUCKET_NAME")

    # List objects
    paginator = s3.get_paginator("list_objects_v2")
    total_objects = 0
    total_size = 0
    sample_keys = []

    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            total_objects += 1
            total_size += obj["Size"]
            if len(sample_keys) < 5:
                sample_keys.append(obj["Key"])

    section("s3_list", "PASS",
            bucket=bucket,
            total_objects=total_objects,
            total_size_bytes=total_size,
            sample_keys=sample_keys)

    # Write + delete self-test
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    test_key = f"_fiesta_sanity_check/{ts}.txt"
    test_body = f"sanity check by SUB-E at {ts}".encode()

    s3.put_object(Bucket=bucket, Key=test_key, Body=test_body)
    s3.delete_object(Bucket=bucket, Key=test_key)

    section("s3_write_delete", "PASS",
            test_key=test_key, write="OK", delete="OK")

except Exception as exc:
    section("s3_access", "FAIL", error=str(exc))
    traceback.print_exc()


# ════════════════════════════════════════════════════════════════════════════
# 3. SendGrid API
# ════════════════════════════════════════════════════════════════════════════
print("\n=== 3. SendGrid API ===")

try:
    import requests as req

    sg_key = env("SENDGRID_API_KEY")
    headers = {"Authorization": f"Bearer {sg_key}"}

    resp = req.get("https://api.sendgrid.com/v3/user/account",
                   headers=headers, timeout=15)
    account_info = resp.json() if resp.ok else resp.text

    plan = account_info.get("type", "unknown") if isinstance(account_info, dict) else "unknown"

    section("sendgrid_account", "PASS" if resp.status_code == 200 else "FAIL",
            http_status=resp.status_code,
            plan_tier=plan)

    # Verified senders
    resp2 = req.get("https://api.sendgrid.com/v3/verified_senders",
                    headers=headers, timeout=15)
    senders = []
    if resp2.ok:
        data = resp2.json()
        for s in data.get("results", []):
            senders.append(f"{s.get('nickname','?')} <{s.get('from_email','?')}> verified={s.get('verified', '?')}")

    section("sendgrid_senders", "PASS" if resp2.status_code == 200 else "FAIL",
            http_status=resp2.status_code,
            senders=senders)

    # Sandbox send test (API contract only — no actual delivery)
    payload = {
        "personalizations": [{"to": [{"email": "test@example.com"}]}],
        "from": {"email": "info@smarter.tax"},
        "subject": "SUB-E sanity sandbox test",
        "content": [{"type": "text/plain", "value": "SUB-E sanity check — sandbox mode, not delivered"}],
        "mail_settings": {"sandbox_mode": {"enable": True}},
    }
    resp3 = req.post("https://api.sendgrid.com/v3/mail/send",
                     headers={**headers, "Content-Type": "application/json"},
                     json=payload, timeout=15)
    section("sendgrid_sandbox_send", "PASS" if resp3.status_code == 200 else "FAIL",
            http_status=resp3.status_code,
            note="sandbox_mode=true — no email delivered")

except Exception as exc:
    section("sendgrid", "FAIL", error=str(exc))
    traceback.print_exc()


# ════════════════════════════════════════════════════════════════════════════
# 4. Gmail SMTP (auth only — no message sent)
# ════════════════════════════════════════════════════════════════════════════
print("\n=== 4. Gmail SMTP ===")

try:
    smtp_user = env("GMAIL_USERNAME")
    smtp_pass = env("GMAIL_APP_PASSWORD")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
        banner = smtp.ehlo()[1].decode(errors="replace")[:200]
        smtp.starttls()
        smtp.ehlo()
        smtp.login(smtp_user, smtp_pass)
        smtp.quit()

    section("gmail_smtp", "PASS",
            host="smtp.gmail.com:587",
            tls="OK", auth="OK",
            banner_snippet=banner[:80])

except smtplib.SMTPAuthenticationError as exc:
    section("gmail_smtp", "FAIL", error=f"Auth failed: {exc}")
except Exception as exc:
    section("gmail_smtp", "FAIL", error=str(exc))
    traceback.print_exc()


# ════════════════════════════════════════════════════════════════════════════
# 5. Gemini API
# ════════════════════════════════════════════════════════════════════════════
print("\n=== 5. Gemini API ===")

try:
    import requests as req

    gemini_key = env("GEMINI_API_KEY")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={gemini_key}"
    )
    payload = {"contents": [{"parts": [{"text": "hi"}]}]}
    resp = req.post(url, json=payload, timeout=30)

    token_count = None
    if resp.ok:
        data = resp.json()
        meta = data.get("usageMetadata", {})
        token_count = meta.get("candidatesTokenCount") or meta.get("totalTokenCount")

    section("gemini_api", "PASS" if resp.status_code == 200 else "FAIL",
            http_status=resp.status_code,
            response_token_count=token_count)

except Exception as exc:
    section("gemini_api", "FAIL", error=str(exc))
    traceback.print_exc()


# ════════════════════════════════════════════════════════════════════════════
# 6. Zhipu / Z.ai API  (mirrors glm_ocr_client.py pattern)
# ════════════════════════════════════════════════════════════════════════════
print("\n=== 6. Zhipu / Z.ai API ===")

try:
    import requests as req

    zhipu_key = env("ZHIPU_API_KEY")
    # Pattern from glm_ocr_client.py: OpenAI-compatible chat-completions
    # Base URL: https://api.z.ai/api/paas/v4/chat/completions
    # Auth: Bearer ZHIPU_API_KEY
    endpoint = "https://api.z.ai/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {zhipu_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "glm-4.5v",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }
    resp = req.post(endpoint, headers=headers, json=payload, timeout=30)

    completion = None
    if resp.ok:
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            completion = choices[0].get("message", {}).get("content", "")[:50]

    section("zhipu_api", "PASS" if resp.status_code == 200 else "FAIL",
            http_status=resp.status_code,
            endpoint=endpoint,
            model="glm-4.5v",
            response_snippet=str(completion)[:80] if completion else None,
            error_body=resp.text[:200] if not resp.ok else None)

except Exception as exc:
    section("zhipu_api", "FAIL", error=str(exc))
    traceback.print_exc()


# ════════════════════════════════════════════════════════════════════════════
# 7. Replit Connectors check
# ════════════════════════════════════════════════════════════════════════════
print("\n=== 7. Replit Connectors ===")

import subprocess, re as _re

try:
    result = subprocess.run(
        ["grep", "-rn",
         r"connectors\.replit\.com\|REPLIT_CONNECTORS\|CONNECTORS_HOSTNAME",
         ".",
         "--include=*.py",
         "--exclude-dir=.local",
         "--exclude-dir=__pycache__",
         "--exclude-dir=.git",
         "--exclude-dir=bin"],   # exclude this sanity script itself
        cwd="C:/Users/mahes/fiesta_replit_source/DevelopSriLanka",
        capture_output=True, text=True, timeout=30
    )
    hits = [l.strip() for l in result.stdout.splitlines() if l.strip()]

    # Filter out .local/ (skill/template files, not app code)
    app_hits = [h for h in hits if ".local" not in h and "bin/sanity" not in h]

    if app_hits:
        section("replit_connectors", "WARN",
                note="Replit Connector references found in app code",
                hits=app_hits)
    else:
        section("replit_connectors", "PASS",
                note="ZERO production .py files reference Replit Connectors",
                skipped_local_files=len(hits))

except Exception as exc:
    section("replit_connectors", "FAIL", error=str(exc))
    traceback.print_exc()


# ════════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════════
print("\n=== SUMMARY ===")
pass_count = sum(1 for v in results.values() if v["status"] == "PASS")
fail_count = sum(1 for v in results.values() if v["status"] == "FAIL")
warn_count = sum(1 for v in results.values() if v["status"] == "WARN")
total = len(results)

if fail_count == 0:
    verdict = "ALL GREEN" if warn_count == 0 else "GREEN (with warnings)"
elif fail_count <= 2:
    verdict = "DEGRADED"
else:
    verdict = "BLOCKED"

print(f"Verdict : {verdict}")
print(f"Pass    : {pass_count}/{total}")
print(f"Fail    : {fail_count}/{total}")
print(f"Warn    : {warn_count}/{total}")
print()
for k, v in results.items():
    print(f"  {v['status']:5s}  {k}")

# Persist results for report generator
OUT = "C:/Users/mahes/fiesta_replit_source/DevelopSriLanka/bin/sanity/_last_run.json"
with open(OUT, "w") as fh:
    json.dump({
        "run_at": datetime.datetime.utcnow().isoformat() + "Z",
        "verdict": verdict,
        "pass": pass_count,
        "fail": fail_count,
        "warn": warn_count,
        "total": total,
        "results": results,
    }, fh, indent=2, default=str)
print(f"\nFull results saved to {OUT}")
