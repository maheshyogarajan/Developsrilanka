"""
Standalone S3 Direct Upload Module

This module provides a simple API for directly uploading images to S3
without any dependencies on other parts of the application.
"""

import os
import io
import uuid
import logging
import traceback
from PIL import Image
import boto3
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def upload_receipt_to_s3(image, organization_id=None, receipt_id=None):
    """
    Upload an image directly to S3 with maximum reliability.
    
    Args:
        image: PIL Image object or file-like object or bytes
        organization_id: Optional organization ID for path structure
        receipt_id: Optional receipt ID for path structure
        
    Returns:
        s3_key if successful, None if failed
    """
    try:
        logger.info("Starting direct S3 upload...")
        
        # Check if S3 credentials are available
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_region = os.environ.get('AWS_REGION')
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        
        if not all([aws_access_key, aws_secret_key, aws_region, bucket_name]):
            logger.error("Missing S3 credentials in environment variables")
            return None
        
        # Initialize S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        
        # Generate a unique identifier for the image
        image_id = str(uuid.uuid4())
        logger.info(f"Generated UUID for image: {image_id}")
        
        # Create the S3 key based on organization and receipt if provided
        # Use receipts/ folder for production use
        if organization_id and receipt_id:
            s3_key = f"receipts/{organization_id}_{receipt_id}_{image_id}.jpg"
        elif organization_id:
            s3_key = f"receipts/{organization_id}_{image_id}.jpg"
        else:
            s3_key = f"receipts/{image_id}.jpg"
        
        logger.info(f"Generated S3 key: {s3_key}")
        
        # Handle different input types
        if isinstance(image, Image.Image):
            # Handle PIL Image
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=85)
            img_byte_arr.seek(0)
            
            # Upload to S3
            s3_client.upload_fileobj(img_byte_arr, bucket_name, s3_key)
            logger.info(f"Uploaded PIL Image to S3 with key: {s3_key}")
        elif isinstance(image, bytes):
            # Handle bytes directly
            img_byte_arr = io.BytesIO(image)
            s3_client.upload_fileobj(img_byte_arr, bucket_name, s3_key)
            logger.info(f"Uploaded bytes to S3 with key: {s3_key}")
        elif hasattr(image, 'read'):
            # Handle file-like object
            s3_client.upload_fileobj(image, bucket_name, s3_key)
            logger.info(f"Uploaded file-like object to S3 with key: {s3_key}")
        elif isinstance(image, str) and os.path.isfile(image):
            # Handle file path
            s3_client.upload_file(image, bucket_name, s3_key)
            logger.info(f"Uploaded file from path to S3 with key: {s3_key}")
        else:
            logger.error(f"Unsupported image type: {type(image)}")
            return None
        
        # Verify the upload by checking if the object exists
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.info(f"✅ S3 upload verified: {s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"❌ S3 upload verification failed: {str(e)}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error in direct S3 upload: {str(e)}")
        logger.error(traceback.format_exc())
        return None

def test_s3_connection():
    """Test S3 connection and credentials without uploading anything."""
    try:
        # Check if S3 credentials are available
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_region = os.environ.get('AWS_REGION')
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        
        if not all([aws_access_key, aws_secret_key, aws_region, bucket_name]):
            logger.error("Missing S3 credentials in environment variables")
            return False
        
        # Initialize S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        
        # Try to list objects in the bucket
        s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
        logger.info(f"✅ Successfully connected to S3 bucket: {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"❌ Error testing S3 connection: {str(e)}")
        logger.error(traceback.format_exc())
        return False

# If this file is run directly, test the S3 connection
if __name__ == "__main__":
    test_s3_connection()