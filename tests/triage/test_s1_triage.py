"""S1 Triage test suite — 13 cases covering the catalog, validators, route auth,
question rendering, answer persistence, session-state branching, validation
rejection, and redirect-after-final.

Run: pytest tests/triage/test_s1_triage.py -v
"""

from __future__ import annotations

import pytest
from werkzeug.datastructures import MultiDict

from tests.triage.conftest import login_as


# ---------------------------------------------------------------------------
# 1. Catalog / pure-function tests (no Flask)
# ---------------------------------------------------------------------------


def test_01_catalog_has_three_questions_in_order():
    """The S1 spec is 3 questions: earning_source, earning_vehicle, filing_history."""
    from fiesta.triage import QUESTIONS, QUESTION_ORDER

    assert len(QUESTIONS) == 3
    assert QUESTION_ORDER == ["earning_source", "earning_vehicle", "filing_history"]

    # Each question has the structural fields the template + route rely on.
    for q in QUESTIONS:
        assert "id" in q and "prompt" in q and "options" in q and "kind" in q
        assert q["kind"] in {"single", "multi"}
        assert len(q["options"]) >= 2  # never a degenerate 1-option question
        for opt in q["options"]:
            assert "id" in opt and "label" in opt
            # Ids are slug-like; spaces would be a smell
            assert " " not in opt["id"]


def test_02_validator_accepts_good_single_answer():
    """Single-select: a valid option id is accepted and returned as a string."""
    from fiesta.triage.validators import validate_answer

    assert validate_answer("earning_source", "pure_foreign") == "pure_foreign"
    assert validate_answer("earning_source", "mixed") == "mixed"
    assert validate_answer("filing_history", "used_lankatax") == "used_lankatax"

    # Whitespace tolerant
    assert validate_answer("earning_source", "  pure_local  ") == "pure_local"

    # Single value wrapped in a list (browser radio quirk) is unwrapped
    assert validate_answer("earning_source", ["pure_foreign"]) == "pure_foreign"


def test_03_validator_accepts_good_multi_answer():
    """Multi-select: list of valid option ids accepted, deduped, preserves order."""
    from fiesta.triage.validators import validate_answer

    out = validate_answer(
        "earning_vehicle", ["solo_freelancer", "property"]
    )
    assert out == ["solo_freelancer", "property"]

    # Single string is upgraded to a one-element list
    out2 = validate_answer("earning_vehicle", "solo_freelancer")
    assert out2 == ["solo_freelancer"]

    # Dedup with preserved order
    out3 = validate_answer(
        "earning_vehicle",
        ["studio_with_subcontractors", "solo_freelancer", "studio_with_subcontractors"],
    )
    assert out3 == ["studio_with_subcontractors", "solo_freelancer"]


def test_04_validator_rejects_unknown_option():
    """Submitting an option id that isn't in the catalog must raise."""
    from fiesta.triage.validators import validate_answer, TriageValidationError

    with pytest.raises(TriageValidationError):
        validate_answer("earning_source", "freelance_in_singapore")  # not in catalog

    with pytest.raises(TriageValidationError):
        validate_answer("earning_vehicle", ["solo_freelancer", "made_up_value"])

    with pytest.raises(TriageValidationError):
        validate_answer("filing_history", "")

    # Unknown question id
    with pytest.raises(TriageValidationError):
        validate_answer("totally_not_a_qid", "anything")


def test_05_validator_rejects_wrong_shape():
    """Multi expects list-like; single expects scalar. Reject crossed wires."""
    from fiesta.triage.validators import validate_answer, TriageValidationError

    # Multi-select must not be empty
    with pytest.raises(TriageValidationError):
        validate_answer("earning_vehicle", [])

    # Single-select with multi values
    with pytest.raises(TriageValidationError):
        validate_answer("earning_source", ["pure_foreign", "mixed"])

    # Non-string / non-list garbage
    with pytest.raises(TriageValidationError):
        validate_answer("earning_source", 42)
    with pytest.raises(TriageValidationError):
        validate_answer("earning_vehicle", {"not": "a list"})


def test_06_full_payload_validator_requires_all_three():
    """validate_full_answers must reject any missing question."""
    from fiesta.triage.validators import validate_full_answers, TriageValidationError

    # Complete payload — passes and is returned cleaned
    full = {
        "earning_source": "mixed",
        "earning_vehicle": ["solo_freelancer", "employee_with_side"],
        "filing_history": "filed_manually_with_help",
    }
    cleaned = validate_full_answers(full)
    assert cleaned["earning_source"] == "mixed"
    assert cleaned["earning_vehicle"] == ["solo_freelancer", "employee_with_side"]
    assert cleaned["filing_history"] == "filed_manually_with_help"

    # Missing one
    with pytest.raises(TriageValidationError):
        validate_full_answers(
            {"earning_source": "mixed", "earning_vehicle": ["solo_freelancer"]}
        )

    # Not a dict at all
    with pytest.raises(TriageValidationError):
        validate_full_answers("nope")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Route auth — must be logged in
# ---------------------------------------------------------------------------


def test_07_anonymous_get_redirects_to_login(client):
    """Anonymous GET /fie/triage redirects (Flask-Login default = 302 to /login)."""
    resp = client.get("/fie/triage", follow_redirects=False)
    assert resp.status_code in (301, 302)
    # Flask-Login bounces to LOGIN_VIEW; the path should mention login
    location = resp.headers.get("Location", "")
    assert "login" in location.lower() or "signup" in location.lower()


def test_08_anonymous_post_redirects_to_login(client):
    """Anonymous POST /fie/triage same treatment — never accepts a write."""
    resp = client.post(
        "/fie/triage",
        data={"question_id": "earning_source", "answer_earning_source": "mixed"},
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302)
    location = resp.headers.get("Location", "")
    assert "login" in location.lower() or "signup" in location.lower()


# ---------------------------------------------------------------------------
# 3. Question rendering — logged-in GETs render the right question
# ---------------------------------------------------------------------------


def test_09_first_get_renders_q1(client, user_a, app):
    """Logged-in user with no triage state sees question 1 (earning_source)."""
    login_as(client, user_a)

    resp = client.get("/fie/triage")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The first question's prompt + at least one option label are on the page
    from fiesta.triage import QUESTIONS_BY_ID
    q1 = QUESTIONS_BY_ID["earning_source"]
    assert q1["prompt"] in body
    # Progress label "1 of 3"
    assert "1 of 3" in body
    # The form posts to the submit endpoint
    assert "/fie/triage" in body
    # CSRF hidden input is wired
    assert 'name="csrf_token"' in body or "csrf_token" in body
    # Hidden question_id
    assert 'name="question_id"' in body
    assert 'value="earning_source"' in body


# ---------------------------------------------------------------------------
# 4. Answer persistence + session-state branching
# ---------------------------------------------------------------------------


def test_10_session_advances_question_by_question(client, user_a, app, db_session):
    """Submitting Q1 advances to Q2; submitting Q2 advances to Q3; submitting Q3
    finalises and persists to User.triage_answers (and the session is wiped)."""
    login_as(client, user_a)

    from fiesta.triage.routes import SESSION_KEY

    # Q1 -> Q2
    resp = client.post(
        "/fie/triage",
        data={"question_id": "earning_source", "answer_earning_source": "mixed"},
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        st = sess.get(SESSION_KEY) or {}
        assert st.get("answers", {}).get("earning_source") == "mixed"
        # _resolve_current walks ahead to the next unanswered
        assert "earning_source" in st.get("answers", {})

    # Q2 (multi) -> Q3
    md = MultiDict()
    md.add("question_id", "earning_vehicle")
    md.add("answer_earning_vehicle", "solo_freelancer")
    md.add("answer_earning_vehicle", "property")
    resp2 = client.post(
        "/fie/triage",
        data=md,
        follow_redirects=False,
    )
    assert resp2.status_code in (301, 302)
    with client.session_transaction() as sess:
        st = sess.get(SESSION_KEY) or {}
        assert st["answers"]["earning_vehicle"] == ["solo_freelancer", "property"]

    # Q3 -> finalise
    resp3 = client.post(
        "/fie/triage",
        data={
            "question_id": "filing_history",
            "answer_filing_history": "used_lankatax",
        },
        follow_redirects=False,
    )
    assert resp3.status_code in (301, 302)

    # Session triage state is gone
    with client.session_transaction() as sess:
        assert SESSION_KEY not in sess

    # User row has persisted answers
    from models import User
    fresh = db_session.get(User, user_a.id)
    assert fresh is not None
    ans = fresh.triage_answers
    assert isinstance(ans, dict)
    assert ans["earning_source"] == "mixed"
    assert ans["earning_vehicle"] == ["solo_freelancer", "property"]
    assert ans["filing_history"] == "used_lankatax"
    assert "completed_at" in ans

    # Clean up the persisted answers so the user_a teardown finds a fresh row.
    fresh.triage_answers = None
    db_session.commit()


# ---------------------------------------------------------------------------
# 5. Validation rejection at the route boundary
# ---------------------------------------------------------------------------


def test_11_route_rejects_bad_answer(client, user_a, app, db_session):
    """POSTing a garbage option id flashes an error and does NOT persist."""
    login_as(client, user_a)

    resp = client.post(
        "/fie/triage",
        data={
            "question_id": "earning_source",
            "answer_earning_source": "totally_not_an_option",
        },
        follow_redirects=False,
    )
    # Redirect back to the form, not finalise
    assert resp.status_code in (301, 302)
    assert "/fie/triage" in resp.headers.get("Location", "")

    # Nothing got written to the user
    from models import User
    fresh = db_session.get(User, user_a.id)
    assert fresh is not None
    assert fresh.triage_answers in (None, {}, [])

    # Session also has no answer recorded for that q
    from fiesta.triage.routes import SESSION_KEY
    with client.session_transaction() as sess:
        st = sess.get(SESSION_KEY) or {}
        assert "earning_source" not in st.get("answers", {})


# ---------------------------------------------------------------------------
# 6. Redirect after final answer
# ---------------------------------------------------------------------------


def test_12_finishing_redirects_to_safe_next_or_dashboard(client, user_a, app, db_session):
    """After the final question, redirect honours ?next if it's a relative path,
    otherwise falls back to '/' or the dashboard. External URLs are NOT honoured
    (open-redirect prevention)."""
    login_as(client, user_a)

    # Pre-seed the first 2 answers via the route so we hit the finaliser on Q3.
    client.post(
        "/fie/triage",
        data={"question_id": "earning_source", "answer_earning_source": "pure_foreign"},
    )
    md = MultiDict()
    md.add("question_id", "earning_vehicle")
    md.add("answer_earning_vehicle", "solo_freelancer")
    client.post("/fie/triage", data=md)

    # Case A: relative next is honoured
    resp_safe = client.post(
        "/fie/triage",
        data={
            "question_id": "filing_history",
            "answer_filing_history": "never_filed",
            "next": "/some/internal/path",
        },
        follow_redirects=False,
    )
    assert resp_safe.status_code in (301, 302)
    assert resp_safe.headers.get("Location", "").endswith("/some/internal/path")

    # Reset for case B: ensure persistent answers are wiped so the next round
    # of POSTs actually finalises again (a user with completed_at gets bounced
    # to dashboard on the GET, but POSTs still need a clean slate).
    from models import User
    fresh = db_session.get(User, user_a.id)
    fresh.triage_answers = None
    db_session.commit()

    # Re-seed
    client.post(
        "/fie/triage",
        data={"question_id": "earning_source", "answer_earning_source": "pure_local"},
    )
    md2 = MultiDict()
    md2.add("question_id", "earning_vehicle")
    md2.add("answer_earning_vehicle", "solo_freelancer")
    client.post("/fie/triage", data=md2)

    # Case B: external next URL is REJECTED — must redirect to a safe default.
    resp_evil = client.post(
        "/fie/triage",
        data={
            "question_id": "filing_history",
            "answer_filing_history": "never_filed",
            "next": "https://evil.example.com/steal",
        },
        follow_redirects=False,
    )
    assert resp_evil.status_code in (301, 302)
    location = resp_evil.headers.get("Location", "")
    assert "evil.example.com" not in location

    # Clean up
    fresh = db_session.get(User, user_a.id)
    fresh.triage_answers = None
    db_session.commit()


# ---------------------------------------------------------------------------
# 7. Already-complete users skip the flow
# ---------------------------------------------------------------------------


def test_13_completed_users_skip_to_dashboard(client, user_a, app, db_session):
    """A GET /fie/triage from a user whose triage_answers already has
    completed_at should redirect away (no infinite loop)."""
    # Mark user as already-completed
    user_a.triage_answers = {
        "earning_source": "mixed",
        "earning_vehicle": ["solo_freelancer"],
        "filing_history": "used_lankatax",
        "completed_at": "2026-05-19T10:00:00Z",
    }
    db_session.commit()

    login_as(client, user_a)
    resp = client.get("/fie/triage", follow_redirects=False)
    assert resp.status_code in (301, 302)
    # And it does NOT 302 back to itself
    location = resp.headers.get("Location", "")
    assert "/fie/triage" not in location

    # Clean up
    user_a.triage_answers = None
    db_session.commit()
