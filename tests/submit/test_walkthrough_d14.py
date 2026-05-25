"""tests.submit.test_walkthrough_d14 — Tier-D6 minor: IRA citations in walkthrough.

D14 — B19 step text procedural; IRA citations missing from card body.

Root cause: fiesta.submit.routes.get_walkthrough() called
_walkthrough_step_data() (a hardcoded placeholder list where every
ira_citation is '(annotation pending CEO capture)') instead of
_load_walkthrough_steps() (which loads the well-formed
fiesta_ird_walkthrough/annotations.yaml — 12 steps with real
IRA §6 / §51 / §92 / §120 citations).

Fix: switch get_walkthrough() to _load_walkthrough_steps(); also bridge
the YAML schema (`step`, `screenshot_url`) and template schema (`step`,
`image_filename`) so the loader's output is template-compatible.

These tests exercise the loader directly + verify the rendered template
contains the real citations.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


_TEMPLATE_PATH = _ROOT / "templates" / "submit" / "walkthrough.html"
_YAML_PATH = _ROOT / "fiesta_ird_walkthrough" / "annotations.yaml"


# --------------------------------------------------------------------------- #
# 1) Loader returns steps with real IRA citations                              #
# --------------------------------------------------------------------------- #


def test_d14_loader_returns_real_ira_citations_from_yaml():
    """_load_walkthrough_steps must produce steps whose ira_citation is the
    real §-prefixed text from annotations.yaml, NOT the
    '(annotation pending)' placeholder.
    """
    from fiesta.submit.routes import _load_walkthrough_steps

    assert _YAML_PATH.exists(), (
        f"annotations.yaml must exist at {_YAML_PATH} for this test to be "
        "meaningful — if missing, D14 was not actually fixed"
    )

    steps = _load_walkthrough_steps()
    assert len(steps) == 12, f"Expected 12 steps, got {len(steps)}"

    # Every step's ira_citation must be non-empty and contain a "§" or
    # an IRA reference. Placeholders are explicitly rejected.
    for s in steps:
        cit = s.get("ira_citation") or ""
        assert cit, f"Step {s.get('step')} has empty ira_citation"
        assert "annotation pending" not in cit.lower(), (
            f"Step {s.get('step')} still shows placeholder: {cit!r}"
        )
        # Real citations contain a § sign or "IRA"
        assert "§" in cit or "IRA" in cit.upper(), (
            f"Step {s.get('step')} ira_citation {cit!r} does not look like "
            "a real IRA section reference"
        )


def test_d14_loader_emits_template_compatible_keys():
    """The loader must emit both `step` (number — for template) and
    `image_filename` (filename — derived from screenshot_url path)."""
    from fiesta.submit.routes import _load_walkthrough_steps

    steps = _load_walkthrough_steps()
    assert steps, "Loader returned no steps"

    for s in steps:
        # Template uses `step.step` for the badge number
        assert "step" in s, f"Step dict missing `step` key: {s}"
        assert isinstance(s["step"], int) or str(s["step"]).isdigit()

        # Template uses `step.image_filename` for the screenshot path
        assert "image_filename" in s, (
            f"Step dict missing `image_filename` key (template requires it): {s}"
        )

        # Backward-compat: step_number + screenshot_url must remain present
        assert "step_number" in s, "Backward-compat key step_number missing"
        assert "screenshot_url" in s, "Backward-compat key screenshot_url missing"


# --------------------------------------------------------------------------- #
# 2) Route uses the YAML loader, not the placeholder list                      #
# --------------------------------------------------------------------------- #


def test_d14_get_walkthrough_uses_yaml_loader():
    """The get_walkthrough() view function must call _load_walkthrough_steps
    rather than (only) the hardcoded _walkthrough_step_data placeholder list.
    """
    import inspect
    from fiesta.submit import routes as submit_routes

    src = inspect.getsource(submit_routes.get_walkthrough)
    assert "_load_walkthrough_steps" in src, (
        "get_walkthrough() must invoke _load_walkthrough_steps to surface "
        "real IRA citations (D14)"
    )


# --------------------------------------------------------------------------- #
# 3) Rendered template surfaces the IRA citations                              #
# --------------------------------------------------------------------------- #


def test_d14_template_renders_ira_citation_from_loader_output():
    """Render walkthrough.html with the loader's output and confirm each
    step's IRA citation text is present in the rendered HTML."""
    from jinja2 import Environment, DictLoader
    from fiesta.submit.routes import _load_walkthrough_steps

    raw = _TEMPLATE_PATH.read_text(encoding="utf-8")
    no_extends = re.sub(r"\{%\s*extends\s+'empty_layout\.html'\s*%\}", "", raw, count=1)
    base = "{% block title %}{% endblock %}{% block content %}{% endblock %}"

    env = Environment(
        loader=DictLoader({"base.html": base, "walkthrough.html": no_extends}),
        autoescape=True,
    )

    def _url_for(name, **kwargs):
        return f"/{name}/" + "/".join(str(v) for v in kwargs.values())

    def _csrf_token():
        return "test-csrf"

    env.globals["url_for"] = _url_for
    env.globals["csrf_token"] = _csrf_token

    steps = _load_walkthrough_steps()
    html = env.get_template("walkthrough.html").render(
        submission=None,
        steps=steps,
        tax_year="25/26",
    )

    # Each step's citation must appear verbatim in the body. Spot-check 3
    # distinct citations from the YAML so a partial fix gets caught.
    expected_phrases = [
        "§92 IRA",   # step 1 (login)
        "§6(1) IRA", # step 6 (deductions)
        "§51 IRA",   # step 7 (personal relief)
    ]
    for phrase in expected_phrases:
        assert phrase in html, (
            f"Rendered walkthrough HTML must contain {phrase!r} from "
            "annotations.yaml (D14)"
        )
