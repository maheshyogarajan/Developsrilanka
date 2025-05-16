# CSRF Hardening Changelog

## Summary
This update implements comprehensive Cross-Site Request Forgery (CSRF) protection measures across the application. The changes follow security best practices to protect users from CSRF attacks by enforcing the use of CSRF tokens for all state-changing operations and converting GET requests that modify data to POST requests with proper token verification.

## Key Changes

### Core Framework Changes
- Added `CSRF_HARDENING_ENABLED` feature flag to control the enhanced CSRF protection
  - Set via environment variable or .env file
  - Controls whether state-changing GET routes are disabled
  - Allows toggling CSRF protection for compatibility testing
- Implemented secure cookie settings (when CSRF hardening is enabled):
  - `SESSION_COOKIE_SECURE = True`: Ensures cookies are only sent over HTTPS
  - `SESSION_COOKIE_HTTPONLY = True`: Prevents JavaScript from accessing cookies
  - `SESSION_COOKIE_SAMESITE = 'Lax'`: Restricts cookie sending to same-site contexts

### JavaScript Enhancements
- Created a CSRF token interceptor for fetch requests (`static/js/fix-csrf.js`)
- Added automatic CSRF token inclusion in all fetch API calls
- Implemented a mechanism to intercept and transform destructive GET links to POST forms

### Route Improvements
1. **Authentication Routes**:
   - Modified logout route to use POST instead of GET
   - Added CSRF token verification to all login and registration forms

2. **Organization Management**:
   - Updated organization invitation routes with proper CSRF protection
   - Added confirmation page with CSRF token for invitation actions
   - Modified invitation cancellation to use POST method instead of GET

3. **User Invitations**:
   - Implemented two-step process for friend invitation acceptance
   - Added `confirm_friend_invitation.html` page with CSRF-protected form
   - Updated invitation acceptance route to handle both GET (confirmation) and POST (processing)

### Testing
- Added `test_csrf.py` with comprehensive tests for CSRF protection:
  - Testing CSRF token generation and validation
  - Verifying that POST requests without CSRF tokens are rejected
  - Confirming that state-changing operations require proper tokens
  - Testing API routes for CSRF protection
  - Added feature flag-aware test mechanisms to handle both enabled and disabled states
- Implemented conditional assertions based on the CSRF hardening flag status:
  - When enabled: GET state-changing routes should return 405 Method Not Allowed
  - When disabled: GET state-changing routes maintain backward compatibility

## Security Benefits
- Protection against CSRF attacks that could force users to perform unwanted actions
- Reduced risk of attackers exploiting authenticated sessions
- Improved defense against clickjacking and cross-site scripting
- Compliance with modern web security best practices

## Implementation Details
The changes follow the principle of requiring explicit user actions (clicking a form button) for all state-changing operations, ensuring proper CSRF token validation for each request, and implementing secure cookie policies to prevent token theft.

## Migration Notes
All destructive links in client-side JavaScript that were previously using GET methods should be updated to use POST methods with CSRF tokens. The JavaScript shim (`fix-csrf.js`) helps with automatic conversion for most cases, but custom implementations may need manual updates.

## Configuration Options
- Environment Variable: `CSRF_HARDENING_ENABLED` (default: true)
  - When set to `true`: Enhanced CSRF protection is enabled
  - When set to `false`: Legacy behavior maintained for backward compatibility
  - Usage: Add to environment or .env file: `CSRF_HARDENING_ENABLED=false`
  
- The feature flag system can be used to temporarily disable CSRF hardening for testing or to resolve compatibility issues, but it is recommended to keep CSRF hardening enabled for production environments.