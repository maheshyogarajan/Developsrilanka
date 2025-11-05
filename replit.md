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