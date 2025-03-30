"""
Check the image paths for a specific expense directly from the database.
"""
import os
import logging
from flask import Flask
from app import app, db
from models import CompanyExpense, Receipt

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def check_expense_image(expense_id=32):
    """
    Check the image path for a specific expense.
    """
    try:
        with app.app_context():
            # Query the expense
            expense = CompanyExpense.query.get(expense_id)
            if not expense:
                logger.error(f"Expense with ID {expense_id} not found")
                return None
                
            # Query the associated receipt
            receipt = Receipt.query.get(expense.receipt_id)
            if not receipt:
                logger.error(f"Receipt with ID {expense.receipt_id} not found")
                return None
                
            # Log the image paths
            logger.info(f"Expense ID: {expense_id}")
            logger.info(f"Receipt ID: {receipt.id}")
            logger.info(f"Image path in receipt: {receipt.image_path}")
            logger.info(f"S3 key in receipt: {receipt.s3_key}")
            
            # Check if the image file exists
            if receipt.image_path:
                # Try different possible paths
                possible_paths = [
                    receipt.image_path,  # As stored
                    os.path.join('static', receipt.image_path),  # With static prefix
                    os.path.join('static', receipt.image_path.replace('static/', '')),  # Normalized
                ]
                
                for path in possible_paths:
                    if os.path.exists(path):
                        logger.info(f"Image file exists at: {path}")
                        break
                else:
                    logger.warning(f"Image file not found at any of the expected paths: {possible_paths}")
            else:
                logger.warning("No image path stored for this receipt")
                
            # Generate the image URL using the to_dict method
            receipt_dict = receipt.to_dict()
            image_url = receipt_dict.get('image_url')
            logger.info(f"Image URL from receipt.to_dict(): {image_url}")
            
            return {
                'expense_id': expense_id,
                'receipt_id': receipt.id,
                'image_path': receipt.image_path,
                's3_key': receipt.s3_key,
                'image_url': image_url
            }
    
    except Exception as e:
        logger.error(f"Error checking expense image: {str(e)}", exc_info=True)
        return None

if __name__ == "__main__":
    result = check_expense_image()
    if result:
        print(f"\nExpense ID: {result['expense_id']}")
        print(f"Receipt ID: {result['receipt_id']}")
        print(f"Image path: {result['image_path']}")
        print(f"S3 key: {result['s3_key']}")
        print(f"Image URL: {result['image_url']}")
    else:
        print("Failed to check expense image. See logs for details.")