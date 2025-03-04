"""
One-time script to fix NULL user_ids in the receipt table.
This will assign all receipts that have NULL user_id to the first user in the system.
"""

from app import app, db
from models import Receipt, User
import logging

logging.basicConfig(level=logging.INFO)

def fix_null_user_ids():
    """
    Find all receipts with NULL user_id and assign them to the first user in the system.
    """
    with app.app_context():
        # Check if there are any receipts with NULL user_id
        null_user_receipts = Receipt.query.filter(Receipt.user_id == None).all()
        
        if not null_user_receipts:
            logging.info("No receipts with NULL user_id found.")
            return
        
        logging.info(f"Found {len(null_user_receipts)} receipts with NULL user_id.")
        
        # Get the first user in the system
        first_user = User.query.first()
        
        if not first_user:
            logging.error("No users found in the system. Cannot assign receipts.")
            return
        
        logging.info(f"Will assign null user_id receipts to user {first_user.id} ({first_user.email})")
        
        # Update all receipts with NULL user_id
        try:
            for receipt in null_user_receipts:
                receipt.user_id = first_user.id
                logging.info(f"Assigned receipt {receipt.id} (from {receipt.vendor_name}) to user {first_user.id}")
            
            # Commit the changes
            db.session.commit()
            logging.info(f"Successfully updated {len(null_user_receipts)} receipts.")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating receipts: {str(e)}")

if __name__ == "__main__":
    fix_null_user_ids()