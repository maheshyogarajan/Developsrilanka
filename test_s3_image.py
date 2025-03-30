"""
Test script to verify S3 image storage and retrieval functionality.
This will test both the S3 class methods and the model property.
"""

import os
import sys
from datetime import datetime
from PIL import Image
import io

# Add current directory to path to allow imports
sys.path.append('.')

try:
    # Test S3 connection
    from s3_storage import S3Storage, generate_presigned_url
    
    print("=== S3 CONNECTION TESTING ===")
    
    # Check if S3 credentials are available
    s3_creds_available = all([
        os.environ.get('AWS_S3_BUCKET_NAME'),
        os.environ.get('AWS_ACCESS_KEY_ID'),
        os.environ.get('AWS_SECRET_ACCESS_KEY'),
        os.environ.get('AWS_REGION')
    ])
    
    print(f"S3 credentials present: {s3_creds_available}")
    
    if s3_creds_available:
        # Create a test image in memory
        test_img = Image.new('RGB', (100, 100), color='red')
        
        # Initialize S3 storage
        s3 = S3Storage()
        print(f"S3 bucket name: {s3.bucket_name}")
        
        # Upload test image
        test_s3_key = s3.upload_image(test_img, organization_id=1)
        print(f"Uploaded test image to S3 with key: {test_s3_key}")
        
        # Generate presigned URL
        presigned_url = s3.generate_presigned_url(test_s3_key)
        if presigned_url:
            print(f"Generated presigned URL: {presigned_url[:60]}...")
        else:
            print(f"Failed to generate presigned URL")
        
        # Test standalone function
        standalone_url = generate_presigned_url(test_s3_key)
        if standalone_url:
            print(f"Generated standalone presigned URL: {standalone_url[:60]}...")
        else:
            print(f"Failed to generate standalone presigned URL")
            
        # Clean up - delete test image
        deleted = s3.delete_image(test_s3_key)
        print(f"Deleted test image: {deleted}")
        
    else:
        print("Skipping S3 tests as credentials are not available")
    
    # Test model integration
    print("\n=== MODEL INTEGRATION TESTING ===")
    
    from models import Receipt
    from app import db
    import flask
    from main import app
    
    # Create Flask application context
    with app.app_context():
        # Check for an existing receipt with s3_key
        s3_receipt = db.session.query(Receipt).filter(Receipt.s3_key.isnot(None)).first()
        
        if s3_receipt:
            print(f"Found receipt with S3 key: {s3_receipt.s3_key}")
            s3_url = s3_receipt.s3_url
            print(f"S3 URL property result: {s3_url[:60] if s3_url else 'None'}")
        else:
            print("No receipts with S3 keys found in database")
            
        # Check for an existing receipt without s3_key
        local_receipt = db.session.query(Receipt).filter(Receipt.s3_key.is_(None), Receipt.image_path.isnot(None)).first()
        
        if local_receipt:
            print(f"Found receipt with local image path: {local_receipt.image_path}")
            s3_url = local_receipt.s3_url
            print(f"S3 URL property result for local image: {s3_url or 'None'}")
        else:
            print("No receipts with local image paths found in database")
    
    print("\nS3 integration tests completed successfully")
    
except Exception as e:
    print(f"ERROR: {str(e)}")
    import traceback
    traceback.print_exc()