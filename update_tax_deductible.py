
import os
import json
import logging
from flask import Flask
import google.generativeai as genai
from app import app, db
from models import Receipt, ReceiptItem
from sri_lanka_tax_rules import get_classifier

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Configure Gemini API
try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
except Exception as e:
    logging.error(f"Error configuring Gemini API: {str(e)}")

def classify_tax_deductible(item_data, receipt_data):
    """
    Classify item tax deductibility using Sri Lankan tax rules.
    
    Args:
        item_data: Dictionary containing item information
        receipt_data: Dictionary containing parent receipt information
    
    Returns:
        Dictionary with comprehensive tax deductibility information:
        - tax_deductible: Boolean
        - deductibility_percentage: 0-100
        - tax_law_reference: String reference to tax law
        - classification_confidence: 0.0-1.0
        - deduction_notes: Detailed explanation
    """
    try:
        logging.debug(f"Starting tax deductibility classification for item: {item_data['name']}")
        
        # Use the Sri Lankan tax rules classifier
        classifier = get_classifier()
        
        # Classify the item
        classification = classifier.classify_item(
            item_name=item_data.get('name', ''),
            item_price=item_data.get('price', 0),
            vendor_name=receipt_data.get('vendor_name', ''),
            major_category=receipt_data.get('expense_major_category', ''),
            minor_category=receipt_data.get('expense_minor_category', '')
        )
        
        logging.info(f"Classification result for '{item_data['name']}': {classification['deductibility_percentage']}% deductible, confidence={classification['classification_confidence']:.2f}")
        logging.debug(f"Tax law reference: {classification['tax_law_reference']}")
        
        return classification
        
    except Exception as e:
        logging.error(f"Error in tax deductibility classification: {str(e)}")
        # Return conservative default classification
        return {
            'tax_deductible': False,
            'deductibility_percentage': 0,
            'tax_law_reference': 'Section 25 - Inland Revenue Act (error in classification)',
            'classification_confidence': 0.0,
            'deduction_notes': f'Classification error: {str(e)}. Defaulting to non-deductible for safety.'
        }

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
                
                # Get enhanced classification with Sri Lankan tax rules
                classification = classify_tax_deductible(item_dict, receipt_dict)
                
                # Update item with comprehensive tax deductibility information
                item.tax_deductible = classification['tax_deductible']
                item.deductibility_percentage = classification['deductibility_percentage']
                item.tax_law_reference = classification['tax_law_reference']
                item.classification_confidence = classification['classification_confidence']
                item.deduction_notes = classification['deduction_notes']
                
                updated_count += 1
                logging.info(f"Updated item '{item.name}': {classification['deductibility_percentage']}% deductible (confidence: {classification['classification_confidence']:.2f})")
                
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
