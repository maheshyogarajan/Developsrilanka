
import os
import json
import logging
from flask import Flask
import google.generativeai as genai
from app import app, db
from models import Receipt

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Configure Gemini API
try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
except Exception as e:
    logging.error(f"Error configuring Gemini API: {str(e)}")

def classify_expense(receipt_data):
    """
    Use Gemini to classify receipt data into expense categories.
    
    Args:
        receipt_data: Dictionary containing receipt information
    
    Returns:
        Dictionary with major and minor expense categories
    """
    try:
        logging.debug("Starting expense classification with Gemini")
        
        # Configure Gemini model
        try:
            # Try the newer model first
            model = genai.GenerativeModel('gemini-2.0-flash')
            logging.debug("Using gemini-2.0-flash model")
        except Exception as model_error:
            logging.warning(f"Could not use gemini-2.0-flash, falling back: {str(model_error)}")
            # Use a text-capable model for category classification
            model = genai.GenerativeModel('gemini-1.5-pro')
            logging.debug("Using gemini-1.5-pro model")
        
        # Create a prompt with the receipt data
        prompt = f"""
        Analyze this receipt data and classify it according to IFRS expense categories:
        
        Vendor Name: {receipt_data['vendor_name']}
        Items: {', '.join([item['name'] for item in receipt_data['items']]) if receipt_data['items'] else 'No items listed'}
        Total Amount: {receipt_data['total_amount']}
        
        Provide the most appropriate expense category classification in JSON format with these fields:
        - expense_major_category: The primary IFRS expense category this receipt belongs to
        - expense_minor_category: The subcategory within the major expense category
        
        Major categories include: Operating Expenses, Cost of Goods Sold, Administrative Expenses, Selling Expenses, Research and Development, Finance Costs, Employee Benefits.
        
        Minor categories include: Office Supplies, Travel and Transportation, Meals and Entertainment, Utilities, Rent, Professional Services, Marketing and Advertising, Repairs and Maintenance, IT Services, Telecommunications.
        
        Return ONLY the JSON object with the two fields mentioned above and nothing else.
        """
        
        # Generate content with Gemini
        logging.debug("Sending classification request to Gemini API")
        response = model.generate_content(prompt)
        logging.debug("Received response from Gemini API")
        
        # Parse the response to extract JSON
        response_text = response.text
        logging.debug(f"Raw response text: {response_text[:200]}...")
        
        # Extract JSON
        if "```json" in response_text:
            json_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_text = response_text.split("```")[1].strip()
        else:
            json_text = response_text.strip()
        
        # Check if the response starts with a curly brace (JSON object)
        if not json_text.startswith('{'):
            import re
            json_match = re.search(r'(\{.*\})', json_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                raise ValueError("Could not find valid JSON object in response")
                
        # Parse the JSON response
        classification = json.loads(json_text)
        logging.debug(f"Classification result: {classification}")
        
        return classification
        
    except Exception as e:
        logging.error(f"Error in expense classification: {str(e)}")
        return {
            "expense_major_category": "Operating Expenses",  # Default fallback
            "expense_minor_category": "Other"
        }

def update_expense_categories():
    """
    Update expense categories for receipts where they are missing.
    """
    with app.app_context():
        try:
            # Find receipts with missing categories
            missing_categories = Receipt.query.filter(
                (Receipt.expense_major_category == None) | 
                (Receipt.expense_major_category == '') |
                (Receipt.expense_minor_category == None) |
                (Receipt.expense_minor_category == '')
            ).all()
            
            logging.info(f"Found {len(missing_categories)} receipts with missing categories")
            
            # Process each receipt
            for receipt in missing_categories:
                receipt_dict = receipt.to_dict()
                logging.debug(f"Processing receipt ID {receipt.id} from {receipt.vendor_name}")
                
                # Get classification from Gemini
                classification = classify_expense(receipt_dict)
                
                # Update the receipt
                if not receipt.expense_major_category or receipt.expense_major_category == '':
                    receipt.expense_major_category = classification.get('expense_major_category')
                    logging.info(f"Updated major category to: {receipt.expense_major_category}")
                
                if not receipt.expense_minor_category or receipt.expense_minor_category == '':
                    receipt.expense_minor_category = classification.get('expense_minor_category')
                    logging.info(f"Updated minor category to: {receipt.expense_minor_category}")
                
                # Save changes
                db.session.commit()
                logging.info(f"Updated receipt ID {receipt.id} successfully")
            
            logging.info("All receipts with missing categories have been updated")
            return len(missing_categories)
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating expense categories: {str(e)}")
            return 0

if __name__ == "__main__":
    count = update_expense_categories()
    print(f"Updated {count} receipts with missing expense categories")
