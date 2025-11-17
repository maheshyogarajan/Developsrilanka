import os
import json
import base64
import logging
import time
import traceback  # Added import
import uuid
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, flash, redirect, url_for, send_file, g
import google.generativeai as genai
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text  # Add SQLAlchemy text support for direct queries
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from itsdangerous import SignatureExpired, BadSignature
from authlib.integrations.flask_client import OAuth
import requests
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Import Celery task queue components
from celery import Celery
from celery.result import AsyncResult

# Import Gemini error handling utilities
from gemini_error_handler import (
    GeminiErrorLogger,
    gemini_circuit_breaker,
    generate_with_retry
)

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Database setup
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Initialize Flask app
app = Flask(__name__)

# Configure template auto-reload and disable caching
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Import feature flags module
from feature_flags import is_feature_enabled

# CSRF Hardening Configuration
CSRF_HARDENING_ENABLED = is_feature_enabled("CSRF_HARDENING_ENABLED")
logging.info(f"CSRF HARDENING {'ACTIVE' if CSRF_HARDENING_ENABLED else 'DISABLED'}")

if CSRF_HARDENING_ENABLED:
    app.config.update(
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_METHODS={'POST', 'PUT', 'PATCH', 'DELETE'},
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax'
    )

# Initialize CSRF Protection
csrf = CSRFProtect(app)

# Initialize Flask-Mail
mail = Mail()

# Configure Flask app for sending emails
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = os.environ.get('GMAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('GMAIL_APP_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = ('DevelopSriLanka.com', os.environ.get('GMAIL_USERNAME'))
app.config['MAIL_MAX_EMAILS'] = 5
app.config['MAIL_DEBUG'] = True
mail.init_app(app)

# Log email configuration for debugging
logging.info(f"Mail configuration: Server={app.config['MAIL_SERVER']}, Port={app.config['MAIL_PORT']}")
logging.info(f"Mail username configured: {'Yes' if app.config['MAIL_USERNAME'] else 'No'}")
logging.info(f"Mail password configured: {'Yes' if app.config['MAIL_PASSWORD'] else 'No'}")

# Production error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', 
                           error_code=404, 
                           error_message="The page you're looking for doesn't exist."), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('error.html', 
                           error_code=500, 
                           error_message="Something went wrong on our end. We're working to fix it."), 500
app.secret_key = os.environ.get("SESSION_SECRET", "dev_secret_key")

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# Flask-Login user loader
@login_manager.user_loader
def load_user(user_id):
    # Import here to avoid circular imports
    from models import User, FriendInvitation
    
    try:
        # Attempt to get the user
        return User.query.get(int(user_id))
    except Exception as e:
        # Log the error
        import logging
        logging.error(f"Error loading user {user_id}: {str(e)}")
        
        # Try to recover from a transaction error
        try:
            from sqlalchemy import text
            # Close the current session
            db.session.rollback()
            db.session.close()
            # Reconnect and try a simple query
            db.session.execute(text("SELECT 1"))
            # Try again to get the user after recovery
            return User.query.get(int(user_id))
        except Exception as recovery_error:
            logging.error(f"Failed to recover DB connection: {str(recovery_error)}")
            # Return None instead of raising an exception
            return None

# Add CSRF token to all templates
@app.context_processor
def inject_csrf_token():
    """Add CSRF token to all templates."""
    from template_utils import get_csrf_token
    return dict(csrf_token=get_csrf_token)

# Setup OAuth
oauth = OAuth(app)

# Configure OAuth providers
google = oauth.register(
    name='google',
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

facebook = oauth.register(
    name='facebook',
    client_id=os.environ.get("FACEBOOK_CLIENT_ID"),
    client_secret=os.environ.get("FACEBOOK_CLIENT_SECRET"),
    access_token_url='https://graph.facebook.com/oauth/access_token',
    access_token_params=None,
    authorize_url='https://www.facebook.com/dialog/oauth',
    authorize_params=None,
    api_base_url='https://graph.facebook.com/',
    client_kwargs={'scope': 'email'},
)

# Configure SQLAlchemy - using DATABASE_URL environment variable
database_url = os.environ.get("DATABASE_URL")
logging.debug(f"Database URL (masked): {database_url[:15]}...{database_url[-15:] if database_url else None}")

if database_url:
    # Fix potential "postgres://" vs "postgresql://" issue
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    logging.info("Database configuration successful")
else:
    logging.error("DATABASE_URL environment variable not found!")

# Initialize the database with the app
db.init_app(app)

# Configure Gemini API
try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
except Exception as e:
    logging.error(f"Error configuring Gemini API: {str(e)}")
    
# Configure Celery with our application
# If redis is not available, use SQLite as a broker
broker_url = os.environ.get('REDIS_URL', 'sqla+sqlite:///celery.db')
result_backend = os.environ.get('REDIS_URL', 'db+sqlite:///celery-results.db')

# Create a Celery instance
celery = Celery(
    'develop_sri_lanka',
    broker=broker_url,
    backend=result_backend
)

# Import Celery tasks
try:
    from image_processor import process_receipt_image, save_receipt_to_database
    logging.info("Successfully imported Celery image processing tasks")
except ImportError as e:
    logging.error(f"Error importing image processing tasks: {str(e)}")

# Receipt data schema for extraction
RECEIPT_FIELDS = [
    "vendor_name",
    "vendor_address",
    "vendor_contact",
    "date",
    "items",
    "total_amount",
    "service_charge",
    "vat_registration_number",
    "sscl_tax",
    "vat_tax",
    "expense_major_category",
    "expense_minor_category"
]

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring the application status."""
    try:
        # Check database connection
        db.session.execute(text('SELECT 1'))
        
        # Check Gemini API configuration
        gemini_configured = bool(os.environ.get("GEMINI_API_KEY"))
        
        # Return health status
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat(),
            'database': 'connected',
            'gemini_api': 'configured' if gemini_configured else 'not_configured',
            'version': os.environ.get('APP_VERSION', '1.0.0')
        })
    except Exception as e:
        logging.error(f"Health check failed: {str(e)}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        }), 500

@app.route('/db-recovery')
def db_recovery():
    """
    Special recovery route to reset database connections.
    This is used to recover from database transaction errors.
    """
    from reset_db_transaction import reset_db_session, hard_reset_connection
    
    success = False
    try:
        # Try normal reset first
        success = reset_db_session()
        
        # If that doesn't work, try hard reset
        if not success:
            success = hard_reset_connection()
            
        if success:
            flash("Database connection has been successfully reset.", "success")
        else:
            flash("Could not reset database connection. Please restart the application.", "warning")
            
    except Exception as e:
        flash(f"Error during recovery: {str(e)}", "danger")
    
    return redirect(url_for('home'))

@app.route('/')
def home():
    """Render the homepage with welcome message and features."""
    # Hard-coded statistics for dashboard
    receipt_stats = {
        'total': '112,000+',
        'active_invoices': '2,400+'
    }
    return render_template('home.html', stats=receipt_stats)

@app.route('/preview')
def preview():
    """Render the receipt preview page without login requirement."""
    return render_template('preview.html')

@app.route('/scan')
@login_required
def index():
    """Render the receipt scanning page of the application."""
    # Check if user has any organizations
    if not current_user.organizations:
        # Redirect to getting started if no organizations exist
        flash("Please set up an organization before scanning receipts.", "warning")
        return redirect(url_for('getting_started.wizard'))
    
    # Check if email is verified
    if hasattr(current_user, 'is_email_verified') and not current_user.is_email_verified:
        flash('Email verification is required before scanning receipts. Please check your inbox or request a new verification email.', 'warning')
        return redirect(url_for('verify_email_reminder'))
    
    return render_template('index.html')

@app.route('/history')
@login_required
def receipt_history():
    """Redirect to the unified receipt history page."""
    from flask import redirect, url_for
    return redirect(url_for('unified_view.history'))

@app.route('/analytics')
@login_required
def analytics():
    """Render the feature unavailable page for the removed analytics feature."""
    return render_template('feature_unavailable.html')

@app.route('/analytics/redesign')
@login_required
def analytics_redesign():
    """Render the feature unavailable page for the removed Financial Intelligence Center feature."""
    return render_template('feature_unavailable.html')
@app.route('/tax-savings', methods=['GET', 'POST'])
@login_required
def tax_savings():
    """Render the feature unavailable page for the removed tax savings feature."""
    return render_template('feature_unavailable.html')
    
    # Initialize variables
    income_details = None
    tax_savings_estimate = None
    total_tax_deductible = 0.0
    error = None
    
    try:
        # Get the user's income details if they exist
        income_details = UserIncome.query.filter_by(user_id=current_user.id).first()
        
        # Calculate total tax deductible expenses
        receipts = Receipt.query.filter_by(user_id=current_user.id).all()
        for receipt in receipts:
            tax_deductible = receipt.get_tax_deductible_amount()
            total_tax_deductible += min(tax_deductible, receipt.total_amount)
        
        if request.method == 'POST':
            # Process form submission
            # LKR Income
            employment_income = float(request.form.get('employment_income', 0) or 0)
            business_income = float(request.form.get('business_income', 0) or 0)
            investment_income = float(request.form.get('investment_income', 0) or 0)
            # USD Income
            usd_consulting_income = float(request.form.get('usd_consulting_income', 0) or 0)
            
            # Validate inputs
            if (employment_income < 0 or business_income < 0 or 
                investment_income < 0 or usd_consulting_income < 0):
                error = "Income values cannot be negative."
            else:
                # Save or update the income details
                if income_details:
                    # Update existing record
                    income_details.employment_income = employment_income
                    income_details.business_income = business_income
                    income_details.investment_income = investment_income
                    income_details.usd_consulting_income = usd_consulting_income
                    income_details.updated_at = datetime.utcnow()
                else:
                    # Create new record
                    income_details = UserIncome(
                        user_id=current_user.id,
                        employment_income=employment_income,
                        business_income=business_income,
                        investment_income=investment_income,
                        usd_consulting_income=usd_consulting_income
                    )
                    db.session.add(income_details)
                
                db.session.commit()
                
                # Calculate potential tax savings
                tax_savings_estimate = calculate_tax_savings_simplified(
                    employment_income,
                    business_income,
                    investment_income,
                    usd_consulting_income,
                    total_tax_deductible
                )
        
        elif income_details:
            # If GET request and income details exist, calculate based on existing data
            tax_savings_estimate = calculate_tax_savings_simplified(
                income_details.employment_income,
                income_details.business_income,
                income_details.investment_income,
                income_details.usd_consulting_income,
                total_tax_deductible
            )
        
        return render_template(
            'tax_savings.html',
            income_details=income_details,
            total_tax_deductible=total_tax_deductible,
            tax_savings_estimate=tax_savings_estimate,
            error=error
        )
    
    except Exception as e:
        logging.error(f"Error in tax savings calculator: {str(e)}")
        return render_template('tax_savings.html', error=str(e))

def calculate_tax_savings_simplified(employment_income, business_income, investment_income, 
                                   usd_consulting_income, tax_deductible_amount):
    """
    Calculate potential tax savings based on income types and tax deductible expenses.
    
    This is a simplified calculation based on the rules:
    - 36% tax rate for LKR business income (prioritized due to higher rate)
    - 15% tax rate for USD consulting income (secondary priority)
    - Tax deductible expenses are first applied to LKR business income
    - Any remaining deductible amount is then applied to USD consulting income
    
    Args:
        employment_income: LKR income from employment
        business_income: LKR income from business
        investment_income: LKR income from investments
        usd_consulting_income: USD income from consulting
        tax_deductible_amount: Total tax deductible expenses
    
    Returns:
        Dictionary with tax savings information
    """
    # Handle None values by converting to 0
    employment_income = float(employment_income or 0)
    business_income = float(business_income or 0)
    investment_income = float(investment_income or 0)
    usd_consulting_income = float(usd_consulting_income or 0)
    tax_deductible_amount = float(tax_deductible_amount or 0)
    
    # LKR Income calculations
    total_lkr_income = employment_income + business_income + investment_income
    
    # USD Income calculations
    total_usd_income = usd_consulting_income
    
    # Use simple fixed rates for tax savings calculations
    lkr_business_tax_rate = 0.36  # 36% tax rate for LKR business income
    usd_consulting_tax_rate = 0.15  # 15% tax rate for USD consulting income
    
    # Calculate maximum tax deductible amounts that can be applied
    max_lkr_deduction = business_income  # Only business income is eligible for tax deductions
    max_usd_deduction = usd_consulting_income  # All consulting income is eligible
    
    # Prioritize LKR business income for tax deductions (higher rate)
    tax_deductible_for_lkr = 0
    tax_deductible_for_usd = 0
    
    # Calculate deductions by prioritizing LKR business income first (higher tax rate)
    remaining_deductible = tax_deductible_amount
    
    # Step 1: Apply deductions to LKR business income first (36% rate)
    if business_income > 0 and remaining_deductible > 0:
        tax_deductible_for_lkr = min(remaining_deductible, max_lkr_deduction)
        remaining_deductible -= tax_deductible_for_lkr
    
    # Step 2: Apply any remaining deductions to USD consulting income (15% rate)
    if usd_consulting_income > 0 and remaining_deductible > 0:
        tax_deductible_for_usd = min(remaining_deductible, max_usd_deduction)
        remaining_deductible -= tax_deductible_for_usd
    
    # Calculate tax savings for each income stream
    lkr_tax_savings = tax_deductible_for_lkr * lkr_business_tax_rate
    usd_tax_savings = tax_deductible_for_usd * usd_consulting_tax_rate
    total_tax_savings = lkr_tax_savings + usd_tax_savings
    
    # For presentation, convert USD values to LKR using a fixed exchange rate
    usd_to_lkr_rate = 320  # 1 USD = 320 LKR (example rate)
    usd_income_in_lkr = total_usd_income * usd_to_lkr_rate
    usd_tax_savings_in_lkr = usd_tax_savings * usd_to_lkr_rate
    
    # Calculate allocation percentages for the progress bar
    lkr_allocation_percent = (tax_deductible_for_lkr / tax_deductible_amount * 100) if tax_deductible_amount > 0 else 0
    usd_allocation_percent = (tax_deductible_for_usd / tax_deductible_amount * 100) if tax_deductible_amount > 0 else 0
    
    # Return dictionary with calculated values
    return {
        # LKR Income
        'total_lkr_income': total_lkr_income,
        'employment_income': employment_income,
        'business_income': business_income,
        'investment_income': investment_income,
        'tax_deductible_for_lkr': tax_deductible_for_lkr,
        'lkr_tax_savings': lkr_tax_savings,
        'lkr_business_tax_rate': lkr_business_tax_rate,
        'lkr_allocation_percent': lkr_allocation_percent,
        
        # USD Income
        'total_usd_income': total_usd_income,
        'usd_consulting_income': usd_consulting_income,
        'tax_deductible_for_usd': tax_deductible_for_usd,
        'usd_tax_savings': usd_tax_savings,
        'usd_consulting_tax_rate': usd_consulting_tax_rate,
        'usd_to_lkr_rate': usd_to_lkr_rate,
        'usd_income_in_lkr': usd_income_in_lkr,
        'usd_tax_savings_in_lkr': usd_tax_savings_in_lkr,
        'usd_allocation_percent': usd_allocation_percent,
        
        # Totals
        'total_tax_deductible': tax_deductible_amount,
        'remaining_unused_deduction': remaining_deductible,
        'total_tax_savings': total_tax_savings,
        'total_tax_savings_in_lkr': lkr_tax_savings + usd_tax_savings_in_lkr
    }

@app.route('/receipts/by_major_category/<category>')
@login_required
def receipts_by_major_category(category):
    """Get receipts for a specific major expense category for the current user."""
    from models import Receipt
    
    try:
        # URL decode the category name if needed
        from urllib.parse import unquote
        category = unquote(category)
        
        # Query receipts for the specified category that belong to the current user
        receipts = Receipt.query.filter_by(
            expense_major_category=category,
            user_id=current_user.id
        ).order_by(Receipt.date.desc()).all()
        
        # Convert to list of dictionaries
        receipt_list = []
        for receipt in receipts:
            receipt_dict = {
                'id': receipt.id,
                'date': receipt.date.strftime('%Y-%m-%d') if receipt.date else 'Unknown',
                'vendor_name': receipt.vendor_name,
                'total_amount': receipt.total_amount,
                'expense_major_category': receipt.expense_major_category or 'Uncategorized',
                'expense_minor_category': receipt.expense_minor_category or 'Uncategorized'
            }
            receipt_list.append(receipt_dict)
        
        return jsonify({
            'success': True, 
            'category': category,
            'receipts': receipt_list
        })
    
    except Exception as e:
        logging.error(f"Error fetching receipts by major category: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/receipts/by_minor_category/<category>')
@login_required
def receipts_by_minor_category(category):
    """Get receipts for a specific minor expense category for the current user."""
    from models import Receipt
    
    try:
        # URL decode the category name if needed
        from urllib.parse import unquote
        category = unquote(category)
        
        # Query receipts for the specified subcategory that belong to the current user
        receipts = Receipt.query.filter_by(
            expense_minor_category=category,
            user_id=current_user.id
        ).order_by(Receipt.date.desc()).all()
        
        # Convert to list of dictionaries
        receipt_list = []
        for receipt in receipts:
            receipt_dict = {
                'id': receipt.id,
                'date': receipt.date.strftime('%Y-%m-%d') if receipt.date else 'Unknown',
                'vendor_name': receipt.vendor_name,
                'total_amount': receipt.total_amount,
                'expense_major_category': receipt.expense_major_category or 'Uncategorized',
                'expense_minor_category': receipt.expense_minor_category or 'Uncategorized'
            }
            receipt_list.append(receipt_dict)
        
        return jsonify({
            'success': True, 
            'category': category,
            'receipts': receipt_list
        })
    
    except Exception as e:
        logging.error(f"Error fetching receipts by minor category: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/preview/scan', methods=['POST'])
def preview_scan_receipt():
    """Process the uploaded receipt image and extract data using Gemini Vision for preview mode (no login required)."""
    if 'receipt' not in request.files:
        response = jsonify({'error': 'No receipt image uploaded'})
        response.headers.set('Content-Type', 'application/json')
        return response, 400
    
    receipt_file = request.files['receipt']
    if receipt_file.filename == '':
        response = jsonify({'error': 'No receipt image selected'})
        response.headers.set('Content-Type', 'application/json')
        return response, 400
    
    try:
        # Start timing for performance logging
        start_time = time.time()
        
        # Read and process the image
        img_bytes = receipt_file.read()
        
        # Ensure the image is in a format Gemini can process
        try:
            img = Image.open(BytesIO(img_bytes))
            
            # Force conversion to JPEG if it's not already
            if img.format != 'JPEG':
                logging.info(f"Converting image from {img.format} to JPEG for better compatibility")
                img_byte_arr = BytesIO()
                img = img.convert('RGB')  # Convert to RGB mode
                img.save(img_byte_arr, format='JPEG', quality=85)
                img_byte_arr.seek(0)
                img = Image.open(img_byte_arr)
        except Exception as img_error:
            logging.error(f"Error processing image: {str(img_error)}")
            response = jsonify({'error': f'Unable to process image format. Please try with a JPEG or PNG image. Details: {str(img_error)}'})
            response.headers.set('Content-Type', 'application/json')
            return response, 400
        
        # Always use synchronous processing for preview mode
        logging.info("Using synchronous receipt processing for preview mode")
        extracted_data = process_receipt_with_gemini(img)
        
        if not extracted_data:
            response = jsonify({'error': 'Failed to extract data from the receipt'})
            response.headers.set('Content-Type', 'application/json')
            return response, 500
            
        # For preview, save the image locally
        # Generate a unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_filename = f"preview_{timestamp}.jpg"
        upload_folder = os.path.join('static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        local_path = os.path.join(upload_folder, unique_filename)
        
        try:
            img.save(local_path)
            # Store the path relative to static folder
            extracted_data['image_path'] = os.path.join('uploads', unique_filename)
            logging.info(f"Preview image saved locally to {local_path}")
        except Exception as save_error:
            logging.error(f"Error saving preview image locally: {str(save_error)}")
        
        # Store the extracted data in session for potential correction
        session['receipt_data'] = extracted_data
        
        # Calculate processing time
        processing_time = time.time() - start_time
        logging.info(f"Preview receipt processing completed in {processing_time:.2f} seconds")
        
        response = jsonify({'success': True, 'data': extracted_data})
        response.headers.set('Content-Type', 'application/json')
        return response
    
    except Exception as e:
        logging.error(f"Error processing receipt in preview mode: {str(e)}")
        logging.error(traceback.format_exc())
        response = jsonify({'error': f'Error processing receipt: {str(e)}'})
        response.headers.set('Content-Type', 'application/json')
        return response, 500

@app.route('/scan', methods=['POST'])
@login_required
def scan_receipt():
    """Process the uploaded receipt image and extract data using Gemini Vision."""
    # Check if email is verified
    if hasattr(current_user, 'is_email_verified') and not current_user.is_email_verified:
        return jsonify({
            'error': 'Email verification is required before scanning receipts', 
            'redirect': url_for('verify_email_reminder')
        }), 403
    
    if 'receipt' not in request.files:
        response = jsonify({'error': 'No receipt image uploaded'})
        response.headers.set('Content-Type', 'application/json')
        return response, 400
    
    receipt_file = request.files['receipt']
    if receipt_file.filename == '':
        response = jsonify({'error': 'No receipt image selected'})
        response.headers.set('Content-Type', 'application/json')
        return response, 400
    
    try:
        # Start timing for performance logging
        start_time = time.time()
        
        # Read and process the image
        img_bytes = receipt_file.read()
        receipt_file.seek(0)  # Reset file pointer for later use
        
        # Ensure the image is in a format Gemini can process
        try:
            img = Image.open(BytesIO(img_bytes))
            
            # Force conversion to JPEG if it's not already
            if img.format != 'JPEG':
                logging.info(f"Converting image from {img.format} to JPEG for better compatibility")
                img_byte_arr = BytesIO()
                img = img.convert('RGB')  # Convert to RGB mode
                img.save(img_byte_arr, format='JPEG', quality=85)
                img_byte_arr.seek(0)
                img = Image.open(img_byte_arr)
                
            # Make a copy of the image for immediate S3 upload
            img_for_s3 = img.copy()
        except Exception as img_error:
            logging.error(f"Error processing image: {str(img_error)}")
            response = jsonify({'error': f'Unable to process image format. Please try with a JPEG or PNG image. Details: {str(img_error)}'})
            response.headers.set('Content-Type', 'application/json')
            return response, 400
            
        # Get the user's organization ID for S3 storage path
        organization_id = None
        if current_user.is_authenticated and hasattr(current_user, 'get_default_organization'):
            default_org = current_user.get_default_organization()
            if default_org:
                organization_id = default_org.id
                logging.info(f"Using organization_id={organization_id} for S3 storage path from organization {default_org.name}")
            else:
                logging.warning("User has no default organization")
        else:
            logging.warning("User has no get_default_organization method")
            
        # IMMEDIATE S3 UPLOAD: Upload the image to S3 FIRST, before any processing
        # This ensures we don't lose the image even if processing fails
        try:
            from s3_direct_upload import process_receipt_image_upload
            upload_result = process_receipt_image_upload(img_for_s3, organization_id)
            
            if upload_result and upload_result['s3_key']:
                logging.info(f"✅ IMMEDIATE S3 UPLOAD SUCCESSFUL: {upload_result['s3_key']}")
                # Store both keys in session for database update later
                session['receipt_s3_key'] = upload_result['s3_key']
                session['receipt_thumbnail_s3_key'] = upload_result['thumbnail_s3_key']
                logging.info(f"Stored s3_key={upload_result['s3_key']} and thumbnail_s3_key={upload_result['thumbnail_s3_key']} in session")
            else:
                logging.error("❌ IMMEDIATE S3 UPLOAD FAILED, but continuing with processing")
        except Exception as s3_error:
            logging.error(f"❌ ERROR DURING IMMEDIATE S3 UPLOAD: {str(s3_error)}")
            logging.error(traceback.format_exc())
            # Continue with processing even if S3 upload fails
        
        # Decide if we should use async processing or direct processing
        # We'll use async processing if the ENABLE_ASYNC_PROCESSING env var is set
        use_async = os.environ.get('ENABLE_ASYNC_PROCESSING', 'True').lower() in ('true', '1', 'yes')
        
        if use_async:
            # For async processing, we need to send the image as base64
            buffered = BytesIO()
            img.save(buffered, format="JPEG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            # Create a task ID to store in the session
            task_id = f"receipt_scan_{int(time.time())}"
            
            # Submit the task to the Celery queue
            logging.info(f"Submitting async task with ID: {task_id}")
            
            # Explicitly check if Celery worker is available
            try:
                i = celery.control.inspect()
                if not i.active_queues():
                    logging.warning("No active Celery workers found. Starting background worker...")
                    # For this case, we'll process synchronously as fallback
                    extracted_data = process_receipt_with_gemini(img)
                    session['receipt_data'] = extracted_data
                    return jsonify({'success': True, 'data': extracted_data})
            except Exception as worker_check_error:
                logging.error(f"Error checking Celery workers: {str(worker_check_error)}")
                
            # Submit to queue if we reach here
            result = process_receipt_image.delay(
                img_base64, 
                receipt_file.filename,
                'process_receipt_with_gemini',
                organization_id
            )
            
            # Store task ID in session for future reference
            session['receipt_task_id'] = result.id
            
            # Log task submission for debugging
            logging.info(f"Celery task submitted with ID: {result.id} - this should be picked up by a worker")
            
            # Calculate processing time for this part
            processing_time = time.time() - start_time
            logging.info(f"Async task submitted in {processing_time:.2f} seconds, task ID: {result.id}")
            
            # Return a response indicating the task has been queued
            return jsonify({
                'success': True,
                'async': True,
                'task_id': result.id,
                'message': 'Receipt processing started. Please wait...'
            })
        else:
            # Synchronous (direct) processing for development or fallback
            logging.info("Using synchronous receipt processing")
            
            # Save the image locally AND to S3 (if enabled) FIRST, before processing
            from image_processor import save_uploaded_image
            
            # Save the image and get storage results (local path and s3 key)
            storage_result = None
            try:
                # Log image details before saving
                logging.info("============ Receipt Image Details ============")
                logging.info(f"Image format: {img.format}, size: {img.size}, mode: {img.mode}")
                logging.info(f"Image filename: {receipt_file.filename}")
                logging.info(f"Organization ID for storage: {organization_id}")
                logging.info("==========================================")
                
                # Check AWS environment variables before S3 upload attempt
                aws_vars_present = all([
                    os.environ.get('AWS_S3_BUCKET_NAME'),
                    os.environ.get('AWS_ACCESS_KEY_ID'),
                    os.environ.get('AWS_SECRET_ACCESS_KEY'),
                    os.environ.get('AWS_REGION')
                ])
                logging.info(f"AWS environment variables present: {aws_vars_present}")
                
                # Save the image to storage (local and/or S3)
                storage_result = save_uploaded_image(img, receipt_file.filename, organization_id)
                image_path_value = storage_result.get('image_path', '') if storage_result else ''
                s3_key_value = storage_result.get('s3_key', '') if storage_result else ''
                
                # Log detailed storage results
                logging.info("============ Storage Result ============")
                logging.info(f"image_path: '{image_path_value}'")
                logging.info(f"s3_key: '{s3_key_value}'")
                logging.info("=======================================")
                
                # If no S3 key was returned, log a warning
                if not s3_key_value and aws_vars_present:
                    logging.warning("S3 upload failed or was not attempted despite AWS credentials being present")
                elif not aws_vars_present:
                    logging.info("S3 upload was not attempted because AWS credentials are not fully configured")
                
                logging.info(f"Image saved with relative path {image_path_value} and S3 key {s3_key_value}")
            except Exception as save_error:
                logging.error(f"Error saving image: {str(save_error)}")
                image_path_value = None
                s3_key_value = None
            
            # Process receipt with Gemini AFTER saving the image
            extracted_data = process_receipt_with_gemini(img)
            
            if not extracted_data:
                return jsonify({'error': 'Failed to extract data from the receipt'}), 500
                
            # Now add the image paths to extracted data
            extracted_data['image_path'] = image_path_value if image_path_value else ''
            extracted_data['s3_key'] = s3_key_value if s3_key_value else ''
            
            # Double check the path values
            logging.info(f"Final image_path in extracted_data: {extracted_data.get('image_path')}")
            logging.info(f"Final s3_key in extracted_data: {extracted_data.get('s3_key')}")
            
            # Store the extracted data in session for potential correction
            session['receipt_data'] = extracted_data
            
            # Calculate processing time
            processing_time = time.time() - start_time
            logging.info(f"Synchronous receipt processing completed in {processing_time:.2f} seconds")
            
            return jsonify({'success': True, 'data': extracted_data})
    
    except Exception as e:
        logging.error(f"Error processing receipt: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify({'error': f'Error processing receipt: {str(e)}'}), 500


@app.route('/task_status/<task_id>', methods=['GET'])
@login_required
def task_status(task_id):
    """Check the status of an asynchronous task and retrieve results if complete."""
    try:
        # Create an AsyncResult object for the task
        task_result = AsyncResult(task_id)
        
        # Log detailed status information for debugging
        logging.info(f"Task {task_id} current state: {task_result.state}")
        
        # Check if Celery worker is available
        try:
            i = celery.control.inspect()
            active_workers = i.active_queues()
            if not active_workers:
                logging.warning("No active Celery workers detected while checking task status")
        except Exception as worker_error:
            logging.error(f"Error checking Celery workers during task status: {str(worker_error)}")
        
        # Check the status of the task
        if task_result.ready():
            # Task has completed, get the result
            if task_result.successful():
                result = task_result.get()
                logging.info(f"Task {task_id} completed successfully with data")
                
                # Validate the image paths in the result before storing in session
                # Check s3_key
                s3_key = result.get('s3_key', '')
                logging.debug(f"Received s3_key in task result: '{s3_key}'")
                
                # Check image_path
                image_path = result.get('image_path', '')
                logging.debug(f"Received image_path in task result: '{image_path}'")
                
                # Store the extracted data in session for potential correction
                session['receipt_data'] = result
                
                # Extra verification
                logging.debug(f"Receipt data for task {task_id}: s3_key={result.get('s3_key', 'None')}, image_path={result.get('image_path', 'None')}")
                
                return jsonify({
                    'status': 'completed',
                    'success': True,
                    'data': result
                })
            else:
                # Task failed
                error = str(task_result.result)
                logging.error(f"Task {task_id} failed: {error}")
                return jsonify({
                    'status': 'failed',
                    'error': f"Receipt processing failed: {error}"
                }), 500
        else:
            # Task is still pending
            logging.info(f"Task {task_id} is still pending with state: {task_result.state}")
            return jsonify({
                'status': 'pending',
                'state': task_result.state,
                'message': 'Receipt processing in progress...'
            })
    
    except Exception as e:
        logging.error(f"Error checking task status: {str(e)}")
        logging.error(traceback.format_exc())  # Add traceback for better debugging
        return jsonify({'error': f'Error checking task status: {str(e)}'}), 500

@app.route('/preview/update_data', methods=['POST'])
def preview_update_data():
    """Update the extracted receipt data with user corrections in preview mode (no login required)."""
    try:
        updated_data = request.json
        
        if 'receipt_data' not in session:
            response = jsonify({'error': 'No receipt data found in session'})
            response.headers.set('Content-Type', 'application/json')
            return response, 400
            
        # Get current session data to preserve image paths
        current_session_data = session.get('receipt_data', {})
        
        # Preserve image paths from original session data
        original_image_path = current_session_data.get('image_path', '')
        original_s3_key = current_session_data.get('s3_key', '')
        
        # Log the paths we're preserving
        logging.info(f"Preserving image paths in preview_update_data - image_path: '{original_image_path}', s3_key: '{original_s3_key}'")
        
        # Make sure updated_data includes these paths
        if 'image_path' not in updated_data or not updated_data['image_path']:
            updated_data['image_path'] = original_image_path
        
        if 's3_key' not in updated_data or not updated_data['s3_key']:
            updated_data['s3_key'] = original_s3_key
        
        # Update the session data with corrected values
        session['receipt_data'] = updated_data
        
        # Log the final data for debugging
        logging.info(f"Final preview updated data - image_path: '{updated_data.get('image_path')}', s3_key: '{updated_data.get('s3_key')}'")
        
        response = jsonify({'success': True, 'data': updated_data})
        response.headers.set('Content-Type', 'application/json')
        return response
    
    except Exception as e:
        logging.error(f"Error updating data in preview mode: {str(e)}")
        response = jsonify({'error': f'Error updating data: {str(e)}'})
        response.headers.set('Content-Type', 'application/json')
        return response, 500

@app.route('/update_data', methods=['POST'])
@login_required
def update_data():
    """Update the extracted receipt data with user corrections."""
    try:
        updated_data = request.json
        
        if 'receipt_data' not in session:
            response = jsonify({'error': 'No receipt data found in session'})
            response.headers.set('Content-Type', 'application/json')
            return response, 400
        
        # Get current session data to preserve image paths and other data
        current_session_data = session.get('receipt_data', {})
        
        # Preserve image paths from original session data
        original_image_path = current_session_data.get('image_path', '')
        original_s3_key = current_session_data.get('s3_key', '')
        
        # Log the paths we're preserving
        logging.info(f"Preserving image paths in update_data - image_path: '{original_image_path}', s3_key: '{original_s3_key}'")
        
        # Make sure updated_data includes these paths
        if 'image_path' not in updated_data or not updated_data['image_path']:
            updated_data['image_path'] = original_image_path
        
        if 's3_key' not in updated_data or not updated_data['s3_key']:
            updated_data['s3_key'] = original_s3_key
            
        # Handle simplified organization context from the improved UI
        is_company_expense = updated_data.get('is_company_expense', False)
        
        # Get the user's default organization for personal expenses
        from models import OrganizationUser
        personal_finances_org = None
        if not is_company_expense:
            # Find the Personal Finances organization
            personal_finances = OrganizationUser.query.join(
                OrganizationUser.organization
            ).filter(
                OrganizationUser.user_id == current_user.id,
                db.text("LOWER(organization.name) = 'personal finances'")
            ).first()
            
            if personal_finances:
                personal_finances_org = personal_finances.organization_id
                logging.info(f"Found Personal Finances organization ID: {personal_finances_org}")
                
                # Set the organization_id for the receipt
                updated_data['organization_id'] = personal_finances_org
                updated_data['expense_organization_id'] = None  # Clear company org selection
                updated_data['expense_client_id'] = None  # Clear client selection
                updated_data['expense_description'] = ''  # Clear expense description
            else:
                logging.warning(f"No Personal Finances organization found for user {current_user.id}")
        else:
            # For company expenses, use the selected organization
            company_org_id = updated_data.get('expense_organization_id')
            client_id = updated_data.get('expense_client_id')
            
            if company_org_id:
                logging.info(f"Company expense with organization ID: {company_org_id}")
                
                # Set the organization_id for the receipt
                updated_data['organization_id'] = company_org_id
            else:
                logging.warning("Company expense selected but no organization provided")
        
        # Update the session data with corrected values
        session['receipt_data'] = updated_data
        
        # Log the final data for debugging
        logging.info(f"Final updated data - image_path: '{updated_data.get('image_path')}', s3_key: '{updated_data.get('s3_key')}'")
        logging.info(f"Organization context - is_company_expense: {is_company_expense}, organization_id: {updated_data.get('organization_id')}")
        
        response = jsonify({'success': True, 'data': updated_data})
        response.headers.set('Content-Type', 'application/json')
        return response
    
    except Exception as e:
        logging.error(f"Error updating data: {str(e)}")
        logging.error(traceback.format_exc())
        response = jsonify({'error': f'Error updating data: {str(e)}'})
        response.headers.set('Content-Type', 'application/json')
        return response, 500

@app.route('/save', methods=['POST'])
@login_required
def save_receipt():
    """Save the receipt data to the database."""
    from models import Receipt, ReceiptItem, CompanyExpense, ExpenseStatus
    
    try:
        # Check if we have receipt data in the session
        if 'receipt_data' not in session:
            return jsonify({'error': 'No receipt data to save'}), 400
        
        receipt_data = session['receipt_data']
        
        # Check if we have directly uploaded S3 keys from the scan process
        # If yes, use them instead of the ones in receipt_data
        if 'receipt_s3_key' in session and session['receipt_s3_key']:
            direct_s3_key = session['receipt_s3_key']
            logging.info(f"Found direct S3 key in session: {direct_s3_key}")
            # Update the receipt data with this key
            receipt_data['s3_key'] = direct_s3_key
            logging.info(f"Updated receipt_data with direct S3 key: {direct_s3_key}")
        
        # Also check for a thumbnail S3 key in the session
        if 'receipt_thumbnail_s3_key' in session and session['receipt_thumbnail_s3_key']:
            thumbnail_s3_key = session['receipt_thumbnail_s3_key']
            logging.info(f"Found thumbnail S3 key in session: {thumbnail_s3_key}")
            # Add the thumbnail key to the receipt data
            receipt_data['thumbnail_s3_key'] = thumbnail_s3_key
            logging.info(f"Updated receipt_data with thumbnail S3 key: {thumbnail_s3_key}")
        
        # Add additional debug logging
        logging.debug(f"Receipt data keys: {receipt_data.keys() if receipt_data else 'None'}")
        logging.info(f"Image path: {receipt_data.get('image_path', 'None')}")
        logging.info(f"S3 key: {receipt_data.get('s3_key', 'None')}")
        
        # Extract vendor_name and total_amount early to avoid undefined variable errors
        vendor_name = receipt_data.get('vendor_name', 'Unknown Vendor') 
        total_amount = receipt_data.get('total_amount', 0)
        
        # Parse date if it exists
        receipt_date = None
        if receipt_data.get('date'):
            try:
                receipt_date = datetime.strptime(receipt_data['date'], '%Y-%m-%d').date()
            except ValueError:
                logging.warning(f"Invalid date format: {receipt_data['date']}")
        
        # Get organization ID with comprehensive fallback logic
        organization_id = None

        # Priority 1: From receipt data (if organization was embedded during processing)
        if receipt_data.get('organization_id'):
            organization_id = receipt_data.get('organization_id')
            logging.info(f"Using organization_id from receipt_data: {organization_id}")

        # Priority 2: From session context (most common case)
        if not organization_id and session.get('receipt_organization_id'):
            organization_id = session.get('receipt_organization_id')
            logging.info(f"Using organization_id from session: {organization_id}")

        # Convert to int if string (handle frontend string values)
        if organization_id and isinstance(organization_id, str) and organization_id.isdigit():
            organization_id = int(organization_id)

        # Priority 3: Default organization as last resort with explicit warning
        if not organization_id:
            default_org = None
            if hasattr(current_user, 'get_default_organization'):
                default_org = current_user.get_default_organization()
                if default_org:
                    organization_id = default_org.id
                    logging.warning(f"No organization context found, falling back to default organization: {organization_id}")
                else:
                    logging.error("No default organization found for user")
                    return jsonify({'error': 'No organization context available'}), 400

        # Final validation
        if not organization_id:
            logging.error("Could not determine organization for receipt")
            return jsonify({'error': 'Organization context required for receipt saving'}), 400

        logging.info(f"Final organization_id for receipt: {organization_id}")
        
        # Get the S3 key if available in the receipt data
        s3_key = receipt_data.get('s3_key', '')
        image_path = receipt_data.get('image_path')
        
        # Handle case where image_path is the string 'None' instead of actual path or None
        if image_path == 'None':
            image_path = None
        
        # Log what we're working with
        logging.info(f"Receipt data has s3_key: '{s3_key}' and image_path: '{image_path}'")
        
        # Validate image path exists if provided
        if image_path and image_path != 'None':
            try:
                if image_path.startswith('uploads/'):
                    # Ensure correct path format
                    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', image_path)
                else:
                    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', os.path.basename(image_path))
                
                logging.info(f"Using local image path: {full_path}")
                
                if not os.path.exists(full_path):
                    logging.warning(f"Image file not found at {full_path}")
                    
                    # If the file doesn't exist, set image_path to None
                    logging.info("Setting image_path to None because the file doesn't exist")
                    image_path = None
            except Exception as error:
                logging.error(f"Error validating image path: {str(error)}")
                # Set image_path to None if there's an error
                image_path = None
        
        # Log detailed information about the receipt being created
        logging.info(f"Creating new receipt with s3_key='{s3_key}' and image_path='{image_path}'")
        
        # Log detailed information about receipt data
        logging.info("============ Receipt Save Details ============")
        logging.info(f"User ID: {current_user.id}")
        logging.info(f"Organization ID: {organization_id}")
        logging.info(f"Image path: '{image_path}'")
        logging.info(f"S3 key: '{s3_key}'")
        logging.info(f"Vendor: {receipt_data.get('vendor_name', 'Unknown Vendor')}")
        logging.info(f"Total amount: {receipt_data.get('total_amount', 0)}")
        logging.info(f"Date: {receipt_date}")
        logging.info(f"Number of items: {len(receipt_data.get('items', []))}")
        logging.info("=============================================")
        
        # Get thumbnail S3 key if available
        thumbnail_s3_key = receipt_data.get('thumbnail_s3_key', '')
        logging.info(f"Thumbnail S3 key: '{thumbnail_s3_key}'")
        
        # Create a new receipt and associate it with the current user
        new_receipt = Receipt(
            user_id=current_user.id,  # Set the user_id from the logged in user
            organization_id=organization_id,
            vendor_name=receipt_data.get('vendor_name', 'Unknown Vendor'),
            vendor_address=receipt_data.get('vendor_address'),
            vendor_contact=receipt_data.get('vendor_contact'),
            date=receipt_date,
            total_amount=float(receipt_data.get('total_amount', 0)),
            service_charge=float(receipt_data.get('service_charge', 0) or 0),
            vat_registration_number=receipt_data.get('vat_registration_number'),
            sscl_tax=float(receipt_data.get('sscl_tax', 0) or 0),
            vat_tax=float(receipt_data.get('vat_tax', 0) or 0),
            expense_major_category=receipt_data.get('expense_major_category'),
            expense_minor_category=receipt_data.get('expense_minor_category'),
            s3_key=s3_key,
            thumbnail_s3_key=thumbnail_s3_key,
            image_path=image_path
        )
        logging.info(f"Creating new receipt with s3_key='{s3_key}', thumbnail_s3_key='{thumbnail_s3_key}', and image_path='{image_path}'")
        
        # Add the receipt to the database session
        db.session.add(new_receipt)
        db.session.flush()  # This assigns an ID to new_receipt without committing
        
        # Add receipt items if they exist
        items = receipt_data.get('items', [])
        for item in items:
            # Safely extract and validate deductibility fields with type coercion
            deduct_pct = item.get('deductibility_percentage')
            if deduct_pct is not None:
                try:
                    deduct_pct = int(float(deduct_pct))
                    # Clamp to 0-100 range
                    deduct_pct = max(0, min(100, deduct_pct))
                except (ValueError, TypeError):
                    logging.warning(f"Invalid deductibility_percentage: {deduct_pct}, defaulting to None")
                    deduct_pct = None
            
            confidence = item.get('classification_confidence')
            if confidence is not None:
                try:
                    confidence = float(confidence)
                    # Clamp to 0.0-1.0 range
                    confidence = max(0.0, min(1.0, confidence))
                except (ValueError, TypeError):
                    logging.warning(f"Invalid classification_confidence: {confidence}, defaulting to None")
                    confidence = None
            
            new_item = ReceiptItem(
                receipt_id=new_receipt.id,
                name=item.get('name', 'Unknown Item'),
                quantity=float(item.get('quantity', 1) or 1),
                price=float(item.get('price', 0) or 0),
                tax_deductible=bool(item.get('tax_deductible', False)),
                deductibility_percentage=deduct_pct,
                tax_law_reference=item.get('tax_law_reference'),
                classification_confidence=confidence,
                deduction_notes=item.get('deduction_notes')
            )
            db.session.add(new_item)
            
        # Check if this receipt should be tagged as a company expense
        is_company_expense = receipt_data.get('is_company_expense', False)
        is_reimbursable = receipt_data.get('is_reimbursable', True)
        expense_description = receipt_data.get('expense_description', '')
        
        # Get selected organization and client for company expense
        expense_organization_id = receipt_data.get('expense_organization_id')
        expense_client_id = receipt_data.get('expense_client_id')
        
        # If user selected a specific organization for the expense, use that
        if is_company_expense and expense_organization_id:
            organization_id = expense_organization_id
        
        # If it's a company expense and the user has an organization, create a company expense record
        company_expense_id = None
        if is_company_expense and organization_id:
            company_expense = CompanyExpense(
                receipt_id=new_receipt.id,
                user_id=current_user.id,
                organization_id=organization_id,
                client_id=expense_client_id if expense_client_id else None,
                description=expense_description,
                status=ExpenseStatus.SUBMITTED.value,
                is_reimbursable=is_reimbursable,
                notes="Submitted during receipt scanning",
                submitted_date=datetime.utcnow()
            )
            db.session.add(company_expense)
            db.session.flush()
            company_expense_id = company_expense.id
        
        # Add trust points for adding a receipt
        from models import TrustActivity
        trust_activity = TrustActivity(
            user_id=current_user.id,
            activity_type='receipt_added',
            points=10,  # 10 points for adding a receipt
            description=f'Added receipt for {new_receipt.vendor_name}'
        )
        db.session.add(trust_activity)
        
        # Update user's trust points
        current_user.trust_points = current_user.trust_points + 10
        
        # Recalculate trust level based on new points
        current_user.recalculate_trust_level()
        
        # Commit all changes
        db.session.commit()
        
        # Return success with the receipt ID and company expense ID if applicable
        response_data = {
            'success': True, 
            'message': 'Receipt saved successfully',
            'receipt_id': new_receipt.id
        }
        
        if company_expense_id:
            response_data['company_expense_id'] = company_expense_id
            response_data['message'] = 'Receipt saved and submitted as company expense'
            
        return jsonify(response_data)
    
    except Exception as e:
        # Roll back in case of error
        db.session.rollback()
        logging.error(f"Error saving receipt: {str(e)}")
        return jsonify({'error': f'Error saving receipt: {str(e)}'}), 500

@app.route('/receipts', methods=['GET'])
@app.route('/list_receipts', methods=['GET'])  # Add alias route for legacy frontend compatibility
@login_required
def list_receipts():
    """Get a list of all saved receipts for the current user, with organization-aware filtering and role-based visibility."""
    from models import Receipt, OrganizationUser
    import traceback
    from sqlalchemy import or_, and_
    from error_logger import log_receipt_history_error, log_database_error, handle_and_log_exception, ErrorTypes
    from utils import can_view_organization_receipts
    
    try:
        # Add detailed logging to troubleshoot
        user_id = current_user.id
        logging.debug(f"list_receipts: Processing for user_id={user_id}")
        
        # Check if we should show all organizations or filter by a specific one
        # Enhanced parameter handling with better type conversion
        show_all_param = request.args.get('show_all')
        org_id_param = request.args.get('organization_id')
        
        # Convert show_all parameter properly (handle strings and booleans)
        show_all_organizations = False
        if show_all_param:
            if isinstance(show_all_param, str):
                show_all_organizations = show_all_param.lower() == 'true'
            else:
                show_all_organizations = bool(show_all_param)
        
        # Handle organization_id parameter
        selected_org_id = None
        if org_id_param:
            if org_id_param.lower() == 'all':
                show_all_organizations = True
            else:
                try:
                    selected_org_id = org_id_param
                    # Validate that this organization belongs to the user
                    user_org_ids = [str(org.organization_id) for org in OrganizationUser.query.filter_by(user_id=user_id).all()]
                    if selected_org_id not in user_org_ids:
                        logging.warning(f"Organization ID {selected_org_id} does not belong to user {user_id}")
                        selected_org_id = None
                except Exception as e:
                    logging.warning(f"Invalid organization_id parameter: {org_id_param}. Error: {str(e)}")
                    selected_org_id = None
                    
        # Add diagnostic logging for organization filtering
        logging.debug(f"Receipt organization filtering: show_all={show_all_organizations}, org_id={selected_org_id}")
        
        # Get all user's organizations for the filter dropdown
        user_organizations = OrganizationUser.query.filter_by(user_id=user_id).all()
        organizations = []
        
        # Create a dictionary mapping org_id to role for quick lookup
        org_roles = {}
        
        for org_user in user_organizations:
            organizations.append({
                'id': org_user.organization_id,
                'name': org_user.organization.name,
                'is_default': org_user.is_default,
                'role': org_user.role  # Include role for UI indication
            })
            org_roles[org_user.organization_id] = org_user.role
        
        # Default organization for filtering if not showing all and no specific org selected
        default_org = None
        if not show_all_organizations and not selected_org_id:
            default_org = current_user.get_default_organization()
            if default_org:
                selected_org_id = str(default_org.id)
        
        # Prepare the query based on organization filtering
        try:
            if show_all_organizations:
                # Show all receipts across all user's organizations with role-based filtering
                logging.debug(f"list_receipts: Showing receipts from all organizations for user_id={user_id}")
                
                # We'll create multiple queries and union them together
                queries = []
                
                # Always add personal receipts (not belonging to any organization)
                personal_query = Receipt.query.filter(
                    and_(
                        Receipt.user_id == user_id,
                        or_(
                            Receipt.organization_id.is_(None),
                            Receipt.organization_id == 0
                        )
                    )
                )
                queries.append(personal_query)
                
                # Add organization receipts with role-based filtering
                for org in organizations:
                    org_id = org['id']
                    role = org_roles.get(org_id)
                    
                    # Owners and admins see all organization receipts
                    if role in ['owner', 'admin']:
                        org_query = Receipt.query.filter(Receipt.organization_id == org_id)
                    # Members and viewers only see their own receipts
                    else:
                        org_query = Receipt.query.filter(
                            and_(
                                Receipt.organization_id == org_id,
                                Receipt.user_id == user_id
                            )
                        )
                    queries.append(org_query)
                
                # Combine all queries with union
                if queries:
                    query = queries[0]
                    for q in queries[1:]:
                        query = query.union(q)
                    
                    # Apply ordering
                    receipts = query.order_by(Receipt.date.desc()).all()
                else:
                    receipts = []
                
                logging.debug(f"list_receipts: Found {len(receipts)} receipts across all organizations for user_id={user_id}")
                
            elif selected_org_id:
                # Filter by specific organization with role-based access
                org_id = int(selected_org_id)
                role = org_roles.get(org_id)
                
                logging.debug(f"list_receipts: Filtering by organization_id={org_id} for user_id={user_id} with role={role}")
                
                # Different query based on role
                if role in ['owner', 'admin']:
                    # Owners and admins see all organization receipts
                    receipts = Receipt.query.filter(
                        Receipt.organization_id == org_id
                    ).order_by(Receipt.date.desc()).all()
                else:
                    # Members and viewers only see their own receipts
                    receipts = Receipt.query.filter(
                        and_(
                            Receipt.organization_id == org_id,
                            Receipt.user_id == user_id
                        )
                    ).order_by(Receipt.date.desc()).all()
                
                logging.debug(f"list_receipts: Found {len(receipts)} receipts for organization_id={org_id} and user_id={user_id}")
            else:
                # No organization filtering - fall back to user's receipts (unchanged)
                logging.debug(f"list_receipts: No organization filter, using user_id filter")
                receipts = Receipt.query.filter_by(user_id=user_id).order_by(Receipt.date.desc()).all()
                logging.debug(f"list_receipts: Found {len(receipts)} receipts for user_id={user_id}")
                
        except Exception as db_error:
            # Log database-specific error and re-raise
            log_database_error(
                error=db_error,
                operation="query",
                model="Receipt",
                additional_info={
                    "user_id": user_id,
                    "selected_org_id": selected_org_id,
                    "show_all": show_all_organizations,
                    "query_type": "filtered_receipts"
                }
            )
            raise
        
        # Convert to list of dictionaries
        receipt_list = []
        try:
            for receipt in receipts:
                receipt_dict = receipt.to_dict()
                # Simplify the output for the list view
                receipt_dict.pop('items', None)  # Remove items to reduce payload size
                receipt_list.append(receipt_dict)
            
            logging.debug(f"list_receipts: Returning {len(receipt_list)} receipts to client")
            
            # Return organizations along with receipts for the filter UI
            response_data = {
                'success': True, 
                'receipts': receipt_list,
                'organizations': organizations,
                'selected_org_id': selected_org_id,
                'show_all': show_all_organizations
            }
            
            return jsonify(response_data)
        except Exception as conversion_error:
            # Log specific error for data conversion issues
            log_receipt_history_error(
                error=conversion_error,
                sub_component="data_conversion",
                additional_info={
                    "user_id": user_id,
                    "receipt_count": len(receipts) if receipts else 0
                }
            )
            raise
    
    except Exception as e:
        # Handle and log the error with our new system
        error_response = handle_and_log_exception(
            e=e,
            error_type=ErrorTypes.RECEIPT_HISTORY,
            component="list_receipts",
            additional_info={
                "user_id": current_user.id if current_user else None,
                "has_organization": bool(org) if 'org' in locals() else None
            }
        )
        
        # Return a more informative error to the client with the error_id for support reference
        return jsonify({
            'error': f"Error retrieving receipts: {str(e)}",
            'error_id': error_response.get('error_id', 'unknown'),
            'error_type': error_response.get('error_type', ErrorTypes.UNKNOWN)
        }), 500
        
@app.route('/receipts/delete', methods=['POST'])
@login_required
def delete_receipts():
    """Delete multiple receipts by ID, ensuring they belong to the current user's organization."""
    from models import Receipt
    from sqlalchemy import or_, and_
    from error_logger import log_receipt_history_error, log_database_error, handle_and_log_exception, ErrorTypes
    
    try:
        # Get receipt IDs from request
        data = request.get_json()
        receipt_ids = data.get('receipt_ids', [])
        
        if not receipt_ids:
            return jsonify({'error': 'No receipt IDs provided'}), 400
        
        try:
            # Convert string IDs to integers
            receipt_ids = [int(id) for id in receipt_ids]
        except ValueError as ve:
            # Log validation error
            log_receipt_history_error(
                error=ve,
                sub_component="delete_validation",
                additional_info={
                    "invalid_ids": receipt_ids,
                    "error_details": "Failed to convert receipt IDs to integers"
                }
            )
            return jsonify({'error': 'Invalid receipt ID format'}), 400
        
        # Get the user's default organization
        org = current_user.get_default_organization()
        user_id = current_user.id
        
        try:
            if org:
                org_id = org.id
                # Find receipts to delete that belong to the user's organization
                # or receipts that belong to the user and have null organization_id (legacy data)
                receipts_to_delete = Receipt.query.filter(
                    Receipt.id.in_(receipt_ids),
                    or_(
                        Receipt.organization_id == org_id,
                        and_(
                            Receipt.user_id == user_id,
                            or_(
                                Receipt.organization_id.is_(None),
                                Receipt.organization_id == 0
                            )
                        )
                    )
                ).all()
            else:
                # Fallback to user's receipts if no organization
                receipts_to_delete = Receipt.query.filter(
                    Receipt.id.in_(receipt_ids),
                    Receipt.user_id == current_user.id
                ).all()
            
            deleted_count = len(receipts_to_delete)
            
            # Log if there's a mismatch between requested and actual deletion count
            if deleted_count != len(receipt_ids):
                log_receipt_history_error(
                    error=f"Receipt ID mismatch: {len(receipt_ids)} requested, {deleted_count} found",
                    sub_component="delete_mismatch",
                    additional_info={
                        "user_id": user_id,
                        "org_id": org_id if org else None,
                        "requested_ids": receipt_ids,
                        "found_ids": [r.id for r in receipts_to_delete]
                    }
                )
        except Exception as db_error:
            # Log database-specific error and re-raise
            log_database_error(
                error=db_error,
                operation="query",
                model="Receipt",
                additional_info={
                    "user_id": user_id,
                    "organization_id": org_id if org else None,
                    "receipt_ids": receipt_ids
                }
            )
            raise
        
        try:
            # Delete each receipt (will cascade to items thanks to relationship setup)
            for receipt in receipts_to_delete:
                db.session.delete(receipt)
            
            # Commit the transaction
            db.session.commit()
            
            # Log successful deletion
            logging.info(f"User {user_id} deleted {deleted_count} receipts: {[r.id for r in receipts_to_delete]}")
            
            return jsonify({
                'success': True, 
                'deleted_count': deleted_count,
                'message': f'Successfully deleted {deleted_count} receipt(s)'
            })
        except Exception as db_error:
            # Roll back and log database error
            db.session.rollback()
            log_database_error(
                error=db_error,
                operation="delete",
                model="Receipt",
                additional_info={
                    "user_id": user_id,
                    "receipt_ids": [r.id for r in receipts_to_delete]
                }
            )
            raise
    
    except Exception as e:
        # Handle and log the error with our new system
        error_response = handle_and_log_exception(
            e=e,
            error_type=ErrorTypes.RECEIPT_HISTORY,
            component="delete_receipts",
            additional_info={
                "user_id": current_user.id if current_user else None,
                "receipt_ids": receipt_ids if 'receipt_ids' in locals() else None,
                "has_organization": bool(org) if 'org' in locals() else None
            }
        )
        
        # Return a more informative error to the client
        return jsonify({
            'error': f"Error deleting receipts: {str(e)}",
            'error_id': error_response.get('error_id', 'unknown'),
            'error_type': error_response.get('error_type', ErrorTypes.UNKNOWN)
        }), 500

@app.route('/receipts/<int:receipt_id>', methods=['GET'])
@login_required
def get_receipt(receipt_id):
    """Get details of a specific receipt that belongs to the current user's organization."""
    from models import Receipt
    from sqlalchemy import or_, and_
    
    try:
        # Get the user's default organization
        org = current_user.get_default_organization()
        user_id = current_user.id
        
        if org:
            org_id = org.id
            # Find the receipt by ID and either:
            # 1. matching organization_id, or
            # 2. null organization_id and user ownership (legacy data)
            receipt = Receipt.query.filter(
                Receipt.id == receipt_id,
                or_(
                    Receipt.organization_id == org_id,
                    and_(
                        Receipt.user_id == user_id,
                        or_(
                            Receipt.organization_id.is_(None),
                            Receipt.organization_id == 0
                        )
                    )
                )
            ).first()
        else:
            # Fallback to user ownership if no organization
            receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
        
        if not receipt:
            logging.warning(f"Receipt {receipt_id} not found or doesn't belong to user {user_id} or org {org.id if org else 'None'}")
            return jsonify({'error': 'Receipt not found or does not belong to your organization'}), 404
        
        # Convert to dictionary with all details
        receipt_dict = receipt.to_dict()
        
        return jsonify({'success': True, 'receipt': receipt_dict})
    
    except Exception as e:
        logging.error(f"Error getting receipt: {str(e)}")
        return jsonify({'error': f'Error getting receipt: {str(e)}'}), 500
        
@app.route('/view_receipt/<int:receipt_id>')
@login_required
def view_receipt(receipt_id):
    """Redirect to the unified receipt view for consistent URL structure."""
    from flask import redirect, url_for
    # Redirect to the unified receipt view
    return redirect(url_for('unified_view.view_unified_receipt', receipt_id=receipt_id))

@app.route('/export', methods=['GET'])
@login_required
def export_data():
    """Export the extracted receipt data as JSON."""
    if 'receipt_data' not in session:
        return jsonify({'error': 'No receipt data to export'}), 400
        
    return jsonify(session['receipt_data'])

@app.route('/export/excel', methods=['GET'])
@login_required
def export_excel():
    """Export the current user's organization receipts as Excel file."""
    import pandas as pd
    import io
    from models import Receipt
    from datetime import datetime
    from sqlalchemy import or_, and_
    
    try:
        # Get the user's default organization
        org = current_user.get_default_organization()
        user_id = current_user.id
        
        if org:
            org_id = org.id
            # Comprehensive query that handles legacy data (null organization_id)
            # This handles 3 cases:
            # 1. Receipts belonging to the user's organization
            # 2. Receipts belonging to the user directly (for compatibility with old data)
            # 3. Receipts with null organization_id that belong to the user
            receipts = Receipt.query.filter(
                or_(
                    Receipt.organization_id == org_id,  # Organization receipts
                    and_(                              # Legacy receipts
                        Receipt.user_id == user_id,
                        or_(
                            Receipt.organization_id.is_(None),
                            Receipt.organization_id == 0
                        )
                    )
                )
            ).order_by(Receipt.date.desc()).all()
        else:
            # Fallback to user's receipts if no organization is set
            receipts = Receipt.query.filter_by(user_id=current_user.id).order_by(Receipt.date.desc()).all()
        
        if not receipts:
            return jsonify({'error': 'No receipts to export'}), 404
        
        # Convert to list of dictionaries for pandas
        receipts_data = []
        for receipt in receipts:
            receipt_dict = receipt.to_dict()
            
            # Format the receipt data for Excel
            receipts_data.append({
                'Date': receipt.date.strftime('%Y-%m-%d') if receipt.date else '',
                'Vendor': receipt.vendor_name,
                'Category': receipt.expense_major_category or 'Uncategorized',
                'Subcategory': receipt.expense_minor_category or 'Uncategorized',
                'Total Amount (Rs)': receipt.total_amount,
                'VAT (Rs)': receipt.vat_tax,
                'SSCL Tax (Rs)': receipt.sscl_tax,
                'Service Charge (Rs)': receipt.service_charge,
                'Tax Deductible Amount (Rs)': receipt.get_tax_deductible_amount(),
                'Address': receipt.vendor_address or '',
                'Contact': receipt.vendor_contact or '',
                'VAT Reg #': receipt.vat_registration_number or ''
            })
        
        # Create a DataFrame
        df = pd.DataFrame(receipts_data)
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Receipts', index=False)
            
            # Get the xlsxwriter workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets['Receipts']
            
            # Add some formatting
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'bg_color': '#D9EAD3',
                'border': 1
            })
            
            currency_format = workbook.add_format({'num_format': '#,##0.00'})
            
            # Apply formatting
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
                # Apply currency formatting to amount columns
                if 'Amount' in value or 'VAT' in value or 'Tax' in value or 'Charge' in value:
                    worksheet.set_column(col_num, col_num, 18, currency_format)
                else:
                    worksheet.set_column(col_num, col_num, 18)
        
        # Set up the response
        output.seek(0)
        
        # Generate a filename with current date
        filename = f"receipts_export_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        logging.error(f"Error exporting receipts to Excel: {str(e)}")
        return jsonify({'error': f'Error exporting receipts to Excel: {str(e)}'}), 500

def process_receipt_with_gemini(image):
    """
    Process the receipt image using Gemini Vision API with timeout and retry logic.
    
    Args:
        image: PIL Image object of the receipt
    
    Returns:
        Dictionary containing extracted receipt data, or None on failure
    """
    try:
        logging.debug("Starting receipt image processing with Gemini Vision")
        
        # Configure request options with a simple timeout (no built-in retry)
        # Our custom retry logic handles retries, so we don't want Google's retry compounding
        from google.generativeai.types import RequestOptions
        
        # Single request timeout: 60s per attempt
        # Global timeout will be enforced by generate_with_retry
        request_options = RequestOptions(
            timeout=60.0  # 60 seconds per individual request
        )
        
        # Configure Gemini model - using the correct fallback chain
        # Primary: gemini-2.5-flash (most capable)
        # Fallback 1: gemini-2.0-flash (faster, good performance)
        # Fallback 2: gemini-2.0-flash-lite (most cost-efficient)
        model = None
        model_name = None
        
        for attempt_model in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.0-flash-lite']:
            try:
                model = genai.GenerativeModel(attempt_model)
                model_name = attempt_model
                logging.info(f"Using {attempt_model} model for vision processing")
                break
            except Exception as model_error:
                logging.warning(f"Could not initialize {attempt_model}: {str(model_error)}")
                continue
        
        if not model:
            error_msg = "Failed to initialize any Gemini model"
            logging.error(error_msg)
            raise Exception(error_msg)
        
        # Create the enhanced prompt for receipt data extraction with multilingual and bank transfer support
        prompt = """
        You are DocParse Pro, a multimodal extraction agent trained on IFRS and global tax rules. 
        Always follow the "RESPONSE_SCHEMA" exactly—no extra keys, no omissions.
        Extract information from documents in any language including English, Sinhala (සිංහල), Tamil (தமிழ்), Hindi, and other scripts.

        [IMAGE of one receipt OR bank-transfer screenshot is attached]

        TASK:
         1. Detect document type internally (receipt vs bank transfer).
         2. For bank transfers: Use Beneficiary/Recipient/Payee name as vendor_name. Create single item with transfer purpose as name.
         3. For multilingual content: Convert all text to English/Latin script in response. Convert dates to ISO format regardless of original language.
         4. For numbers: Always return as numeric values, handle any number format (1,234.56 or 1.234,56 or ១២៣៤.៥៦).
         5. Populate every field in RESPONSE_SCHEMA. Use "" for missing text and 0 for missing numbers.
         6. Classification must match existing system categories exactly.
         7. Return exactly one JSON object—no commentary, no explanations.

        RESPONSE_SCHEMA (exact match to Receipt model):
        {
          "vendor_name": "string (converted to English if needed)",
          "vendor_address": "string (converted to English if needed)", 
          "vendor_contact": "string",
          "date": "YYYY-MM-DD (always ISO format)",
          "items": [{"name": "string", "quantity": "number", "price": "number", "tax_deductible": "boolean", "deductibility_percentage": "number 0-100", "tax_law_reference": "string", "deduction_notes": "string"}],
          "total_amount": "number (never string)",
          "service_charge": "number",
          "vat_tax": "number", 
          "sscl_tax": "number",
          "vat_registration_number": "string",
          "expense_major_category": "Operating Expenses | Administrative Expenses | Cost of Goods Sold | Employee Benefits | Finance Costs",
          "expense_minor_category": "Meals and Entertainment | Travel and Transportation | Professional Services | Office Supplies | Marketing and Advertising | Utilities | Rent and Facilities | Software and SaaS | Bank and Merchant Fees | Repairs and Maintenance | Training, Education and Development | Legal and Accounting | Telecommunications | Administrative and General"
        }

        TAX DEDUCTIBILITY RULES (Sri Lankan Inland Revenue Act No. 24 of 2017):
        
        FULLY DEDUCTIBLE (100%):
        - Operating expenses: salaries, rent, utilities, office supplies, maintenance (Section 25)
        - Professional services: legal, accounting, consulting fees (Section 25)
        - Marketing & advertising: ALL marketing costs even if capital in nature (Section 26A)
        - Research & Development: business upgrading, innovation, product development (Section 26)
        - Business travel: flights, hotels, transport for business purposes (Section 25)
        - Software & SaaS: subscriptions, cloud services, business software (Section 25)
        - Training: employee education, workshops, certifications (Section 25)
        
        PARTIALLY DEDUCTIBLE (50%):
        - Meals & entertainment: limited unless directly client-related and documented
        
        NON-DEDUCTIBLE (0%):
        - Penalties & fines: all violations and late fees (Section 25(2))
        - Personal expenses: gym, beauty, personal clothing, entertainment
        - Capital expenditure: equipment, vehicles, property (use depreciation instead)
        - Provisions & reserves: only actual bad debts written off are deductible
        - Donations: only to IRD-approved charities (max ⅓ income or Rs. 75,000)
        
        For each item, set:
        - tax_deductible: true if deductibility_percentage > 0
        - deductibility_percentage: 0, 50, or 100 based on rules above
        - tax_law_reference: cite specific section (e.g., "Section 25 - IRA 2017")
        - deduction_notes: brief explanation of classification

        CLASSIFICATION EXAMPLES (must follow exactly):
        - Food/restaurants → "Operating Expenses" + "Meals and Entertainment" [50% deductible, entertainment restrictions]
        - Travel/transport → "Operating Expenses" + "Travel and Transportation" [100% if business purpose]
        - Software/subscriptions → "Operating Expenses" + "Software and SaaS" [100% deductible]
        - Professional services → "Operating Expenses" + "Professional Services" [100% deductible]
        - Office supplies → "Operating Expenses" + "Office Supplies" [100% deductible]
        - Marketing/ads → "Operating Expenses" + "Marketing and Advertising" [100% even if capital, Section 26A]
        - Training/courses → "Administrative Expenses" + "Training, Education and Development" [100% deductible]
        - Bank fees → "Finance Costs" + "Bank and Merchant Fees" [100% deductible]
        - Penalties/fines → Any category + relevant subcategory [0% non-deductible, Section 25(2)]
        - Unknown/unclear → "Administrative Expenses" + "Administrative and General" [0% default until verified]

        Return ONLY the JSON object and nothing else.
        """
        
        # Send the request to Gemini API with circuit breaker and retry logic
        logging.info(f"Sending request to Gemini API ({model_name}) with image")
        
        try:
            # Wrap the API call with circuit breaker and retry logic
            # Global timeout: 180s (3 minutes) total budget for all retries
            def make_gemini_call():
                return generate_with_retry(
                    model=model,
                    prompt=prompt,
                    image=image,
                    max_retries=5,
                    initial_delay=1.0,
                    request_options=request_options,
                    global_timeout=180.0  # 3 minutes max for receipt processing
                )
            
            # Execute with circuit breaker protection
            response = gemini_circuit_breaker.call(make_gemini_call)
            
            # Check if we got a signal to try fallback model (None response from 503)
            if response is None and model_name != 'gemini-2.0-flash-lite':
                logging.warning(f"{model_name} overloaded, trying fallback model")
                # Try next model in fallback chain
                fallback_models = {
                    'gemini-2.5-flash': 'gemini-2.0-flash',
                    'gemini-2.0-flash': 'gemini-2.0-flash-lite'
                }
                if model_name in fallback_models:
                    try:
                        fallback_name = fallback_models[model_name]
                        model = genai.GenerativeModel(fallback_name)
                        model_name = fallback_name
                        logging.info(f"Retrying with fallback model: {fallback_name}")
                        response = generate_with_retry(
                            model,
                            prompt,
                            image,
                            max_retries=3,
                            request_options=request_options,
                            global_timeout=90.0  # 90s for fallback model
                        )
                    except Exception as fallback_error:
                        logging.error(f"Fallback model failed: {str(fallback_error)}")
                        raise
            
            if response is None:
                raise Exception("All Gemini models failed or returned None")
            
            logging.info(f"Received response from Gemini API ({model_name})")
            
        except Exception as api_error:
            # Use structured error logging
            error_log = GeminiErrorLogger.log_error(
                error=api_error,
                context={
                    'function': 'process_receipt_with_gemini',
                    'model': model_name,
                    'image_size': image.size if hasattr(image, 'size') else None,
                    'user_id': current_user.id if current_user and hasattr(current_user, 'id') else None
                }
            )
            
            # Get user-friendly error message
            error_category = error_log.get('error_category', 'UNKNOWN')
            user_message = GeminiErrorLogger.get_user_friendly_message(error_category)
            
            logging.error(f"Gemini API call failed with category {error_category}: {user_message}")
            raise
        
        # Parse the response to extract JSON
        response_text = response.text
        logging.debug(f"Raw response text: {response_text[:200]}...")  # Log first 200 chars of response
        
        # Initialize json_text
        json_text = ""
        
        # If the response contains markdown code blocks, extract just the JSON part
        try:
            if "```json" in response_text:
                json_text = response_text.split("```json")[1].split("```")[0].strip()
                logging.debug("Extracted JSON from ```json code block")
            elif "```" in response_text:
                json_text = response_text.split("```")[1].strip()
                logging.debug("Extracted JSON from generic code block")
            else:
                json_text = response_text.strip()
                logging.debug("Using raw response as JSON")
            
            # Check if the response starts with a curly brace (JSON object)
            if not json_text.startswith('{'):
                # Try to find a JSON object in the text
                import re
                json_match = re.search(r'(\{.*\})', json_text, re.DOTALL)
                if json_match:
                    json_text = json_match.group(1)
                    logging.debug("Extracted JSON using regex")
                else:
                    raise ValueError("Could not find valid JSON object in response")
            
            if not json_text:
                raise ValueError("Empty JSON response")
                
            logging.debug(f"JSON text to parse: {json_text[:200]}...")
            
            # Parse the JSON response
            extracted_data = json.loads(json_text)
            logging.debug(f"Successfully parsed JSON with keys: {list(extracted_data.keys())}")
        except (json.JSONDecodeError, ValueError) as err:
            logging.error(f"JSON parsing error: {str(err)}")
            if json_text:
                logging.error(f"Failed JSON content: {json_text}")
            # Create a basic empty structure as fallback
            extracted_data = {field: "" if field not in ['items', 'total_amount', 'service_charge', 'sscl_tax', 'vat_tax'] 
                             else [] if field == 'items' else 0 
                             for field in RECEIPT_FIELDS}
        
        # Ensure all required fields are present
        for field in RECEIPT_FIELDS:
            if field not in extracted_data:
                if field == 'items':
                    extracted_data[field] = []
                elif field in ['total_amount', 'service_charge', 'sscl_tax', 'vat_tax']:
                    extracted_data[field] = 0
                else:
                    extracted_data[field] = ""
        
        return extracted_data
    
    except Exception as e:
        logging.error(f"Error in Gemini Vision processing: {str(e)}")
        return None

# Authentication Routes
@app.route('/login')
def login():
    """Render the login page with social login options."""
    return render_template('login.html')
    
@app.route('/register')
def register():
    """Render the registration page for new users."""
    # Check if we have an invitation token from the URL
    invitation_token = request.args.get('invitation')
    if invitation_token:
        # Store token in session for post-registration processing
        session['invitation_token'] = invitation_token
        flash("Please complete registration to accept the invitation.", "info")
        logging.info(f"Registration page accessed with invitation token: {invitation_token}")
    
    return render_template('register.html')

@app.route('/verify-email/<token>')
def verify_email(token):
    """Verify a user's email address using a verification token."""
    try:
        # Get the user based on the token
        from models import User
        user = User.query.filter_by(email_verification_token=token).first()
        if not user:
            flash('Invalid or expired verification link. Please request a new one.', 'danger')
            return redirect(url_for('login'))
            
        # Verify the token
        from email_verification import verify_token
        try:
            email = verify_token(token, user.email_verification_salt)
            
            # Check if the email matches the user's email
            if email != user.email:
                flash('Invalid verification link. Please request a new one.', 'danger')
                return redirect(url_for('login'))
                
            # Mark the user as verified
            user.is_email_verified = True
            user.email_verification_token = None
            user.email_verification_salt = None
            db.session.commit()
            
            # If user is logged in, redirect to dashboard
            if current_user.is_authenticated:
                flash('Your email has been verified! You now have access to all features.', 'success')
                return redirect(url_for('index'))
            else:
                flash('Your email has been verified! Please log in to continue.', 'success')
                return redirect(url_for('login'))
                
        except SignatureExpired:
            flash('The verification link has expired. Please request a new one.', 'warning')
            return redirect(url_for('verify_email_reminder'))
            
        except BadSignature:
            flash('Invalid verification link. Please request a new one.', 'danger')
            return redirect(url_for('login'))
            
    except Exception as e:
        logging.error(f"Error verifying email: {str(e)}")
        flash('An error occurred while verifying your email. Please try again.', 'danger')
        return redirect(url_for('login'))

@app.route('/verify-email-reminder')
@login_required
def verify_email_reminder():
    """Show a reminder page for email verification."""
    return render_template('verify_email_reminder.html')

@app.route('/resend-verification-email')
@login_required
def resend_verification_email():
    """Resend the verification email."""
    try:
        # Don't allow resending if already verified
        if current_user.is_email_verified:
            flash('Your email is already verified!', 'info')
            return redirect(url_for('index'))
        
        # Check if we sent an email recently (within 15 minutes)
        if current_user.email_verification_sent_at:
            last_sent = current_user.email_verification_sent_at
            now = datetime.now()
            time_diff = (now - last_sent).total_seconds() / 60  # Minutes
            
            if time_diff < 15:
                minutes_left = int(15 - time_diff)
                flash(f'Please wait {minutes_left} more minutes before requesting another verification email.', 'warning')
                return redirect(url_for('verify_email_reminder'))
        
        # Send a new verification email
        from email_verification import send_verification_email
        success = send_verification_email(current_user)
        
        if success:
            db.session.commit()  # Save the new token and timestamp
            flash('A new verification email has been sent. Please check your inbox.', 'success')
        else:
            flash('Failed to send verification email. Please try again later.', 'danger')
            
        return redirect(url_for('verify_email_reminder'))
        
    except Exception as e:
        logging.error(f"Error resending verification email: {str(e)}")
        flash('An error occurred. Please try again later.', 'danger')
        return redirect(url_for('verify_email_reminder'))

@app.route('/email_login', methods=['POST'])
def email_login():
    """Handle email-based login, registration, and password reset."""
    from models import User
    from werkzeug.security import generate_password_hash, check_password_hash
    import traceback
    
    email = request.form.get('email')
    password = request.form.get('password')
    action = request.form.get('action')
    
    logging.info(f"Login attempt for email: {email}, action: {action}")
    
    if not email or not password:
        logging.warning("Email or password missing in form data")
        flash('Email and password are required', 'danger')
        return redirect(url_for('login'))
    
    try:
        if action == 'login':
            # Login flow
            user = User.query.filter_by(email=email).first()
            
            if not user:
                logging.info(f"User with email {email} not found")
                flash('Email not registered. Please use the "Register / Reset Password" button below to create an account.', 'warning')
                return redirect(url_for('login'))
            
            if not user.password_hash:
                logging.info(f"User {email} exists but has no password hash")
                flash('Account exists but no password is set. Please use the "Register / Reset Password" button to set a password.', 'warning')
                return redirect(url_for('login'))
            
            logging.info(f"Checking password for user {email}, password_hash: {user.password_hash[:20]}...")
            password_match = check_password_hash(user.password_hash, password)
            logging.info(f"Password match result: {password_match}")
                
            if not password_match:
                flash('Incorrect password. Please try again or use the "Register / Reset Password" button if you forgot your password.', 'danger')
                return redirect(url_for('login'))
            
            # User authenticated successfully
            login_user(user)
            # We'll handle success feedback through the animation instead of flash messages
            
            # Check if there's a pending invitation token in the session
            invitation_token = session.get('invitation_token')
            if invitation_token:
                # Import required models
                from models import FriendInvitation, OrganizationInvitation
                
                # Process friend invitation
                try:
                    # Check for friend invitation first
                    friend_invitation = FriendInvitation.query.filter_by(token=invitation_token).first()
                    if friend_invitation and not friend_invitation.accepted and friend_invitation.expires_at > datetime.utcnow():
                        # Mark invitation as accepted
                        friend_invitation.accepted = True
                        friend_invitation.accepted_at = datetime.utcnow()
                        friend_invitation.accepted_by_user_id = user.id
                        friend_invitation.converted_to_user_id = user.id
                        
                        # Record who sent the invitation to grant them rewards
                        inviter = friend_invitation.invited_by
                        
                        # Grant reward to the inviter (extra days of access)
                        reward_days = 7  # One week free for each successful invitation
                        friend_invitation.reward_days_granted = reward_days
                        friend_invitation.reward_granted_at = datetime.utcnow()
                        
                        # Update inviter's stats
                        inviter.successful_invites_count += 1
                        inviter.earned_access_days += reward_days
                        
                        # Add trust points for successful invitation
                        from models import TrustActivity
                        trust_activity = TrustActivity(
                            user_id=inviter.id,
                            activity_type='invitation_accepted',
                            points=25,  # High value for successful invitations
                            description=f'Invitation accepted by: {user.email}'
                        )
                        db.session.add(trust_activity)
                        db.session.commit()
                        
                        # Clear the token from session
                        session.pop('invitation_token', None)
                        
                        # Access invited_by relationship instead of sender (which doesn't exist)
                        flash(f"You've successfully accepted the invitation from {friend_invitation.invited_by.name}!", "success")
                        logging.info(f"User {user.id} accepted friend invitation with token {invitation_token}")
                        
                        # Redirect to profile page to show the accepted invitation
                        return redirect(url_for('profile'))
                    
                    # Check for organization invitation
                    org_invitation = OrganizationInvitation.query.filter_by(token=invitation_token).first()
                    if org_invitation and not org_invitation.accepted and not org_invitation.is_expired():
                        logging.info(f"Processing organization invitation for user {user.id} with token {invitation_token}")
                        
                        # Redirect to the organization invitation acceptance route
                        # Clear the token from session as it will be processed by the organization route
                        session.pop('invitation_token', None)
                        
                        # Redirect to the organization invitation acceptance route
                        return redirect(url_for('organizations.accept_invitation', token=invitation_token))
                    
                    # If we get here, neither invitation was valid
                    logging.warning(f"Invalid or expired invitation token in session: {invitation_token}")
                    session.pop('invitation_token', None)
                    
                except Exception as e:
                    logging.error(f"Error processing invitation after login: {str(e)}")
                    logging.error(traceback.format_exc())
            
            # Redirect directly to scan page for better user experience
            return redirect(url_for('index'))
            
        elif action == 'register':
            # Import enhanced registration logger
            from registration_logger import (
                log_registration_start, 
                log_registration_success, 
                log_registration_error,
                log_organization_creation
            )
            
            # Log start of registration process
            log_registration_start(email, 'standard')
            
            try:
                # Registration and password reset flow
                existing_user = User.query.filter_by(email=email).first()
                
                if existing_user:
                    # We no longer allow password resets through this route
                    log_registration_error(email, "Attempted to register with existing email", 'standard', False)
                    flash('An account with this email already exists. Please sign in with your password or contact an administrator for assistance.', 'warning')
                    return redirect(url_for('register'))
                
                # This is a new registration
                logging.info(f"Creating new user: {email}")
                # Calculate initial 30-day access period
                from datetime import datetime, timedelta
                initial_expiration = datetime.utcnow() + timedelta(days=30)
                
                new_user = User(
                    email=email,
                    password_hash=generate_password_hash(password),
                    name=email.split('@')[0],  # Use part of email as name
                    role='user',  # Set default role
                    subscription_status='free_trial',
                    access_expiration_date=initial_expiration  # Set initial 30-day access period
                )
                
                db.session.add(new_user)
                db.session.commit()
                
                # Log successful user creation
                log_registration_success(new_user.id, email, 'standard')
                
                # Create Personal Finances organization for the new user
                # We're using a single database transaction for both the user and organization
                try:
                    log_organization_creation(new_user.id, email, status="started")
                    from user_organization_helper import create_personal_finances_organization
                    
                    # Pass commit=False to use our current transaction
                    org = create_personal_finances_organization(new_user, commit=False)
                    
                    # Send email verification
                    try:
                        from email_verification import send_verification_email
                        send_verification_email(new_user)
                        logging.info(f"Verification email sent to {email}")
                    except Exception as email_error:
                        logging.error(f"Failed to send verification email to {email}: {str(email_error)}")
                        # Continue with registration even if email fails
                    
                    # Now commit the entire transaction (both user and organization)
                    db.session.commit()
                    
                    if org:
                        log_organization_creation(new_user.id, email, org.id, "success")
                    else:
                        # This shouldn't happen since we're raising exceptions now
                        log_organization_creation(new_user.id, email, status="failed")
                        
                except Exception as org_error:
                    # Log the error with details
                    log_registration_error(email, org_error, 'standard')
                    log_organization_creation(new_user.id, email, status="failed")
                    
                    # Rollback the transaction
                    db.session.rollback()
                    
                    # Since organization creation failed, we should also rollback the user
                    # and report a complete error to the user
                    flash('We encountered a problem creating your account. Please try again or contact support if the issue persists.', 'danger')
                    logging.error(f"Error creating Personal Finances organization: {str(org_error)}")
                    return redirect(url_for('register'))
                
                # Log in the new user
                login_user(new_user)
                
                # Display welcome message on first login - different from login success message
                flash('Account verification complete! Your account has been created and you are now logged in.', 'success')
                
                # Process any pending invitation
                invitation_token = session.get('invitation_token')
                if invitation_token:
                    try:
                        # Import required models
                        from models import FriendInvitation, OrganizationInvitation
                        
                        # Process friend invitation
                        friend_invitation = FriendInvitation.query.filter_by(token=invitation_token).first()
                        if friend_invitation and not friend_invitation.accepted and friend_invitation.expires_at > datetime.utcnow():
                            # Mark invitation as accepted
                            friend_invitation.accepted = True
                            friend_invitation.accepted_at = datetime.utcnow()
                            friend_invitation.accepted_by_user_id = new_user.id
                            db.session.commit()
                            logging.info(f"Accepted friend invitation with token {invitation_token} for new user {new_user.id}")
                    except Exception as inv_error:
                        logging.error(f"Error processing invitation for new user: {str(inv_error)}")
                
                # Redirect to scan page
                return redirect(url_for('index'))
                
            except Exception as e:
                # Log full registration error details
                log_registration_error(email, e, 'standard')
                
                # Clean up partial user data if needed
                db.session.rollback()
                
                # Show user-friendly error message
                flash('We encountered a problem creating your account. Please try again or contact support if the issue persists.', 'danger')
                return redirect(url_for('register'))
        
        else:
            logging.error(f"Invalid action value received: '{action}'. Expected 'login' or 'register'.")
            flash(f"Something went wrong. Please try again using the Sign In or Register button. (Invalid action: {action})", 'danger')
            return redirect(url_for('login'))
            
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in email login/registration: {str(e)}")
        logging.error(traceback.format_exc())  # Log the full traceback
        flash('We encountered a problem processing your request. Please try again or contact support if the issue persists.', 'danger')
        return redirect(url_for('login'))

@app.route('/auth/google')
def google_auth():
    """Initiate Google OAuth authentication."""
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    """Handle the Google OAuth callback."""
    try:
        token = google.authorize_access_token()
        resp = google.get('userinfo')
        user_info = resp.json()
        
        # Extract user data
        social_id = user_info['id']
        email = user_info['email']
        name = user_info.get('name', '')
        picture = user_info.get('picture', '')
        
        # Check if user exists
        from models import User
        user = User.query.filter_by(social_id=social_id).first()
        
        if not user:
            # Create a new user
            user = User(
                social_id=social_id,
                social_provider='google',
                email=email,
                name=name,
                profile_pic=picture
            )
            db.session.add(user)
            db.session.commit()
            
            # Create Personal Finances organization for new Google OAuth users
            try:
                from user_organization_helper import create_personal_finances_organization
                create_personal_finances_organization(user)
                logging.info(f"Created Personal Finances organization for new Google OAuth user {user.id}")
            except Exception as e:
                logging.error(f"Error creating Personal Finances organization for Google OAuth user: {str(e)}")
        
        # Log the user in
        login_user(user)
        flash('Successfully logged in with Google!', 'success')
        
        # Check if there's a pending invitation token in the session
        invitation_token = session.get('invitation_token')
        if invitation_token:
            # Import required models
            from models import FriendInvitation, OrganizationInvitation
            
            # Process the invitation
            try:
                # Check for friend invitation first
                friend_invitation = FriendInvitation.query.filter_by(token=invitation_token).first()
                if friend_invitation and not friend_invitation.accepted and friend_invitation.expires_at > datetime.utcnow():
                    # Mark invitation as accepted
                    friend_invitation.accepted = True
                    friend_invitation.accepted_at = datetime.utcnow()
                    friend_invitation.accepted_by_user_id = user.id
                    db.session.commit()
                    
                    # Clear the token from session
                    session.pop('invitation_token', None)
                    
                    # Access invited_by relationship instead of sender (which doesn't exist)
                    flash(f"You've successfully accepted the invitation from {friend_invitation.invited_by.name}!", "success")
                    logging.info(f"User {user.id} accepted friend invitation with token {invitation_token} after Google login")
                    
                    # Redirect to profile page to show the accepted invitation
                    return redirect(url_for('profile'))
                
                # Check for organization invitation
                org_invitation = OrganizationInvitation.query.filter_by(token=invitation_token).first()
                if org_invitation and not org_invitation.accepted and not org_invitation.is_expired():
                    logging.info(f"Processing organization invitation for user {user.id} with token {invitation_token} after Google login")
                    
                    # Redirect to the organization invitation acceptance route
                    # Clear the token from session as it will be processed by the organization route
                    session.pop('invitation_token', None)
                    
                    # Redirect to the organization invitation acceptance route
                    return redirect(url_for('organizations.accept_invitation', token=invitation_token))
                
                # If we get here, neither invitation was valid
                logging.warning(f"Invalid or expired invitation token in session after Google login: {invitation_token}")
                session.pop('invitation_token', None)
                
            except Exception as e:
                logging.error(f"Error processing invitation after Google login: {str(e)}")
                logging.error(traceback.format_exc())
        
        # Redirect directly to scan page for better user experience
        return redirect(url_for('index'))
    
    except Exception as e:
        logging.error(f"Error in Google authentication: {str(e)}")
        flash('Failed to log in with Google.', 'danger')
        return redirect(url_for('login'))

@app.route('/auth/facebook')
def facebook_auth():
    """Initiate Facebook OAuth authentication."""
    redirect_uri = url_for('facebook_callback', _external=True)
    return facebook.authorize_redirect(redirect_uri)

@app.route('/auth/facebook/callback')
def facebook_callback():
    """Handle the Facebook OAuth callback."""
    try:
        token = facebook.authorize_access_token()
        resp = facebook.get('me', params={'fields': 'id,name,email,picture'})
        user_info = resp.json()
        
        # Extract user data
        social_id = user_info['id']
        email = user_info.get('email')
        
        # Facebook might not return email if user hasn't shared it
        if not email:
            flash('Email access is required to use this service.', 'warning')
            return redirect(url_for('login'))
        
        name = user_info.get('name', '')
        picture = user_info.get('picture', {}).get('data', {}).get('url', '')
        
        # Check if user exists
        from models import User
        user = User.query.filter_by(social_id=social_id).first()
        
        if not user:
            # Create a new user
            user = User(
                social_id=social_id,
                social_provider='facebook',
                email=email,
                name=name,
                profile_pic=picture
            )
            db.session.add(user)
            db.session.commit()
            
            # Create Personal Finances organization for new Facebook OAuth users
            try:
                from user_organization_helper import create_personal_finances_organization
                create_personal_finances_organization(user)
                logging.info(f"Created Personal Finances organization for new Facebook OAuth user {user.id}")
            except Exception as e:
                logging.error(f"Error creating Personal Finances organization for Facebook OAuth user: {str(e)}")
        
        # Log the user in
        login_user(user)
        flash('Successfully logged in with Facebook!', 'success')
        
        # Check if there's a pending invitation token in the session
        invitation_token = session.get('invitation_token')
        if invitation_token:
            # Import required models
            from models import FriendInvitation, OrganizationInvitation
            
            # Process the invitation
            try:
                # Check for friend invitation first
                friend_invitation = FriendInvitation.query.filter_by(token=invitation_token).first()
                if friend_invitation and not friend_invitation.accepted and friend_invitation.expires_at > datetime.utcnow():
                    # Mark invitation as accepted
                    friend_invitation.accepted = True
                    friend_invitation.accepted_at = datetime.utcnow()
                    friend_invitation.accepted_by_user_id = user.id
                    db.session.commit()
                    
                    # Clear the token from session
                    session.pop('invitation_token', None)
                    
                    # Access invited_by relationship instead of sender (which doesn't exist)
                    flash(f"You've successfully accepted the invitation from {friend_invitation.invited_by.name}!", "success")
                    logging.info(f"User {user.id} accepted friend invitation with token {invitation_token} after Facebook login")
                    
                    # Redirect to profile page to show the accepted invitation
                    return redirect(url_for('profile'))
                
                # Check for organization invitation
                org_invitation = OrganizationInvitation.query.filter_by(token=invitation_token).first()
                if org_invitation and not org_invitation.accepted and not org_invitation.is_expired():
                    logging.info(f"Processing organization invitation for user {user.id} with token {invitation_token} after Facebook login")
                    
                    # Redirect to the organization invitation acceptance route
                    # Clear the token from session as it will be processed by the organization route
                    session.pop('invitation_token', None)
                    
                    # Redirect to the organization invitation acceptance route
                    return redirect(url_for('organizations.accept_invitation', token=invitation_token))
                
                # If we get here, neither invitation was valid
                logging.warning(f"Invalid or expired invitation token in session after Facebook login: {invitation_token}")
                session.pop('invitation_token', None)
                
            except Exception as e:
                logging.error(f"Error processing invitation after Facebook login: {str(e)}")
                logging.error(traceback.format_exc())
        
        # Redirect directly to scan page for better user experience
        return redirect(url_for('index'))
    
    except Exception as e:
        logging.error(f"Error in Facebook authentication: {str(e)}")
        flash('Failed to log in with Facebook.', 'danger')
        return redirect(url_for('login'))

@app.route('/logout', methods=['GET', 'POST'] if not CSRF_HARDENING_ENABLED else ['POST'])
@login_required
def logout():
    """Log the user out."""
    print(f"DEBUG: Logout route accessed with CSRF_HARDENING_ENABLED={CSRF_HARDENING_ENABLED}")
    logout_user()
    # Let's not redirect to login page with a flash message
    # The user knows they logged out, and it clutters the login page
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    """Show the user's profile page."""
    try:
        logging.info(f"Accessing profile page for user: {current_user.id} - {current_user.name}")
        
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Get friend invitations sent by the user
        friend_invitations = FriendInvitation.query.filter_by(
            invited_by_user_id=current_user.id
        ).order_by(FriendInvitation.created_at.desc()).all()
        
        # Process invitations to ensure proper display and status
        for invitation in friend_invitations:
            # Check if the invited person is an existing user
            from models import User
            existing_user = User.query.filter_by(email=invitation.email).first()
            invitation.is_existing_user = existing_user is not None
            
            # Fix the is_expired check by using a closure pattern
            def make_is_expired(inv): 
                exp_date = inv.expires_at
                return lambda: datetime.utcnow() > exp_date
                
            invitation.is_expired = make_is_expired(invitation)
            
            # Hide the token attribute to prevent it from showing in the template
            # but keep it accessible for backend operations
            invitation._token = invitation.token
            
            # Modify how the invitation is rendered in string context to prevent token leakage
            invitation.__str__ = lambda: f"Invitation to {invitation.email}"
            invitation.__repr__ = lambda: f"Invitation to {invitation.email}"
        
        # Get trust activity statistics
        from models import TrustActivity
        
        # Reset the invitation counter if needed (e.g., if it's a new day)
        current_user.reset_invitation_counter()
            
        # Count successful invitations
        successful_invites = FriendInvitation.query.filter_by(
            invited_by_user_id=current_user.id,
            accepted=True
        ).count()
        
        # Get invitation stats
        invitation_stats = {
            'sent_total': len(friend_invitations),
            'sent_today': current_user.invitations_sent_today,
            'daily_limit': current_user.get_daily_invitation_limit(),
            'successful': successful_invites,
            'available': current_user.get_available_invitations()
        }
        
        # Get recently earned trust points
        recent_activities = TrustActivity.query.filter_by(
            user_id=current_user.id
        ).order_by(TrustActivity.created_at.desc()).limit(5).all()
        
        # Get comprehensive trust activity summary
        trust_summary = current_user.get_trust_activity_summary()
        
        # Get subscription information
        subscription_info = {
            'status': current_user.get_subscription_status_display(),
            'days_remaining': current_user.get_days_remaining(),
            'is_expired': current_user.is_access_expired(),
            'expiration_date': current_user.access_expiration_date
        }
        
        # Prepare template context for error logging
        template_context = {
            'sent_friend_invitations': f"<List[{len(friend_invitations)}]>",
            'invitation_stats': invitation_stats,
            'recent_activities': f"<List[{len(recent_activities)}]>",
            'trust_summary': trust_summary,
            'subscription_info': subscription_info
        }
        
        try:
            return render_template(
                'profile.html', 
                sent_friend_invitations=friend_invitations,
                invitation_stats=invitation_stats,
                recent_activities=recent_activities,
                trust_summary=trust_summary,
                subscription_info=subscription_info,
                debug_timestamp=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            )
        except Exception as template_error:
            # Import here to avoid circular imports
            from error_logger import log_template_error
            log_template_error('profile.html', str(template_error), profile, template_context)
            flash(f"Error loading profile template: {str(template_error)}", "danger")
            return redirect(url_for('home'))
    except Exception as e:
        logging.error(f"Error rendering profile page: {str(e)}")
        logging.error(traceback.format_exc())
        flash(f"Error loading profile: {str(e)}", "danger")
        return redirect(url_for('home'))

@app.route('/invite-friends')
@login_required
def invite_friends():
    """Show the invite friends page with trust level and invitation quota information."""
    # Check if we need to reset the daily invitation counter
    current_user.reset_invitation_counter()
    
    # Get invitation stats
    invitation_stats = {
        'daily_limit': current_user.get_daily_invitation_limit(),
        'sent_today': current_user.invitations_sent_today,
        'available': current_user.get_available_invitations(),
        'trust_level': current_user.trust_level,
        'trust_level_name': current_user.get_trust_level_name()
    }
    
    return render_template('invite_friends.html', 
                          invitation_stats=invitation_stats)
    
@app.route('/update-trust-ranking-visibility', methods=['POST'])
@login_required
def update_trust_ranking_visibility():
    """Update the user's visibility in trust rankings."""
    try:
        data = request.get_json()
        show_in_ranking = data.get('show_in_ranking', False)
        
        # Update the user's preference
        current_user.show_in_trust_ranking = show_in_ranking
        
        # If showing in ranking for the first time, set consent timestamp
        if show_in_ranking and not current_user.trust_ranking_consent_at:
            current_user.trust_ranking_consent_at = datetime.utcnow()
        
        db.session.commit()
        
        # Log the activity
        from models import TrustActivity
        activity_type = 'trust_ranking_opt_in' if show_in_ranking else 'trust_ranking_opt_out'
        description = 'Opted into trust ranking system' if show_in_ranking else 'Opted out of trust ranking system'
        
        trust_activity = TrustActivity(
            user_id=current_user.id,
            activity_type=activity_type,
            points=0,  # No points for this administrative action
            description=description
        )
        db.session.add(trust_activity)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating trust ranking visibility: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/resend-invitation', methods=['POST'])
@login_required
def resend_invitation():
    """Resend an expired friend invitation."""
    invitation_id = request.form.get('invitation_id')
    if not invitation_id:
        flash('Invalid invitation ID.', 'danger')
        return redirect(url_for('profile'))
    
    try:
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Find the invitation by ID and verify ownership
        invitation = FriendInvitation.query.filter_by(
            id=invitation_id, 
            invited_by_user_id=current_user.id
        ).first()
        
        if not invitation:
            flash('Invitation not found or you are not authorized to modify it.', 'danger')
            return redirect(url_for('profile'))
        
        # Update expiration date
        invitation.expires_at = datetime.utcnow() + timedelta(days=7)
        db.session.commit()
        
        # Resend the email
        if send_invitation_email(invitation.email, current_user, invitation.personal_message, invitation.token):
            flash(f'Invitation to {invitation.email} has been resent!', 'success')
        else:
            flash(f'Failed to resend invitation to {invitation.email}.', 'danger')
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error resending invitation: {str(e)}")
        flash('An error occurred while resending the invitation.', 'danger')
    
    return redirect(url_for('profile'))

@app.route('/cancel-invitation', methods=['POST'])
@login_required
def cancel_invitation():
    """Cancel a pending friend invitation."""
    invitation_id = request.form.get('invitation_id')
    if not invitation_id:
        flash('Invalid invitation ID.', 'danger')
        return redirect(url_for('profile'))
    
    try:
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Find the invitation by ID and verify ownership
        invitation = FriendInvitation.query.filter_by(
            id=invitation_id, 
            invited_by_user_id=current_user.id
        ).first()
        
        if not invitation:
            flash('Invitation not found or you are not authorized to modify it.', 'danger')
            return redirect(url_for('profile'))
        
        # Delete the invitation
        db.session.delete(invitation)
        db.session.commit()
        
        flash(f'Invitation to {invitation.email} has been canceled.', 'success')
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error canceling invitation: {str(e)}")
        flash('An error occurred while canceling the invitation.', 'danger')
    
    return redirect(url_for('profile'))

@app.route('/send-invitations', methods=['POST'])
@login_required
def send_invitations():
    """Process and send friend invitations via email with trust-based limits."""
    emails_input = request.form.get('emails', '')
    personal_message = request.form.get('message', '')
    
    # Process email list (split by commas and clean)
    email_list = [email.strip() for email in emails_input.split(',') if email.strip()]
    
    if not email_list:
        flash('Please enter at least one email address.', 'warning')
        return redirect(url_for('invite_friends'))
    
    # Check available invitations
    available_invitations = current_user.get_available_invitations()
    if available_invitations <= 0:
        flash('You have reached your daily invitation limit. Please try again tomorrow.', 'warning')
        return redirect(url_for('invite_friends'))
    
    # Limit the number of emails to process based on available invitations
    if len(email_list) > available_invitations:
        email_list = email_list[:available_invitations]
        flash(f'Only processing the first {available_invitations} email(s) due to your daily invitation limit.', 'info')
    
    # Initialize counters for success/failure reporting
    sent_count = 0
    failed_emails = []
    
    # Import the FriendInvitation model
    from models import FriendInvitation
    
    for email in email_list:
        try:
            # Use more comprehensive email validation
            from email_validator import validate_email, EmailNotValidError
            
            try:
                # Validate the email
                valid = validate_email(email)
                # Convert to normal form (lowercase, etc)
                normalized_email = valid.email
                
                # Check for existing invitation
                existing_invitation = FriendInvitation.query.filter_by(
                    invited_by_user_id=current_user.id,
                    email=normalized_email,
                    accepted=False
                ).first()
                
                if existing_invitation and not existing_invitation.is_expired():
                    # Update the invitation rather than creating a new one
                    expires_at = datetime.utcnow() + timedelta(days=7)
                    existing_invitation.expires_at = expires_at
                    existing_invitation.personal_message = personal_message
                    db.session.commit()
                    invitation = existing_invitation
                else:
                    # Create a new invitation with token
                    token = str(uuid.uuid4())
                    expires_at = datetime.utcnow() + timedelta(days=7)
                    
                    invitation = FriendInvitation(
                        invited_by_user_id=current_user.id,
                        email=normalized_email,
                        token=token,
                        expires_at=expires_at,
                        personal_message=personal_message
                    )
                    
                    db.session.add(invitation)
                    db.session.commit()
                
                # Send invitation email
                if send_invitation_email(normalized_email, current_user, personal_message, invitation.token):
                    sent_count += 1
                    # Track invitation in user's daily count
                    current_user.track_sent_invitation()
                    
                    # Track in trust activity system
                    from models import TrustActivity
                    trust_activity = TrustActivity(
                        user_id=current_user.id,
                        activity_type='invitation_sent',
                        points=5,
                        description=f'Sent invitation to: {normalized_email}'
                    )
                    db.session.add(trust_activity)
                    db.session.commit()
                    
                    logging.info(f"Successfully sent invitation to {normalized_email}")
                else:
                    failed_emails.append(normalized_email)
                    logging.warning(f"Failed to send invitation to {normalized_email}")
            except EmailNotValidError as e:
                failed_emails.append(f"{email} (invalid: {str(e)})")
                logging.warning(f"Invalid email format: {email} - {str(e)}")
                
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Error sending invitation to {email}: {str(e)}")
            logging.error(f"Stack trace: {error_details}")
            failed_emails.append(email)
    
    # Provide feedback to the user
    if sent_count > 0:
        flash(f'Successfully sent {sent_count} invitation(s)!', 'success')
    
    if failed_emails:
        flash(f'Failed to send to: {", ".join(failed_emails)}', 'warning')
    
    return redirect(url_for('invite_friends'))

# Email sending function for invitations
@app.route('/accept-invitation/<token>', methods=['GET', 'POST'])
def accept_friend_invitation(token):
    """Handle friend invitation acceptance via unique token."""
    try:
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Find the invitation by token
        invitation = FriendInvitation.query.filter_by(token=token).first()
        
        if not invitation:
            flash("Invalid invitation link. The invitation may have been removed or the link is incorrect.", "danger")
            return redirect(url_for('home'))
            
        # Check if invitation has expired
        if invitation.expires_at < datetime.utcnow():
            flash("This invitation has expired. Please ask your friend to send a new invitation.", "warning")
            return redirect(url_for('home'))
            
        # Check if invitation has already been accepted
        if invitation.accepted:
            flash("You have already accepted this invitation. Please log in to continue.", "info")
            return redirect(url_for('login'))
            
        # If it's a GET request, show the confirmation page
        if request.method == 'GET':
            return render_template('confirm_friend_invitation.html', invitation=invitation, token=token)
        
        # If it's a POST request, process the acceptance
        elif request.method == 'POST':
            # If user is logged in, mark as accepted
            if current_user.is_authenticated:
                invitation.accepted = True
                invitation.accepted_at = datetime.utcnow()
                invitation.accepted_by_user_id = current_user.id
                db.session.commit()
                
                # Access invited_by relationship instead of sender (which doesn't exist)
                flash(f"You've successfully accepted the invitation from {invitation.invited_by.name}!", "success")
                return redirect(url_for('profile'))
            else:
                # Store token in session for after registration/login
                session['invitation_token'] = token
                flash("Please sign up or log in to accept the invitation.", "info")
                return redirect(url_for('register', invitation=token))
    
    except Exception as e:
        logging.error(f"Error processing invitation acceptance: {str(e)}")
        flash("An error occurred while processing the invitation. Please try again later.", "danger")
        return redirect(url_for('home'))

def send_invitation_email(to_email, from_user, personal_message='', token=None):
    """
    Send an invitation email to a friend using SendGrid.
    
    Args:
        to_email: Recipient's email address
        from_user: User object of the sender
        personal_message: Optional personal message
        token: Optional invitation token for tracking acceptance
        
    Returns:
        Boolean indicating success or failure
    """
    import traceback  # For detailed error logging
    import os
    import json
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    
    # Import the SendGrid logger for detailed API logging
    from sendgrid_logger import (
        log_api_request, 
        log_api_response, 
        log_api_error, 
        log_email_content
    )
    
    try:
        # Get the app URL for links
        app_url = request.host_url.rstrip('/')
        
        # Create the subject for email
        subject = f"{from_user.name} invites you to join DevelopSriLanka.com"
        
        # Log the email attempt
        logging.info(f"Sending friend invitation email to: {to_email}")
        logging.info(f"From: {from_user.name} <{from_user.email}>")
        logging.info(f"Subject: {subject}")
        
        # Get the SendGrid API key
        sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
        
        # Check if SendGrid API key is configured
        if not sendgrid_api_key:
            logging.warning("SendGrid API key is not configured. Email will not be sent.")
            logging.info(f"Would have sent friend invitation email to: {to_email}")
            logging.info(f"Subject: {subject}")
            logging.info(f"With personal message: {personal_message}")
            flash("Email feature requires SendGrid API key configuration", "warning")
            return False
        
        # Create HTML email with branding
        # Note: keeping variable name as 'sender' in template for compatibility
        html_content = render_template('email/friend_invitation.html',
                                sender=from_user,  # Template uses 'sender', though model uses 'invited_by'
                                personal_message=personal_message,
                                app_url=app_url,
                                token=token,
                                current_year=datetime.utcnow().year)
        
        # Use the verified sender email address from SendGrid account
        sender_email = 'info@developsrilanka.com'
        
        # Log which sender email we're using
        logging.info(f"Using verified SendGrid sender email address: {sender_email}")
            
        # Validate the recipient email address 
        import re
        recipient_email = to_email.strip().lower()
        # More comprehensive email validation pattern
        email_pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*[a-zA-Z0-9]@[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$'
        
        if not re.match(email_pattern, recipient_email):
            logging.error(f"Invalid recipient email format: {recipient_email}")
            return False
            
        # Create SendGrid mail message with proper sender formatting
        message = Mail(
            from_email=sender_email,  # Just use plain email address
            to_emails=recipient_email,  # Use the validated and normalized email
            subject=subject,
            html_content=html_content
        )
        
        # Set the friendly display name separately for better compatibility
        message.from_email.name = f"{from_user.name} via DevelopSriLanka"
        
        # Send the email using SendGrid
        try:
            # Log email sending details using both regular logger and SendGrid logger
            logging.info(f"Preparing to send friend invitation email with SendGrid:")
            logging.info(f"From: {from_user.name} via DevelopSriLanka <{sender_email}>")
            logging.info(f"To: {recipient_email}")
            logging.info(f"Subject: {subject}")
            
            # Use detailed SendGrid logger to log the API request
            log_api_request(
                recipient_email=recipient_email, 
                sender_email=sender_email,
                sender_name=f"{from_user.name} via DevelopSriLanka",
                subject=subject
            )
            
            # Log the email content for debugging
            log_email_content(html_content=html_content)
            
            # Create SendGrid client and send message
            sg = SendGridAPIClient(sendgrid_api_key)
            
            # Capture raw API request data for logging
            api_request = message.get()
            print(f"SENDGRID API REQUEST: {json.dumps(api_request, indent=2)}")
            
            # Send the message
            response = sg.send(message)
            
            # Log the response with both loggers
            logging.info(f"SendGrid response status code: {response.status_code}")
            logging.info(f"SendGrid response body: {response.body}")
            logging.info(f"SendGrid response headers: {response.headers}")
            
            # Use detailed SendGrid logger for response
            log_api_response(
                status_code=response.status_code,
                response_body=response.body,
                response_headers=response.headers
            )
            
            # Log successful sending
            logging.info(f"Successfully sent friend invitation via SendGrid to {recipient_email}")
            print(f"SENDGRID EMAIL SENT: Successfully sent friend invitation to {recipient_email}")
            
            # Create a success message with SPAM folder notice in red text
            from markupsafe import Markup
            success_message = Markup(f'Invitation sent successfully to {recipient_email}! <span style="color: red;">(Please ask your friend to check their SPAM folder if not visible in inbox)</span>')
            flash(success_message, 'success')
            
            return True
            
        except Exception as sendgrid_error:
            error_details = traceback.format_exc()
            
            # Standard logger
            logging.error(f"==== SENDGRID ERROR DETAILS ====")
            logging.error(f"Failed to send friend invitation via SendGrid to {recipient_email}")
            logging.error(f"Error type: {type(sendgrid_error).__name__}")
            logging.error(f"Error message: {str(sendgrid_error)}")
            logging.error(f"From email: {sender_email}")
            logging.error(f"From name: {from_user.name} via DevelopSriLanka")
            logging.error(f"Full error traceback: {error_details}")
            logging.error(f"==== END SENDGRID ERROR DETAILS ====")
            
            # Use detailed SendGrid logger for error
            log_api_error(
                error=sendgrid_error,
                recipient_email=recipient_email,
                error_type=type(sendgrid_error).__name__,
                detailed_traceback=True
            )
            
            # Also print to console for immediate debugging
            print(f"SENDGRID EMAIL ERROR: Failed to send friend invitation to {recipient_email}")
            print(f"Error type: {type(sendgrid_error).__name__}")
            print(f"Error message: {str(sendgrid_error)}")
            
            flash(f"Failed to send invitation: {str(sendgrid_error)}", "danger")
            return False
            
    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Failed to prepare friend invitation email: {str(e)}")
        logging.error(f"Error details: {error_details}")
        flash(f"Friend invitation system error: {str(e)}", "danger")
        return False

# Update the middleware to handle authentication required routes
@app.before_request
def check_authentication():
    """Check if user is authenticated for protected routes and set default organization."""
    # Set up default organization for authenticated users
    if current_user.is_authenticated:
        from models import Organization, OrganizationUser
        # Get the user's default organization - usually the first one they're a member of
        user_org = OrganizationUser.query.filter_by(user_id=current_user.id).first()
        if user_org:
            g.default_organization = Organization.query.get(user_org.organization_id)
        else:
            g.default_organization = None
    else:
        g.default_organization = None
            
    # Paths that require authentication
    protected_paths = [
        '/scan', 
        '/history', 
        '/analytics', 
        '/profile',
        '/receipts',
        '/export',
        '/export/excel',
        '/save',
        '/invite-friends',
        '/send-invitations'
    ]
    
    # Check for paths that start with these prefixes
    protected_prefixes = [
        '/receipts/',
        '/view_receipt/',
        '/reports/'
    ]
    
    # Check if the current path is protected and user is not authenticated
    is_protected = (
        request.path in protected_paths or
        any(request.path.startswith(prefix) for prefix in protected_prefixes)
    )
    
    if is_protected and not current_user.is_authenticated:
        # Store the requested URL for redirecting after login
        session['next'] = request.url
        flash('Please log in to access this page.', 'info')
        return redirect(url_for('login'))

@app.route('/api/log_client_error', methods=['POST'])
@csrf.exempt
def log_client_error():
    """
    API endpoint to receive and log client-side errors.
    This creates a bridge between frontend and backend error logging systems.
    """
    from error_logger import log_error, ErrorTypes
    
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No error data provided"}), 400
            
        # Extract required fields
        error_message = data.get('error_message', 'Unknown client error')
        component = data.get('component', 'client')
        error_type = data.get('error_type', ErrorTypes.CLIENT)
        additional_info = data.get('additional_info', {})
        
        # Add client metadata
        client_meta = {
            'user_agent': data.get('user_agent'),
            'url': data.get('url'),
            'client_error_id': data.get('error_id'),
            'client_timestamp': data.get('timestamp')
        }
        
        # Merge with any existing additional info
        if isinstance(additional_info, dict):
            additional_info.update(client_meta)
        else:
            additional_info = client_meta
            
        # Log the error using our backend system
        error_id = log_error(
            error=error_message,
            error_type=error_type,
            component=component,
            additional_info=additional_info,
            log_level="ERROR",
            capture_request=True
        )
        
        return jsonify({
            "success": True, 
            "error_id": error_id,
            "message": "Error logged successfully"
        })
        
    except Exception as e:
        # Log the exception in logging the client error
        logging.error(f"Error logging client error: {str(e)}")
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": "Server error processing client error log"
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
