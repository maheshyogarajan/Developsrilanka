
import os
import json
import logging
from typing import Literal
from flask import Flask
import google.generativeai as genai
from pydantic import BaseModel
from app import app, db
from models import Receipt

logging.basicConfig(level=logging.DEBUG)

try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
except Exception as e:
    logging.error(f"Error configuring Gemini API: {str(e)}")


class ExpenseClassification(BaseModel):
    """Pydantic schema for structured output from Gemini expense classification."""
    
    expense_major_category: Literal[
        "Operating Expenses",
        "Administrative Expenses",
        "Cost of Goods Sold",
        "Employee Benefits",
        "Finance Costs"
    ]
    expense_minor_category: Literal[
        "Meals and Entertainment",
        "Travel and Transportation",
        "Professional Services",
        "Office Supplies",
        "Marketing and Advertising",
        "Utilities",
        "Rent and Facilities",
        "Software and SaaS",
        "Bank and Merchant Fees",
        "Repairs and Maintenance",
        "Training, Education and Development",
        "Legal and Accounting",
        "Telecommunications",
        "Administrative and General"
    ]


def classify_expense(receipt_data):
    """
    Use Gemini structured outputs to classify receipt data into expense categories.
    
    Args:
        receipt_data: Dictionary containing receipt information
    
    Returns:
        Dictionary with major and minor expense categories
    """
    try:
        logging.debug("Starting expense classification with Gemini structured outputs")
        
        generation_config = {
            "response_mime_type": "application/json",
            "response_schema": ExpenseClassification
        }
        
        try:
            model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)
            logging.debug("Using gemini-2.5-flash model with structured outputs")
        except Exception as model_error:
            logging.warning(f"Could not use gemini-2.5-flash, falling back: {str(model_error)}")
            model = genai.GenerativeModel('gemini-2.5-flash-lite', generation_config=generation_config)
            logging.debug("Using gemini-2.5-flash-lite model with structured outputs")
        
        prompt = f"""
        Analyze this receipt data and classify it according to IFRS expense categories:
        
        Vendor Name: {receipt_data['vendor_name']}
        Items: {', '.join([item['name'] for item in receipt_data['items']]) if receipt_data['items'] else 'No items listed'}
        Total Amount: {receipt_data['total_amount']}
        
        Major categories: Operating Expenses, Cost of Goods Sold, Administrative Expenses, Employee Benefits, Finance Costs.
        
        Minor categories: Office Supplies, Travel and Transportation, Meals and Entertainment, Utilities, Rent and Facilities, Professional Services, Marketing and Advertising, Repairs and Maintenance, Software and SaaS, Bank and Merchant Fees, Training/Education/Development, Legal and Accounting, Telecommunications, Administrative and General.
        
        Classify appropriately based on the vendor and items.
        """
        
        logging.debug("Sending classification request to Gemini API")
        response = model.generate_content(prompt)
        logging.debug("Received response from Gemini API")
        
        if hasattr(response, 'text') and response.text:
            structured_data = json.loads(response.text)
            logging.info("Extracted structured classification from response.text")
            
            classification_obj = ExpenseClassification.model_validate(structured_data)
            classification = classification_obj.model_dump()
            logging.debug(f"Classification result: {classification}")
            return classification
        else:
            raise ValueError("No structured response from Gemini")
        
    except Exception as e:
        logging.error(f"Error in expense classification: {str(e)}")
        return {
            "expense_major_category": "Operating Expenses",
            "expense_minor_category": "Administrative and General"
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
