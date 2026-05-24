"""
Test fixtures for the Tier D6 / A2 pixels + UTM capture suite.

Mirrors tests/analytics/conftest.py:
  - Loads the FIESTA env file from CEO OS working files (only fills missing keys).
  - Boots the main Flask app via `import main`.
  - Disables CSRF for ergonomic POSTs.

Adds a per-test `pixel_env` fixture that lets tests flip the four pixel
env vars and have them reset on teardown — so test ordering can't leak.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = Path("G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env")
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def app():
    """Boot the main Flask app exactly once per session."""
    import main  # noqa: F401 — triggers blueprint + pixel registration
    from app import app as flask_app, db
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        db.create_all()
    yield flask_app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


# The 4 env vars pixels.py reads. Each test fixture saves prior values
# and restores them on teardown.
_PIXEL_ENV_KEYS = (
    "PIXELS_ENABLED",
    "META_PIXEL_ID",
    "LINKEDIN_PARTNER_ID",
    "TWITTER_PIXEL_ID",
    "PIXELS_ALLOW_IN_DEV",
    "PIXELS_DISABLE_IN_TEST",
    "FLASK_ENV",
    "PYTEST_CURRENT_TEST",
)


@pytest.fixture
def pixel_env(monkeypatch):
    """Yield a setter function that mutates pixel env vars for the duration
    of the test. Original values restored on teardown.

    NOTE on PYTEST_CURRENT_TEST: pytest sets this env var on every test
    phase boundary (setup/call/teardown). Simply removing it from os.environ
    isn't sufficient because pytest restores it before the test call runs.
    So when a test needs to verify the pixel-render path, we monkeypatch
    pixels._in_test_mode to return False for the duration of the test.

    Usage::

        def test_meta_renders(pixel_env, client):
            # bypass_test_mode_check=True forces pixels.py to render even
            # though we're inside pytest.
            pixel_env(bypass_test_mode_check=True,
                      PIXELS_ENABLED='1', META_PIXEL_ID='123456789')
            ...

    Passing ``None`` as a value removes the env var entirely (so the
    pixels.py default kicks in).

    IMPORTANT: we resolve the live `pixels` module via sys.modules at call
    time, not at fixture-import time. Some tests do
    ``importlib.reload(pixels)``, which leaves the app's already-registered
    context processor pointing at the OLD module while `import pixels` from
    a later test fixture would otherwise return the NEW one. Resolving via
    sys.modules['pixels'] guarantees we always monkeypatch the same module
    object the context processor actually invokes.
    """
    import sys

    saved = {k: os.environ.get(k) for k in _PIXEL_ENV_KEYS}

    def setter(*, bypass_test_mode_check=False, **kwargs):
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        if bypass_test_mode_check:
            # Resolve the *active* pixels module (the one the app's context
            # processor closed over at registration time). sys.modules is
            # the canonical reference; importing afresh might give us a
            # reloaded copy that the app never sees.
            mod = sys.modules.get("pixels")
            if mod is None:
                import pixels as mod  # type: ignore  # last-resort
            # Force pixels.py to ignore BOTH the PYTEST_CURRENT_TEST env signal
            # AND the Flask app.testing=True belt-and-braces check, so the
            # enabled rendering path can be verified end-to-end.
            monkeypatch.setattr(mod, "_in_test_mode", lambda: False)
            monkeypatch.setattr(mod, "_flask_app_testing", lambda: False)

    yield setter

    # Restore env (monkeypatch unwinds automatically on teardown).
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
