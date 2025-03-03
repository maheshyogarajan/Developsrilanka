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
    "vendor_address",
    "vendor_contact",
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
            
            logging.debug(f"JSON text to parse: {json_text[:200]}...")
            
            # Parse the JSON response
            extracted_data = json.loads(json_text)
            logging.debug(f"Successfully parsed JSON with keys: {list(extracted_data.keys())}")
        except json.JSONDecodeError as json_err:
            logging.error(f"JSON parsing error: {str(json_err)}")
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
