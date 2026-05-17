"""
T2 — Wave H H3 council #1 critical fix: import_confirm MUST skip ambiguous
rows (foreign_amount=0 or missing currency). It MUST NOT create an LKR=LKR
'remittance' which corrupts the ledger and renders 15% tax compute invalid.

T3 — Wave H H2: import payload survives via server-side RemittanceImportBatch
table, not via session cookie. A 100-row statement should round-trip cleanly.
"""
from datetime import datetime, timedelta

import pytest

from .conftest import login_as


def _seed_batch(db_session, user, candidates):
    from remittance_models import RemittanceImportBatch
    import uuid
    b = RemittanceImportBatch(
        import_id=str(uuid.uuid4())[:12],
        user_id=user.id,
        filename="pytest.csv",
        kind="csv",
        candidates=candidates,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db_session.add(b)
    db_session.commit()
    return b


def test_zero_foreign_amount_row_is_skipped(client, db_session, user_a):
    """The headline data-corruption bug: foreign_amount=0 must NOT create an entry."""
    from remittance_models import RemittanceEntry
    candidates = [
        {"row_index": 0, "txn_date": "2026-03-15", "description": "Suspicious 0 USD wire",
         "lkr_amount": "0.00", "foreign_currency": "USD", "foreign_amount": "0",
         "implied_rate": None, "likely_payer": None, "source_country_iso2": None,
         "is_foreign_remittance": True, "confidence": "low", "reason": "zero-amount test"},
    ]
    batch = _seed_batch(db_session, user_a, candidates)
    before = RemittanceEntry.query.filter_by(user_id=user_a.id).count()

    login_as(client, user_a)
    resp = client.post(
        f"/remittance/import/{batch.import_id}/confirm",
        data={"include[0]": "1"},
        follow_redirects=False,
    )
    after = RemittanceEntry.query.filter_by(user_id=user_a.id).count()
    assert after == before, (
        f"Zero-amount row created a phantom entry. before={before} after={after}. "
        "This is the data-corruption bug from punch list #1."
    )
    # Redirect is fine; status itself isn't the test — the count is.
    assert resp.status_code in (200, 302)


def test_missing_currency_row_is_skipped(client, db_session, user_a):
    from remittance_models import RemittanceEntry
    candidates = [
        {"row_index": 0, "txn_date": "2026-03-15", "description": "Unknown credit",
         "lkr_amount": "50000", "foreign_currency": "", "foreign_amount": "100",
         "implied_rate": None, "likely_payer": None, "source_country_iso2": None,
         "is_foreign_remittance": True, "confidence": "low", "reason": "missing ccy"},
    ]
    batch = _seed_batch(db_session, user_a, candidates)
    before = RemittanceEntry.query.filter_by(user_id=user_a.id).count()

    login_as(client, user_a)
    client.post(
        f"/remittance/import/{batch.import_id}/confirm",
        data={"include[0]": "1"},
        follow_redirects=False,
    )
    after = RemittanceEntry.query.filter_by(user_id=user_a.id).count()
    assert after == before, "Missing-currency row should be skipped (no LKR=LKR fallback)."


def test_valid_row_creates_entry(client, db_session, user_a):
    from remittance_models import RemittanceEntry
    candidates = [
        {"row_index": 0, "txn_date": "2026-03-15", "description": "TT INWARD STRIPE USD",
         "lkr_amount": "305500", "foreign_currency": "USD", "foreign_amount": "1000",
         "implied_rate": "305.50", "likely_payer": "STRIPE INC", "source_country_iso2": "US",
         "is_foreign_remittance": True, "confidence": "high", "reason": "TT INWARD + USD"},
    ]
    batch = _seed_batch(db_session, user_a, candidates)
    before = RemittanceEntry.query.filter_by(user_id=user_a.id).count()

    login_as(client, user_a)
    client.post(
        f"/remittance/import/{batch.import_id}/confirm",
        data={"include[0]": "1"},
        follow_redirects=False,
    )
    after = RemittanceEntry.query.filter_by(user_id=user_a.id).count()
    assert after == before + 1, "Valid USD remittance row should create exactly one entry."


def test_unselected_row_is_skipped(client, db_session, user_a):
    from remittance_models import RemittanceEntry
    candidates = [
        {"row_index": 0, "txn_date": "2026-03-15", "description": "Valid USD",
         "lkr_amount": "305500", "foreign_currency": "USD", "foreign_amount": "1000",
         "implied_rate": None, "likely_payer": None, "source_country_iso2": None,
         "is_foreign_remittance": True, "confidence": "high", "reason": ""},
    ]
    batch = _seed_batch(db_session, user_a, candidates)
    before = RemittanceEntry.query.filter_by(user_id=user_a.id).count()

    login_as(client, user_a)
    # Note: no include[0] in form data → row is NOT selected
    client.post(
        f"/remittance/import/{batch.import_id}/confirm",
        data={},
        follow_redirects=False,
    )
    after = RemittanceEntry.query.filter_by(user_id=user_a.id).count()
    assert after == before, "Unselected row should not be imported."


def test_100_row_batch_survives_round_trip(client, db_session, user_a):
    """T3 — Wave H H2: 100 candidates fit in JSON column, no session-cookie overflow."""
    from remittance_models import RemittanceEntry
    candidates = [
        {"row_index": i, "txn_date": f"2026-0{(i % 9) + 1}-{(i % 28) + 1:02d}",
         "description": f"TT INWARD ROW {i} USD",
         "lkr_amount": str(305000 + i * 100), "foreign_currency": "USD",
         "foreign_amount": str(1000 + i), "implied_rate": "305.50",
         "likely_payer": f"PAYER {i}", "source_country_iso2": "US",
         "is_foreign_remittance": True, "confidence": "high", "reason": "100-row stress"}
        for i in range(100)
    ]
    batch = _seed_batch(db_session, user_a, candidates)

    login_as(client, user_a)
    form = {f"include[{i}]": "1" for i in range(100)}
    resp = client.post(
        f"/remittance/import/{batch.import_id}/confirm",
        data=form,
        follow_redirects=False,
    )
    created = RemittanceEntry.query.filter_by(user_id=user_a.id).count()
    assert created == 100, (
        f"100-row batch should round-trip cleanly. Got {created} entries. "
        "If <100, session-cookie storage may still be in use (Wave H H2 regression)."
    )


def test_other_user_cannot_confirm_my_batch(client, db_session, user_a, user_b):
    """H1 again — batch confirm is user-scoped."""
    from remittance_models import RemittanceEntry
    candidates = [
        {"row_index": 0, "txn_date": "2026-03-15", "description": "x",
         "lkr_amount": "305500", "foreign_currency": "USD", "foreign_amount": "1000",
         "implied_rate": None, "likely_payer": None, "source_country_iso2": None,
         "is_foreign_remittance": True, "confidence": "high", "reason": ""},
    ]
    batch = _seed_batch(db_session, user_a, candidates)
    before = RemittanceEntry.query.filter_by(user_id=user_a.id).count()

    login_as(client, user_b)
    resp = client.post(
        f"/remittance/import/{batch.import_id}/confirm",
        data={"include[0]": "1"},
        follow_redirects=False,
    )
    after = RemittanceEntry.query.filter_by(user_id=user_a.id).count()
    assert after == before, (
        "User B was able to confirm user A's import batch — cross-user data write."
    )
    # 404 is the expected response (batch not visible to user_b)
    assert resp.status_code in (302, 404)
