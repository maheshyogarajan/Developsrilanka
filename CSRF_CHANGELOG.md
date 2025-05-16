# CSRF Hardening Changelog

## Version 1.0.0 - May 16, 2025

### Added
- Feature flag system for toggling CSRF hardening via `CSRF_HARDENING_ENABLED` environment variable
- Secure cookie settings (Secure, HttpOnly, SameSite=Lax) when the feature flag is enabled
- JavaScript shim for automatic conversion of GET links to POST forms with CSRF tokens
- POST-based confirmation pages for friend invitation acceptance
- POST-based confirmation pages for organization invitation acceptance/cancellation
- Additional CSRF token injection in all forms throughout the application
- Comprehensive test suite with feature-flag-aware testing
- Cookie security settings verification tests

### Changed
- User logout: GET → POST with CSRF token
- Friend invitation cancellation: GET → POST with CSRF token
- Organization invitation cancellation: GET → POST with CSRF token
- Converted various state-changing GET operations to POST with proper CSRF protection
- Enhanced error handling to return 405 Method Not Allowed for state-changing GET requests

### Security
- Added strict CSRF token validation for all state-changing operations
- Implemented token regeneration on authentication boundaries
- Added robust error handling for missing or invalid CSRF tokens
- Enhanced cookie security with HTTP-only, Secure flags, and SameSite=Lax setting

## Backward Compatibility
- Legacy behavior maintained through feature flag (set `CSRF_HARDENING_ENABLED=false` to disable enhanced protections)
- All tests pass in both enabled and disabled modes
- Frontend JavaScript shim ensures backward compatibility for most use cases