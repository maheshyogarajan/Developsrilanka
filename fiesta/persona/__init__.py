"""fiesta.persona — X2 cross-screen Persona switch (top-bar).

V1 reality: only persona='self' is functional. Spouse / dependant / parent
slots exist in the model + UI but are LOCKED in v1; v1.1 unlocks them.

The top-bar dropdown is the trust-building proof point: customers SEE that
FIESTA will support multi-persona filing, so they understand the platform
is built for the full household tax picture rather than a single-return
toy. Greyed slots capture v1.1 interest as a demand signal.

Council brief: working files/strategic/council/_briefs/fiesta_council_brief.json
S2 finding 405: v1 self-file persona LOCKED.
"""
from .models import (
    Persona,
    PersonaInterest,
    PERSONA_TYPES,
    PERSONA_TYPE_SELF,
    PERSONA_TYPE_SPOUSE,
    PERSONA_TYPE_DEPENDANT_1,
    PERSONA_TYPE_DEPENDANT_2,
    PERSONA_TYPE_PARENT_1,
    PERSONA_TYPE_PARENT_2,
    LOCKED_PERSONA_TYPES,
    ensure_self_persona,
    current_persona,
)

__all__ = [
    "Persona",
    "PersonaInterest",
    "PERSONA_TYPES",
    "PERSONA_TYPE_SELF",
    "PERSONA_TYPE_SPOUSE",
    "PERSONA_TYPE_DEPENDANT_1",
    "PERSONA_TYPE_DEPENDANT_2",
    "PERSONA_TYPE_PARENT_1",
    "PERSONA_TYPE_PARENT_2",
    "LOCKED_PERSONA_TYPES",
    "ensure_self_persona",
    "current_persona",
]
