"""
Simple test script to check S3 upload functionality.
"""

import os
import logging
import boto3
from PIL import Image
from io import BytesIO
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_s3_connection():
    """Test connecting to S3 with current credentials"""
    logger.info("Testing S3 connection...")
    
    # Get credentials from environment variables
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    aws_region = os.environ.get('AWS_REGION')
    aws_bucket = os.environ.get('AWS_S3_BUCKET_NAME')
    
    logger.info(f"AWS credentials available: {bool(aws_access_key and aws_secret_key)}")
    logger.info(f"AWS_REGION: {aws_region}")
    logger.info(f"AWS_S3_BUCKET_NAME: {aws_bucket}")
    
    # Create S3 client
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        
        logger.info("S3 client initialized successfully")
        
        # Test listing buckets to verify connection
        response = s3_client.list_buckets()
        buckets = [bucket['Name'] for bucket in response['Buckets']]
        logger.info(f"Available buckets: {buckets}")
        
        if aws_bucket in buckets:
            logger.info(f"Target bucket '{aws_bucket}' exists")
        else:
            logger.warning(f"Target bucket '{aws_bucket}' not found in available buckets")
        
        return True
    except Exception as e:
        logger.error(f"Error connecting to S3: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def test_s3_upload():
    """Test uploading a simple file to S3"""
    logger.info("Testing S3 file upload...")
    
    # Get credentials from environment variables
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    aws_region = os.environ.get('AWS_REGION')
    aws_bucket = os.environ.get('AWS_S3_BUCKET_NAME')
    
    # Create S3 client
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        
        # Generate a simple image
        img = Image.new('RGB', (100, 100), color='red')
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        # Generate a unique key
        test_key = f"test/{uuid.uuid4()}.jpg"
        
        # Upload the image
        logger.info(f"Uploading test image to S3 with key: {test_key}")
        s3_client.upload_fileobj(img_byte_arr, aws_bucket, test_key)
        
        # Verify the upload
        try:
            s3_client.head_object(Bucket=aws_bucket, Key=test_key)
            logger.info(f"Image upload successful, verified object exists: {test_key}")
            return True
        except Exception as verify_error:
            logger.error(f"Error verifying uploaded file: {str(verify_error)}")
            return False
            
    except Exception as e:
        logger.error(f"Error uploading to S3: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def main():
    """Run all tests"""
    logger.info("Starting S3 connection and upload tests")
    
    # Test S3 connection
    connection_ok = test_s3_connection()
    if not connection_ok:
        logger.error("S3 connection test failed")
        return
    
    # Test S3 upload
    upload_ok = test_s3_upload()
    if not upload_ok:
        logger.error("S3 upload test failed")
        return
    
    logger.info("All S3 tests completed successfully")

if __name__ == "__main__":
    main()