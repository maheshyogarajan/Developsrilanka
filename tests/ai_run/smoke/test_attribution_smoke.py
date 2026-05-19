"""
Meta-test: verify BasicAttributionSmokeTest runs end-to-end against the live
Neon DB.

Uses the standard ai_run conftest fixtures (app, db_session) so the Flask
app_context + DB connection are already wired. The smoke does its own
app.app_context() pushes in each phase, mirroring how the Celery worker
operates in production — that's load-bearing for the validity of the smoke.
"""
import pytest

from tests.ai_run.smoke.base import BasicAttributionSmokeTest


@pytest.mark.usefixtures("app", "db_session")
def test_basic_attribution_smoke_passes():
    smoke = BasicAttributionSmokeTest()
    result = smoke.run()

    assert result.passed, (
        f"BasicAttributionSmokeTest failed: error={result.error}"
    )
    assert result.event_id is not None
    assert result.evidence.get("claimed_by_org") == "acquisition_studio"
    assert result.evidence.get("attribution_kind") == "last-touch"
    assert pytest.approx(result.evidence.get("confidence")) == 0.9
    # Cleanup is best-effort; warn if it left rows behind but don't fail.
    if result.cleanup_error:
        # Surface for CI logs without failing the test.
        print(f"cleanup-warning: {result.cleanup_error}")
