"""
Debug S3 Upload Utility

This script tests direct image upload to AWS S3 and database connectivity.
It's meant for debugging S3 upload issues in isolation.
"""

import os
import sys
import logging
import io
from PIL import Image
import boto3
from botocore.exceptions import ClientError
from pprint import pprint
import datetime
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def create_test_image(width=300, height=400):
    """Create a simple test image for S3 upload testing."""
    image = Image.new('RGB', (width, height), color='white')
    return image

def test_s3_upload():
    """Test uploading an image to S3."""
    
    # Check if S3 credentials are available
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    aws_region = os.environ.get('AWS_REGION')
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    
    if not all([aws_access_key, aws_secret_key, aws_region, bucket_name]):
        logging.error("Missing S3 credentials in environment variables")
        print("\nMissing AWS credentials. Please set these environment variables:")
        print("- AWS_ACCESS_KEY_ID")
        print("- AWS_SECRET_ACCESS_KEY") 
        print("- AWS_REGION")
        print("- AWS_S3_BUCKET_NAME")
        return False
    
    # Initialize S3 client
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        logging.info(f"Successfully connected to AWS S3")
    except Exception as e:
        logging.error(f"Failed to initialize S3 client: {str(e)}")
        return False
    
    # Create a test image
    test_image = create_test_image()
    
    # Generate a unique key for the test upload
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    test_id = str(uuid.uuid4())[:8]
    test_key = f"debug_test/test_upload_{timestamp}_{test_id}.jpg"
    
    try:
        # Convert the image to bytes
        img_byte_arr = io.BytesIO()
        test_image.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        # Upload the image to S3
        logging.info(f"Uploading test image to S3 with key: {test_key}")
        s3_client.upload_fileobj(img_byte_arr, bucket_name, test_key)
        
        # Verify the upload
        try:
            s3_client.head_object(Bucket=bucket_name, Key=test_key)
            logging.info(f"✅ Upload verified: {test_key}")
            
            # Generate a pre-signed URL for viewing (expires in 1 hour)
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': test_key},
                ExpiresIn=3600
            )
            logging.info(f"Pre-signed URL (expires in 1 hour): {url}")
            
            return True, test_key, url
        except ClientError:
            logging.error(f"Couldn't verify upload for key: {test_key}")
            return False, test_key, None
    except Exception as e:
        logging.error(f"Error uploading to S3: {str(e)}")
        return False, None, None

def test_upload_with_path(path="test/"):
    """Test uploading an image to a specific S3 path."""
    
    # Create a test image
    test_image = create_test_image()
    
    # Generate a unique key with the specified path
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    test_id = str(uuid.uuid4())[:8]
    test_key = f"{path}test_upload_{timestamp}_{test_id}.jpg"
    
    # Check if S3 credentials are available
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    aws_region = os.environ.get('AWS_REGION')
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    
    if not all([aws_access_key, aws_secret_key, aws_region, bucket_name]):
        logging.error("Missing S3 credentials in environment variables")
        return False, None, None
    
    # Initialize S3 client
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
    except Exception as e:
        logging.error(f"Failed to initialize S3 client: {str(e)}")
        return False, None, None
    
    try:
        # Convert the image to bytes
        img_byte_arr = io.BytesIO()
        test_image.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        # Upload the image to S3
        logging.info(f"Uploading test image to S3 with key: {test_key}")
        s3_client.upload_fileobj(img_byte_arr, bucket_name, test_key)
        
        # Verify the upload
        try:
            s3_client.head_object(Bucket=bucket_name, Key=test_key)
            logging.info(f"✅ Upload verified: {test_key}")
            
            # Generate a pre-signed URL for viewing (expires in 1 hour)
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': test_key},
                ExpiresIn=3600
            )
            logging.info(f"Pre-signed URL (expires in 1 hour): {url}")
            
            return True, test_key, url
        except ClientError:
            logging.error(f"Couldn't verify upload for key: {test_key}")
            return False, test_key, None
    except Exception as e:
        logging.error(f"Error uploading to S3: {str(e)}")
        return False, None, None

def test_s3_direct_upload():
    """Test using our new s3_direct_upload module."""
    from s3_direct_upload import upload_receipt_to_s3
    
    # Create a test image
    test_image = create_test_image()
    
    try:
        # Upload the image using our direct upload module
        s3_key = upload_receipt_to_s3(test_image, organization_id=1)
        
        if s3_key:
            logging.info(f"✅ Direct upload successful: {s3_key}")
            
            # Get bucket name from environment
            bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
            
            # Initialize S3 client for URL generation
            aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
            aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
            aws_region = os.environ.get('AWS_REGION')
            
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
                region_name=aws_region
            )
            
            # Generate a pre-signed URL for viewing (expires in 1 hour)
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=3600
            )
            logging.info(f"Pre-signed URL (expires in 1 hour): {url}")
            
            return True, s3_key, url
        else:
            logging.error("Direct upload failed")
            return False, None, None
    except Exception as e:
        logging.error(f"Error with direct upload: {str(e)}")
        return False, None, None

def run_all_tests():
    """Run all available S3 upload tests."""
    results = {
        "s3_credentials_present": all([
            os.environ.get('AWS_ACCESS_KEY_ID'),
            os.environ.get('AWS_SECRET_ACCESS_KEY'),
            os.environ.get('AWS_REGION'),
            os.environ.get('AWS_S3_BUCKET_NAME')
        ]),
        "tests": {}
    }
    
    # Test 1: Basic debug folder upload
    logging.info("\n==== Test 1: Basic Upload to debug_test/ folder ====")
    success, key, url = test_s3_upload()
    results["tests"]["debug_test_folder"] = {
        "success": success,
        "key": key,
        "url": url
    }
    
    # Test 2: Upload to test/ folder 
    logging.info("\n==== Test 2: Upload to test/ folder ====")
    success, key, url = test_upload_with_path("test/")
    results["tests"]["test_folder"] = {
        "success": success,
        "key": key,
        "url": url
    }
    
    # Test 3: Upload to receipts/ folder (original path structure)
    logging.info("\n==== Test 3: Upload to receipts/ folder ====")
    success, key, url = test_upload_with_path("receipts/")
    results["tests"]["receipts_folder"] = {
        "success": success,
        "key": key,
        "url": url
    }
    
    # Test 4: Direct upload module test
    logging.info("\n==== Test 4: Direct Upload Module ====")
    try:
        success, key, url = test_s3_direct_upload()
        results["tests"]["direct_upload_module"] = {
            "success": success,
            "key": key,
            "url": url
        }
    except Exception as e:
        logging.error(f"Direct upload module test failed: {str(e)}")
        results["tests"]["direct_upload_module"] = {
            "success": False,
            "error": str(e)
        }
    
    # Print results
    logging.info("\n==== S3 Upload Test Results ====")
    pprint(results)
    
    # Determine overall success
    successful_tests = sum(1 for test in results["tests"].values() if test.get("success", False))
    total_tests = len(results["tests"])
    
    logging.info(f"\nSummary: {successful_tests}/{total_tests} tests successful")
    
    if successful_tests == total_tests:
        logging.info("✅ All S3 tests succeeded! Your configuration is working properly.")
    elif successful_tests > 0:
        logging.info("⚠️ Some S3 tests succeeded, but not all. Check the detailed results above.")
    else:
        logging.error("❌ All S3 tests failed. Your S3 configuration is not working correctly.")

if __name__ == "__main__":
    print("\n=== S3 Upload Debug Utility ===\n")
    
    if len(sys.argv) > 1:
        if sys.argv[1] == 'run-all':
            run_all_tests()
        elif sys.argv[1] == 'direct':
            success, key, url = test_s3_direct_upload()
            if success:
                print(f"\n✅ Direct S3 upload test succeeded!")
                print(f"Key: {key}")
                print(f"URL: {url}")
            else:
                print("\n❌ Direct S3 upload test failed!")
        elif sys.argv[1].startswith('path='):
            path = sys.argv[1].split('=')[1]
            print(f"Testing upload to path: {path}")
            success, key, url = test_upload_with_path(path)
            if success:
                print(f"\n✅ Upload to {path} succeeded!")
                print(f"Key: {key}")
                print(f"URL: {url}")
            else:
                print(f"\n❌ Upload to {path} failed!")
    else:
        # Default to the basic S3 upload test
        success, key, url = test_s3_upload()
        if success:
            print("\n✅ S3 upload test succeeded!")
            print(f"Key: {key}")
            print(f"URL: {url}")
        else:
            print("\n❌ S3 upload test failed!")
            
        print("\nFor more tests, run one of:")
        print("- python debug_s3_upload.py run-all")
        print("- python debug_s3_upload.py direct")
        print("- python debug_s3_upload.py path=custom/path/")