# wsgi.py — production entry point for gunicorn.
# Imports from main so all blueprint registrations run at module load,
# not just when `python main.py` is invoked directly.
# (Previously imported from `app` directly, which missed ~15 blueprint
# registrations including `getting_started_bp`, causing url_for() lookups
# in layout.html to BuildError -> bare "Internal Server Error" on every
# authenticated page.)
from main import app  # noqa: F401

if __name__ == "__main__":
    app.run()
