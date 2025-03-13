# Architecture Overview

## Overview

The Receipt Scanner application is a web-based system designed to help users in Sri Lanka scan and process receipts, manage invoices, and track finances. The application features user authentication, receipt processing with AI integration, bank account management, invoice generation, and administrative analytics.

The system follows a monolithic architecture pattern built on Flask, with PostgreSQL for data storage, and integrates with Google's Generative AI for receipt processing. It utilizes Celery for asynchronous task processing, making it suitable for handling computationally intensive operations like image processing.

## System Architecture

### High-Level Architecture

The application follows a traditional three-tier architecture:

1. **Presentation Layer**: Flask templates for rendering HTML pages
2. **Application Layer**: Flask routes and Python business logic
3. **Data Layer**: PostgreSQL database accessed via SQLAlchemy ORM

### Core Components

- **Web Server**: Flask application served via Gunicorn
- **Database**: PostgreSQL for persistent data storage
- **ORM**: SQLAlchemy for database interactions
- **Authentication**: Flask-Login with OAuth integration (Google, Facebook)
- **Asynchronous Processing**: Celery with Redis/SQLite as broker
- **Image Processing**: PIL (Python Imaging Library) for image manipulation
- **AI Integration**: Google's Generative AI for receipt analysis

## Key Components

### Backend Components

1. **Core Application (`app.py`)**
   - Flask application initialization
   - Database connection setup
   - Mail server configuration
   - Authentication initialization

2. **Models**
   - User authentication and profile data
   - Receipt and receipt items
   - Bank accounts
   - Invoices and payments
   - Income tracking

3. **Route Modules**
   - Admin routes for administrative functions
   - Invoice routes for invoice management
   - Bank account routes for managing financial accounts

4. **Image Processing (`image_processor.py`)**
   - Image upload and storage
   - Asynchronous processing via Celery
   - Integration with Google's Generative AI

5. **Admin Analytics (`admin_analytics.py`)**
   - Performance-optimized database queries
   - Caching mechanisms for frequently accessed data
   - Statistical analysis of application data

6. **Security (`decorators.py`)**
   - Role-based access control
   - Admin-specific route protection

### Authentication System

The application uses Flask-Login for session management with multiple authentication methods:

1. **Local Authentication**: Username/password login
2. **OAuth Integration**: Google and Facebook login options
3. **Role-Based Access Control**: Admin and user role separation

### Database Schema

The primary data models include:

1. **User**: Account information, authentication details, and role
2. **Receipt**: Scanned receipt metadata and processing results
3. **ReceiptItem**: Individual line items from receipts
4. **BankAccount**: User's bank account information
5. **Invoice**: Generated invoices with status tracking
6. **InvoiceItem**: Line items for invoices
7. **Payment**: Payment records for invoices
8. **UserIncome**: Income tracking for users

## Data Flow

### Receipt Processing Flow

1. User uploads receipt image
2. Image is stored temporarily
3. If async processing is enabled:
   - Task is queued in Celery
   - User receives notification when processing completes
4. If sync processing:
   - Image is processed immediately
5. Google's Generative AI extracts information from the receipt
6. Extracted data is stored in the database
7. User is presented with the processed receipt data for verification

### Invoice Generation Flow

1. User selects a client and creates invoice
2. User adds line items to the invoice
3. System calculates totals, taxes, and due dates
4. Invoice is stored in the database
5. User can optionally send invoice via email
6. User can track payment status and update as needed

### Admin Analytics Flow

1. Admin logs into the system
2. System loads pre-computed or cached analytics data
3. Dashboard displays key metrics and trends
4. Admin can generate reports and export data

## External Dependencies

### Google Generative AI
- Used for receipt image analysis and data extraction
- Requires API key configuration

### OAuth Providers
- Google OAuth for authentication
- Facebook OAuth for authentication
- Requires client ID and secret configuration

### Email Service
- Gmail SMTP for sending invoices and notifications
- Requires Gmail username and app password

### Celery Task Queue
- Used for asynchronous processing
- Primary broker: Redis (with SQLite fallback)
- Task monitoring and management

## Deployment Strategy

The application supports multiple deployment methods:

### Replit Deployment
- Built-in deployment from Replit environment
- Configured via `.replit` file
- Supports automatic deployment on commits

### Traditional Server Deployment
- Requires Python 3.9+ and PostgreSQL 14+
- Served via Gunicorn WSGI server
- Environment variables for configuration

### Environment Configuration
Key environment variables:
- `FLASK_ENV`: Application environment
- `DATABASE_URL`: PostgreSQL connection string
- `GEMINI_API_KEY`: Google Generative AI API key
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: OAuth credentials
- `FACEBOOK_CLIENT_ID` and `FACEBOOK_CLIENT_SECRET`: OAuth credentials
- `SESSION_SECRET`: Security key for sessions
- `ENABLE_ASYNC_PROCESSING`: Toggle for async processing

### DNS Configuration
- Custom domain setup for developsrilanka.com
- A records or CNAME records depending on hosting

## Development Practices

### Caching Strategy
- LRU cache with timeout for admin analytics
- Multi-level caching based on data volatility
- Cache durations: short (5 min), medium (30 min), long (3 hours)

### Security Considerations
- CSRF protection
- Secure session management
- Role-based access control
- Environment-specific configuration

### Error Handling
- Structured error logging
- User-friendly error messages
- Separate error handling for API vs UI responses