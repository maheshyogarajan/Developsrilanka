"""
Deploy gate: run every registered subagent smoke test sequentially.

Usage:
    python -m tests.ai_run.smoke.run_all

Exit code:
    0  — every registered smoke PASSED
    1  — at least one smoke FAILED (deploy MUST be rolled back per the
         deployment checklist; see DEPLOYMENT_CHECKLIST.md)

Output format is intentionally compact + grep-friendly. CI/cron should
capture stdout and stderr; both go to a single stream here.
"""
from __future__ import annotations

import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    _setup_logging()
    # Import the base module — this side-effects the @register_smoke decorators
    # in base.py, populating the registry. Future subagent smokes either live
    # in base.py OR self-import from their own test module before run_all is
    # invoked.
    from tests.ai_run.smoke import base  # noqa: F401
    from tests.ai_run.smoke.base import all_registered_smokes

    smokes = all_registered_smokes()
    if not smokes:
        print("RUN_ALL: no smoke tests registered. Nothing to gate.")
        return 1

    print(f"RUN_ALL: executing {len(smokes)} smoke test(s)")
    print("-" * 72)

    results = []
    for cls in smokes:
        instance = cls()
        result = instance.run()
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        line = (
            f"  [{status}] {result.subagent_name:30s} "
            f"{result.duration_seconds:>6.2f}s  marker={result.marker}"
        )
        print(line)
        if not result.passed:
            print(f"        error: {result.error}")
        if result.cleanup_error:
            print(f"        cleanup-warning: {result.cleanup_error}")
        if result.evidence:
            print(f"        evidence: {result.evidence}")

    print("-" * 72)
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    print(f"RUN_ALL: {passed} passed, {failed} failed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
