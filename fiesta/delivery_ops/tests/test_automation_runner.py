"""Tests for fiesta.delivery_ops.automation_runner.

Wave 2b SL adapter. No live SF calls — all SF interactions mocked via the
sf_client DI seam. Phase Gate Y also injected.

Run:
    cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
    python -m pytest fiesta/delivery_ops/tests/ -v
"""
from __future__ import annotations

import pytest

from fiesta.delivery_ops.automation_runner import (
    AUTOMATION_TYPE_MAP,
    AUTOMATION_ROUTING,
    SYSTEM_BOT_TSE_ID,
    AutomationRunnerError,
    build_processing_task_payload,
    build_resolver_change_payload,
    invoke_sl_automation,
)


# --------------------------------------------------------------------------- #
# Fixtures — fake SF client + Phase Gate reader
# --------------------------------------------------------------------------- #


class FakeSFClient:
    """Records calls, returns scripted responses.

    Defaults: customer query returns one happy-path Customer__c row;
    Resolver_Change__c POST returns {id: "rc_fake_id"};
    Processing_task__c POST returns {id: "pt_fake_id"}.
    """

    def __init__(self, *, customer_records=None,
                 resolver_change_resp=None, pt_resp=None,
                 query_raises=None):
        self.customer_records = (
            customer_records
            if customer_records is not None
            else [{
                "Id": "a0F2w000001abcdEAA",
                "Name": "Test Customer A",
                "Contact__c": "0032w00000contact1AAA",
                "Assigned_Relationship_Manager__c": "0052w00000rmuser1AAA",
            }]
        )
        self.resolver_change_resp = resolver_change_resp or {"id": "rc_fake_id"}
        self.pt_resp = pt_resp or {"id": "pt_fake_id"}
        self.query_raises = query_raises

        # Recording
        self.queries = []
        self.posts = []  # list of (sobject, body)

    def query(self, soql):
        self.queries.append(soql)
        if self.query_raises:
            raise self.query_raises
        return {"records": list(self.customer_records)}

    def post(self, sobject, body):
        self.posts.append((sobject, body))
        if sobject == "Resolver_Change__c":
            return dict(self.resolver_change_resp)
        if sobject == "Processing_task__c":
            return dict(self.pt_resp)
        return {"error": True, "status": 500, "message": f"no fake for {sobject}"}


def _gate_active():
    return True, "Phase Gate Y active (test stub)"


def _gate_inactive():
    return False, "Phase Gate Y not stamped (test stub)"


# --------------------------------------------------------------------------- #
# Pure-payload tests (no I/O)
# --------------------------------------------------------------------------- #


class TestBuildProcessingTaskPayload:
    @pytest.mark.parametrize("auto_type,expected_pt_type", list(AUTOMATION_TYPE_MAP.items()))
    def test_payload_shape_for_each_automation_type(self, auto_type, expected_pt_type):
        payload = build_processing_task_payload(
            customer_id="a0F2w000001abcdEAA",
            customer_name="Jane Doe",
            contact_id="0032w00000contactEAA",
            relationship_manager_id="0052w00000rmuserEAA",
            automation_type=auto_type,
        )
        # Required fields per PCSE Strategist D §3.1.
        assert payload["Subject__c"] == f"{expected_pt_type} - Jane Doe"
        assert payload["Status__c"] == "Open"
        assert payload["Processing_task_type__c"] == expected_pt_type
        assert payload["Client_name__c"] == "a0F2w000001abcdEAA"
        assert payload["Primary_processsing_person__c"] == SYSTEM_BOT_TSE_ID
        assert "Due_date__c" in payload
        # Best-effort fields.
        assert payload["Contact__c"] == "0032w00000contactEAA"
        assert payload["Relationship_Manager__c"] == "0052w00000rmuserEAA"

    def test_payload_omits_nullable_fields_when_missing(self):
        payload = build_processing_task_payload(
            customer_id="a0F2w000001abcdEAA",
            customer_name="No Contact",
            contact_id=None,
            relationship_manager_id=None,
            automation_type="LOGIN_CHECK",
        )
        assert "Contact__c" not in payload
        assert "Relationship_Manager__c" not in payload
        assert payload["Processing_task_type__c"] == "IRD Credential Verification"

    def test_payload_uses_supplied_due_date(self):
        payload = build_processing_task_payload(
            customer_id="a0F2w000001abcdEAA",
            customer_name="X",
            contact_id=None,
            relationship_manager_id=None,
            automation_type="PIN_REQUEST",
            due_date="2026-12-31",
        )
        assert payload["Due_date__c"] == "2026-12-31"

    def test_payload_rejects_unknown_automation_type(self):
        with pytest.raises(AutomationRunnerError):
            build_processing_task_payload(
                customer_id="a0F2w000001abcdEAA",
                customer_name="X",
                contact_id=None,
                relationship_manager_id=None,
                automation_type="MAGIC_PONY",
            )

    def test_resolver_change_payload_marks_irreversible(self):
        rc = build_resolver_change_payload(
            automation_type="PIN_REQUEST",
            customer_id="a0F2w000001abcdEAA",
            trace_id="t-1",
        )
        # Rule P1 shape.
        assert rc["Target_Object__c"] == "Processing_task__c"
        assert rc["Change_Type__c"] == "automation_invoke"
        # PCSE Strategist D §3.3 — IRD-side effects are not reversible from SF.
        assert rc["Reversible__c"] is False
        # PIN Creation per the AUTOMATION_TYPE_MAP.
        assert rc["New_Value__c"] == "PIN Creation"


# --------------------------------------------------------------------------- #
# invoke_sl_automation — dry-run paths
# --------------------------------------------------------------------------- #


class TestDryRunHappyPath:
    @pytest.mark.parametrize("auto_type", sorted(AUTOMATION_TYPE_MAP))
    def test_dry_run_no_writes_for_each_type(self, auto_type):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type=auto_type,
            dry_run=True,
            sf_client=sf,
            phase_gate_reader=_gate_inactive,  # ignored in dry_run
        )
        assert out["ok"] is True, out
        assert out["dry_run"] is True
        assert out["processing_task_id"] is None
        assert out["resolver_change_id"] is None
        # Customer query happened, but NO POSTs.
        assert len(sf.queries) == 1
        assert len(sf.posts) == 0
        assert "dry_run_no_write" in out["actions_taken"]
        # would_insert shape.
        assert out["would_insert"]["Processing_task_type__c"] == AUTOMATION_TYPE_MAP[auto_type]
        # Routing hint surfaced.
        assert out["dispatched_to"] == AUTOMATION_ROUTING[auto_type]["dispatched_to"]
        assert out["expected_completion_minutes"] == AUTOMATION_ROUTING[auto_type]["expected_minutes"]


# --------------------------------------------------------------------------- #
# invoke_sl_automation — input validation
# --------------------------------------------------------------------------- #


class TestInputValidation:
    def test_rejects_empty_customer_id(self):
        out = invoke_sl_automation(
            customer_id="",
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=FakeSFClient(),
        )
        assert out["ok"] is False
        assert any("customer_id required" in e for e in out["errors"])

    def test_rejects_malformed_customer_id(self):
        out = invoke_sl_automation(
            customer_id="not-an-sf-id",
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=FakeSFClient(),
        )
        assert out["ok"] is False
        assert any("SF Id shape" in e for e in out["errors"])

    def test_accepts_18_char_customer_id(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",  # 18 char
            automation_type="LOGIN_CHECK",
            dry_run=True,
            sf_client=sf,
        )
        assert out["ok"] is True, out["errors"]

    def test_accepts_15_char_customer_id(self):
        sf = FakeSFClient(customer_records=[{
            "Id": "a0F2w000001abcd",  # 15 char
            "Name": "Short Id Cust",
            "Contact__c": None,
            "Assigned_Relationship_Manager__c": None,
        }])
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcd",
            automation_type="LOGIN_CHECK",
            dry_run=True,
            sf_client=sf,
        )
        assert out["ok"] is True, out["errors"]

    def test_rejects_unknown_automation_type(self):
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="WHAT_EVEN_IS_THIS",
            dry_run=True,
            sf_client=FakeSFClient(),
        )
        assert out["ok"] is False
        assert any("automation_type" in e for e in out["errors"])

    def test_customer_not_found_returns_error(self):
        sf = FakeSFClient(customer_records=[])
        out = invoke_sl_automation(
            customer_id="a0F2w99999notreEAA",  # 18-char shape, but no records
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=sf,
        )
        assert out["ok"] is False
        assert any("no Customer__c found" in e for e in out["errors"])

    def test_sf_query_exception_returns_error(self):
        sf = FakeSFClient(query_raises=RuntimeError("network down"))
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=sf,
        )
        assert out["ok"] is False
        assert any("sf_query_failed" in e for e in out["errors"])

    def test_sf_query_http_error_returns_error(self):
        class BadQueryClient(FakeSFClient):
            def query(self, soql):
                self.queries.append(soql)
                return {"error": True, "status": 401, "message": "INVALID_SESSION_ID"}

        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=BadQueryClient(),
        )
        assert out["ok"] is False
        assert any("sf_query_http_error" in e for e in out["errors"])


# --------------------------------------------------------------------------- #
# invoke_sl_automation — live mode + Phase Gate Y
# --------------------------------------------------------------------------- #


class TestLiveMode:
    def test_live_mode_blocked_when_phase_gate_inactive(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=False,
            sf_client=sf,
            phase_gate_reader=_gate_inactive,
        )
        assert out["ok"] is False
        assert any("live mode refused" in e for e in out["errors"])
        assert "phase_gate_y_blocked" in out["actions_taken"]
        # No SF writes happened.
        assert len(sf.posts) == 0

    def test_live_mode_succeeds_with_phase_gate_active(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=False,
            sf_client=sf,
            phase_gate_reader=_gate_active,
        )
        assert out["ok"] is True, out["errors"]
        assert out["resolver_change_id"] == "rc_fake_id"
        assert out["processing_task_id"] == "pt_fake_id"
        # Exactly 2 posts: Resolver_Change__c first, then Processing_task__c.
        assert [p[0] for p in sf.posts] == ["Resolver_Change__c", "Processing_task__c"]
        # Order matters per Rule P1.

    def test_live_mode_resolver_change_failure_aborts_pt_insert(self):
        sf = FakeSFClient(
            resolver_change_resp={"error": True, "status": 400, "message": "REQUIRED_FIELD_MISSING"},
        )
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=False,
            sf_client=sf,
            phase_gate_reader=_gate_active,
        )
        assert out["ok"] is False
        assert any("resolver_change_failed" in e for e in out["errors"])
        assert "resolver_change_aborted_write" in out["actions_taken"]
        # ONLY the Resolver_Change__c POST happened; no PT insert.
        assert [p[0] for p in sf.posts] == ["Resolver_Change__c"]

    def test_live_mode_pt_insert_failure_logged_with_rc_id(self):
        sf = FakeSFClient(
            pt_resp={"error": True, "status": 400, "message": "FIELD_INTEGRITY_EXCEPTION"},
        )
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="LOGIN_CHECK",
            dry_run=False,
            sf_client=sf,
            phase_gate_reader=_gate_active,
        )
        assert out["ok"] is False
        assert any("pt_insert_failed" in e for e in out["errors"])
        # Resolver_Change__c DID get created (since RC is BEFORE PT per P1);
        # caller now knows the orphan to investigate.
        assert out["resolver_change_id"] == "rc_fake_id"
        assert "pt_insert_failed" in out["actions_taken"]

    def test_live_mode_phase_gate_reader_exception_returns_error(self):
        def boom():
            raise RuntimeError("supabase down")

        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="LOGIN_CHECK",
            dry_run=False,
            sf_client=FakeSFClient(),
            phase_gate_reader=boom,
        )
        assert out["ok"] is False
        assert any("phase_gate_reader_raised" in e for e in out["errors"])
        assert "phase_gate_y_error" in out["actions_taken"]


# --------------------------------------------------------------------------- #
# invoke_sl_automation — routing hint metadata
# --------------------------------------------------------------------------- #


class TestRoutingHints:
    def test_aws_lambda_route_for_pin_request(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=sf,
        )
        assert out["dispatched_to"] == "aws_lambda"
        assert out["expected_completion_minutes"] == 5

    def test_docker_poller_route_for_login_check(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="LOGIN_CHECK",
            dry_run=True,
            sf_client=sf,
        )
        assert out["dispatched_to"] == "docker_poller"
        assert out["expected_completion_minutes"] == 2

    def test_din_collection_flagged_unverified(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="DIN_COLLECTION",
            dry_run=True,
            sf_client=sf,
        )
        # Listener may be paused — caller is told to check.
        assert out["dispatched_to"] == "docker_poller_unverified"
        assert out["expected_completion_minutes"] is None


# --------------------------------------------------------------------------- #
# trace_id behaviour
# --------------------------------------------------------------------------- #


class TestTraceId:
    def test_trace_id_supplied_is_preserved(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=sf,
            trace_id="caller-supplied-trace",
        )
        assert out["trace_id"] == "caller-supplied-trace"

    def test_trace_id_auto_generated_when_absent(self):
        sf = FakeSFClient()
        out = invoke_sl_automation(
            customer_id="a0F2w000001abcdEAA",
            automation_type="PIN_REQUEST",
            dry_run=True,
            sf_client=sf,
        )
        assert out["trace_id"].startswith("ar-")
        assert len(out["trace_id"]) == 3 + 12  # "ar-" + 12 hex
