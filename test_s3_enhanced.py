"""
Enhanced S3 testing script to verify S3 connectivity and troubleshoot the receipt upload issue.
This script tests the complete workflow and provides detailed logging.
"""

import os
import sys
import logging
import traceback
from io import BytesIO
from PIL import Image
from datetime import datetime

# Configure detailed logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# First, check if we can import boto3
try:
    import boto3
    from botocore.exceptions import ClientError
    logger.info("Successfully imported boto3 and botocore")
except ImportError as e:
    logger.error(f"Failed to import boto3: {str(e)}")
    sys.exit(1)

# Check environment variables
def check_aws_env_vars():
    """Check if all required AWS environment variables are set."""
    required_vars = ['AWS_S3_BUCKET_NAME', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION']
    missing_vars = []
    
    for var in required_vars:
        value = os.environ.get(var)
        if not value:
            missing_vars.append(var)
            logger.error(f"Missing required environment variable: {var}")
        else:
            # Only log that we have a value, not the actual value
            logger.info(f"✓ Found environment variable: {var}")
    
    return not missing_vars

def create_test_image():
    """Create a simple test image for uploading."""
    # Create a 200x200 red square image
    img = Image.new('RGB', (200, 200), color='red')
    
    # Add timestamp text to make the image unique
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    img_buffer = BytesIO()
    img.save(img_buffer, format='JPEG')
    img_buffer.seek(0)
    
    logger.info(f"Created test image ({img.size[0]}x{img.size[1]}, {img_buffer.getbuffer().nbytes} bytes)")
    return img

def create_s3_client():
    """Create an S3 client using environment variables."""
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('AWS_REGION')
        )
        logger.info("Successfully created S3 client")
        return s3_client
    except Exception as e:
        logger.error(f"Failed to create S3 client: {str(e)}")
        logger.error(traceback.format_exc())
        return None

def test_bucket_exists(s3_client):
    """Test if the bucket exists and is accessible."""
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    try:
        response = s3_client.head_bucket(Bucket=bucket_name)
        logger.info(f"✓ Bucket '{bucket_name}' exists and is accessible")
        logger.info(f"Response: {response}")
        return True
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '404':
            logger.error(f"Bucket '{bucket_name}' does not exist")
        elif error_code == '403':
            logger.error(f"Access denied to bucket '{bucket_name}'")
        else:
            logger.error(f"Error checking bucket '{bucket_name}': {str(e)}")
        logger.error(traceback.format_exc())
        return False
    except Exception as e:
        logger.error(f"Unexpected error checking bucket: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def test_upload_image(s3_client, img):
    """Test uploading an image to S3."""
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    try:
        # Generate a test key
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_key = f"test/test_image_{timestamp}.jpg"
        
        # Convert image to bytes
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        # Log buffer size
        buffer_size = img_byte_arr.getbuffer().nbytes
        logger.info(f"Image buffer size: {buffer_size} bytes")
        
        # Upload the image
        logger.info(f"Uploading to bucket '{bucket_name}' with key '{s3_key}'")
        s3_client.upload_fileobj(img_byte_arr, bucket_name, s3_key)
        
        logger.info(f"✓ Successfully uploaded image to S3 with key: {s3_key}")
        return s3_key
    except ClientError as e:
        logger.error(f"Error uploading image to S3: {str(e)}")
        logger.error(traceback.format_exc())
        return None
    except Exception as e:
        logger.error(f"Unexpected error uploading image: {str(e)}")
        logger.error(traceback.format_exc())
        return None

def test_verify_upload(s3_client, s3_key):
    """Verify that the image was uploaded correctly."""
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    try:
        # Check if the object exists
        response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        logger.info(f"✓ Verified object exists in S3: {s3_key}")
        logger.info(f"Object metadata: {response}")
        
        # Generate a presigned URL
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=3600
        )
        logger.info(f"✓ Generated presigned URL: {url}")
        
        return True
    except ClientError as e:
        logger.error(f"Error verifying upload: {str(e)}")
        logger.error(traceback.format_exc())
        return False
    except Exception as e:
        logger.error(f"Unexpected error verifying upload: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def test_download_image(s3_client, s3_key):
    """Test downloading the uploaded image."""
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    try:
        # Create a byte stream to store the retrieved image
        img_data = BytesIO()
        
        # Download the image to the byte stream
        s3_client.download_fileobj(bucket_name, s3_key, img_data)
        img_data.seek(0)
        
        # Open the image with PIL
        image = Image.open(img_data)
        
        logger.info(f"✓ Successfully downloaded image from S3: {s3_key}")
        logger.info(f"Downloaded image size: {image.size}, format: {image.format}")
        
        return True
    except ClientError as e:
        logger.error(f"Error downloading image from S3: {str(e)}")
        logger.error(traceback.format_exc())
        return False
    except Exception as e:
        logger.error(f"Unexpected error downloading image: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def test_delete_image(s3_client, s3_key):
    """Test deleting the uploaded image."""
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    try:
        # Delete the object
        s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
        
        logger.info(f"✓ Successfully deleted image from S3: {s3_key}")
        
        # Verify the object is deleted
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.error(f"Object still exists after deletion: {s3_key}")
            return False
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.info(f"✓ Verified object no longer exists: {s3_key}")
                return True
            else:
                logger.error(f"Error verifying deletion: {str(e)}")
                return False
                
    except ClientError as e:
        logger.error(f"Error deleting image from S3: {str(e)}")
        logger.error(traceback.format_exc())
        return False
    except Exception as e:
        logger.error(f"Unexpected error deleting image: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def test_s3_module():
    """Test the S3 module directly."""
    try:
        # Import our S3 module
        import s3_storage
        logger.info("Successfully imported s3_storage module")
        
        # Create a test image
        img = create_test_image()
        
        # Test the module's functions
        # 1. Test the upload_image_to_s3 function
        logger.info("Testing upload_image_to_s3 function...")
        s3_key = s3_storage.upload_image_to_s3(img, organization_id="test_org")
        
        if s3_key:
            logger.info(f"✓ Successfully uploaded image using module: {s3_key}")
            
            # 2. Test the generate_presigned_url function
            logger.info("Testing generate_presigned_url function...")
            url = s3_storage.generate_presigned_url(s3_key)
            
            if url:
                logger.info(f"✓ Successfully generated presigned URL using module: {url}")
            else:
                logger.error("Failed to generate presigned URL using module")
            
            # 3. Test the get_image_from_s3 function
            logger.info("Testing get_image_from_s3 function...")
            try:
                image = s3_storage.get_image_from_s3(s3_key)
                logger.info(f"✓ Successfully retrieved image using module")
                logger.info(f"Retrieved image size: {image.size}, format: {image.format}")
            except Exception as e:
                logger.error(f"Failed to retrieve image using module: {str(e)}")
            
            # 4. Test the delete_image_from_s3 function
            logger.info("Testing delete_image_from_s3 function...")
            if s3_storage.delete_image_from_s3(s3_key):
                logger.info(f"✓ Successfully deleted image using module: {s3_key}")
            else:
                logger.error(f"Failed to delete image using module: {s3_key}")
        else:
            logger.error("Failed to upload image using module")
        
        return True
    except Exception as e:
        logger.error(f"Error testing S3 module: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def test_image_processor():
    """Test the image_processor module."""
    try:
        # Import our image_processor module
        from image_processor import save_uploaded_image
        logger.info("Successfully imported image_processor module")
        
        # Create a test image
        img = create_test_image()
        
        # Test the save_uploaded_image function
        logger.info("Testing save_uploaded_image function...")
        storage_result = save_uploaded_image(img, "test_image.jpg", organization_id="test_org")
        
        if storage_result:
            logger.info(f"✓ Successfully saved image using image_processor module")
            logger.info(f"Storage result: {storage_result}")
            
            # Check if S3 key was generated
            s3_key = storage_result.get('s3_key')
            if s3_key:
                logger.info(f"✓ S3 key was generated: {s3_key}")
                
                # Test deleting the image from S3
                try:
                    import s3_storage
                    if s3_storage.delete_image_from_s3(s3_key):
                        logger.info(f"✓ Successfully deleted test image from S3: {s3_key}")
                    else:
                        logger.error(f"Failed to delete test image from S3: {s3_key}")
                except Exception as e:
                    logger.error(f"Error deleting test image from S3: {str(e)}")
            else:
                logger.warning("No S3 key was generated")
        else:
            logger.error("Failed to save image using image_processor module")
        
        return True
    except Exception as e:
        logger.error(f"Error testing image_processor module: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def main():
    """Run all the tests."""
    logger.info("===== STARTING S3 INTEGRATION TESTS =====")
    
    # Check AWS environment variables
    if not check_aws_env_vars():
        logger.error("Missing required AWS environment variables. Exiting.")
        return False
    
    # Create an S3 client
    s3_client = create_s3_client()
    if not s3_client:
        logger.error("Failed to create S3 client. Exiting.")
        return False
    
    # Test if the bucket exists
    if not test_bucket_exists(s3_client):
        logger.error("Failed to access S3 bucket. Exiting.")
        return False
    
    # Create a test image
    img = create_test_image()
    
    # Test uploading the image
    s3_key = test_upload_image(s3_client, img)
    if not s3_key:
        logger.error("Failed to upload image to S3. Exiting.")
        return False
    
    # Test verifying the upload
    if not test_verify_upload(s3_client, s3_key):
        logger.warning("Failed to verify upload, but continuing with tests.")
    
    # Test downloading the image
    if not test_download_image(s3_client, s3_key):
        logger.warning("Failed to download image, but continuing with tests.")
    
    # Test deleting the image
    if not test_delete_image(s3_client, s3_key):
        logger.warning("Failed to delete image, but continuing with tests.")
    
    # Test the S3 module directly
    if not test_s3_module():
        logger.warning("Failed to test S3 module directly, but continuing with tests.")
    
    # Test the image_processor module
    if not test_image_processor():
        logger.warning("Failed to test image_processor module, but continuing with tests.")
    
    logger.info("===== S3 INTEGRATION TESTS COMPLETED =====")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)