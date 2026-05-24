"""Profile GET / for anon: instrument each suspected hot spot and print timings."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_ENV_PATH = Path("G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env")
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: F401
from app import app as flask_app

flask_app.config["TESTING"] = True
flask_app.config["WTF_CSRF_ENABLED"] = False

# Monkey-patch events.emit to time it.
import events
_orig_emit = events.emit
_emit_times = []
def _timed_emit(*a, **kw):
    t0 = time.perf_counter()
    try:
        return _orig_emit(*a, **kw)
    finally:
        _emit_times.append(time.perf_counter() - t0)
events.emit = _timed_emit

# Monkey-patch context processors registered on app.
_cp_times = {}
_orig_cps = list(flask_app.template_context_processors[None])
def _wrap_cp(fn):
    name = getattr(fn, '__name__', repr(fn))
    def _wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            _cp_times.setdefault(name, []).append(time.perf_counter() - t0)
    _wrapper.__name__ = name
    return _wrapper

flask_app.template_context_processors[None] = [_wrap_cp(fn) for fn in _orig_cps]

# Time render_template.
from flask import templating as _templating
_orig_render = _templating._render
_render_times = []
def _timed_render(*a, **kw):
    t0 = time.perf_counter()
    try:
        return _orig_render(*a, **kw)
    finally:
        _render_times.append(time.perf_counter() - t0)
_templating._render = _timed_render

# Time before_request handlers.
_br_times = {}
_orig_brs = list(flask_app.before_request_funcs[None])
def _wrap_br(fn):
    name = getattr(fn, '__name__', repr(fn))
    def _wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            _br_times.setdefault(name, []).append(time.perf_counter() - t0)
    _wrapper.__name__ = name
    return _wrapper
flask_app.before_request_funcs[None] = [_wrap_br(fn) for fn in _orig_brs]

client = flask_app.test_client()
# Warm-up.
r = client.get("/")
assert r.status_code == 200
_emit_times.clear()
_cp_times.clear()
_render_times.clear()
_br_times.clear()

print("\n--- 3 measured GET / ---")
for i in range(3):
    t0 = time.perf_counter()
    r = client.get("/")
    total = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    print(f"\nSample {i+1}: total {total:.1f}ms")
    print(f"  emit() calls: {len(_emit_times)}  total: {sum(_emit_times)*1000:.1f}ms")
    print(f"  render_template: {sum(_render_times)*1000:.1f}ms (n={len(_render_times)})")
    print(f"  before_request handlers:")
    for n, ts in _br_times.items():
        print(f"    {n}: {sum(ts)*1000:.1f}ms (n={len(ts)})")
    print(f"  context processors:")
    for n, ts in _cp_times.items():
        print(f"    {n}: {sum(ts)*1000:.1f}ms (n={len(ts)})")
    _emit_times.clear(); _render_times.clear(); _br_times.clear(); _cp_times.clear()
