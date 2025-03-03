import os
import json
import base64
import logging
from io import BytesIO
from flask import Flask, render_template, request, jsonify, session, flash, redirect, url_for
import google.generativeai as genai
from PIL import Image

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev_secret_key")

# Configure Gemini API
try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
except Exception as e:
    logging.error(f"Error configuring Gemini API: {str(e)}")

# Receipt data schema for extraction
RECEIPT_FIELDS = [
    "vendor_name",
    "date",
    "items",
    "total_amount",
    "service_charge",
    "vat_registration_number",
    "sscl_tax",
    "vat_tax"
]

@app.route('/')
def index():
    """Render the main page of the application."""
    return render_template('index.html')

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
        # Convert PIL image to bytes for Gemini
        img_byte_arr = BytesIO()
        image.save(img_byte_arr, format=image.format if image.format else 'JPEG')
        img_byte_arr = img_byte_arr.getvalue()
        
        # Configure Gemini model
        model = genai.GenerativeModel('gemini-pro-vision')
        
        # Create the prompt for receipt data extraction
        prompt = """
        Extract the following information from this receipt image and format it as a JSON object:
        - vendor_name: The name of the store or business
        - date: The date of purchase (format: YYYY-MM-DD)
        - items: An array of objects, each with "name", "quantity", and "price" fields
        - total_amount: The total amount paid
        - service_charge: Any service charge mentioned (0 if none)
        - vat_registration_number: The VAT registration number if present (empty string if none)
        - sscl_tax: Social Security Contribution Levy amount if present (0 if none)
        - vat_tax: The VAT tax amount if present (0 if none)
        
        Return ONLY the JSON object and nothing else. If you cannot find a particular field, use an empty string or 0 depending on the field type.
        """
        
        # Generate content with Gemini Vision
        response = model.generate_content([prompt, image])
        
        # Parse the response to extract JSON
        response_text = response.text
        
        # If the response contains markdown code blocks, extract just the JSON part
        if "```json" in response_text:
            json_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_text = response_text.split("```")[1].strip()
        else:
            json_text = response_text.strip()
        
        # Parse the JSON response
        extracted_data = json.loads(json_text)
        
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
