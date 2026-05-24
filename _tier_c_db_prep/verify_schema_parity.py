#!/usr/bin/env python3
"""
Tier C6 — Schema Parity Verifier (G1 gate).

Compares the Neon source DB (DATABASE_URL) and the Fly PG target DB
(FLY_PG_DATABASE_URL) on:
  - Table list (public schema)
  - Column list per table (name, data_type, is_nullable, column_default)
  - Index list per table (indexname + indexdef)
  - View definitions (incl. vw_tax_bill_context from Tier B)
  - Foreign-key constraints

Exit codes:
  0  parity (cutover may proceed)
  1  divergence detected (cutover MUST NOT proceed; review diff)
  2  unable to connect / runtime error

Council requirement: this script is the G1 gate. Cutover script invokes
it as the first step; non-zero halts cutover.

Usage:
  DATABASE_URL='postgresql://...neon.tech...' \
  FLY_PG_DATABASE_URL='postgres://fiesta_mvp:...@fiesta-pg-bom.flycast:5432/fiesta_mvp?sslmode=disable' \
  python verify_schema_parity.py [--verbose] [--json]

NOTE: FLY_PG_DATABASE_URL with .flycast host is only reachable from inside
Fly. Locally, run via `flyctl ssh console -a fiesta-mvp` or `flyctl proxy`
to fiesta-pg-bom + use the proxy DSN.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. `pip install psycopg2-binary`.", file=sys.stderr)
    sys.exit(2)


def connect(dsn: str, label: str):
    try:
        return psycopg2.connect(dsn, connect_timeout=15)
    except Exception as exc:
        print(f"ERROR: cannot connect to {label}: {exc}", file=sys.stderr)
        sys.exit(2)


def fetch_tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_type   = 'BASE TABLE'
             ORDER BY table_name
            """
        )
        return [r[0] for r in cur.fetchall()]


def fetch_columns(conn) -> dict[str, list[dict[str, Any]]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT table_name,
                   column_name,
                   data_type,
                   is_nullable,
                   column_default
              FROM information_schema.columns
             WHERE table_schema = 'public'
             ORDER BY table_name, ordinal_position
            """
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            out.setdefault(row["table_name"], []).append(
                {
                    "column_name": row["column_name"],
                    "data_type": row["data_type"],
                    "is_nullable": row["is_nullable"],
                    "column_default": row["column_default"],
                }
            )
        return out


def fetch_indexes(conn) -> dict[str, list[dict[str, str]]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT tablename, indexname, indexdef
              FROM pg_indexes
             WHERE schemaname = 'public'
             ORDER BY tablename, indexname
            """
        )
        out: dict[str, list[dict[str, str]]] = {}
        for row in cur.fetchall():
            # Strip schema prefixes to make defs comparable between hosts.
            indexdef = (row["indexdef"] or "").replace("public.", "")
            out.setdefault(row["tablename"], []).append(
                {"indexname": row["indexname"], "indexdef": indexdef}
            )
        return out


def fetch_views(conn) -> dict[str, str]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT table_name, view_definition
              FROM information_schema.views
             WHERE table_schema = 'public'
             ORDER BY table_name
            """
        )
        return {
            r["table_name"]: " ".join((r["view_definition"] or "").split())
            for r in cur.fetchall()
        }


def fetch_foreign_keys(conn) -> dict[str, list[dict[str, str]]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                tc.table_name,
                tc.constraint_name,
                kcu.column_name,
                ccu.table_name  AS foreign_table_name,
                ccu.column_name AS foreign_column_name
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage  kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema    = kcu.table_schema
              JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
               AND ccu.table_schema    = tc.table_schema
             WHERE tc.constraint_type = 'FOREIGN KEY'
               AND tc.table_schema    = 'public'
             ORDER BY tc.table_name, tc.constraint_name
            """
        )
        out: dict[str, list[dict[str, str]]] = {}
        for row in cur.fetchall():
            out.setdefault(row["table_name"], []).append(
                {
                    "constraint_name": row["constraint_name"],
                    "column_name": row["column_name"],
                    "foreign_table_name": row["foreign_table_name"],
                    "foreign_column_name": row["foreign_column_name"],
                }
            )
        return out


def diff_lists(label: str, a: list[Any], b: list[Any]) -> list[str]:
    """Return human-readable diff lines for two ordered/orderable lists."""
    set_a, set_b = set(a), set(b)
    diffs = []
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)
    if only_a:
        diffs.append(f"[{label}] only in SOURCE (Neon): {only_a}")
    if only_b:
        diffs.append(f"[{label}] only in TARGET (Fly): {only_b}")
    return diffs


def diff_dicts(label: str, a: dict, b: dict) -> list[str]:
    diffs = []
    all_keys = sorted(set(a.keys()) | set(b.keys()))
    for key in all_keys:
        if key not in a:
            diffs.append(f"[{label}] table only in TARGET: {key}")
            continue
        if key not in b:
            diffs.append(f"[{label}] table only in SOURCE: {key}")
            continue
        if a[key] != b[key]:
            diffs.append(f"[{label}] mismatch for {key!r}")
            # Try to surface specifics
            if isinstance(a[key], list) and isinstance(b[key], list):
                a_set = {json.dumps(x, sort_keys=True) for x in a[key]}
                b_set = {json.dumps(x, sort_keys=True) for x in b[key]}
                for missing in sorted(a_set - b_set):
                    diffs.append(f"  - missing from TARGET: {missing}")
                for extra in sorted(b_set - a_set):
                    diffs.append(f"  + extra in TARGET:    {extra}")
            elif isinstance(a[key], str) and isinstance(b[key], str):
                diffs.append(f"  - SOURCE: {a[key][:300]}")
                diffs.append(f"  + TARGET: {b[key][:300]}")
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="Schema parity verifier (G1 gate)")
    parser.add_argument("--verbose", action="store_true", help="Print full per-section summary")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable report")
    parser.add_argument(
        "--source-dsn",
        default=os.environ.get("DATABASE_URL"),
        help="Source DSN (default: $DATABASE_URL, the live Neon DB)",
    )
    parser.add_argument(
        "--target-dsn",
        default=os.environ.get("FLY_PG_DATABASE_URL"),
        help="Target DSN (default: $FLY_PG_DATABASE_URL, the new Fly PG cluster)",
    )
    args = parser.parse_args()

    if not args.source_dsn or not args.target_dsn:
        print(
            "ERROR: both DATABASE_URL (source/Neon) and FLY_PG_DATABASE_URL "
            "(target/Fly bom) must be set, either as env vars or via "
            "--source-dsn / --target-dsn.",
            file=sys.stderr,
        )
        return 2

    src = connect(args.source_dsn, "SOURCE (Neon)")
    tgt = connect(args.target_dsn, "TARGET (Fly bom)")

    report: dict[str, Any] = {
        "source": "Neon (us-east-1)",
        "target": "Fly Postgres (bom)",
        "sections": {},
        "diffs": [],
    }

    sections = {
        "tables":       (fetch_tables,        diff_lists),
        "columns":      (fetch_columns,       diff_dicts),
        "indexes":      (fetch_indexes,       diff_dicts),
        "views":        (fetch_views,         diff_dicts),
        "foreign_keys": (fetch_foreign_keys,  diff_dicts),
    }

    for name, (fetcher, differ) in sections.items():
        src_data = fetcher(src)
        tgt_data = fetcher(tgt)
        diffs = differ(name, src_data, tgt_data)
        report["sections"][name] = {
            "source_count": len(src_data),
            "target_count": len(tgt_data),
            "diff_count":   len(diffs),
        }
        report["diffs"].extend(diffs)

    src.close()
    tgt.close()

    has_diffs = bool(report["diffs"])

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"SOURCE: {report['source']}")
        print(f"TARGET: {report['target']}")
        print()
        for name, summary in report["sections"].items():
            status = "OK" if summary["diff_count"] == 0 else f"DIFF ({summary['diff_count']})"
            print(
                f"  {name:14s} source={summary['source_count']:4d}  "
                f"target={summary['target_count']:4d}  {status}"
            )
        print()
        if has_diffs:
            print("DIFFS:")
            for d in report["diffs"]:
                print(f"  {d}")
            print()
            print("RESULT: DIVERGENCE — cutover MUST NOT proceed.")
        else:
            print("RESULT: PARITY — cutover may proceed (G1 gate green).")

    return 1 if has_diffs else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(2)
