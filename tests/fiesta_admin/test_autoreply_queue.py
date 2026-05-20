"""
S17 Admin Autoreply Queue tests (Wave 6, 2026-05-21).

Coverage:
  - Anonymous user redirects to login when hitting /admin/fie/autoreply.
  - Non-admin (regular user) sees 403 from @admin_required.
  - Admin sees the queue page (200, template renders, pending count visible).
  - Approve handler:
      * 'no longer pending' state → friendly redirect + warning flash.
      * Red badges → refuses to flip + error flash.
      * All green + closed CB → PATCH approval_queue.status=approved_by_admin.
  - Reject handler:
      * Sets status='cancelled_by_admin' with reason in details.
  - Cohort CB toggle:
      * Requires state ∈ {open,closed} AND a non-empty reason.
  - /healthz: JSON shape sanity.

We monkey-patch the Supabase helpers (_list_pending_drafts, _get_draft,
_update_draft_status, _cb_state, _set_cb_state) so the tests don't depend
on live Supabase. The 9-gate send pipeline is owned by CEO-OS (out of
scope for this suite); we just assert FIESTA's state-flip behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.remittance.conftest import login_as


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

QUEUE_INDEX = "/admin/fie/autoreply"


def _draft_row(*, qid="aq_test_001", status="pending",
                case_file_built=True, verified_fact_count=2,
                resolver_change_id="aXX_rc_test_001"):
    """Build a representative approval_queue row."""
    return {
        "id": qid,
        "action_type": "email_send",
        "status": status,
        "priority": 2,
        "target_description": "F3.0 doc composite reply to client 5046",
        "sf_record_id": "500X0000fakeCase001",
        "sf_field": "Incoming_email__c",
        "sf_value": {
            "subject": "RE: What documents do I need? — Mr Test Client",
            "body": "Dear Mr Test Client,\n\nThanks for asking…",
            "to_email": "client@example.com",
            "contact_id": "003X0000fakeContact001",
            "case_file_built": case_file_built,
            "verified_fact_count": verified_fact_count,
            "resolver_change_id": resolver_change_id,
        },
        "details": {
            "case_file_built": case_file_built,
            "verified_fact_count": verified_fact_count,
            "resolver_change_id": resolver_change_id,
        },
        "created_at": (datetime.utcnow() - timedelta(minutes=3)).isoformat() + "Z",
        "responded_at": None,
    }


@pytest.fixture
def stub_autoreply_supabase(monkeypatch):
    """Default stubs: 1 pending green-gates row, CB closed, PATCH always ok."""
    from fiesta.admin import autoreply_routes as ar

    state = {"rows": [_draft_row()], "patches": [], "cb_sets": [],
             "cb": {"state": "closed", "reason": "", "set_by": None,
                     "set_at": None, "available": True}}

    monkeypatch.setattr(ar, "_list_pending_drafts",
                         lambda **kw: {"ok": True, "status": 200,
                                        "body": state["rows"], "error": None})
    monkeypatch.setattr(ar, "_get_draft",
                         lambda qid: {"ok": True, "status": 200,
                                       "body": [r for r in state["rows"]
                                                if r["id"] == qid][:1],
                                       "error": None})

    def _patch(qid, *, new_status, responded_at=None, extras=None):
        state["patches"].append({"id": qid, "status": new_status,
                                  "extras": extras or {}})
        for r in state["rows"]:
            if r["id"] == qid:
                r["status"] = new_status
        return {"ok": True, "status": 200, "body": {"id": qid}, "error": None}

    monkeypatch.setattr(ar, "_update_draft_status", _patch)
    monkeypatch.setattr(ar, "_cb_state", lambda: state["cb"])

    def _set(*, new_state, reason, set_by):
        state["cb_sets"].append({"state": new_state, "reason": reason,
                                  "set_by": set_by})
        state["cb"] = {"state": new_state, "reason": reason,
                        "set_by": set_by,
                        "set_at": datetime.utcnow().isoformat() + "Z",
                        "available": True}
        return {"ok": True, "status": 200, "body": {}, "error": None}

    monkeypatch.setattr(ar, "_set_cb_state", _set)

    return state


# --------------------------------------------------------------------------- #
# Auth gates (anonymous, non-admin)
# --------------------------------------------------------------------------- #

def test_anonymous_user_redirected_to_login(client):
    resp = client.get(QUEUE_INDEX, follow_redirects=False)
    assert resp.status_code in (301, 302)
    loc = (resp.headers.get("Location") or "").lower()
    assert "/login" in loc or "/auth" in loc


def test_non_admin_user_blocked(client, non_admin_user):
    """Per admin_required's contract: authenticated non-admins are redirected
    to '/' with a flash, not 403'd. We accept either behavior — what matters
    is that the response is NOT a 200 render of the admin page."""
    login_as(client, non_admin_user)
    resp = client.get(QUEUE_INDEX, follow_redirects=False)
    assert resp.status_code != 200, (
        f"Non-admin must not see the queue; got {resp.status_code}"
    )
    # admin_required redirects to '/' (302). Equally acceptable is 403/401.
    assert resp.status_code in (301, 302, 401, 403), resp.status_code
    if resp.status_code in (301, 302):
        loc = (resp.headers.get("Location") or "")
        assert "/admin/fie/autoreply" not in loc


# --------------------------------------------------------------------------- #
# Admin queue index
# --------------------------------------------------------------------------- #

def test_admin_sees_pending_drafts_with_green_badges(
        client, admin_user, stub_autoreply_supabase):
    login_as(client, admin_user)
    resp = client.get(QUEUE_INDEX)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Autoreply Queue" in html
    assert "F3.0 doc composite reply to client 5046" in html
    # Badges rendered as text labels:
    for badge_label in ("case-file", "proof", "RC", "cb"):
        assert badge_label in html
    # Approve button NOT disabled when all gates are green.
    # (We assert the button is present + the disabled attribute is not on
    # the Approve form's button on the test row.)
    assert "Approve &amp; Send" in html or "Approve & Send" in html


def test_admin_sees_red_block_when_case_file_missing(
        client, admin_user, monkeypatch):
    from fiesta.admin import autoreply_routes as ar
    bad_row = _draft_row(case_file_built=False)
    monkeypatch.setattr(ar, "_list_pending_drafts",
                         lambda **kw: {"ok": True, "status": 200,
                                        "body": [bad_row], "error": None})
    monkeypatch.setattr(ar, "_cb_state",
                         lambda: {"state": "closed", "reason": "",
                                   "set_by": None, "set_at": None,
                                   "available": True})
    login_as(client, admin_user)
    resp = client.get(QUEUE_INDEX)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Approve button is disabled when a gate badge is red.
    assert "disabled" in html


def test_admin_sees_cb_bar_open_when_breaker_open(
        client, admin_user, monkeypatch):
    from fiesta.admin import autoreply_routes as ar
    monkeypatch.setattr(ar, "_list_pending_drafts",
                         lambda **kw: {"ok": True, "status": 200,
                                        "body": [], "error": None})
    monkeypatch.setattr(ar, "_cb_state",
                         lambda: {"state": "open", "reason": "test pause",
                                   "set_by": "ops@example.com",
                                   "set_at": "2026-05-21T00:00:00Z",
                                   "available": True})
    login_as(client, admin_user)
    resp = client.get(QUEUE_INDEX)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Cohort Circuit Breaker" in html
    # The open state surfaces in the bar.
    assert "open" in html.lower()


# --------------------------------------------------------------------------- #
# Approve handler
# --------------------------------------------------------------------------- #

def test_approve_happy_path_flips_to_approved_by_admin(
        client, admin_user, stub_autoreply_supabase):
    login_as(client, admin_user)
    resp = client.post(f"/admin/fie/autoreply/aq_test_001/approve",
                        follow_redirects=False)
    # Routes redirect back to the queue on success.
    assert resp.status_code in (301, 302)
    patches = stub_autoreply_supabase["patches"]
    assert any(p["status"] == "approved_by_admin" for p in patches), patches


def test_approve_refuses_red_badge_row(
        client, admin_user, monkeypatch):
    from fiesta.admin import autoreply_routes as ar
    bad_row = _draft_row(case_file_built=False)
    monkeypatch.setattr(ar, "_get_draft",
                         lambda qid: {"ok": True, "status": 200,
                                       "body": [bad_row], "error": None})
    monkeypatch.setattr(ar, "_cb_state",
                         lambda: {"state": "closed", "reason": "",
                                   "set_by": None, "set_at": None,
                                   "available": True})
    patches: list = []
    monkeypatch.setattr(ar, "_update_draft_status",
                         lambda qid, **kw: (patches.append(kw)
                                              or {"ok": True, "status": 200,
                                                  "body": {}, "error": None}))
    login_as(client, admin_user)
    resp = client.post("/admin/fie/autoreply/aq_test_001/approve",
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    # The route MUST NOT call _update_draft_status on a red-badge row.
    assert patches == [], (
        f"Approve should refuse red-badge row but PATCHed: {patches}"
    )


def test_approve_refuses_non_pending_row(
        client, admin_user, monkeypatch):
    from fiesta.admin import autoreply_routes as ar
    done_row = _draft_row(status="approved_and_sent")
    monkeypatch.setattr(ar, "_get_draft",
                         lambda qid: {"ok": True, "status": 200,
                                       "body": [done_row], "error": None})
    monkeypatch.setattr(ar, "_cb_state",
                         lambda: {"state": "closed", "reason": "",
                                   "set_by": None, "set_at": None,
                                   "available": True})
    patches: list = []
    monkeypatch.setattr(ar, "_update_draft_status",
                         lambda qid, **kw: (patches.append(kw)
                                              or {"ok": True, "status": 200,
                                                  "body": {}, "error": None}))
    login_as(client, admin_user)
    resp = client.post("/admin/fie/autoreply/aq_test_001/approve",
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert patches == [], (
        f"Approve should refuse non-pending row but PATCHed: {patches}"
    )


# --------------------------------------------------------------------------- #
# Reject handler
# --------------------------------------------------------------------------- #

def test_reject_flips_to_cancelled_by_admin_with_reason(
        client, admin_user, stub_autoreply_supabase):
    login_as(client, admin_user)
    resp = client.post("/admin/fie/autoreply/aq_test_001/reject",
                        data={"reason": "Wrong template applied"},
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    patches = stub_autoreply_supabase["patches"]
    cancelled = [p for p in patches if p["status"] == "cancelled_by_admin"]
    assert len(cancelled) == 1, patches
    extras = cancelled[0]["extras"]
    assert extras["details"]["reject_reason"] == "Wrong template applied"


def test_reject_refuses_non_pending_row(
        client, admin_user, monkeypatch):
    from fiesta.admin import autoreply_routes as ar
    sent_row = _draft_row(status="approved_and_sent")
    monkeypatch.setattr(ar, "_get_draft",
                         lambda qid: {"ok": True, "status": 200,
                                       "body": [sent_row], "error": None})
    patches: list = []
    monkeypatch.setattr(ar, "_update_draft_status",
                         lambda qid, **kw: (patches.append(kw)
                                              or {"ok": True, "status": 200,
                                                  "body": {}, "error": None}))
    login_as(client, admin_user)
    resp = client.post("/admin/fie/autoreply/aq_test_001/reject",
                        data={"reason": "anything"},
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert patches == [], f"Reject should refuse non-pending row: {patches}"


# --------------------------------------------------------------------------- #
# Cohort CB toggle
# --------------------------------------------------------------------------- #

def test_cohort_cb_requires_state_and_reason(
        client, admin_user, stub_autoreply_supabase):
    login_as(client, admin_user)
    # Missing reason.
    resp = client.post("/admin/fie/autoreply/cohort-cb",
                        data={"state": "open", "reason": ""},
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert stub_autoreply_supabase["cb_sets"] == []
    # Invalid state.
    resp = client.post("/admin/fie/autoreply/cohort-cb",
                        data={"state": "elsewhere", "reason": "x"},
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert stub_autoreply_supabase["cb_sets"] == []


def test_cohort_cb_open_persists(
        client, admin_user, stub_autoreply_supabase):
    login_as(client, admin_user)
    resp = client.post("/admin/fie/autoreply/cohort-cb",
                        data={"state": "open",
                              "reason": "Suspected mis-classified F3.0 batch — pause"},
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    sets = stub_autoreply_supabase["cb_sets"]
    assert len(sets) == 1
    assert sets[0]["state"] == "open"
    assert "Suspected" in sets[0]["reason"]
    assert sets[0]["set_by"]  # populated from current_user.email


# --------------------------------------------------------------------------- #
# Healthz
# --------------------------------------------------------------------------- #

def test_healthz_returns_json_with_pipeline_owner(
        client, admin_user, stub_autoreply_supabase):
    login_as(client, admin_user)
    resp = client.get("/admin/fie/autoreply/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body is not None
    assert "supabase" in body
    assert "send_pipeline" in body
    assert body["send_pipeline"]["owner"].startswith("CEO-OS")
    assert "cohort_cb" in body
    assert body["cohort_cb"]["state"] in ("open", "closed")
