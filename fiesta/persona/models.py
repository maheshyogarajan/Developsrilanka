"""Persona models — X2 cross-screen persona switch.

Two tables:
  - persona            : one row per (user_id, persona_id). V1: only persona_id='self'
                         is ever created by code paths; the other persona_ids exist as
                         constants and are reserved for v1.1+ user-created entries.
  - persona_interest   : v1.1 waitlist capture. (user_id, persona_type, created_at).
                         Idempotent on (user_id, persona_type) via unique constraint.

Design notes
  * V1 lock: `ensure_self_persona(user)` is the ONLY path that creates a Persona row.
    Multi-persona creation routes do NOT exist in v1. The model is DB-ready so v1.1
    can add a /persona/create endpoint without a migration.
  * `current_persona(user)` is the cross-screen helper. In v1 it ALWAYS returns the
    'self' Persona. In v1.1 it will read from a session/cookie selector.
  * Slug discipline: persona_id is a string slug, not an integer. Stable identifier
    used by analytics and (future) URL params.

Council brief X2: "Persona switch (top bar)".
"""
from datetime import datetime
from typing import Optional

from app import db


PERSONA_TYPE_SELF = "self"
PERSONA_TYPE_SPOUSE = "spouse"
PERSONA_TYPE_DEPENDANT_1 = "dependant_1"
PERSONA_TYPE_DEPENDANT_2 = "dependant_2"
PERSONA_TYPE_PARENT_1 = "parent_1"
PERSONA_TYPE_PARENT_2 = "parent_2"

# Ordered list — drives dropdown display order in templates.
PERSONA_TYPES = [
    PERSONA_TYPE_SELF,
    PERSONA_TYPE_SPOUSE,
    PERSONA_TYPE_DEPENDANT_1,
    PERSONA_TYPE_DEPENDANT_2,
    PERSONA_TYPE_PARENT_1,
    PERSONA_TYPE_PARENT_2,
]

# Everything except 'self' is locked in v1.
LOCKED_PERSONA_TYPES = [pt for pt in PERSONA_TYPES if pt != PERSONA_TYPE_SELF]

# Human labels used by the dropdown. Empowerment voice — "Self (you)", not
# "User type: self".
PERSONA_LABELS = {
    PERSONA_TYPE_SELF: "Self (you)",
    PERSONA_TYPE_SPOUSE: "Add spouse",
    PERSONA_TYPE_DEPENDANT_1: "Add dependant",
    PERSONA_TYPE_DEPENDANT_2: "Add second dependant",
    PERSONA_TYPE_PARENT_1: "Add parent",
    PERSONA_TYPE_PARENT_2: "Add second parent",
}

# Relationship label stored on the row when the persona is materialised (v1.1).
PERSONA_RELATIONSHIP = {
    PERSONA_TYPE_SELF: "self",
    PERSONA_TYPE_SPOUSE: "spouse",
    PERSONA_TYPE_DEPENDANT_1: "dependant",
    PERSONA_TYPE_DEPENDANT_2: "dependant",
    PERSONA_TYPE_PARENT_1: "parent",
    PERSONA_TYPE_PARENT_2: "parent",
}


class Persona(db.Model):
    """A filing persona owned by a user.

    V1: exactly one row per user where persona_id='self'.
    V1.1+: up to six rows per user (one per persona_id in PERSONA_TYPES).

    Isolation invariant: every query that surfaces persona data MUST filter by
    user_id. The unique constraint on (user_id, persona_id) prevents accidental
    cross-user collisions.
    """

    __tablename__ = "persona"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    persona_id = db.Column(db.String(32), nullable=False)  # slug, e.g. 'self'
    nic = db.Column(db.String(20), nullable=True)
    name = db.Column(db.String(255), nullable=True)
    relationship = db.Column(db.String(32), nullable=False, default="self")
    active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "persona_id", name="uq_persona_user_slug"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"<Persona id={self.id} user_id={self.user_id} persona_id={self.persona_id!r}>"

    @property
    def can_create_more(self) -> bool:
        """V1: locked. V1.1 will return True once multi-persona ships."""
        return False

    @property
    def display_label(self) -> str:
        """Label shown in the top-bar pill. Prefers user-supplied name when set."""
        if self.name:
            return self.name
        return PERSONA_LABELS.get(self.persona_id, self.persona_id)


class PersonaInterest(db.Model):
    """V1.1 waitlist capture — one row per (user, persona_type) pair.

    Used as a demand signal to decide which secondary persona-type to ship FIRST
    in v1.1 (spouse vs dependant vs parent). Idempotent: re-clicking "Notify me"
    on the spouse slot doesn't create duplicate rows, just updates updated_at.
    """

    __tablename__ = "persona_interest"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    persona_type = db.Column(db.String(32), nullable=False)
    email = db.Column(db.String(255), nullable=True)  # captured from user.email at write time
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "persona_type", name="uq_persona_interest_user_type"
        ),
    )


def ensure_self_persona(user) -> Persona:
    """Idempotent: return the user's 'self' Persona, creating it if missing.

    Called by /persona routes and (eventually) at signup. Safe to call repeatedly.
    """
    existing = Persona.query.filter_by(user_id=user.id, persona_id=PERSONA_TYPE_SELF).first()
    if existing is not None:
        return existing
    p = Persona(
        user_id=user.id,
        persona_id=PERSONA_TYPE_SELF,
        name=getattr(user, "name", None),
        nic=getattr(user, "nic", None),
        relationship=PERSONA_RELATIONSHIP[PERSONA_TYPE_SELF],
        active=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


def current_persona(user) -> Optional[Persona]:
    """Cross-screen helper: returns the currently active Persona for `user`.

    V1 contract: always returns the 'self' Persona (creating it on first call).
    V1.1 contract: reads selected persona_id from session, returns matching row;
    falls back to 'self'.

    Returns None ONLY if `user` is anonymous / unauthenticated.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return ensure_self_persona(user)
