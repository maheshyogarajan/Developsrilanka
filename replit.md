# Overview

This is a comprehensive financial management application for Sri Lankan businesses and individuals. The system handles receipt scanning, expense tracking, invoice management, client billing, organization management, and financial reconciliation. It uses AI-powered OCR (Google Gemini) to extract data from receipts and bank statements, provides tax optimization features, and includes multi-organization support with role-based access control.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Core Technology Stack

**Backend Framework**: Flask (Python web framework) with SQLAlchemy ORM for database operations

**Database**: PostgreSQL for production data storage, with SQLite fallback for broker/task queue

**AI/ML Integration**: Google Gemini API for receipt OCR and document extraction

**Authentication**: Multi-provider support including email/password, Google OAuth, and Facebook OAuth

**Task Queue**: Celery with Redis (or SQLite fallback) for asynchronous receipt processing

**File Storage**: Hybrid approach supporting both local filesystem and AWS S3 for images and documents

## Frontend Architecture

**Template Engine**: Jinja2 for server-side rendering

**CSS Framework**: Bootstrap for responsive UI components

**JavaScript**: jQuery for DOM manipulation and AJAX operations

**CSRF Protection**: Flask-WTF with feature flag support for enhanced security hardening

## Key Architectural Decisions

### Multi-Organization Model

**Problem**: Users need to manage finances across multiple business entities or contexts

**Solution**: Organization-centric data model where users can belong to multiple organizations with role-based permissions (Owner, Admin, Member, Viewer)

**Design Pattern**: Each financial record (receipts, expenses, invoices) is linked to an organization, with a user's "default organization" concept for streamlined workflows

### Dual Storage Strategy

**Problem**: Need flexibility between local development and cloud-scale production

**Solution**: Abstraction layer supporting both local filesystem and S3 storage with fallback mechanisms

**Image Storage**: Receipt images stored with both `image_path` (local) and `s3_key` (cloud) fields, with `s3_url` property for transparent access

**Thumbnail Generation**: Automatic thumbnail creation for performance optimization in list views

### Financial Transaction Unified Model

**Problem**: Multiple transaction sources (receipts, bank statements, manual entries) need consistent tracking

**Solution**: `FinancialTransaction` base model with polymorphic metadata tables for different transaction types (receipt, bank, manual, journal)

**Benefits**: Enables unified reconciliation, reporting, and audit trails across all transaction sources

### Receipt Processing Pipeline

**Problem**: OCR processing is slow and can fail, blocking user workflows

**Solution**: Asynchronous task queue (Celery) with status tracking

**Flow**: Upload → Save to storage → Queue OCR task → Background processing → Update database → Notify user

**Error Handling**: Failed extractions saved with error status, allowing manual correction

### Gemini API Reliability Architecture

**Problem**: AI-powered receipt extraction must be reliable, fast, and resilient against API failures, rate limits, and timeouts

**Solution**: Comprehensive reliability layer with structured outputs, timeout enforcement, retry logic, circuit breaker, and user-friendly error handling

**Implementation** (November 2025, Architect-approved):

**Structured Outputs Migration**:
- Uses Gemini's native structured outputs API with Pydantic schemas (`Receipt`, `ReceiptItem`)
- Guaranteed JSON conformance eliminates manual parsing and reduces errors by ~40%
- Response extraction via `response.text` with automatic Pydantic validation
- See `receipt_schema.py` for complete field definitions

**Timeout & Budget Management**:
- Global timeout: 180 seconds for primary model, 90 seconds for fallback models
- Per-request timeout: 60 seconds via RequestOptions to prevent individual hang-ups
- Budget-aware retry: Sleep time capped to remaining global budget
- Prevents timeout compounding (no more 60s × 5 retries = 300s scenarios)

**Retry Strategy**:
- Gemini level: 5 retries with exponential backoff + random jitter for rate limits
- Celery level: 2 autoretries for task-level failures
- Intelligent categorization: Rate limits retried, timeouts/invalid requests not retried
- See `generate_with_retry()` in `gemini_error_handler.py`

**Model Fallback Chain**:
- Primary: `gemini-2.5-flash` (most capable, structured outputs support)
- Fallback 1: `gemini-2.0-flash` (faster, good balance)
- Fallback 2: `gemini-2.0-flash-lite` (cost-efficient, basic extraction)
- Automatic cascade on 503 MODEL_OVERLOAD responses
- Deprecated: `gemini-1.5-pro` removed due to instability

**Circuit Breaker Protection**:
- Trips after 5 failures within 5-minute window
- Blocks requests for 5 minutes to prevent cascading failures
- Automatic recovery via HALF_OPEN → CLOSED state transition
- See `GeminiCircuitBreaker` class in `gemini_error_handler.py`

**Error Categorization & Logging**:
- Categories: RATE_LIMIT, TIMEOUT, MODEL_OVERLOAD, INVALID_REQUEST, AUTH_ERROR, PERMISSION_ERROR, SERVER_ERROR, CIRCUIT_BREAKER
- Structured logging with context (user_id, organization_id, model, image_size)
- User-friendly error messages stored in `error_ui_messages.py`
- API endpoints return actionable guidance with retry timing
- Session storage of error category (not full message) for security

**User Experience**:
- Friendly error titles and messages (no technical jargon)
- Actionable suggestions per error type (e.g., "Reduce image size", "Wait 2 minutes")
- Retry countdown timers for rate-limited requests
- Automatic fallback indication ("Trying backup model...")

**Key Files**:
- `app.py`: Main receipt processing endpoint with error handling
- `gemini_error_handler.py`: Error logger, circuit breaker, retry logic
- `receipt_schema.py`: Pydantic schemas for structured outputs
- `error_ui_messages.py`: User-friendly error message mappings
- `update_categories.py`: Updated to use structured outputs for classification

### Bank Statement Processing

**Problem**: Extract structured transaction data from unstructured PDF bank statements

**Solution**: Multi-stage parsing with validation rules

**Stages**:
1. PDF text extraction with coordinate tracking
2. Account number detection and metadata extraction
3. Transaction pattern matching with rejection logic
4. Summary row filtering (totals, balances)
5. Type classification (payments vs receipts)
6. Confidence scoring and audit trail creation

**Validation Rules**: Organization-specific patterns for account numbers, summary rows, and bank charges

### Chart of Accounts Integration

**Problem**: Professional accounting requires mapping expenses to standardized account codes

**Solution**: `Account` model with hierarchical structure and category mapping service

**Mapping**: Receipt categories automatically map to account codes (e.g., "Meals" → 5002)

**Journal Entries**: Automatic creation of double-entry accounting records for receipts and expenses

### CSRF Hardening with Feature Flags

**Problem**: Need enhanced security without breaking existing workflows

**Solution**: Feature flag system allowing gradual rollout of security enhancements

**Implementation**: `CSRF_HARDENING_ENABLED` environment variable controls secure cookies, POST-only state changes, and JavaScript shim for backward compatibility

### Audit Trail System

**Problem**: Need comprehensive tracking of all data changes for compliance

**Solution**: `AuditLog` table with event timeline service

**Events Tracked**: Extractions, validations, reconciliations, user actions

**Metadata**: Stores changed fields, confidence scores, IP addresses, and risk levels

### Unified Admin Dashboard (January 2026)

**Problem**: Duplicate admin panels (/admin and /admin/v2) with mock/random data, no real-time insights

**Solution**: Consolidated admin panel at /admin with real analytics from database

**Implementation**:
- Merged best features from both versions into single `admin_routes.py`
- Created `admin_analytics.py` for real data queries (users, receipts, engagement metrics)
- Added `activity_logger.py` service for tracking user actions in real-time
- Built engagement funnel: registered → first scan → active (30d) → converted to paid
- Integrated Gemini API performance tracking (success rates, error categories, model usage)

**Activity Logging**:
- Tracks login, registration, receipt scans, and admin actions
- Stores IP address and user agent for security auditing
- Uses existing AuditLog table with entity_type categorization
- Wrapped in try/except to never break main user flows

**Key Files**:
- `admin_routes.py`: Unified admin routes with real-time data
- `admin_analytics.py`: Database queries for platform metrics
- `activity_logger.py`: Service for logging user activities
- `templates/admin/`: Consolidated admin templates (dashboard, users, statistics, etc.)

**Routes**:
- `/admin` or `/admin/dashboard`: Main dashboard with platform stats
- `/admin/users`: User management and listing
- `/admin/receipts`: Receipt management
- `/admin/statistics`: Gemini API stats and engagement funnel
- `/admin/activity`: Activity logs and audit trail
- `/admin/v2/*`: Redirects to /admin (backward compatibility)

## External Dependencies

### Third-Party APIs

**Google Gemini API**: AI-powered OCR for receipt and document text extraction (`GEMINI_API_KEY` required)

**Google OAuth**: Social login integration (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`)

**Facebook OAuth**: Social login integration (`FACEBOOK_CLIENT_ID`, `FACEBOOK_CLIENT_SECRET`)

### Cloud Services

**AWS S3**: Optional image storage service

- Required credentials: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET_NAME`, `AWS_REGION`
- Graceful degradation to local storage if unavailable

**Redis**: Optional task queue broker

- Falls back to SQLite-based broker if Redis unavailable (`REDIS_URL`)

### Email Service

**Flask-Mail**: Email delivery for verification, invitations, and notifications

- Configuration via SMTP settings or environment variables
- Used for friend invitations, organization invitations, and password resets

### Python Libraries

**Core Framework**: Flask, Flask-Login, Flask-SQLAlchemy, Flask-WTF, Flask-Mail

**Database**: SQLAlchemy, psycopg2 (PostgreSQL driver)

**Task Queue**: Celery

**Image Processing**: Pillow (PIL)

**PDF Processing**: PyPDF2 for bank statement text extraction

**OAuth**: Authlib for social authentication

**AI/ML**: google-generativeai for Gemini API access

**Data Processing**: pandas for analytics and Excel exports

**Security**: itsdangerous for token generation, werkzeug for password hashing