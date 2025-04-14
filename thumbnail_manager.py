"""
Thumbnail Manager Module

This module handles thumbnail generation, storage, and retrieval for receipt images.
It works with both S3 storage and local file storage, maintaining the same interfaces.
"""

import os
import io
from PIL import Image, ImageOps
import logging
from s3_storage import upload_file, check_file_exists, generate_presigned_url, download_file

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
THUMBNAIL_SIZE = (200, 200)  # Default thumbnail size
THUMBNAIL_PREFIX = "thumbnails/"  # S3 folder for thumbnails
THUMBNAIL_QUALITY = 85  # JPEG quality (1-100)
THUMBNAIL_FORMAT = "JPEG"  # Output format


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


def generate_thumbnail_for_s3(original_s3_key):
    """
    Generate a thumbnail for an image stored in S3 and upload it back to S3.
    
    Args:
        original_s3_key (str): The original image S3 key
        
    Returns:
        str: The thumbnail S3 key or None if failed
    """
    if not original_s3_key:
        return None
        
    try:
        # Generate the thumbnail S3 key
        thumbnail_s3_key = get_thumbnail_s3_key(original_s3_key)
        
        # Check if thumbnail already exists
        if check_file_exists(thumbnail_s3_key):
            logger.info(f"Thumbnail already exists: {thumbnail_s3_key}")
            return thumbnail_s3_key
        
        # Download the original image
        image_data = download_file(original_s3_key)
        if not image_data:
            logger.error(f"Failed to download original image: {original_s3_key}")
            return None
            
        # Create the thumbnail
        thumbnail_data = create_thumbnail(image_data)
        if not thumbnail_data:
            logger.error(f"Failed to create thumbnail for: {original_s3_key}")
            return None
            
        # Upload the thumbnail to S3
        if upload_file(thumbnail_data, thumbnail_s3_key):
            logger.info(f"Successfully generated and uploaded thumbnail: {thumbnail_s3_key}")
            return thumbnail_s3_key
        else:
            logger.error(f"Failed to upload thumbnail: {thumbnail_s3_key}")
            return None
            
    except Exception as e:
        logger.error(f"Error generating thumbnail for {original_s3_key}: {str(e)}")
        return None


def get_thumbnail_url(original_s3_key):
    """
    Get a URL for the thumbnail of an image.
    
    Will check if a thumbnail exists, and if not, generate one.
    
    Args:
        original_s3_key (str): The original image S3 key
        
    Returns:
        str: The thumbnail URL or None if failed
    """
    if not original_s3_key:
        return None
        
    # Generate the thumbnail S3 key
    thumbnail_s3_key = get_thumbnail_s3_key(original_s3_key)
    
    # Check if thumbnail exists
    if not check_file_exists(thumbnail_s3_key):
        # Generate the thumbnail
        thumbnail_s3_key = generate_thumbnail_for_s3(original_s3_key)
        
    # Generate a URL for the thumbnail
    if thumbnail_s3_key:
        return generate_presigned_url(thumbnail_s3_key)
    
    # Fallback to original image URL if thumbnail generation failed
    return generate_presigned_url(original_s3_key)


def ensure_thumbnail_exists(original_s3_key):
    """
    Ensure a thumbnail exists for an image, generating one if necessary.
    This is useful for background generation of thumbnails.
    
    Args:
        original_s3_key (str): The original image S3 key
        
    Returns:
        bool: True if thumbnail exists or was created, False otherwise
    """
    if not original_s3_key:
        return False
        
    # Generate the thumbnail S3 key
    thumbnail_s3_key = get_thumbnail_s3_key(original_s3_key)
    
    # Check if thumbnail exists
    if check_file_exists(thumbnail_s3_key):
        return True
        
    # Generate the thumbnail
    return generate_thumbnail_for_s3(original_s3_key) is not None