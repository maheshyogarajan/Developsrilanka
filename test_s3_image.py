"""
Test script for S3 storage functionality.
This is a focused test for S3 image upload and retrieval.
"""

import os
import sys
import io
import logging
from datetime import datetime
from PIL import Image
import random
import string

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Disable other loggers
logging.getLogger('boto3').setLevel(logging.ERROR)
logging.getLogger('botocore').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('s3transfer').setLevel(logging.ERROR)
logging.getLogger('sqlalchemy').setLevel(logging.ERROR)
logging.getLogger('flask').setLevel(logging.ERROR)

# Add current directory to path
sys.path.append('.')

def generate_random_string(length=10):
    """Generate a random string."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def generate_test_image(width=300, height=400, color='white'):
    """Generate a test image in memory."""
    return Image.new('RGB', (width, height), color=color)

def test_s3_upload():
    """Test S3 upload functionality."""
    try:
        # Import required modules
        from s3_storage import S3Storage, generate_presigned_url
        
        # Check if S3 credentials are available
        s3_creds_available = all([
            os.environ.get('AWS_S3_BUCKET_NAME'),
            os.environ.get('AWS_ACCESS_KEY_ID'),
            os.environ.get('AWS_SECRET_ACCESS_KEY'),
            os.environ.get('AWS_REGION')
        ])
        
        logger.info(f"S3 credentials available: {s3_creds_available}")
        
        if not s3_creds_available:
            logger.warning("Skipping S3 upload test as credentials are not available")
            return {
                'status': 'SKIPPED',
                'reason': 'S3 credentials not available'
            }
        
        # Generate test image
        test_img = generate_test_image(300, 400, 'blue')
        
        # Initialize S3 storage
        s3 = S3Storage()
        logger.info(f"Initialized S3 storage with bucket: {s3.bucket_name}")
        
        # Generate unique key
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = generate_random_string(8)
        s3_key = f"receipts/test_{timestamp}_{random_suffix}.jpg"
        
        logger.info(f"Generated S3 key: {s3_key}")
        
        # Save image to memory buffer
        img_byte_arr = io.BytesIO()
        test_img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        # Upload to S3
        logger.info("Uploading image to S3...")
        s3.s3_client.upload_fileobj(
            img_byte_arr, 
            s3.bucket_name, 
            s3_key
        )
        logger.info("Image uploaded successfully")
        
        # Generate presigned URL
        logger.info("Generating presigned URL...")
        presigned_url = generate_presigned_url(s3_key)
        
        if presigned_url:
            logger.info(f"Generated presigned URL: {presigned_url[:50]}...")
            return {
                'status': 'SUCCESS',
                's3_key': s3_key,
                'presigned_url': presigned_url
            }
        else:
            logger.error("Failed to generate presigned URL")
            return {
                'status': 'FAILED',
                'reason': 'Failed to generate presigned URL'
            }
    
    except Exception as e:
        logger.error(f"Error in S3 upload test: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'status': 'ERROR',
            'error': str(e)
        }

def test_image_processor():
    """Test image processor with S3 integration."""
    try:
        # Import required modules
        from image_processor import save_uploaded_image
        
        # Check if S3 credentials are available
        s3_creds_available = all([
            os.environ.get('AWS_S3_BUCKET_NAME'),
            os.environ.get('AWS_ACCESS_KEY_ID'),
            os.environ.get('AWS_SECRET_ACCESS_KEY'),
            os.environ.get('AWS_REGION')
        ])
        
        logger.info(f"S3 credentials available: {s3_creds_available}")
        
        # Generate test image
        test_img = generate_test_image(300, 400, 'green')
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = generate_random_string(8)
        filename = f"test_{timestamp}_{random_suffix}.jpg"
        
        logger.info(f"Testing image processor with filename: {filename}")
        
        # Test with and without organization_id
        results = {}
        
        # Test with organization_id=1
        try:
            logger.info("Testing with organization_id=1")
            result = save_uploaded_image(test_img, filename, 1)
            
            if result:
                logger.info(f"Image saved successfully with organization_id=1")
                s3_key = result.get('s3_key', '')
                image_path = result.get('image_path', '')
                
                results['with_org_id'] = {
                    'status': 'SUCCESS',
                    's3_key': s3_key,
                    'image_path': image_path,
                    'has_s3_key': bool(s3_key),
                    'has_image_path': bool(image_path)
                }
                
                if s3_key and s3_creds_available:
                    # Test presigned URL generation
                    from s3_storage import generate_presigned_url
                    presigned_url = generate_presigned_url(s3_key)
                    results['with_org_id']['presigned_url'] = presigned_url is not None
            else:
                logger.error("No result returned from save_uploaded_image with organization_id=1")
                results['with_org_id'] = {
                    'status': 'FAILED',
                    'reason': 'No result returned'
                }
        except Exception as e:
            logger.error(f"Error with organization_id=1: {str(e)}")
            results['with_org_id'] = {
                'status': 'ERROR',
                'error': str(e)
            }
        
        # Test without organization_id
        try:
            logger.info("Testing without organization_id")
            result = save_uploaded_image(test_img, filename)
            
            if result:
                logger.info(f"Image saved successfully without organization_id")
                s3_key = result.get('s3_key', '')
                image_path = result.get('image_path', '')
                
                results['without_org_id'] = {
                    'status': 'SUCCESS',
                    's3_key': s3_key,
                    'image_path': image_path,
                    'has_s3_key': bool(s3_key),
                    'has_image_path': bool(image_path)
                }
                
                if s3_key and s3_creds_available:
                    # Test presigned URL generation
                    from s3_storage import generate_presigned_url
                    presigned_url = generate_presigned_url(s3_key)
                    results['without_org_id']['presigned_url'] = presigned_url is not None
            else:
                logger.error("No result returned from save_uploaded_image without organization_id")
                results['without_org_id'] = {
                    'status': 'FAILED',
                    'reason': 'No result returned'
                }
        except Exception as e:
            logger.error(f"Error without organization_id: {str(e)}")
            results['without_org_id'] = {
                'status': 'ERROR',
                'error': str(e)
            }
        
        return results
    
    except Exception as e:
        logger.error(f"Error in image processor test: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'status': 'ERROR',
            'error': str(e)
        }

def test_receipt_record_with_s3():
    """Test creating a receipt record with S3 key."""
    try:
        # Import required modules
        from main import app
        from models import db, Receipt, ReceiptItem
        
        # Check if S3 credentials are available
        s3_creds_available = all([
            os.environ.get('AWS_S3_BUCKET_NAME'),
            os.environ.get('AWS_ACCESS_KEY_ID'),
            os.environ.get('AWS_SECRET_ACCESS_KEY'),
            os.environ.get('AWS_REGION')
        ])
        
        logger.info(f"S3 credentials available: {s3_creds_available}")
        
        if not s3_creds_available:
            logger.warning("Proceeding with receipt test, but S3 URL generation may fail")
        
        # Generate unique S3 key
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = generate_random_string(8)
        s3_key = f"receipts/1/test_{timestamp}_{random_suffix}.jpg"
        
        logger.info(f"Using S3 key for test: {s3_key}")
        
        with app.app_context():
            # Create receipt with S3 key
            logger.info("Creating test receipt with S3 key")
            receipt = Receipt(
                user_id=1,
                organization_id=1,
                vendor_name="Test S3 Vendor",
                total_amount=100.0,
                date=datetime.now().date(),
                s3_key=s3_key,
                expense_major_category="Test Category",
                expense_minor_category="S3 Test"
            )
            
            # Add receipt item
            item = ReceiptItem(
                name="Test Item",
                quantity=1,
                price=100.0,
                tax_deductible=True
            )
            receipt.items.append(item)
            
            # Save to database
            db.session.add(receipt)
            db.session.commit()
            
            logger.info(f"Receipt created with ID: {receipt.id}")
            
            # Test retrieving receipt
            retrieved_receipt = db.session.query(Receipt).filter_by(id=receipt.id).first()
            
            if retrieved_receipt:
                logger.info(f"Retrieved receipt with ID: {retrieved_receipt.id}")
                
                # Test S3 URL property
                s3_url = retrieved_receipt.s3_url
                logger.info(f"S3 URL generated: {s3_url is not None}")
                
                # Convert to dict to test serialization
                receipt_dict = retrieved_receipt.to_dict()
                
                return {
                    'status': 'SUCCESS',
                    'receipt_id': retrieved_receipt.id,
                    's3_key': retrieved_receipt.s3_key,
                    's3_url_generated': s3_url is not None,
                    's3_url_in_dict': receipt_dict.get('s3_url') is not None,
                    'image_url_in_dict': receipt_dict.get('image_url') is not None
                }
            else:
                logger.error("Failed to retrieve receipt")
                return {
                    'status': 'FAILED',
                    'reason': 'Failed to retrieve receipt'
                }
    
    except Exception as e:
        logger.error(f"Error in receipt record test: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'status': 'ERROR',
            'error': str(e)
        }

if __name__ == "__main__":
    print("=== S3 IMAGE STORAGE TEST ===")
    
    # Test S3 upload
    print("\n1. Testing direct S3 upload...")
    s3_result = test_s3_upload()
    print(f"S3 Upload Test Result: {s3_result['status']}")
    if s3_result['status'] == 'SUCCESS':
        print(f"  - S3 Key: {s3_result['s3_key']}")
        print(f"  - Presigned URL generated: Yes")
    
    # Test image processor
    print("\n2. Testing image processor with S3 integration...")
    processor_result = test_image_processor()
    if 'with_org_id' in processor_result:
        print(f"  - With organization_id: {processor_result['with_org_id']['status']}")
        if processor_result['with_org_id']['status'] == 'SUCCESS':
            print(f"    - Has S3 key: {processor_result['with_org_id']['has_s3_key']}")
            print(f"    - Has image path: {processor_result['with_org_id']['has_image_path']}")
    
    if 'without_org_id' in processor_result:
        print(f"  - Without organization_id: {processor_result['without_org_id']['status']}")
        if processor_result['without_org_id']['status'] == 'SUCCESS':
            print(f"    - Has S3 key: {processor_result['without_org_id']['has_s3_key']}")
            print(f"    - Has image path: {processor_result['without_org_id']['has_image_path']}")
    
    # Test receipt record with S3
    print("\n3. Testing receipt record with S3 key...")
    receipt_result = test_receipt_record_with_s3()
    print(f"Receipt Test Result: {receipt_result['status']}")
    if receipt_result['status'] == 'SUCCESS':
        print(f"  - Receipt ID: {receipt_result['receipt_id']}")
        print(f"  - S3 Key: {receipt_result['s3_key']}")
        print(f"  - S3 URL generated: {receipt_result['s3_url_generated']}")
        print(f"  - S3 URL in dict: {receipt_result['s3_url_in_dict']}")
    
    print("\nS3 image storage test completed")