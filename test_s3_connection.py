"""
Simple test script to verify S3 connection and basic functionality.
"""

import os
import sys
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add current directory to path
sys.path.append('.')

def check_s3_credentials():
    """Check if S3 credentials are properly configured."""
    s3_creds = {
        'AWS_S3_BUCKET_NAME': os.environ.get('AWS_S3_BUCKET_NAME'),
        'AWS_ACCESS_KEY_ID': os.environ.get('AWS_ACCESS_KEY_ID'),
        'AWS_SECRET_ACCESS_KEY': os.environ.get('AWS_SECRET_ACCESS_KEY', 'REDACTED'),
        'AWS_REGION': os.environ.get('AWS_REGION')
    }
    
    # Hide secret key value
    if s3_creds['AWS_SECRET_ACCESS_KEY']:
        s3_creds['AWS_SECRET_ACCESS_KEY'] = s3_creds['AWS_SECRET_ACCESS_KEY'][:4] + '****'
    
    for key, value in s3_creds.items():
        if key != 'AWS_SECRET_ACCESS_KEY':
            logger.info(f"{key}: {'PRESENT' if value else 'MISSING'}")
        else:
            logger.info(f"{key}: {'PRESENT' if value else 'MISSING'} ({value if value else ''})")
    
    return all([
        s3_creds['AWS_S3_BUCKET_NAME'],
        s3_creds['AWS_ACCESS_KEY_ID'],
        s3_creds['AWS_SECRET_ACCESS_KEY'] != 'REDACTED',
        s3_creds['AWS_REGION']
    ])

def test_s3_presigned_url_generation():
    """Test generating a presigned URL for an S3 object."""
    try:
        from s3_storage import upload_image_to_s3, generate_presigned_url
        from PIL import Image
        
        # Create a test image
        test_img = Image.new('RGB', (100, 100), color='red')
        
        # Upload the test image to S3
        logger.info("Uploading test image to S3...")
        s3_key = upload_image_to_s3(test_img)
        logger.info(f"Uploaded test image with S3 key: {s3_key}")
        
        # Generate presigned URL for the uploaded image
        logger.info(f"Testing presigned URL generation with actual S3 key: {s3_key}")
        url = generate_presigned_url(s3_key)
        
        if url:
            logger.info(f"Successfully generated presigned URL: {url[:50]}...")
            return True
        else:
            logger.error(f"Failed to generate presigned URL for uploaded object key: {s3_key}")
            return False
    
    except Exception as e:
        logger.error(f"Error testing presigned URL generation: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def find_receipt_with_s3_key():
    """Find a receipt with an S3 key to test with."""
    try:
        from main import app
        from models import db, Receipt
        
        with app.app_context():
            receipt = db.session.query(Receipt).filter(
                Receipt.s3_key.isnot(None)
            ).order_by(Receipt.id.desc()).first()
            
            if receipt:
                logger.info(f"Found receipt with ID {receipt.id} and S3 key: {receipt.s3_key}")
                return receipt
            else:
                logger.warning("No receipts with S3 keys found in database")
                return None
    
    except Exception as e:
        logger.error(f"Error finding receipt with S3 key: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def test_receipt_s3_url_property():
    """Test the Receipt's s3_url property with a real receipt."""
    try:
        receipt = find_receipt_with_s3_key()
        
        if not receipt:
            logger.warning("Skipping s3_url property test as no receipt with S3 key was found")
            return False
        
        # Test s3_url property
        s3_url = receipt.s3_url
        
        if s3_url:
            logger.info(f"Successfully generated S3 URL from receipt: {s3_url[:50]}...")
            
            # Test to_dict() method
            receipt_dict = receipt.to_dict()
            dict_s3_url = receipt_dict.get('s3_url')
            
            if dict_s3_url:
                logger.info("Successfully included S3 URL in receipt.to_dict()")
                return True
            else:
                logger.error("Receipt.to_dict() did not include S3 URL")
                return False
        else:
            logger.error(f"Failed to generate S3 URL for receipt with key: {receipt.s3_key}")
            return False
    
    except Exception as e:
        logger.error(f"Error testing receipt s3_url property: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=== S3 CONNECTION TEST ===")
    
    # Check S3 credentials
    print("\n1. Checking S3 credentials...")
    creds_available = check_s3_credentials()
    print(f"S3 credentials check: {'PASSED' if creds_available else 'FAILED'}")
    
    if not creds_available:
        print("Cannot proceed with tests - S3 credentials are not available")
        sys.exit(1)
    
    # Test presigned URL generation
    print("\n2. Testing presigned URL generation...")
    url_generation_ok = test_s3_presigned_url_generation()
    print(f"Presigned URL generation test: {'PASSED' if url_generation_ok else 'FAILED'}")
    
    # Test Receipt's s3_url property
    print("\n3. Testing Receipt's s3_url property...")
    s3_url_property_ok = test_receipt_s3_url_property()
    print(f"Receipt s3_url property test: {'PASSED' if s3_url_property_ok else 'FAILED'}")
    
    # Overall result
    all_passed = creds_available and url_generation_ok and s3_url_property_ok
    print(f"\nOverall S3 connection test: {'PASSED' if all_passed else 'FAILED'}")