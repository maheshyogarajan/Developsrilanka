"""
Test script to verify S3 connectivity and permissions.
"""

import os
import io
import boto3
from botocore.exceptions import ClientError
from PIL import Image
import uuid
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_s3_client():
    """
    Create and return an S3 client using environment variables for credentials.
    
    Returns:
        boto3.client: The S3 client object
    """
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('AWS_REGION')
        )
        return s3_client
    except Exception as e:
        logger.error(f"Failed to create S3 client: {str(e)}")
        raise

def test_s3_connectivity():
    """Test if we can connect to S3 and list buckets."""
    try:
        s3_client = get_s3_client()
        response = s3_client.list_buckets()
        
        print(f"Connection successful. Found {len(response['Buckets'])} buckets:")
        for bucket in response['Buckets']:
            print(f"- {bucket['Name']}")
            
        # Check if our configured bucket exists
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        bucket_exists = False
        for bucket in response['Buckets']:
            if bucket['Name'] == bucket_name:
                bucket_exists = True
                break
                
        if bucket_exists:
            print(f"✅ Bucket '{bucket_name}' exists and is accessible.")
        else:
            print(f"❌ Bucket '{bucket_name}' does not exist or is not accessible.")
            
        return bucket_exists
    except Exception as e:
        print(f"❌ Error connecting to S3: {str(e)}")
        return False

def test_s3_permissions():
    """Test if we have proper permissions to perform operations on the S3 bucket."""
    try:
        s3_client = get_s3_client()
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        
        # Create a test image
        image = Image.new('RGB', (100, 100), color = 'red')
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        # Generate a unique key for the test image
        test_image_key = f"test/test_image_{uuid.uuid4()}.jpg"
        
        # Test upload
        print(f"Attempting to upload test image to '{bucket_name}/{test_image_key}'...")
        s3_client.upload_fileobj(img_byte_arr, bucket_name, test_image_key)
        print(f"✅ Upload successful.")
        
        # Test download
        print(f"Attempting to download test image from '{bucket_name}/{test_image_key}'...")
        download_buffer = io.BytesIO()
        s3_client.download_fileobj(bucket_name, test_image_key, download_buffer)
        print(f"✅ Download successful.")
        
        # Test generate presigned URL
        print(f"Attempting to generate presigned URL for '{bucket_name}/{test_image_key}'...")
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': test_image_key},
            ExpiresIn=3600
        )
        print(f"✅ Generated presigned URL: {url}")
        
        # Test delete
        print(f"Attempting to delete test image from '{bucket_name}/{test_image_key}'...")
        s3_client.delete_object(Bucket=bucket_name, Key=test_image_key)
        print(f"✅ Deletion successful.")
        
        print("\n✅ All S3 operations completed successfully. Your S3 configuration is working correctly.")
        return True
    except Exception as e:
        print(f"❌ Error during S3 operations: {str(e)}")
        return False

def print_environment_vars():
    """Print environment variables related to AWS (without exposing secrets)."""
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    aws_region = os.environ.get('AWS_REGION')
    aws_s3_bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    
    # Safely display values (without exposing full secrets)
    print("AWS Environment Variables:")
    
    # For key, show first and last 4 chars if it's at least 8 chars
    if aws_access_key_id and len(aws_access_key_id) >= 8:
        masked_key = aws_access_key_id[:4] + '*' * (len(aws_access_key_id) - 8) + aws_access_key_id[-4:]
        print(f"- AWS_ACCESS_KEY_ID: {masked_key}")
    elif aws_access_key_id:
        print(f"- AWS_ACCESS_KEY_ID: {'*' * len(aws_access_key_id)}")
    else:
        print(f"- AWS_ACCESS_KEY_ID: Not set")
    
    # For secret, only show length and masking
    if aws_secret_access_key:
        print(f"- AWS_SECRET_ACCESS_KEY: {'*' * len(aws_secret_access_key)} (length: {len(aws_secret_access_key)})")
    else:
        print(f"- AWS_SECRET_ACCESS_KEY: Not set")
    
    # Show region directly
    print(f"- AWS_REGION: {aws_region if aws_region else 'Not set'}")
    
    # Show bucket name directly
    print(f"- AWS_S3_BUCKET_NAME: {aws_s3_bucket_name if aws_s3_bucket_name else 'Not set'}")

if __name__ == "__main__":
    print("\n---- Testing S3 Connection ----\n")
    print_environment_vars()
    
    print("\n---- Testing S3 Connectivity ----\n")
    if test_s3_connectivity():
        print("\n---- Testing S3 Permissions ----\n")
        test_s3_permissions()
    else:
        print("\n❌ S3 connectivity test failed. Skipping permissions test.")