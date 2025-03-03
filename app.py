import os
import json
import base64
import logging
from io import BytesIO
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, flash, redirect, url_for
import google.generativeai as genai
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Database setup
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev_secret_key")

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

@app.route('/')
def index():
    """Render the main page of the application."""
    return render_template('index.html')

@app.route('/history')
def receipt_history():
    """Render the receipt history page."""
    return render_template('receipts.html')

@app.route('/analytics')
def analytics():
    """Render the analytics page with expense category summaries."""
    from models import Receipt
    from sqlalchemy import func
    
    try:
        # Get total expenses
        total_expenses = db.session.query(func.sum(Receipt.total_amount)).scalar() or 0
        
        # Get total by expense categories (major)
        major_categories = db.session.query(
            Receipt.expense_major_category,
            func.sum(Receipt.total_amount).label('total')
        ).filter(Receipt.expense_major_category != None, Receipt.expense_major_category != '')\
        .group_by(Receipt.expense_major_category)\
        .order_by(func.sum(Receipt.total_amount).desc())\
        .all()
        
        # Get total by expense subcategories (minor)
        minor_categories = db.session.query(
            Receipt.expense_minor_category,
            func.sum(Receipt.total_amount).label('total')
        ).filter(Receipt.expense_minor_category != None, Receipt.expense_minor_category != '')\
        .group_by(Receipt.expense_minor_category)\
        .order_by(func.sum(Receipt.total_amount).desc())\
        .all()
        
        # Get tax totals
        tax_summary = db.session.query(
            func.sum(Receipt.vat_tax).label('vat_tax'),
            func.sum(Receipt.sscl_tax).label('sscl_tax')
        ).first()
        
        # Convert to dictionaries for easier template handling
        major_category_data = [{'category': cat, 'total': total} for cat, total in major_categories]
        minor_category_data = [{'category': cat, 'total': total} for cat, total in minor_categories]
        
        # Total count of receipts
        receipt_count = Receipt.query.count()
        
        # Get receipt data for the chart (last 10 receipts)
        recent_receipts = Receipt.query.order_by(Receipt.date.desc()).limit(10).all()
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
            recent_receipts=recent_receipt_data
        )
    
    except Exception as e:
        logging.error(f"Error generating analytics: {str(e)}")
        return render_template('analytics.html', error=str(e))

@app.route('/receipts/by_major_category/<category>')
def receipts_by_major_category(category):
    """Get receipts for a specific major expense category."""
    from models import Receipt
    
    try:
        # URL decode the category name if needed
        from urllib.parse import unquote
        category = unquote(category)
        
        # Query receipts for the specified category
        receipts = Receipt.query.filter_by(expense_major_category=category)\
            .order_by(Receipt.date.desc()).all()
        
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
def receipts_by_minor_category(category):
    """Get receipts for a specific minor expense category."""
    from models import Receipt
    
    try:
        # URL decode the category name if needed
        from urllib.parse import unquote
        category = unquote(category)
        
        # Query receipts for the specified subcategory
        receipts = Receipt.query.filter_by(expense_minor_category=category)\
            .order_by(Receipt.date.desc()).all()
        
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

@app.route('/scan', methods=['POST'])
def scan_receipt():
    """Process the uploaded receipt image and extract data using Gemini Vision."""
    if 'receipt' not in request.files:
        return jsonify({'error': 'No receipt image uploaded'}), 400
    
    receipt_file = request.files['receipt']
    if receipt_file.filename == '':
        return jsonify({'error': 'No receipt image selected'}), 400
    
    try:
        # Read and process the image
        img_bytes = receipt_file.read()
        img = Image.open(BytesIO(img_bytes))
        
        # Process with Gemini Vision API
        extracted_data = process_receipt_with_gemini(img)
        
        if not extracted_data:
            return jsonify({'error': 'Failed to extract data from the receipt'}), 500
        
        # Store the extracted data in session for potential correction
        session['receipt_data'] = extracted_data
        
        return jsonify({'success': True, 'data': extracted_data})
    
    except Exception as e:
        logging.error(f"Error processing receipt: {str(e)}")
        return jsonify({'error': f'Error processing receipt: {str(e)}'}), 500

@app.route('/update_data', methods=['POST'])
def update_data():
    """Update the extracted receipt data with user corrections."""
    try:
        updated_data = request.json
        
        if 'receipt_data' not in session:
            return jsonify({'error': 'No receipt data found in session'}), 400
            
        # Update the session data with corrected values
        session['receipt_data'] = updated_data
        
        return jsonify({'success': True, 'data': updated_data})
    
    except Exception as e:
        logging.error(f"Error updating data: {str(e)}")
        return jsonify({'error': f'Error updating data: {str(e)}'}), 500

@app.route('/save', methods=['POST'])
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
        
        # Create a new receipt
        new_receipt = Receipt(
            vendor_name=receipt_data.get('vendor_name', 'Unknown Vendor'),
            vendor_address=receipt_data.get('vendor_address'),
            vendor_contact=receipt_data.get('vendor_contact'),
            date=receipt_date,
            total_amount=float(receipt_data.get('total_amount', 0)),
            service_charge=float(receipt_data.get('service_charge', 0)),
            vat_registration_number=receipt_data.get('vat_registration_number'),
            sscl_tax=float(receipt_data.get('sscl_tax', 0)),
            vat_tax=float(receipt_data.get('vat_tax', 0)),
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
                quantity=float(item.get('quantity', 1)),
                price=float(item.get('price', 0))
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
def list_receipts():
    """Get a list of all saved receipts."""
    from models import Receipt
    
    try:
        # Query all receipts, ordered by date (newest first)
        receipts = Receipt.query.order_by(Receipt.date.desc()).all()
        
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

@app.route('/receipts/<int:receipt_id>', methods=['GET'])
def get_receipt(receipt_id):
    """Get details of a specific receipt."""
    from models import Receipt
    
    try:
        # Find the receipt by ID
        receipt = Receipt.query.get(receipt_id)
        
        if not receipt:
            return jsonify({'error': 'Receipt not found'}), 404
        
        # Convert to dictionary with all details
        receipt_dict = receipt.to_dict()
        
        return jsonify({'success': True, 'receipt': receipt_dict})
    
    except Exception as e:
        logging.error(f"Error getting receipt: {str(e)}")
        return jsonify({'error': f'Error getting receipt: {str(e)}'}), 500
        
@app.route('/view_receipt/<int:receipt_id>')
def view_receipt(receipt_id):
    """Display a specific receipt detail page."""
    from models import Receipt
    from utils import format_currency
    
    try:
        # Find the receipt by ID
        receipt = Receipt.query.get(receipt_id)
        
        if not receipt:
            flash('Receipt not found', 'danger')
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
def export_data():
    """Export the extracted receipt data as JSON."""
    if 'receipt_data' not in session:
        return jsonify({'error': 'No receipt data to export'}), 400
        
    return jsonify(session['receipt_data'])

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
        - items: An array of objects, each with "name", "quantity", and "price" fields
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
