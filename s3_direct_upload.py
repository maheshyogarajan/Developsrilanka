"""
Standalone S3 Direct Upload Module

This module provides a simple API for directly uploading images to S3
without any dependencies on other parts of the application.

It also handles thumbnail generation and uploads thumbnails to S3.
"""

import os
import io
import uuid
import logging
import traceback
from PIL import Image, ImageOps
import boto3
from botocore.exceptions import ClientError

# Constants for thumbnail generation
THUMBNAIL_SIZE = (200, 200)  # Default thumbnail size
THUMBNAIL_PREFIX = "thumbnails/"  # S3 folder for thumbnails
THUMBNAIL_QUALITY = 85  # JPEG quality (1-100)
THUMBNAIL_FORMAT = "JPEG"  # Output format

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def upload_receipt_to_s3(image, organization_id=None, receipt_id=None):
    """
    Upload an image directly to S3 with maximum reliability, and also generate and upload a thumbnail.
    
    Args:
        image: PIL Image object or file-like object or bytes
        organization_id: Optional organization ID for path structure
        receipt_id: Optional receipt ID for path structure
        
    Returns:
        tuple: (s3_key, thumbnail_s3_key) if successful, (s3_key, None) if only main image upload successful, 
              (None, None) if failed
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
            return None, None
        
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
            return None, None
        
        # Verify the upload by checking if the object exists
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.info(f"✅ S3 upload verified: {s3_key}")
            
            # Now that we've successfully uploaded the original image, generate and upload a thumbnail
            logger.info("Generating thumbnail for the uploaded image...")
            
            # Different handling based on input type
            image_data = None
            if isinstance(image, Image.Image):
                # Convert PIL Image to bytes
                img_byte_arr = io.BytesIO()
                image.save(img_byte_arr, format='JPEG', quality=85)
                img_byte_arr.seek(0)
                image_data = img_byte_arr.getvalue()
            elif isinstance(image, bytes):
                # Use bytes directly
                image_data = image
            elif hasattr(image, 'read'):
                # Read file-like object
                image.seek(0)  # Reset file pointer
                image_data = image.read()
                image.seek(0)  # Reset file pointer again for potential future use
            elif isinstance(image, str) and os.path.isfile(image):
                # Read file from path
                with open(image, 'rb') as f:
                    image_data = f.read()
            
            if image_data:
                # Create the thumbnail
                thumbnail_data = create_thumbnail(image_data)
                if thumbnail_data:
                    # Generate thumbnail S3 key
                    thumbnail_s3_key = get_thumbnail_s3_key(s3_key)
                    
                    # Upload thumbnail to S3
                    try:
                        thumbnail_io = io.BytesIO(thumbnail_data)
                        s3_client.upload_fileobj(thumbnail_io, bucket_name, thumbnail_s3_key)
                        logger.info(f"✅ Thumbnail uploaded to S3 with key: {thumbnail_s3_key}")
                        
                        # Verify thumbnail upload
                        s3_client.head_object(Bucket=bucket_name, Key=thumbnail_s3_key)
                        logger.info(f"✅ Thumbnail upload verified: {thumbnail_s3_key}")
                        
                        # Return both the original S3 key and the thumbnail S3 key
                        return s3_key, thumbnail_s3_key
                    except Exception as thumbnail_error:
                        logger.error(f"❌ Error uploading thumbnail: {str(thumbnail_error)}")
                        # Return only the original S3 key if thumbnail upload fails
                        return s3_key, None
                else:
                    logger.error("❌ Failed to generate thumbnail")
                    return s3_key, None
            else:
                logger.error("❌ Could not extract image data for thumbnail generation")
                return s3_key, None
        except ClientError as e:
            logger.error(f"❌ S3 upload verification failed: {str(e)}")
            return None, None
            
    except Exception as e:
        logger.error(f"❌ Error in direct S3 upload: {str(e)}")
        logger.error(traceback.format_exc())
        return None, None

def create_thumbnail(image_data, size=THUMBNAIL_SIZE):
    """
    Create a thumbnail from image data.
    
    Args:
        image_data (bytes): The original image data
        size (tuple): Target thumbnail size (width, height)
        
    Returns:
        bytes: The thumbnail image data
    """
    try:
        # Create BytesIO objects for input/output
        input_stream = io.BytesIO(image_data)
        output_stream = io.BytesIO()
        
        # Open the image and create thumbnail
        with Image.open(input_stream) as img:
            # Apply some automatic enhancements for receipts
            img = ImageOps.exif_transpose(img)  # Handle rotation
            
            # Create a copy to avoid modifying original during resize
            thumb = img.copy()
            thumb.thumbnail(size, Image.LANCZOS)
            
            # Determine format to save (keep original format if possible)
            save_format = img.format if img.format else THUMBNAIL_FORMAT
            
            # Save the thumbnail
            if save_format == 'JPEG' or save_format == 'JPG':
                thumb.save(output_stream, save_format, quality=THUMBNAIL_QUALITY, optimize=True)
            else:
                thumb.save(output_stream, save_format)
        
        # Return the thumbnail image data
        output_stream.seek(0)
        return output_stream.getvalue()
        
    except Exception as e:
        logger.error(f"Error creating thumbnail: {str(e)}")
        return None

def get_thumbnail_s3_key(original_s3_key):
    """
    Generate the S3 key for a thumbnail based on the original image key.
    
    Args:
        original_s3_key (str): The original image S3 key
        
    Returns:
        str: The thumbnail S3 key
    """
    if not original_s3_key:
        return None
        
    # Get filename and path parts
    path_parts = original_s3_key.rsplit('/', 1)
    
    if len(path_parts) == 1:
        # No path, just filename
        filename = path_parts[0]
        thumbnail_key = f"{THUMBNAIL_PREFIX}{filename}"
    else:
        # Has path and filename
        path, filename = path_parts
        thumbnail_key = f"{THUMBNAIL_PREFIX}{path}/{filename}"
    
    return thumbnail_key

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