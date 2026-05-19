"""fiesta.deductions.catalog_loader — pure YAML reader for catalog.yaml.

Kept separate from routes so the test suite can import it without
spinning up the Flask app context. Returns a frozen dict (defensive
copies on every call) so callers cannot accidentally mutate the
canonical catalog.
"""
from __future__ import annotations

import copy
import functools
import logging
import os
import pathlib
from typing import Any

logger = logging.getLogger(__name__)

# yaml is in the FIESTA runtime requirements -- it's used by deployment configs.
try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "PyYAML is required for fiesta.deductions.catalog_loader. "
        "Install via: pip install pyyaml"
    ) from exc


_CATALOG_PATH = pathlib.Path(__file__).resolve().parent / "catalog.yaml"


@functools.lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    """Read + parse catalog.yaml. Cached for the process lifetime."""
    if not _CATALOG_PATH.exists():
        raise FileNotFoundError(f"catalog.yaml missing: {_CATALOG_PATH}")
    with _CATALOG_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("catalog.yaml top-level must be a mapping")
    if "categories" not in data or not isinstance(data["categories"], list):
        raise ValueError("catalog.yaml must have a 'categories' list")
    if len(data["categories"]) < 1:
        raise ValueError("catalog.yaml must have at least 1 category")
    # Sanity-check that each category has the mandatory fields.
    required = {"id", "name", "ira_section", "plain_english_description"}
    for cat in data["categories"]:
        missing = required - set(cat.keys())
        if missing:
            raise ValueError(
                f"catalog category {cat.get('id', '?')} missing: {sorted(missing)}"
            )
    logger.info(
        "Loaded deduction catalog v=%s with %d categories",
        data.get("version", "?"), len(data["categories"]),
    )
    return data


def load_catalog() -> dict[str, Any]:
    """Return a deep copy of the catalog dict — safe to mutate by caller."""
    return copy.deepcopy(_load_raw())


def get_category(category_id: str) -> dict[str, Any] | None:
    """Return one category by id, or None."""
    for cat in _load_raw().get("categories", []):
        if cat.get("id") == category_id:
            return copy.deepcopy(cat)
    return None


def get_category_ids() -> list[str]:
    """All category IDs, in catalog order."""
    return [cat["id"] for cat in _load_raw().get("categories", [])]


def get_caps() -> dict[str, Any]:
    """Statutory caps dict (id -> {type, amount_lkr | percent, rule})."""
    return copy.deepcopy(_load_raw().get("caps", {}))


def reset_cache() -> None:
    """Clear the LRU cache — for tests that swap the catalog file."""
    _load_raw.cache_clear()


# ---------------------------------------------------------------------------
# Test hook: allow tests to point the loader at a fixture catalog.
# ---------------------------------------------------------------------------
def _override_catalog_path(path: pathlib.Path) -> None:  # pragma: no cover
    """ONLY for tests. Replaces the canonical path."""
    global _CATALOG_PATH
    _CATALOG_PATH = path
    reset_cache()
