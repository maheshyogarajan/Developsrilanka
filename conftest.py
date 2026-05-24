"""Top-level pytest conftest.

Tier D1 #B3 (2026-05-24): events.emit() defaults to async (background
ThreadPoolExecutor) for production performance. Tests need synchronous
emission so assertions against persisted Event rows don't race the worker.

Setting EVENTS_SYNC_FOR_TEST=1 before any test module imports events.py
forces emit() to write the row in-line on the calling thread.
"""
import os

# Must happen BEFORE any import of events.py / app.py / analytics_beacon_routes.py
os.environ.setdefault("EVENTS_SYNC_FOR_TEST", "1")
