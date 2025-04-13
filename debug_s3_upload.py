"""
Debug S3 upload issues with direct access.
This script will test S3 connection and upload a test image directly.
"""

import os
import logging
import traceback
import boto3
from botocore.exceptions import ClientError
from PIL import Image
import io
import uuid
import time
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def verify_aws_configuration():
    """Verify AWS environment variables are set."""
    aws_vars = {
        'AWS_S3_BUCKET_NAME': os.environ.get('AWS_S3_BUCKET_NAME'),
        'AWS_ACCESS_KEY_ID': os.environ.get('AWS_ACCESS_KEY_ID'),
        'AWS_SECRET_ACCESS_KEY': os.environ.get('AWS_SECRET_ACCESS_KEY'),
        'AWS_REGION': os.environ.get('AWS_REGION')
    }
    
    logger.info("=== AWS Configuration Status ===")
    all_configured = True
    for key, value in aws_vars.items():
        is_set = bool(value)
        logger.info(f"{key} is {'set' if is_set else 'NOT SET'}")
        if not is_set:
            all_configured = False
    
    return all_configured

def create_test_image():
    """Create a simple test image."""
    # Create a simple colored image
    img = Image.new('RGB', (200, 200), color=(255, 0, 0))
    logger.info(f"Created test image: {img.size} pixels, format: {img.format}")
    return img

def direct_s3_upload(img):
    """Attempt to upload directly using boto3."""
    try:
        # Create S3 client
        logger.info("Creating S3 client...")
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('AWS_REGION')
        )
        logger.info("S3 client created successfully")
        
        # Get bucket name
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        logger.info(f"Using bucket name: {bucket_name}")
        
        # Generate a unique key
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_id = str(uuid.uuid4())
        s3_key = f"debug_test/{timestamp}_{image_id}.jpg"
        logger.info(f"Using S3 key: {s3_key}")
        
        # Convert image to bytes
        logger.info("Converting image to bytes...")
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        buffer_size = img_byte_arr.getbuffer().nbytes
        logger.info(f"Image byte array size: {buffer_size} bytes")
        
        # Upload to S3
        logger.info(f"Uploading to S3 bucket: {bucket_name}, key: {s3_key}")
        start_time = time.time()
        s3_client.upload_fileobj(img_byte_arr, bucket_name, s3_key)
        upload_time = time.time() - start_time
        logger.info(f"Upload completed in {upload_time:.2f} seconds")
        
        # Verify upload
        logger.info("Verifying upload with head_object...")
        try:
            response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.info(f"Verified image exists in S3 at key: {s3_key}")
            logger.info(f"Object metadata: {response}")
            
            # Generate presigned URL
            logger.info("Generating presigned URL...")
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=3600
            )
            logger.info(f"Presigned URL: {url}")
            
            return True, s3_key, url
        except ClientError as e:
            logger.error(f"Error verifying upload: {str(e)}")
            logger.error(traceback.format_exc())
            return False, s3_key, None
            
    except ClientError as e:
        logger.error(f"S3 client error: {str(e)}")
        logger.error(traceback.format_exc())
        return False, None, None
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        return False, None, None

def main():
    """Main function to run all tests."""
    logger.info("=== S3 Upload Debug Script ===")
    
    # Verify AWS configuration
    logger.info("Verifying AWS configuration...")
    if not verify_aws_configuration():
        logger.error("AWS configuration is incomplete. Cannot proceed.")
        return
    
    # Create test image
    logger.info("Creating test image...")
    test_image = create_test_image()
    
    # Upload test image
    logger.info("Uploading test image directly with boto3...")
    success, s3_key, url = direct_s3_upload(test_image)
    
    if success:
        logger.info("=== S3 UPLOAD SUCCESSFUL ===")
        logger.info(f"S3 Key: {s3_key}")
        logger.info(f"Presigned URL: {url}")
    else:
        logger.error("=== S3 UPLOAD FAILED ===")

if __name__ == "__main__":
    main()