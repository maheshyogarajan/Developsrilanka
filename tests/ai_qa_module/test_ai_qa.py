"""
Tests for ai_qa + POST /api/qa — Sprint 4 Tier D3 / D1 (2026-05-24).

Three council-mandated cases:
  1. retrieve() returns sensible top-1 for a foreign-income query.
  2. Low-confidence query returns the canned fallback response.
  3. /api/qa returns 200 with the documented structured payload.
"""
import json

import ai_qa


# --------------------------------------------------------------------------- #
# 1. Retrieval — top-1 for a foreign-income query is the relevant FAQ entry
# --------------------------------------------------------------------------- #
def test_retrieve_top1_foreign_income_query():
    """A canonical foreign-income query must surface the 15% rule FAQ
    as the top hit, with score above the confidence threshold."""
    ai_qa._reset_for_tests()
    hits = ai_qa.retrieve("what is the 15% foreign income rule", k=3)

    assert hits, "expected at least one hit for a foreign-income query"
    top = hits[0]
    assert top["score"] >= ai_qa.CONFIDENCE_THRESHOLD, (
        f"top score {top['score']:.3f} below threshold "
        f"{ai_qa.CONFIDENCE_THRESHOLD}"
    )
    matched_q = top["entry"]["q"].lower()
    # Top hit must mention "foreign income" + "15" — that's the question
    # the corpus has explicitly for this rule.
    assert "foreign income" in matched_q
    assert "15" in matched_q


# --------------------------------------------------------------------------- #
# 2. Low-confidence query returns the canned escalation response
# --------------------------------------------------------------------------- #
def test_low_confidence_query_returns_canned_response():
    """A query with no semantic overlap to the corpus must trigger the
    fallback branch (no guessed answer)."""
    ai_qa._reset_for_tests()
    result = ai_qa.answer("xyzzy quux blorp gronk frobnicate")

    assert result["fallback"] is True, "expected fallback=True for gibberish"
    assert result["answer"] == ai_qa.CANNED_LOW_CONFIDENCE_ANSWER
    assert result["sources"] == []
    assert result["matched_question"] is None
    # confidence may be 0.0 (no token overlap at all) — must NOT be > threshold
    assert result["confidence"] < ai_qa.CONFIDENCE_THRESHOLD


# --------------------------------------------------------------------------- #
# 3. /api/qa returns 200 with the structured payload
# --------------------------------------------------------------------------- #
def test_api_qa_returns_structured_payload(client, app):
    """POST /api/qa with a real question -> 200 + documented JSON shape."""
    ai_qa._reset_for_tests()
    resp = client.post(
        "/api/qa",
        data=json.dumps({"query": "What is the 15% foreign income rule?"}),
        content_type="application/json",
        headers={
            "Origin": "http://localhost",
            "User-Agent": "Mozilla/5.0 (Test)",
        },
    )

    assert resp.status_code == 200, (
        f"expected 200, got {resp.status_code} body={resp.data!r}"
    )
    payload = resp.get_json()
    assert isinstance(payload, dict)

    # Required keys per the API contract documented in qa_routes.api_qa
    for key in ("answer", "sources", "matched_question", "confidence",
                "fallback", "alternatives"):
        assert key in payload, f"missing key {key!r} in {payload!r}"

    assert isinstance(payload["answer"], str) and payload["answer"]
    assert isinstance(payload["sources"], list)
    assert isinstance(payload["alternatives"], list)
    assert isinstance(payload["confidence"], (int, float))
    assert payload["fallback"] is False, (
        "a clear foreign-income query should not hit the fallback branch"
    )
    # The matched question must mention foreign income (this is a sanity
    # check that the retrieval is actually wired through the route).
    assert payload["matched_question"], "matched_question should not be None"
    assert "foreign income" in payload["matched_question"].lower()
