"""fiesta.profile - S3 progressive-disclosure customer profile (Wave 3).

Per FIESTA v1.1 methodology + council brief 2026-05-19:
- Minimal friction: ask only what each downstream screen needs
- Progressive disclosure: capture at first use, not at signup
- Empowerment voice ("Help us help you") not corporate-form voice
- Persona locked to 'sl_foreign_income' in v1 (single-persona platform)

Public surface:
- routes.bp                          : Flask blueprint mounted at /fiesta/profile
- models.FiestaProfile               : SQLAlchemy model
- progressive.required_for_screen    : returns missing required fields for a screen
- progressive.progress_pct           : completion percentage (0-100)
- validators                         : NIC / TIN / address validation

Mount point: app.register_blueprint(bp, url_prefix='/fiesta/profile')
"""

from .progressive import (  # noqa: F401
    required_for_screen,
    progress_pct,
    SCREEN_REQUIREMENTS,
    ALL_PROFILE_FIELDS,
    REQUIRED_BASE_FIELDS,
)
from .validators import (  # noqa: F401
    validate_nic,
    validate_tin,
    validate_address,
    NICValidationError,
    TINValidationError,
    AddressValidationError,
)

__all__ = [
    "required_for_screen",
    "progress_pct",
    "SCREEN_REQUIREMENTS",
    "ALL_PROFILE_FIELDS",
    "REQUIRED_BASE_FIELDS",
    "validate_nic",
    "validate_tin",
    "validate_address",
    "NICValidationError",
    "TINValidationError",
    "AddressValidationError",
]
