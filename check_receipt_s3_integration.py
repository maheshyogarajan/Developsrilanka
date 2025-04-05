"""
Test script to create a receipt record with an S3 key and test the s3_url property.
"""

import os
import sys
import io
import logging
from datetime import datetime
from PIL import Image
import random
import uuid

# Disable logging to see only our print statements
logging.basicConfig(level=logging.ERROR)
for log_name in ['root', 'werkzeug', 'sqlalchemy', 'boto3', 'botocore', 's3transfer', 'urllib3', 's3_storage', 'main', 'template_filters', 'blueprints']:
    logging.getLogger(log_name).setLevel(logging.ERROR)

# Disable other loggers
logging.getLogger('boto3').setLevel(logging.ERROR)
logging.getLogger('botocore').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('s3transfer').setLevel(logging.ERROR)
logging.getLogger('sqlalchemy').setLevel(logging.ERROR)
logging.getLogger('flask').setLevel(logging.ERROR)

# Add current directory to path
sys.path.append('.')

def create_test_receipt_with_s3():
    """Create a test receipt with an S3 key and verify the s3_url property works."""
    try:
        # Import required modules
        from main import app
        from models import db, Receipt, ReceiptItem
        from s3_storage import upload_image_to_s3
        
        # Check if S3 credentials are available
        s3_creds_available = all([
            os.environ.get('AWS_S3_BUCKET_NAME'),
            os.environ.get('AWS_ACCESS_KEY_ID'),
            os.environ.get('AWS_SECRET_ACCESS_KEY'),
            os.environ.get('AWS_REGION')
        ])
        
        print(f"S3 credentials available: {s3_creds_available}")
        
        if not s3_creds_available:
            print("Skipping test as S3 credentials are not available")
            return False
        
        # Create a test image
        test_img = Image.new('RGB', (300, 400), color='blue')
        
        # Upload the image to S3
        print("Uploading test image to S3...")
        test_s3_key = upload_image_to_s3(test_img, organization_id=1)
        print(f"Image uploaded to S3 with key: {test_s3_key}")
        
        # Create a receipt record with the S3 key
        with app.app_context():
            print("Creating test receipt record with S3 key...")
            
            receipt = Receipt(
                user_id=1,  # Assuming user ID 1 exists
                organization_id=1,  # Use org ID 1
                vendor_name="S3 Integration Test",
                vendor_address="123 Test Street",
                vendor_contact="test@example.com",
                date=datetime.now().date(),
                total_amount=random.uniform(100, 200),
                s3_key=test_s3_key,
                expense_major_category="Test",
                expense_minor_category="S3 Integration"
            )
            
            # Add a receipt item
            item = ReceiptItem(
                name="Test Item",
                quantity=1,
                price=random.uniform(100, 200),
                tax_deductible=True
            )
            receipt.items.append(item)
            
            # Save to database
            db.session.add(receipt)
            db.session.commit()
            
            print(f"Receipt created with ID: {receipt.id}")
            
            # Test the s3_url property
            print("Testing s3_url property...")
            s3_url = receipt.s3_url
            
            if s3_url:
                print(f"s3_url property works! URL: {s3_url[:50]}...")
                
                # Test to_dict() method includes s3_url
                receipt_dict = receipt.to_dict()
                dict_s3_url = receipt_dict.get('s3_url')
                
                if dict_s3_url:
                    print("to_dict() method includes s3_url!")
                    result = True
                else:
                    print("ERROR: to_dict() method does not include s3_url")
                    result = False
            else:
                print(f"ERROR: s3_url property returned None for key: {test_s3_key}")
                result = False
            
            # Clean up - delete the receipt
            print(f"Cleaning up - deleting test receipt ID: {receipt.id}")
            db.session.delete(receipt)
            db.session.commit()
            
            return result
            
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=== RECEIPT S3 INTEGRATION TEST ===")
    
    result = create_test_receipt_with_s3()
    
    if result:
        print("\nTEST RESULT: PASSED ✅")
        print("The Receipt model's S3 integration is working correctly!")
    else:
        print("\nTEST RESULT: FAILED ❌")
        print("There are issues with the Receipt model's S3 integration.")