# Overview

This is a comprehensive financial management application designed for Sri Lankan businesses and individuals. It automates financial tracking through AI-powered receipt scanning and bank statement analysis, manages expenses, invoices, and client billing, and offers multi-organization support with robust access control. The platform aims to streamline financial operations, provide tax optimization features, and facilitate accurate financial reconciliation, empowering users with better control over their finances.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Core Technology Stack

The application is built on a Flask (Python) backend using SQLAlchemy ORM with a PostgreSQL database. It integrates Google Gemini for AI-powered OCR, supports multi-provider authentication (email/password, Google, Facebook), and uses Celery with Redis for asynchronous task processing. File storage is handled with a dual strategy supporting local filesystems and AWS S3. The frontend utilizes Jinja2 for templating, Bootstrap for UI, and jQuery for interactivity.

## Key Architectural Decisions

### Multi-Organization Model

The system supports multiple organizations per user, each with distinct financial data and role-based access control (Owner, Admin, Member, Viewer). Financial records are linked to specific organizations, and users can set a default organization for streamlined workflows.

### Dual Storage Strategy

An abstraction layer allows flexible storage of images and documents on either the local filesystem or AWS S3, with automatic fallback mechanisms. Thumbnail generation is implemented for performance optimization.

### Financial Transaction Unified Model

A polymorphic `FinancialTransaction` model unifies data from various sources (receipts, bank statements, manual entries) for consistent tracking, reconciliation, and reporting.

### Receipt Processing Pipeline

Receipt processing is asynchronous via Celery to prevent UI blocking. The pipeline involves upload, storage, OCR task queuing, background processing, database updates, and user notification, with error handling for failed extractions.

### Gemini API Reliability Architecture

The Gemini integration is designed for reliability with structured outputs (Pydantic schemas), comprehensive timeout and budget management, intelligent retry strategies with exponential backoff, and a model fallback chain (`gemini-2.5-flash` → `gemini-2.5-flash-lite`). NOTE: `gemini-2.0-flash` and `gemini-2.0-flash-lite` are deprecated and shut down June 1, 2026 — removed from the chain. A circuit breaker prevents cascading failures, and detailed error categorization with user-friendly messages enhances the user experience.

### Pluggable OCR Provider (Gemini ↔ GLM-OCR)

Receipt OCR is behind a provider abstraction (`ocr_providers.py`). Two providers are supported: `gemini` (legacy single-call Gemini Vision, default) and `glm` (two-stage pipeline). The `glm` provider runs **Stage A** = GLM-OCR (Z.ai) for the expensive vision extraction via `glm_ocr_client.py`, then **Stage B** = a Gemini reasoner (`gemini_reasoner.py`, default `gemini-3-flash-preview`, configurable via `REASONER_MODEL`) for Sri Lankan IRA-2017 tax-deductibility classification and IFRS expense-category assignment on the already-extracted text.

Both providers return data conforming to the existing `Receipt` Pydantic schema. Stage A output is validated through the `StageARawReceipt` Pydantic schema (with one strict-prompt retry on validation failure) before being passed to Stage B. Stage B output is validated against the full `Receipt` schema, and any model whose output fails validation is skipped in favour of the next fallback model; if every reasoner model fails, the local `sri_lanka_tax_rules.py` engine plus a vendor/item keyword-based category inference produces a complete, schema-valid receipt so saves never block.

Reliability: Stage A is wrapped in a dedicated `glm_circuit_breaker` (5 failures / 5 minutes), with model fallback `glm-4.5v → glm-4v-plus → glm-4v`. Stage B reuses the shared `gemini_circuit_breaker` plus a text-only equivalent of `generate_with_retry` (timeout, exponential backoff on 429, fallback-on-overload). Per-`Receipt` audit logging uses the stable identifier `extraction_model="glm-ocr"`; the concrete underlying model name (e.g. `glm-4.5v`) is recorded on the activity-log entry for cost attribution.

**Provider rollout (no redeploy required):**
- Globally: set `OCR_PROVIDER=glm` (or `gemini`) in environment secrets.
- Per organization: set the `Organization.ocr_provider` column for that org. The dispatcher checks the per-org column first and falls back to the global env var. SQL one-liner to flip a single org:
  ```sql
  UPDATE organization SET ocr_provider = 'glm' WHERE id = <org_id>;
  ```
  Set the column back to `NULL` to fall back to the global default.

`compare_ocr_providers.py` scores each provider's factual extraction against the saved Receipt row (treated as user-corrected ground truth) and reports per-provider field accuracy plus an end-to-end per-call cost estimate (Gemini = single OCR call; GLM = Stage A vision OCR + Stage B reasoner) so the savings claim can be verified against real data before flipping the default.

### Bank Statement Processing

A multi-stage parsing system extracts structured transaction data from PDF bank statements, including text extraction, account detection, transaction pattern matching, type classification, and confidence scoring, supported by organization-specific validation rules.

### Chart of Accounts Integration

The system includes an `Account` model for hierarchical chart of accounts integration, automatically mapping receipt categories to standardized account codes for professional accounting and automated journal entry creation.

### CSRF Hardening with Feature Flags

Enhanced CSRF protection is implemented using Flask-WTF, with a feature flag system to allow for controlled rollout of security improvements.

### Audit Trail System

A comprehensive `AuditLog` table tracks all data changes and user actions, including extractions, validations, and reconciliations, storing metadata such as changed fields, confidence scores, and IP addresses for compliance.

### Unified Admin Dashboard

A consolidated admin panel provides real-time analytics, user management, receipt oversight, and performance metrics for the Gemini API and user engagement. It includes activity logging for security auditing.

### Anti-Bot & Email Verification Security

Multi-layered security includes mandatory email verification for new users, registration rate limiting (3 attempts per IP per hour), and admin tools for identifying and cleaning up unverified or inactive accounts.

### Mandatory Onboarding Flow

A two-stage onboarding process ensures users set up both a "Personal Finances" organization and a business-specific organization after email verification, blocking access to main features until onboarding is complete.

# External Dependencies

## Third-Party APIs

- **Google Gemini API**: AI-powered OCR for receipt and document text extraction; also the Stage B reasoner for tax classification when `OCR_PROVIDER=glm`. Requires `GEMINI_API_KEY`.
- **Z.ai (Zhipu) GLM-OCR**: Cheap, accurate vision OCR used as Stage A when `OCR_PROVIDER=glm`. Requires `ZHIPU_API_KEY`. Configurable via `GLM_OCR_MODEL`, `ZAI_BASE_URL`.
- **Google OAuth**: Social login integration.
- **Facebook OAuth**: Social login integration.

## Cloud Services

- **AWS S3**: Optional cloud storage for images and documents.
- **Redis**: Optional task queue broker for Celery.

## Email Service

- **Flask-Mail**: Email delivery for user verification, invitations, and notifications.

## Python Libraries

- **Web Framework**: Flask, Flask-Login, Flask-SQLAlchemy, Flask-WTF, Flask-Mail.
- **Database**: SQLAlchemy, psycopg2.
- **Task Queue**: Celery.
- **Image Processing**: Pillow (PIL).
- **PDF Processing**: PyPDF2.
- **OAuth**: Authlib.
- **AI/ML**: google-generativeai.
- **Data Processing**: pandas.
- **Security**: itsdangerous, werkzeug.