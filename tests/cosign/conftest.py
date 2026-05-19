"""Test fixtures for fiesta.cosign (S10) tests.

We stub `app.db` BEFORE pytest collects test modules. This lets the
cosign models import cleanly without spinning up Flask + Postgres.

This conftest is loaded automatically by pytest before any test module
in this directory is imported -- so the stub is in place before
`from fiesta.cosign.models import ...` runs.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _install_app_stub():
    """Inject a minimal `app` module into sys.modules.

    fiesta.cosign.models does `from app import db` and uses
    db.Model / db.Column / db.Integer / db.String / db.DateTime /
    db.Text / db.Boolean / db.Float / db.Numeric / db.ForeignKey /
    db.Date. We need:
      - db.Model: a base class our model classes can subclass + that
        accepts kwargs in __init__ (real SQLAlchemy Models do)
      - everything else: harmless placeholder callables / values
    """
    if "app" in sys.modules:
        return  # something else stubbed it; leave alone

    class _ModelBase:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    db_stub = MagicMock()
    db_stub.Model = _ModelBase
    # Calling db.Column(...) / db.String(...) / db.Numeric(...) /
    # db.ForeignKey(...) at class-body time should not crash. Return
    # plain placeholders.
    db_stub.Column = lambda *a, **k: None
    db_stub.String = lambda *a, **k: None
    db_stub.Numeric = lambda *a, **k: None
    db_stub.ForeignKey = lambda *a, **k: None
    for name in ("Integer", "Text", "DateTime", "Boolean", "Float", "Date"):
        setattr(db_stub, name, None)

    app_mod = MagicMock()
    app_mod.db = db_stub
    sys.modules["app"] = app_mod


_install_app_stub()
