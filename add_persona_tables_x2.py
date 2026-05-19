"""
Migration: create persona + persona_interest tables for X2 cross-screen Persona switch.

Idempotent. Safe to re-run.

Adds:
  persona            : per-user filing personas. V1: one row per user, persona_id='self'.
                       V1.1+: up to six rows per user (spouse, dependants, parents).
  persona_interest   : v1.1 waitlist capture (demand-signal table).

Council brief X2 + S2 finding 405 (v1 self-only).
"""
import logging

from app import app, db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def run():
    with app.app_context():
        with db.engine.connect() as conn:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS persona (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    persona_id VARCHAR(32) NOT NULL,
                    nic VARCHAR(20),
                    name VARCHAR(255),
                    relationship VARCHAR(32) NOT NULL DEFAULT 'self',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_persona_user_slug UNIQUE (user_id, persona_id)
                )
            """))
            log.info("persona table ensured")

            conn.execute(db.text("""
                CREATE INDEX IF NOT EXISTS ix_persona_user
                    ON persona (user_id)
            """))

            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS persona_interest (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    persona_type VARCHAR(32) NOT NULL,
                    email VARCHAR(255),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_persona_interest_user_type UNIQUE (user_id, persona_type)
                )
            """))
            log.info("persona_interest table ensured")

            conn.execute(db.text("""
                CREATE INDEX IF NOT EXISTS ix_persona_interest_user
                    ON persona_interest (user_id)
            """))

            conn.commit()
            log.info("X2 persona migration committed")


if __name__ == "__main__":
    run()
