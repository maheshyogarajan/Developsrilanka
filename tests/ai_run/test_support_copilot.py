"""
AI Support Copilot tests — Wave 3.2 (2026-05-18).

Validates the deterministic-citation gate + escalation logic + persistence
contract. Council #2 hard constraint: no answer ships without a cited
source node; low-confidence questions escalate, never bluff.

The test set:

  1. _load_kb_finds_all_files       — KB index populates with >5 entries
  2. _retrieve_relevant_kb_returns_chunks — token overlap returns ≥1 hit
  3. should_escalate_keyword_audit  — 'audit' → escalate
  4. should_escalate_low_confidence — confidence 0.5 → escalate
  5. answer_question_for_low_volume_user_emits_event
                                    — full flow creates a SupportTicket + emits
                                      support_message_received + support_answer_drafted
  6. admin_support_queue_requires_admin — non-admin → 403
  7. (bonus) should_escalate_no_citations — empty citations list → escalate
  8. (bonus) answer_question_keyword_skips_gemini — early-escalation path,
                                                     no Gemini called

Fixtures (app, db_session, user_a, user_b, admin_user, login_as) come from
tests/ai_run/conftest.py.

Gemini cost guard: tests monkeypatch _call_gemini_for_answer so no real tokens
are spent. Math + persistence + events + escalation are exercised against the
live test DB.

DB hygiene: an autouse fixture sweeps SupportTicket + Event rows for the
pytest user accounts before AND after each test so a failed assertion
doesn't leave FK rows that block the user fixture's DELETE FROM user.
"""
from datetime import datetime
from decimal import Decimal

import pytest


# --------------------------------------------------------------------------- #
# Helper — register the support blueprint defensively (mirrors ops_sentinel test)
# --------------------------------------------------------------------------- #

def _ensure_support_routes_registered(app):
    """Idempotent. main.py wires the blueprint in production; tests register
    defensively because the ai_run conftest's `app` fixture doesn't know about
    support_routes (Wave 3.2 contract: 'DO NOT touch main.py').
    """
    if "support" not in app.blueprints:
        from support_routes import register_routes
        register_routes(app)


# --------------------------------------------------------------------------- #
# Hygiene — clean any leftover pytest SupportTicket / Event rows
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _purge_support_rows(app, db_session):
    """Before AND after each test, sweep SupportTicket + support_* Event rows
    belonging to the pytest user accounts.
    """
    def _sweep():
        from sqlalchemy import text as _t
        try:
            with app.app_context():
                ids = [
                    r[0] for r in db_session.execute(
                        _t("""SELECT id FROM "user"
                              WHERE email LIKE 'pytest_%@fiesta.local'""")
                    ).fetchall()
                ]
                if not ids:
                    return
                for tbl in ("support_tickets",):
                    try:
                        db_session.execute(
                            _t(f"DELETE FROM {tbl} WHERE user_id = ANY(:ids)"),
                            {"ids": ids},
                        )
                    except Exception:
                        db_session.rollback()
                try:
                    db_session.execute(
                        _t("""DELETE FROM events
                              WHERE user_id = ANY(:ids)
                                AND event_type IN ('support_message_received',
                                                   'support_answer_drafted',
                                                   'support_escalated',
                                                   'support_csat_submitted',
                                                   'support_resolved')"""),
                        {"ids": ids},
                    )
                except Exception:
                    db_session.rollback()
                db_session.commit()
        except Exception:
            try:
                db_session.rollback()
            except Exception:
                pass

    _sweep()
    yield
    _sweep()


# --------------------------------------------------------------------------- #
# 1. _load_kb populates the cache from disk
# --------------------------------------------------------------------------- #

def test_load_kb_finds_all_files(app):
    """The KB index must populate from support_kb/*.md with more than 5 entries.
    Spec calls for 6-10 KB files; we ship 10. Lower-bound assertion gives us
    headroom to add or remove a doc without rewriting the test."""
    from support_copilot import _load_kb

    with app.app_context():
        kb = _load_kb()

    assert isinstance(kb, dict), f"_load_kb must return a dict, got {type(kb)}"
    assert len(kb) > 5, f"Expected >5 KB entries, got {len(kb)} (KB files missing?)"

    # Every entry must have the required shape.
    for kb_id, entry in kb.items():
        assert isinstance(entry, dict), f"KB {kb_id!r} value must be dict"
        for key in ("id", "topic", "source_url", "last_verified", "content", "filename"):
            assert key in entry, f"KB {kb_id!r} missing key {key!r}"
        assert entry["id"] == kb_id, (
            f"KB id mismatch: cache key {kb_id!r} != frontmatter id {entry['id']!r}"
        )
        assert entry["content"].strip(), f"KB {kb_id!r} has empty body"


# --------------------------------------------------------------------------- #
# 2. Retrieval returns at least one chunk on an overlap query
# --------------------------------------------------------------------------- #

def test_retrieve_relevant_kb_returns_chunks(app, db_session, user_a):
    """A question containing 'cbsl' and 'rate' must surface at least one KB
    chunk (the cbsl_middle_rate_rule doc, at minimum)."""
    from support_copilot import _retrieve_relevant_kb, _load_kb

    with app.app_context():
        # Ensure KB cache is populated for this test process.
        _load_kb()
        chunks = _retrieve_relevant_kb(
            "What CBSL rate should I use for a USD remittance?",
            user_id=user_a.id,
            max_chunks=5,
        )

    assert isinstance(chunks, list), f"retrieve must return a list, got {type(chunks)}"
    assert len(chunks) >= 1, (
        f"Token-overlap query should match ≥1 KB doc; got {len(chunks)}. "
        f"KB cache may be empty or the scoring is broken."
    )
    # Every chunk has the right shape.
    for c in chunks:
        for key in ("id", "topic", "content", "score"):
            assert key in c, f"chunk missing key {key!r}: {c.keys()}"
        assert c["score"] > 0, f"chunk score should be > 0, got {c['score']}"

    # The CBSL doc should rank top (id-token + content-token + topic match).
    top = chunks[0]
    assert "cbsl" in top["id"].lower() or "fx" in top["topic"].lower(), (
        f"Expected a CBSL/FX doc to rank top for a CBSL question, got id={top['id']!r}"
    )


# --------------------------------------------------------------------------- #
# 3. Escalation keyword 'audit' → escalate
# --------------------------------------------------------------------------- #

def test_escalation_keyword_audit_triggers(app, user_a):
    """A question containing 'audit' must escalate regardless of confidence."""
    from support_copilot import should_escalate

    # Even with a perfect synthetic answer object, the keyword wins.
    fake_answer = {
        "answer": "Here's a confident answer.",
        "citations": ["pn_it_2025_01_overview"],
        "confidence": 0.95,
        "reason": "",
    }
    with app.app_context():
        escalate, reason = should_escalate(
            "IRD has sent me an audit notice — what do I do?",
            answer_obj=fake_answer,
            user_id=user_a.id,
        )
    assert escalate is True, "must escalate when 'audit' is in the question"
    assert reason.startswith("keyword:"), (
        f"reason should be 'keyword:audit', got {reason!r}"
    )
    assert "audit" in reason.lower(), (
        f"reason must name the keyword that triggered, got {reason!r}"
    )


# --------------------------------------------------------------------------- #
# 4. Confidence < 0.7 → escalate
# --------------------------------------------------------------------------- #

def test_escalation_low_confidence_triggers(app, user_a):
    """Council #2: confidence < 0.7 mandates escalation even with citations."""
    from support_copilot import should_escalate

    fake_answer = {
        "answer": "I think this is right but not sure.",
        "citations": ["pn_it_2025_01_overview"],
        "confidence": 0.5,
        "reason": "uncertain",
    }
    with app.app_context():
        escalate, reason = should_escalate(
            "Some routine pricing question with no flagged keywords.",
            answer_obj=fake_answer,
            user_id=user_a.id,
        )
    assert escalate is True, "confidence 0.5 (< 0.7 threshold) must escalate"
    assert reason.startswith("low_confidence"), (
        f"reason should start with 'low_confidence', got {reason!r}"
    )


# --------------------------------------------------------------------------- #
# 4b. No citations → escalate (council #2 deterministic-source contract)
# --------------------------------------------------------------------------- #

def test_escalation_no_citations_triggers(app, user_a):
    """Empty citations list = no deterministic source node = MUST escalate.
    This is the council #2 anti-hallucination guard at the gate boundary."""
    from support_copilot import should_escalate

    fake_answer = {
        "answer": "Confident but uncited.",
        "citations": [],
        "confidence": 0.95,
        "reason": "",
    }
    with app.app_context():
        escalate, reason = should_escalate(
            "A perfectly safe pricing question.",
            answer_obj=fake_answer,
            user_id=user_a.id,
        )
    assert escalate is True, (
        "council #2: an answer with no citations must escalate, not auto-send"
    )
    assert reason == "no_citations", f"expected reason 'no_citations', got {reason!r}"


# --------------------------------------------------------------------------- #
# 5. Full flow — answer_question persists ticket + emits events
# --------------------------------------------------------------------------- #

def test_answer_question_for_low_volume_user_emits_event(app, db_session, user_a, monkeypatch):
    """End-to-end flow with Gemini monkeypatched. Validates:

      - SupportTicket is persisted with the right fields
      - support_message_received event is emitted
      - support_answer_drafted event is emitted
      - The returned CopilotAnswer matches the persisted row
    """
    from support_copilot import answer_question, CopilotAnswer
    import support_copilot

    # Monkeypatch Gemini to a fixed high-confidence cited answer.
    def _fake_gemini(question, kb_chunks, user_context):
        return {
            "answer": "FIESTA Pro is LKR 1,500/month or LKR 15,000/year. The Family tier is LKR 3,500/month.",
            "citations": ["fiesta_pricing_tiers"],
            "confidence": 0.95,
            "reason": "",
        }
    monkeypatch.setattr(support_copilot, "_call_gemini_for_answer", _fake_gemini)

    # Use a request context so emit() can lift session_id/ip/UA.
    with app.test_request_context("/"):
        ticket_id, answer = answer_question(
            user_id=user_a.id,
            question_text="How much does FIESTA Pro cost?",
        )

    # Return contract
    assert ticket_id > 0, f"answer_question must return a valid ticket id, got {ticket_id}"
    assert answer is not None, "auto-answer path must return a CopilotAnswer (not None)"
    assert isinstance(answer, CopilotAnswer)
    assert answer.ticket_id == ticket_id
    assert "Pro" in answer.answer or "pricing" in answer.answer.lower()
    assert "fiesta_pricing_tiers" in answer.citations
    assert 0.0 <= answer.confidence <= 1.0
    assert abs(answer.confidence - 0.95) < 1e-6

    # Persistence
    from support_copilot_models import SupportTicket
    row = SupportTicket.query.get(ticket_id)
    assert row is not None, "SupportTicket row must be persisted"
    assert row.user_id == user_a.id
    assert row.escalated_to_human is False, "high-confidence cited answer must NOT escalate"
    assert row.ai_answer == answer.answer
    assert row.citations == answer.citations
    # citations was stored as JSON; the round-trip should be the same list
    assert isinstance(row.citations, list)
    assert "fiesta_pricing_tiers" in row.citations
    # NUMERIC(3,2) round-trips as Decimal
    assert Decimal(str(row.confidence)) == Decimal("0.95"), (
        f"persisted confidence drift: {row.confidence!r}"
    )
    assert row.resolved_at is not None, "auto-answers are self-resolving (resolved_at set)"
    assert row.csat_rating is None, "CSAT only set after the user rates"

    # Event emissions
    from event_models import Event
    msg_evts = (
        Event.query
             .filter(Event.user_id == user_a.id,
                     Event.event_type == "support_message_received")
             .all()
    )
    assert len(msg_evts) >= 1, "expected at least 1 support_message_received event"
    drafted_evts = (
        Event.query
             .filter(Event.user_id == user_a.id,
                     Event.event_type == "support_answer_drafted")
             .all()
    )
    assert len(drafted_evts) >= 1, "expected at least 1 support_answer_drafted event"

    # Payload contract on the drafted event
    drafted_payload = drafted_evts[-1].payload or {}
    assert drafted_payload.get("ticket_id") == ticket_id
    assert abs(float(drafted_payload.get("confidence") or 0) - 0.95) < 1e-6
    assert int(drafted_payload.get("citation_count") or 0) == 1


# --------------------------------------------------------------------------- #
# 5b. Early-escalation skips Gemini entirely (cost + safety)
# --------------------------------------------------------------------------- #

def test_answer_question_keyword_skips_gemini(app, db_session, user_a, monkeypatch):
    """A question containing 'audit' must escalate BEFORE Gemini is called.
    Validates the early-escalation optimisation (saves Gemini cost +
    guarantees no auto-answer for red-flag keywords)."""
    import support_copilot
    from support_copilot import answer_question

    gemini_call_count = {"n": 0}

    def _fake_gemini_counted(question, kb_chunks, user_context):
        gemini_call_count["n"] += 1
        # Even if called, return a perfect answer — but the keyword gate
        # should escalate regardless and the count assertion proves we
        # never reached this function.
        return {
            "answer": "should not surface",
            "citations": ["pn_it_2025_01_overview"],
            "confidence": 0.95,
            "reason": "",
        }
    monkeypatch.setattr(support_copilot, "_call_gemini_for_answer", _fake_gemini_counted)

    with app.test_request_context("/"):
        ticket_id, answer = answer_question(
            user_id=user_a.id,
            question_text="What do I do about this IRD audit notice?",
        )

    assert gemini_call_count["n"] == 0, (
        f"Early escalation must skip Gemini call; got {gemini_call_count['n']} calls"
    )
    assert answer is None, "escalated path returns CopilotAnswer=None"
    assert ticket_id > 0

    from support_copilot_models import SupportTicket
    row = SupportTicket.query.get(ticket_id)
    assert row.escalated_to_human is True
    assert row.ai_answer is None, "escalated-before-Gemini ticket should have no AI answer"
    assert row.escalation_reason and row.escalation_reason.startswith("keyword:"), (
        f"reason should be 'keyword:audit', got {row.escalation_reason!r}"
    )


# --------------------------------------------------------------------------- #
# 6. Admin queue gates non-admin → 403
# --------------------------------------------------------------------------- #

def test_admin_support_queue_requires_admin(app, client, db_session, user_a, user_b):
    """user_b (role='user') must NOT see /admin/support/queue.

    Admin-only routes in this codebase use inline abort(403) (not redirect-to-
    index) — matches customer_brain_routes._require_admin and the spec.
    """
    _ensure_support_routes_registered(app)

    # Sanity: ensure user_b is NOT an admin
    user_b.role = "user"
    db_session.commit()

    from tests.ai_run.conftest import login_as
    login_as(client, user_b)
    resp = client.get("/admin/support/queue")
    assert resp.status_code == 403, (
        f"Non-admin must get 403 on /admin/support/queue, got {resp.status_code}. "
        f"(user_b.role={user_b.role!r})"
    )
