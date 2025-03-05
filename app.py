import os
import json
import base64
import logging
import time
import traceback  # Added import
from io import BytesIO
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, flash, redirect, url_for, send_file
import google.generativeai as genai
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text  # Add SQLAlchemy text support for direct queries
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
import requests
from flask_mail import Mail, Message

# Import Celery task queue components
from celery import Celery
from celery.result import AsyncResult

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Database setup
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Initialize Flask app
app = Flask(__name__)

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
    from models import User
    return User.query.get(int(user_id))

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

@app.route('/')
def home():
    """Render the homepage with welcome message and features."""
    return render_template('home.html')

@app.route('/preview')
def preview():
    """Render the receipt preview page without login requirement."""
    return render_template('preview.html')

@app.route('/scan')
@login_required
def index():
    """Render the receipt scanning page of the application."""
    return render_template('index.html')

@app.route('/history')
@login_required
def receipt_history():
    """Render the receipt history page."""
    return render_template('receipts.html')

@app.route('/analytics')
@login_required
def analytics():
    """Render the analytics page with expense category summaries."""
    from models import Receipt, ReceiptItem
    from sqlalchemy import func
    
    try:
        # Get total expenses for the current user
        total_expenses = db.session.query(func.sum(Receipt.total_amount))\
            .filter(Receipt.user_id == current_user.id).scalar() or 0
        
        # Get total by expense categories (major) for the current user
        major_categories = db.session.query(
            Receipt.expense_major_category,
            func.sum(Receipt.total_amount).label('total')
        ).filter(
            Receipt.user_id == current_user.id,
            Receipt.expense_major_category != None, 
            Receipt.expense_major_category != ''
        )\
        .group_by(Receipt.expense_major_category)\
        .order_by(func.sum(Receipt.total_amount).desc())\
        .all()
        
        # Get total by expense subcategories (minor) for the current user
        minor_categories = db.session.query(
            Receipt.expense_minor_category,
            func.sum(Receipt.total_amount).label('total')
        ).filter(
            Receipt.user_id == current_user.id,
            Receipt.expense_minor_category != None, 
            Receipt.expense_minor_category != ''
        )\
        .group_by(Receipt.expense_minor_category)\
        .order_by(func.sum(Receipt.total_amount).desc())\
        .all()
        
        # Get tax totals for the current user
        tax_summary = db.session.query(
            func.sum(Receipt.vat_tax).label('vat_tax'),
            func.sum(Receipt.sscl_tax).label('sscl_tax')
        ).filter(Receipt.user_id == current_user.id).first()
        
        # Calculate total tax deductible amount using the Receipt model method
        # This approach ensures we don't double count or exceed the total expense
        receipts = Receipt.query.filter_by(user_id=current_user.id).all()
        
        # Get the tax deductible amount for each receipt, ensuring none exceeds its total_amount
        total_tax_deductible = 0.0
        for receipt in receipts:
            tax_deductible = receipt.get_tax_deductible_amount()
            # The method already has a min() check, but let's be extra safe
            total_tax_deductible += min(tax_deductible, receipt.total_amount)
        
        # Convert to dictionaries for easier template handling
        major_category_data = [{'category': cat, 'total': total} for cat, total in major_categories]
        minor_category_data = [{'category': cat, 'total': total} for cat, total in minor_categories]
        
        # Total count of receipts for current user
        receipt_count = Receipt.query.filter_by(user_id=current_user.id).count()
        
        # Get receipt data for the chart (last 10 receipts for current user)
        recent_receipts = Receipt.query.filter_by(user_id=current_user.id).order_by(Receipt.date.desc()).limit(10).all()
        recent_receipt_data = [
            {
                'date': receipt.date.strftime('%Y-%m-%d') if receipt.date else 'Unknown',
                'vendor': receipt.vendor_name,
                'amount': receipt.total_amount,
                'category': receipt.expense_major_category or 'Uncategorized'
            } for receipt in recent_receipts
        ]
        
        return render_template(
            'analytics.html',
            total_expenses=total_expenses,
            major_categories=major_category_data,
            minor_categories=minor_category_data,
            tax_summary=tax_summary,
            receipt_count=receipt_count,
            recent_receipts=recent_receipt_data,
            total_tax_deductible=total_tax_deductible
        )
    
    except Exception as e:
        logging.error(f"Error generating analytics: {str(e)}")
        return render_template('analytics.html', error=str(e))

@app.route('/tax-savings', methods=['GET', 'POST'])
@login_required
def tax_savings():
    """Render the tax savings calculator page."""
    from models import Receipt, UserIncome
    from sqlalchemy import func
    
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
                'process_receipt_with_gemini'
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
            extracted_data = process_receipt_with_gemini(img)
            
            if not extracted_data:
                return jsonify({'error': 'Failed to extract data from the receipt'}), 500
            
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
                
                # Store the extracted data in session for potential correction
                session['receipt_data'] = result
                
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
            
        # Update the session data with corrected values
        session['receipt_data'] = updated_data
        
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
            
        # Update the session data with corrected values
        session['receipt_data'] = updated_data
        
        response = jsonify({'success': True, 'data': updated_data})
        response.headers.set('Content-Type', 'application/json')
        return response
    
    except Exception as e:
        logging.error(f"Error updating data: {str(e)}")
        response = jsonify({'error': f'Error updating data: {str(e)}'})
        response.headers.set('Content-Type', 'application/json')
        return response, 500

@app.route('/save', methods=['POST'])
@login_required
def save_receipt():
    """Save the receipt data to the database."""
    from models import Receipt, ReceiptItem
    
    try:
        # Check if we have receipt data in the session
        if 'receipt_data' not in session:
            return jsonify({'error': 'No receipt data to save'}), 400
        
        receipt_data = session['receipt_data']
        
        # Parse date if it exists
        receipt_date = None
        if receipt_data.get('date'):
            try:
                receipt_date = datetime.strptime(receipt_data['date'], '%Y-%m-%d').date()
            except ValueError:
                logging.warning(f"Invalid date format: {receipt_data['date']}")
        
        # Create a new receipt and associate it with the current user
        new_receipt = Receipt(
            user_id=current_user.id,  # Set the user_id from the logged in user
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
            expense_minor_category=receipt_data.get('expense_minor_category')
        )
        
        # Add the receipt to the database session
        db.session.add(new_receipt)
        db.session.flush()  # This assigns an ID to new_receipt without committing
        
        # Add receipt items if they exist
        items = receipt_data.get('items', [])
        for item in items:
            new_item = ReceiptItem(
                receipt_id=new_receipt.id,
                name=item.get('name', 'Unknown Item'),
                quantity=float(item.get('quantity', 1) or 1),
                price=float(item.get('price', 0) or 0),
                tax_deductible=bool(item.get('tax_deductible', False))
            )
            db.session.add(new_item)
        
        # Commit all changes
        db.session.commit()
        
        # Return success with the receipt ID
        return jsonify({
            'success': True, 
            'message': 'Receipt saved successfully',
            'receipt_id': new_receipt.id
        })
    
    except Exception as e:
        # Roll back in case of error
        db.session.rollback()
        logging.error(f"Error saving receipt: {str(e)}")
        return jsonify({'error': f'Error saving receipt: {str(e)}'}), 500

@app.route('/receipts', methods=['GET'])
@login_required
def list_receipts():
    """Get a list of all saved receipts for the current user."""
    from models import Receipt
    
    try:
        # Query receipts for the current user, ordered by date (newest first)
        receipts = Receipt.query.filter_by(user_id=current_user.id).order_by(Receipt.date.desc()).all()
        
        # Convert to list of dictionaries
        receipt_list = []
        for receipt in receipts:
            receipt_dict = receipt.to_dict()
            # Simplify the output for the list view
            receipt_dict.pop('items', None)  # Remove items to reduce payload size
            receipt_list.append(receipt_dict)
        
        return jsonify({'success': True, 'receipts': receipt_list})
    
    except Exception as e:
        logging.error(f"Error listing receipts: {str(e)}")
        return jsonify({'error': f'Error listing receipts: {str(e)}'}), 500
        
@app.route('/receipts/delete', methods=['POST'])
@login_required
def delete_receipts():
    """Delete multiple receipts by ID, ensuring they belong to the current user."""
    from models import Receipt
    
    try:
        # Get receipt IDs from request
        data = request.get_json()
        receipt_ids = data.get('receipt_ids', [])
        
        if not receipt_ids:
            return jsonify({'error': 'No receipt IDs provided'}), 400
        
        # Convert string IDs to integers
        receipt_ids = [int(id) for id in receipt_ids]
        
        # Find receipts to delete that belong to the current user
        receipts_to_delete = Receipt.query.filter(
            Receipt.id.in_(receipt_ids),
            Receipt.user_id == current_user.id
        ).all()
        
        deleted_count = len(receipts_to_delete)
        
        # Delete each receipt (will cascade to items thanks to relationship setup)
        for receipt in receipts_to_delete:
            db.session.delete(receipt)
        
        # Commit the transaction
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'deleted_count': deleted_count,
            'message': f'Successfully deleted {deleted_count} receipt(s)'
        })
    
    except Exception as e:
        # Roll back in case of error
        db.session.rollback()
        logging.error(f"Error deleting receipts: {str(e)}")
        return jsonify({'error': f'Error deleting receipts: {str(e)}'}), 500

@app.route('/receipts/<int:receipt_id>', methods=['GET'])
@login_required
def get_receipt(receipt_id):
    """Get details of a specific receipt that belongs to the current user."""
    from models import Receipt
    
    try:
        # Find the receipt by ID and user_id to ensure ownership
        receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
        
        if not receipt:
            return jsonify({'error': 'Receipt not found or does not belong to you'}), 404
        
        # Convert to dictionary with all details
        receipt_dict = receipt.to_dict()
        
        return jsonify({'success': True, 'receipt': receipt_dict})
    
    except Exception as e:
        logging.error(f"Error getting receipt: {str(e)}")
        return jsonify({'error': f'Error getting receipt: {str(e)}'}), 500
        
@app.route('/view_receipt/<int:receipt_id>')
@login_required
def view_receipt(receipt_id):
    """Display a specific receipt detail page for the current user."""
    from models import Receipt
    from utils import format_currency
    
    try:
        # Find the receipt by ID and user_id to ensure ownership
        receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
        
        if not receipt:
            flash('Receipt not found or does not belong to you', 'danger')
            return redirect(url_for('receipt_history'))
        
        return render_template(
            'view_receipt.html', 
            receipt=receipt, 
            format_currency=format_currency
        )
        
    except Exception as e:
        logging.error(f"Error viewing receipt: {str(e)}")
        flash(f'Error viewing receipt: {str(e)}', 'danger')
        return redirect(url_for('receipt_history'))

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
    """Export the current user's receipts as Excel file."""
    import pandas as pd
    import io
    from models import Receipt
    from datetime import datetime
    
    try:
        # Query the current user's receipts, ordered by date (newest first)
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
    Process the receipt image using Gemini Vision API.
    
    Args:
        image: PIL Image object of the receipt
    
    Returns:
        Dictionary containing extracted receipt data
    """
    try:
        logging.debug("Starting receipt image processing with Gemini Vision")
        
        # Convert PIL image to bytes for Gemini
        img_byte_arr = BytesIO()
        image.save(img_byte_arr, format=image.format if image.format else 'JPEG')
        img_bytes = img_byte_arr.getvalue()
        logging.debug(f"Image converted to bytes, size: {len(img_bytes)} bytes")
        
        # Use base64 encoding for the image data
        import base64
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')
        logging.debug(f"Image encoded to base64")
        
        # Configure Gemini model - using the latest compatible version
        try:
            # Try the newer model first (gemini-1.5-flash)
            model = genai.GenerativeModel('gemini-1.5-flash')
            logging.debug("Using gemini-1.5-flash model")
        except Exception as model_error:
            logging.warning(f"Could not use gemini-1.5-flash, falling back to gemini-pro-vision: {str(model_error)}")
            # Fall back to the original model if needed
            model = genai.GenerativeModel('gemini-pro-vision')
            logging.debug("Using gemini-pro-vision model")
        
        # Create the prompt for receipt data extraction
        prompt = """
        Extract the following information from this receipt image and format it as a JSON object:
        - vendor_name: The name of the store or business
        - vendor_address: The full address of the business (empty string if none)
        - vendor_contact: Phone number, email, or website of the business (empty string if none)
        - date: The date of purchase (format: YYYY-MM-DD)
        - items: An array of objects, each with "name", "quantity", "price", and "tax_deductible" fields (tax_deductible should be a boolean)
        - total_amount: The total amount paid
        - service_charge: Any service charge mentioned (0 if none)
        - vat_registration_number: The VAT registration number if present (empty string if none)
        - sscl_tax: Social Security Contribution Levy amount if present (0 if none)
        - vat_tax: The VAT tax amount if present (0 if none)
        - expense_major_category: The primary IFRS expense category this receipt belongs to
        - expense_minor_category: The subcategory within the major expense category
        
        For IFRS expense categories, analyze the vendor name, items, and overall receipt to determine appropriate classifications.
        Major categories include: Operating Expenses, Cost of Goods Sold, Administrative Expenses, Selling Expenses, Research and Development, Finance Costs, Employee Benefits.
        Minor categories include: Office Supplies, Travel and Transportation, Meals and Entertainment, Utilities, Rent, Professional Services, Marketing and Advertising, Repairs and Maintenance, IT Services, Telecommunications.
        
        For tax_deductible field, determine if each item would likely be tax deductible as a business expense. 
        Common tax deductible items include: business supplies, equipment, travel for business purposes, professional services, etc. 
        Items that are personal in nature or primarily for enjoyment/entertainment should be marked as not tax deductible.
        
        Return ONLY the JSON object and nothing else. If you cannot find a particular field, use an empty string or 0 depending on the field type.
        """
        
        # Generate content with Gemini Vision
        logging.debug("Preparing to send request to Gemini API")
        
        # Create the proper format for the image
        image_part = {
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": img_b64
            }
        }
        
        # Send the request to Gemini API
        logging.debug("Sending request to Gemini API")
        response = model.generate_content([prompt, image_part])
        logging.debug("Received response from Gemini API")
        
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
            
            # Redirect directly to scan page for better user experience
            return redirect(url_for('index'))
            
        elif action == 'register':
            # Registration and password reset flow
            existing_user = User.query.filter_by(email=email).first()
            
            if existing_user:
                # We no longer allow password resets through this route
                logging.warning(f"Attempted to register with existing email: {email}")
                flash('An account with this email already exists. Please sign in with your password or contact an administrator for assistance.', 'warning')
                return redirect(url_for('login'))
            
            # This is a new registration
            logging.info(f"Creating new user: {email}")
            new_user = User(
                email=email,
                password_hash=generate_password_hash(password),
                name=email.split('@')[0],  # Use part of email as name
                role='user'  # Set default role
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            # Log in the new user
            login_user(new_user)
            
            # Display welcome message on first login
            flash('Welcome! Your account has been created successfully.', 'success')
            
            # Animation will handle feedback
            return redirect(url_for('index'))
        
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
        
        # Log the user in
        login_user(user)
        flash('Successfully logged in with Google!', 'success')
        
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
        
        # Log the user in
        login_user(user)
        flash('Successfully logged in with Facebook!', 'success')
        
        # Redirect directly to scan page for better user experience
        return redirect(url_for('index'))
    
    except Exception as e:
        logging.error(f"Error in Facebook authentication: {str(e)}")
        flash('Failed to log in with Facebook.', 'danger')
        return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    """Log the user out."""
    logout_user()
    # Let's not redirect to login page with a flash message
    # The user knows they logged out, and it clutters the login page
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    """Show the user's profile page."""
    return render_template('profile.html')

@app.route('/invite-friends')
@login_required
def invite_friends():
    """Show the invite friends page."""
    return render_template('invite_friends.html')

@app.route('/send-invitations', methods=['POST'])
@login_required
def send_invitations():
    """Process and send friend invitations via email."""
    emails_input = request.form.get('emails', '')
    personal_message = request.form.get('message', '')
    
    # Process email list (split by commas and clean)
    email_list = [email.strip() for email in emails_input.split(',') if email.strip()]
    
    if not email_list:
        flash('Please enter at least one email address.', 'warning')
        return redirect(url_for('invite_friends'))
    
    # Initialize counters for success/failure reporting
    sent_count = 0
    failed_emails = []
    
    for email in email_list:
        try:
            # Use more comprehensive email validation
            from email_validator import validate_email, EmailNotValidError
            
            try:
                # Validate the email
                valid = validate_email(email)
                # Convert to normal form (lowercase, etc)
                normalized_email = valid.email
                
                # Send invitation email
                if send_invitation_email(normalized_email, current_user, personal_message):
                    sent_count += 1
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
def send_invitation_email(to_email, from_user, personal_message=''):
    """
    Send an invitation email to a friend.
    
    Args:
        to_email: Recipient's email address
        from_user: User object of the sender
        personal_message: Optional personal message
        
    Returns:
        Boolean indicating success or failure
    """
    try:
        # Use requests to send an email via SMTP server
        app_url = request.host_url.rstrip('/')
        
        # Construct email subject and body with updated brand name
        subject = f"{from_user.name} invites you to join DevelopSriLanka.com"
        
        # Email body with HTML formatting and DevelopSriLanka.com logo
        html_body = f"""
        <html>
        <body style="font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; background-color: #f9f9f9; padding: 15px;">
            <div style="background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; margin-bottom: 20px;">
                <!-- Header with logo and gradient background -->
                <div style="background: linear-gradient(135deg, #004b87 0%, #00285a 100%); padding: 25px; text-align: center;">
                    <img src="{app_url}/static/images/dev-sri-logo.jpg" alt="DevelopSriLanka.com Logo" style="max-width: 250px; height: auto; margin-bottom: 10px;">
                    <p style="color: rgba(255,255,255,0.9); margin: 5px 0 0; font-size: 16px;">Empowering financial intelligence</p>
                </div>
                
                <!-- Main content -->
                <div style="padding: 30px;">
                    <h2 style="color: #004b87; margin-top: 0; margin-bottom: 20px; font-size: 22px;">You've Been Invited!</h2>
                    <p style="margin-bottom: 20px;"><strong>{from_user.name}</strong> has invited you to join <strong>DevelopSriLanka.com</strong>, 
                    the intelligent platform that helps you track expenses and optimize tax savings.</p>
                    
                    {f'''<div style="background-color: #e6f7ff; padding: 15px; border-radius: 6px; border-left: 4px solid #004b87; margin: 25px 0;">
                        <p style="font-style: italic; margin: 0 0 10px;">"{personal_message}"</p>
                        <p style="text-align: right; margin-bottom: 0; color: #4a5568;">- {from_user.name}</p>
                    </div>''' if personal_message else ''}
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{app_url}/login" style="display: inline-block; background-color: #ff9e1b; color: white; 
                        padding: 12px 28px; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 16px;
                        transition: background-color 0.2s; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                            Join Now
                        </a>
                    </div>
                </div>
            </div>
            
            <!-- Features section -->
            <div style="background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 25px; margin-bottom: 20px;">
                <h3 style="color: #004b87; margin-top: 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px;">Why Join DevelopSriLanka.com?</h3>
                <ul style="padding-left: 20px; margin-top: 15px;">
                    <li style="margin-bottom: 10px;">Track expenses with AI-powered receipt scanning</li>
                    <li style="margin-bottom: 10px;">Calculate potential tax savings based on your income</li>
                    <li style="margin-bottom: 10px;">Effortlessly categorize and organize your business expenses</li>
                    <li style="margin-bottom: 10px;">Get insights to optimize your tax strategy</li>
                </ul>
            </div>
            
            <!-- Footer -->
            <div style="text-align: center; color: #718096; font-size: 0.9em; margin-top: 20px;">
                <p>If you don't want to receive invitation emails, please ignore this message.</p>
                <p>&copy; 2025 DevelopSriLanka.com. All rights reserved.</p>
            </div>
        </body>
        </html>
        """
        
        # Log the email attempt
        logging.info(f"Sending invitation email to: {to_email}")
        logging.info(f"From: {from_user.name} <{from_user.email}>")
        logging.info(f"Subject: {subject}")
        
        # Create a message object
        msg = Message(
            subject=subject,
            recipients=[to_email],
            html=html_body,
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        
        # Check if Gmail credentials are configured
        if not app.config['MAIL_USERNAME'] or not app.config['MAIL_PASSWORD']:
            logging.warning("Gmail credentials are not configured. Email will not be sent.")
            # For development purposes, log what would have been sent
            logging.info(f"Would have sent email to: {to_email}")
            logging.info(f"Subject: {subject}")
            logging.info(f"From: {from_user.name} <{from_user.email}>")
            logging.info(f"With personal message: {personal_message}")
            flash("Email feature requires Gmail credentials configuration", "warning")
            return False
        
        # Send the email using Flask-Mail with verbose logging
        try:
            # Log detailed email headers and info
            logging.info(f"Preparing to send email with the following details:")
            logging.info(f"From: {app.config['MAIL_DEFAULT_SENDER']}")
            logging.info(f"To: {to_email}")
            logging.info(f"Subject: {subject}")
            logging.info(f"SMTP Server: {app.config['MAIL_SERVER']}:{app.config['MAIL_PORT']}")
            logging.info(f"TLS Enabled: {app.config['MAIL_USE_TLS']}")
            logging.info(f"SSL Enabled: {app.config['MAIL_USE_SSL']}")
            
            # Actually send the email
            mail.send(msg)
            
            # Log successful sending
            logging.info(f"Successfully sent invitation email to {to_email}")
            logging.info(f"Email delivered to SMTP server for {to_email}")
            print(f"EMAIL SENT: Successfully delivered invitation to SMTP server for {to_email}")  # Console log for immediate visibility
            
            flash(f"Invitation sent successfully to {to_email}!", "success")
            return True
        except Exception as mail_error:
            error_details = traceback.format_exc()
            logging.error(f"Failed to send email to {to_email}: {str(mail_error)}")
            logging.error(f"Error details: {error_details}")
            logging.error(f"Mail configuration: USERNAME={app.config['MAIL_USERNAME']}, SERVER={app.config['MAIL_SERVER']}")
            print(f"EMAIL ERROR: Failed to send to {to_email}: {str(mail_error)}")  # Console log for immediate visibility
            
            flash(f"Failed to send invitation: {str(mail_error)}", "danger")
            return False
        
    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Failed to send invitation email: {str(e)}")
        logging.error(f"Error details: {error_details}")
        flash(f"Invitation system error: {str(e)}", "danger")
        return False

# Update the middleware to handle authentication required routes
@app.before_request
def check_authentication():
    """Check if user is authenticated for protected routes."""
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
        '/view_receipt/'
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
