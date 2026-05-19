"""X2 Persona switch — v1 self-locked, v1.1 demand-signal tests.

8 cases:
  1. V1: every user has one Persona with persona_id='self', can_create_more=False
  2. V1: dropdown HTML shows greyed items with v1.1 tooltip
  3. /persona/current returns 'self' for v1 users
  4. POST /persona/interest stores correctly for a valid locked type
  5. POST /persona/interest is idempotent (no duplicate row on resubmit)
  6. POST /persona/interest with invalid persona_type returns 400
  7. Cross-screen helper current_persona() returns the 'self' Persona for v1
  8. V1.1 extension prep: model allows multiple Personas isolated by user_id

All tests use ephemeral users (pytest_x2_*@fiesta.local) and clean up after.
"""
import pytest

from .conftest import login_as


# -------------------------------------------------------------------- #
# Case 1: V1 — every user gets exactly one 'self' persona, locked.
# -------------------------------------------------------------------- #
def test_v1_user_has_one_self_persona_locked(app, user_x):
    from fiesta.persona.models import (
        Persona, ensure_self_persona, PERSONA_TYPE_SELF,
    )
    with app.app_context():
        p = ensure_self_persona(user_x)
        assert p.persona_id == PERSONA_TYPE_SELF
        assert p.can_create_more is False
        assert p.active is True

        # Idempotent — second call returns same row.
        p2 = ensure_self_persona(user_x)
        assert p2.id == p.id

        # Exactly one row for this user.
        rows = Persona.query.filter_by(user_id=user_x.id).all()
        assert len(rows) == 1


# -------------------------------------------------------------------- #
# Case 2: Dropdown HTML — greyed items + v1.1 tooltip text.
# -------------------------------------------------------------------- #
def test_dropdown_shows_locked_items_with_tooltip(app, client, user_x):
    login_as(client, user_x)
    # Pull any authenticated page — layout.html includes the switcher.
    resp = client.get("/persona")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Greyed v1.1 items present with empowerment voice.
    assert "Add spouse" in body
    assert "Add dependant" in body
    assert "Add parent" in body
    # Tooltip phrasing per spec.
    assert "Available in v1.1" in body
    # Active persona is "Self (you)".
    assert "Self (you)" in body


# -------------------------------------------------------------------- #
# Case 3: GET /persona/current — JSON contract for cross-screen filtering.
# -------------------------------------------------------------------- #
def test_persona_current_returns_self_for_v1(app, client, user_x):
    login_as(client, user_x)
    resp = client.get("/persona/current")
    assert resp.status_code == 200
    j = resp.get_json()
    assert j["persona_id"] == "self"
    assert j["can_create_more"] is False
    assert j["locked"] is False  # 'self' is the unlocked active persona
    assert j["active"] is True


# -------------------------------------------------------------------- #
# Case 4: POST /persona/interest stores a row.
# -------------------------------------------------------------------- #
def test_interest_capture_stores_row(app, client, user_y):
    from fiesta.persona.models import PersonaInterest
    login_as(client, user_y)
    resp = client.post("/persona/interest", data={"persona_type": "spouse"})
    assert resp.status_code == 200
    j = resp.get_json()
    assert j["ok"] is True
    assert j["persona_type"] == "spouse"
    assert j["already_captured"] is False

    with app.app_context():
        rows = PersonaInterest.query.filter_by(
            user_id=user_y.id, persona_type="spouse"
        ).all()
        assert len(rows) == 1
        assert rows[0].email == user_y.email


# -------------------------------------------------------------------- #
# Case 5: Interest capture is idempotent.
# -------------------------------------------------------------------- #
def test_interest_capture_idempotent(app, client, user_y):
    from fiesta.persona.models import PersonaInterest
    login_as(client, user_y)

    r1 = client.post("/persona/interest", data={"persona_type": "dependant_1"})
    assert r1.status_code == 200
    assert r1.get_json()["already_captured"] is False

    r2 = client.post("/persona/interest", data={"persona_type": "dependant_1"})
    assert r2.status_code == 200
    assert r2.get_json()["already_captured"] is True

    with app.app_context():
        rows = PersonaInterest.query.filter_by(
            user_id=user_y.id, persona_type="dependant_1"
        ).all()
        assert len(rows) == 1  # still exactly one


# -------------------------------------------------------------------- #
# Case 6: Invalid persona_type rejected.
# -------------------------------------------------------------------- #
def test_interest_invalid_type_rejected(app, client, user_x):
    login_as(client, user_x)
    # 'self' is the active persona, not a waitlist target → reject.
    r1 = client.post("/persona/interest", data={"persona_type": "self"})
    assert r1.status_code == 400

    r2 = client.post("/persona/interest", data={"persona_type": "alien_overlord"})
    assert r2.status_code == 400

    r3 = client.post("/persona/interest", data={"persona_type": ""})
    assert r3.status_code == 400


# -------------------------------------------------------------------- #
# Case 7: Cross-screen helper — current_persona(user) returns the 'self' row.
# -------------------------------------------------------------------- #
def test_current_persona_helper_returns_self(app, user_x):
    from fiesta.persona.models import current_persona, PERSONA_TYPE_SELF
    with app.app_context():
        p = current_persona(user_x)
        assert p is not None
        assert p.persona_id == PERSONA_TYPE_SELF
        assert p.user_id == user_x.id


# -------------------------------------------------------------------- #
# Case 8: V1.1 extension prep — model permits multiple Personas isolated by user.
# -------------------------------------------------------------------- #
def test_v1_1_model_supports_multiple_personas_isolated(app, user_x, user_y, user_z):
    """The DB schema must already permit v1.1 multi-persona without migration.
    We bypass `ensure_self_persona` to directly create non-'self' rows for two
    different users and confirm isolation by user_id. user_z has the 'self' row
    only — proves cross-user isolation."""
    from app import db
    from fiesta.persona.models import Persona
    with app.app_context():
        # user_x: self + spouse + dependant_1
        Persona.query.filter_by(user_id=user_x.id).delete()
        Persona.query.filter_by(user_id=user_y.id).delete()
        Persona.query.filter_by(user_id=user_z.id).delete()
        db.session.commit()

        db.session.add(Persona(user_id=user_x.id, persona_id="self",
                               name="Mahesh", relationship="self"))
        db.session.add(Persona(user_id=user_x.id, persona_id="spouse",
                               name="Spouse X", relationship="spouse"))
        db.session.add(Persona(user_id=user_x.id, persona_id="dependant_1",
                               name="Child X1", relationship="dependant"))
        # user_y: just self + parent_1
        db.session.add(Persona(user_id=user_y.id, persona_id="self",
                               name="User Y", relationship="self"))
        db.session.add(Persona(user_id=user_y.id, persona_id="parent_1",
                               name="Parent Y", relationship="parent"))
        # user_z: untouched (no persona rows yet)
        db.session.commit()

        rows_x = Persona.query.filter_by(user_id=user_x.id).all()
        rows_y = Persona.query.filter_by(user_id=user_y.id).all()
        rows_z = Persona.query.filter_by(user_id=user_z.id).all()
        assert len(rows_x) == 3
        assert len(rows_y) == 2
        assert len(rows_z) == 0  # cross-user isolation confirmed

        # Same persona_id can exist for different users (no collision).
        x_self = next(p for p in rows_x if p.persona_id == "self")
        y_self = next(p for p in rows_y if p.persona_id == "self")
        assert x_self.id != y_self.id
        assert x_self.user_id != y_self.user_id
