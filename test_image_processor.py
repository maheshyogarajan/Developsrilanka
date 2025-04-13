"""
Test script specifically for image_processor module to verify S3 upload functionality.
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

def test_save_uploaded_image():
    """Test the save_uploaded_image function from image_processor."""
    try:
        # Import the function to test
        from image_processor import save_uploaded_image
        logger.info("Successfully imported save_uploaded_image function from image_processor")
        
        # Create a test image
        img = create_test_image()
        logger.info(f"Image details before save: format={getattr(img, 'format', 'unknown')}, mode={img.mode}, size={img.size}")
        
        # Test with organization_id
        logger.info("Testing save_uploaded_image with organization_id...")
        filename = f"test_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        organization_id = "test_org"
        
        logger.info(f"Saving image with filename: {filename}, organization_id: {organization_id}")
        result = save_uploaded_image(img, filename, organization_id)
        
        if result:
            logger.info(f"✓ Successfully saved image")
            logger.info(f"Result: {result}")
            
            # Check if S3 key was generated
            s3_key = result.get('s3_key')
            if s3_key:
                logger.info(f"✓ S3 key was generated: {s3_key}")
                return True
            else:
                logger.warning("No S3 key was generated")
                return False
        else:
            logger.error("Failed to save image")
            return False
            
    except Exception as e:
        logger.error(f"Error testing save_uploaded_image: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def test_image_processor_full_flow():
    """Test the full flow of image processing including S3 storage."""
    try:
        # Check if we have a PIL Image module
        try:
            from PIL import Image
            logger.info("Successfully imported PIL.Image")
        except ImportError:
            logger.error("Failed to import PIL.Image")
            return False
            
        # Create a test image
        img = create_test_image()
        
        # Get the organization ID (mocking a user's default organization)
        organization_id = "test_organization_mock"
        
        # Import our modules
        from image_processor import save_uploaded_image
        import s3_storage
        
        # Step 1: Save the uploaded image
        logger.info("Step 1: Saving uploaded image...")
        storage_result = save_uploaded_image(img, "test_full_flow.jpg", organization_id)
        
        if not storage_result:
            logger.error("Failed to save uploaded image")
            return False
            
        logger.info(f"Storage result: {storage_result}")
        
        # Step 2: Verify the S3 key
        s3_key = storage_result.get('s3_key')
        if not s3_key:
            logger.warning("No S3 key was generated")
            return False
            
        logger.info(f"✓ S3 key generated: {s3_key}")
        
        # Step 3: Verify the file exists in S3
        logger.info(f"Step 3: Verifying file exists in S3...")
        
        try:
            s3_client = s3_storage.get_s3_client()
            bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
            
            response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.info(f"✓ Verified object exists in S3: {s3_key}")
            logger.info(f"Object metadata: {response}")
        except Exception as e:
            logger.error(f"Failed to verify object exists in S3: {str(e)}")
            return False
            
        # Step 4: Generate presigned URL
        logger.info(f"Step 4: Generating presigned URL...")
        
        try:
            url = s3_storage.generate_presigned_url(s3_key)
            if url:
                logger.info(f"✓ Generated presigned URL: {url}")
            else:
                logger.error("Failed to generate presigned URL")
                return False
        except Exception as e:
            logger.error(f"Failed to generate presigned URL: {str(e)}")
            return False
            
        # Step 5: Clean up - delete the test image from S3
        logger.info(f"Step 5: Cleaning up - deleting test image from S3...")
        
        try:
            result = s3_storage.delete_image_from_s3(s3_key)
            if result:
                logger.info(f"✓ Successfully deleted test image from S3: {s3_key}")
            else:
                logger.error(f"Failed to delete test image from S3: {s3_key}")
        except Exception as e:
            logger.error(f"Error deleting test image from S3: {str(e)}")
            
        return True
            
    except Exception as e:
        logger.error(f"Error testing full flow: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def main():
    """Run all the tests."""
    logger.info("===== STARTING IMAGE PROCESSOR TESTS =====")
    
    # Test save_uploaded_image function
    logger.info("Testing save_uploaded_image function...")
    if test_save_uploaded_image():
        logger.info("✓ save_uploaded_image test passed")
    else:
        logger.error("✗ save_uploaded_image test failed")
    
    # Test the full flow
    logger.info("Testing full image processing flow...")
    if test_image_processor_full_flow():
        logger.info("✓ Full flow test passed")
    else:
        logger.error("✗ Full flow test failed")
    
    logger.info("===== IMAGE PROCESSOR TESTS COMPLETED =====")
    
if __name__ == "__main__":
    main()