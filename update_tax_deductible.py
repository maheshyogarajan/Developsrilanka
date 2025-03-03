
import os
import json
import logging
from flask import Flask
import google.generativeai as genai
from app import app, db
from models import Receipt, ReceiptItem

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Configure Gemini API
try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
except Exception as e:
    logging.error(f"Error configuring Gemini API: {str(e)}")

def classify_tax_deductible(item_data, receipt_data):
    """
    Use Gemini to classify if an item is tax deductible.
    
    Args:
        item_data: Dictionary containing item information
        receipt_data: Dictionary containing parent receipt information
    
    Returns:
        Boolean indicating if the item is tax deductible
    """
    try:
        logging.debug(f"Starting tax deductibility classification for item: {item_data['name']}")
        
        # Configure Gemini model
        try:
            # Try the newer model first
            model = genai.GenerativeModel('gemini-1.5-flash')
            logging.debug("Using gemini-1.5-flash model")
        except Exception as model_error:
            logging.warning(f"Could not use gemini-1.5-flash, falling back: {str(model_error)}")
            model = genai.GenerativeModel('gemini-pro')
            logging.debug("Using gemini-pro model")
        
        # Create a prompt with the item data
        prompt = f"""
        Analyze this purchase item and determine if it's tax deductible as a business expense:
        
        Item Name: {item_data['name']}
        Item Price: {item_data['price']}
        Vendor Name: {receipt_data['vendor_name']}
        Receipt Major Category: {receipt_data['expense_major_category'] or 'Not classified'}
        Receipt Minor Category: {receipt_data['expense_minor_category'] or 'Not classified'}
        
        Provide ONLY a JSON response with a single field:
        - tax_deductible: true/false boolean indicating if this item would likely be tax deductible as a business expense
        
        Common tax deductible items include: business supplies, equipment, travel for business purposes, professional services, etc.
        Items that are personal in nature or primarily for enjoyment/entertainment should be marked as not tax deductible.
        
        Return ONLY the JSON object with the boolean field mentioned above and nothing else.
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
        is_tax_deductible = classification.get('tax_deductible', False)
        logging.debug(f"Tax deductibility classification result: {is_tax_deductible}")
        
        return is_tax_deductible
        
    except Exception as e:
        logging.error(f"Error in tax deductibility classification: {str(e)}")
        return False  # Default to not tax deductible if there's an error

def update_tax_deductible_status():
    """
    Update tax deductibility status for receipt items where it hasn't been set.
    """
    with app.app_context():
        try:
            # Find receipt items with default tax_deductible status (False)
            # We'll check items where the field exists but is set to the default False value
            items_to_update = ReceiptItem.query.filter_by(tax_deductible=False).all()
            
            logging.info(f"Found {len(items_to_update)} receipt items to check for tax deductibility")
            
            updated_count = 0
            
            # Process each item
            for item in items_to_update:
                # Get the parent receipt for context
                receipt = Receipt.query.get(item.receipt_id)
                if not receipt:
                    logging.warning(f"Could not find parent receipt for item ID {item.id}")
                    continue
                
                receipt_dict = receipt.to_dict()
                item_dict = item.to_dict()
                
                logging.debug(f"Processing item ID {item.id}: '{item.name}' from receipt ID {receipt.id}")
                
                # Get classification from Gemini
                is_tax_deductible = classify_tax_deductible(item_dict, receipt_dict)
                
                # Only update if the classification suggests it's tax deductible
                # (since the default is already False)
                if is_tax_deductible:
                    item.tax_deductible = True
                    updated_count += 1
                    logging.info(f"Updated item '{item.name}' to tax_deductible=True")
                    
                    # Save changes for this item
                    db.session.commit()
            
            logging.info(f"Updated {updated_count} items to be tax deductible out of {len(items_to_update)} checked")
            return updated_count
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating tax deductibility status: {str(e)}")
            return 0

if __name__ == "__main__":
    count = update_tax_deductible_status()
    print(f"Updated {count} receipt items to be tax deductible")
