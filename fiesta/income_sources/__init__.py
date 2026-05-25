"""fiesta.income_sources — G3.6 (Income-Source Picker) blueprint.

Wires the `/api/fiesta/income-sources` JSON API + the standalone
`/fie/income-sources` page that renders the picker partial. The picker
partial itself lives at `templates/_fiesta/income_source_picker.html`
and can be included anywhere (modal, profile page, hub).

See `routes.py` for the contract.
"""
from .routes import register_blueprint, bp

__all__ = ["register_blueprint", "bp"]
