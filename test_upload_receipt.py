"""
Test script to upload a sample receipt to S3 and create a database record.
This will test the end-to-end flow of the S3 integration.
"""

import os
import sys
import io
import logging
from datetime import datetime
from PIL import Image
import random

# Disable all but critical logging
logging.getLogger().setLevel(logging.ERROR)  # Root logger
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('botocore').setLevel(logging.ERROR)
logging.getLogger('boto3').setLevel(logging.ERROR)
logging.getLogger('s3transfer').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('sqlalchemy').setLevel(logging.ERROR)
logging.getLogger('flask').setLevel(logging.ERROR)
logging.getLogger('s3_storage').setLevel(logging.INFO)  # Keep S3 storage info logging

# Disable SQLAlchemy warnings
import warnings
from sqlalchemy import exc as sa_exc
warnings.filterwarnings('ignore', category=sa_exc.SAWarning)

# Add current directory to path to allow imports
sys.path.append('.')

try:
    print("=== UPLOAD RECEIPT TO S3 TEST ===")
    
    # Disable all logging from the app imports
    logging.getLogger('main').setLevel(logging.CRITICAL)
    logging.getLogger('app').setLevel(logging.CRITICAL)
    logging.getLogger('root').setLevel(logging.CRITICAL)
    logging.getLogger('blueprints').setLevel(logging.CRITICAL)
    logging.getLogger('template_filters').setLevel(logging.CRITICAL)
    logging.getLogger('classify_routes').setLevel(logging.CRITICAL)
    logging.getLogger('organization_routes').setLevel(logging.CRITICAL)
    
    # Import Flask app and models
    from main import app
    from models import db, Receipt, ReceiptItem
    from s3_storage import S3Storage, generate_presigned_url
    
    # Check if S3 credentials are available
    s3_creds_available = all([
        os.environ.get('AWS_S3_BUCKET_NAME'),
        os.environ.get('AWS_ACCESS_KEY_ID'),
        os.environ.get('AWS_SECRET_ACCESS_KEY'),
        os.environ.get('AWS_REGION')
    ])
    
    print(f"S3 credentials present: {s3_creds_available}")
    
    if not s3_creds_available:
        print("Skipping test as S3 credentials are not available")
        sys.exit(0)
    
    # Create a test image in memory (100x150 red rectangle with white text)
    img_width, img_height = 300, 400
    test_img = Image.new('RGB', (img_width, img_height), color='white')
    
    # Upload to S3
    s3 = S3Storage()
    organization_id = 1  # Use organization ID 1 for test
    
    # Generate a unique s3_key for this test
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    s3_key = f"receipts/{organization_id}/test_receipt_{timestamp}.jpg"
    
    print(f"Uploading test image with key: {s3_key}")
    
    # Save image to memory buffer
    img_byte_arr = io.BytesIO()
    test_img.save(img_byte_arr, format='JPEG')
    img_byte_arr.seek(0)
    
    # Upload to S3 directly
    s3.s3_client.upload_fileobj(
        img_byte_arr, 
        s3.bucket_name, 
        s3_key
    )
    
    print(f"Image uploaded to S3 successfully")
    
    # Generate presigned URL
    presigned_url = generate_presigned_url(s3_key)
    if presigned_url:
        print(f"Generated presigned URL: {presigned_url[:30]}...")
    else:
        print("Failed to generate presigned URL")
        
    # Create a Receipt record with S3 key
    with app.app_context():
        # Create a new test receipt
        print("Creating test receipt in database...")
        
        new_receipt = Receipt(
            user_id=1,  # Assuming user 1 exists
            organization_id=organization_id,
            vendor_name="Test Vendor for S3",
            vendor_address="123 Test Street",
            vendor_contact="test@example.com",
            date=datetime.now().date(),
            total_amount=random.uniform(10, 100),
            s3_key=s3_key,  # Set the S3 key here
            expense_major_category="Test Category",
            expense_minor_category="S3 Testing"
        )
        
        # Add a test item
        test_item = ReceiptItem(
            name="Test Item",
            quantity=1,
            price=random.uniform(10, 100),
            tax_deductible=True
        )
        
        new_receipt.items.append(test_item)
        
        # Save to database
        db.session.add(new_receipt)
        db.session.commit()
        
        s3_key_short = new_receipt.s3_key[-20:] if new_receipt.s3_key else 'None'
        print(f"Created receipt with ID {new_receipt.id} and S3 key: ...{s3_key_short}")
        
        # Test S3 URL property
        s3_url = new_receipt.s3_url
        s3_url_display = s3_url[:30] + "..." if s3_url else 'None'
        print(f"S3 URL property result: {s3_url_display}")
        
        # Verify receipt can be retrieved
        retrieved_receipt = db.session.query(Receipt).filter_by(id=new_receipt.id).first()
        if retrieved_receipt:
            print(f"Retrieved receipt successfully: ID {retrieved_receipt.id}")
            s3_key_short = retrieved_receipt.s3_key[-20:] if retrieved_receipt.s3_key else 'None'
            print(f"S3 key: ...{s3_key_short}")
            
            retrieved_url = retrieved_receipt.s3_url
            retrieved_url_display = retrieved_url[:30] + "..." if retrieved_url else 'None'
            print(f"S3 URL: {retrieved_url_display}")
        else:
            print("Failed to retrieve receipt")
        
    print("\nUpload receipt test completed successfully")
    
except Exception as e:
    print(f"ERROR: {str(e)}")
    import traceback
    traceback.print_exc()