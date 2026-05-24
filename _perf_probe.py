"""Local perf probe for B-0040: time GET / for anon visitor.

Loads cockpit env (so DATABASE_URL etc. resolve), boots app via main, then
times 1 warm-up + 5 measured anon GETs through the Flask test client.

Run: python _perf_probe.py
"""
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

import main  # noqa: F401 — registers all blueprints
from app import app as flask_app

flask_app.config["TESTING"] = True
flask_app.config["WTF_CSRF_ENABLED"] = False

client = flask_app.test_client()

# Warm-up — pays JIT / first-template-compile / first-cache-fill costs.
r = client.get("/")
assert r.status_code == 200, r.status_code
print(f"warm-up: {r.status_code}")

# 5 measured samples.
samples = []
for i in range(5):
    t0 = time.perf_counter()
    r = client.get("/")
    t1 = time.perf_counter()
    assert r.status_code == 200, r.status_code
    samples.append(t1 - t0)
    print(f"sample {i+1}: {samples[-1]*1000:.1f} ms")

mean = sum(samples) / len(samples)
print(f"mean: {mean*1000:.1f} ms  min: {min(samples)*1000:.1f} ms  max: {max(samples)*1000:.1f} ms")
