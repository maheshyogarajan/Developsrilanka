"""Conftest for tests/ab_test_module/.

Loads DATABASE_URL + friends from the cockpit env file BEFORE pytest
collects the test module, so `from app import db` (transitively triggered
by `from ab_test import ...` in the tests) doesn't crash on a NoneType
slice of DATABASE_URL inside app.py.

Mirrors the env-load pattern at the top of tests/remittance/conftest.py.
The ab_test tests themselves don't touch the live DB — they stub the ORM
layer entirely via mocks — but `import ab_test` pulls in `from app import db`
which evaluates Flask/SQLAlchemy config at import time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = Path("G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env")

if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Make sure the repo root is on sys.path so `import ab_test` works when
# pytest is invoked from the repo root.
sys.path.insert(0, str(_REPO_ROOT))
