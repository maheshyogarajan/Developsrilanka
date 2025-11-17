import base64
import io
import json
import logging
import os
import time
import traceback
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from celery_config import app as celery_app
# Import models and db at the function level to avoid circular imports
import s3_storage
import s3_direct_upload

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure storage directory exists for local storage fallback
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Flag to control whether to use S3 or local storage
# Module-level variables for S3 storage
s3_handler = None

def init_s3_storage():
    """Initialize S3 storage based on environment variables."""
    global s3_handler
    
    # Check if S3 credentials are available
    use_s3 = all([
        os.environ.get('AWS_S3_BUCKET_NAME'),
        os.environ.get('AWS_ACCESS_KEY_ID'),
        os.environ.get('AWS_SECRET_ACCESS_KEY'),
        os.environ.get('AWS_REGION')
    ])
    
    logger.info(f"S3 Storage enabled? {use_s3}")
    logger.info(f"AWS_S3_BUCKET_NAME available: {bool(os.environ.get('AWS_S3_BUCKET_NAME'))}")
    logger.info(f"AWS_ACCESS_KEY_ID available: {bool(os.environ.get('AWS_ACCESS_KEY_ID'))}")
    logger.info(f"AWS_SECRET_ACCESS_KEY available: {bool(os.environ.get('AWS_SECRET_ACCESS_KEY'))}")
    logger.info(f"AWS_REGION available: {bool(os.environ.get('AWS_REGION'))}")
    
    # Initialize S3 storage handler if S3 is enabled
    if use_s3:
        try:
            s3_handler = s3_storage.S3Storage()
            logger.info(f"S3 handler initialized: {s3_handler is not None}")
        except Exception as e:
            logger.error(f"Error initializing S3 storage handler: {str(e)}")
            s3_handler = None
    else:
        logger.info("S3 storage disabled due to missing credentials")
        s3_handler = None
    
    return use_s3

# Initialize S3 storage when module is loaded
USE_S3_STORAGE = init_s3_storage()


def save_uploaded_image(image_data, filename, organization_id=None):
    """
    Save the uploaded image to local storage or S3 depending on configuration.
    
    Args:
        image_data: PIL Image or bytes
        filename: Name to save the file as
        organization_id: Optional organization ID for S3 storage path
    
    Returns:
        Dictionary containing image path and S3 key (if applicable)
    """
    global s3_handler  # Declare global at the start of the function
    
    # Enable more debug logging
    logging.basicConfig(level=logging.DEBUG)
    logger.setLevel(logging.DEBUG)
    logger.debug(f"Starting save_uploaded_image with filename={filename}, organization_id={organization_id}")
    
    try:
        # Debug log all environment variables to check if S3 is properly configured
        logger.info("=== S3 Environment Variables Check ===")
        logger.info(f"AWS_S3_BUCKET_NAME: {bool(os.environ.get('AWS_S3_BUCKET_NAME'))}")
        logger.info(f"AWS_ACCESS_KEY_ID: {bool(os.environ.get('AWS_ACCESS_KEY_ID'))}")
        logger.info(f"AWS_SECRET_ACCESS_KEY: {bool(os.environ.get('AWS_SECRET_ACCESS_KEY'))}")
        logger.info(f"AWS_REGION: {bool(os.environ.get('AWS_REGION'))}")
        logger.info(f"USE_S3_STORAGE calculated value: {USE_S3_STORAGE}")
        logger.info(f"S3 handler initialized: {s3_handler is not None}")
        logger.info(f"Function input - filename: {filename}, organization_id: {organization_id}")
        if hasattr(image_data, 'size'):
            logger.info(f"Image size: {image_data.size}, format: {getattr(image_data, 'format', 'unknown')}")
        elif isinstance(image_data, bytes):
            logger.info(f"Image data type: bytes, size: {len(image_data)} bytes")
        else:
            logger.info(f"Image data type: {type(image_data)}")
        logger.info("=======================================")
        
        # Force check if S3 should be enabled based on environment variables
        s3_enabled = all([
            os.environ.get('AWS_S3_BUCKET_NAME'),
            os.environ.get('AWS_ACCESS_KEY_ID'),
            os.environ.get('AWS_SECRET_ACCESS_KEY'),
            os.environ.get('AWS_REGION')
        ])
        
        logger.info(f"S3 should be enabled based on env vars: {s3_enabled}")
        
        # Always reinitialize S3 handler to ensure a fresh connection
        if s3_enabled:
            logger.info("S3 environment variables are set - initializing fresh S3 handler")
            try:
                s3_handler = s3_storage.S3Storage()
                logger.info(f"Successfully initialized S3 handler: {s3_handler is not None}")
            except Exception as init_error:
                logger.error(f"Error initializing S3 handler: {str(init_error)}")
                logger.error(traceback.format_exc())
        
        # Ensure we're working with a PIL Image
        if not isinstance(image_data, Image.Image):
            if isinstance(image_data, bytes):
                image_data = Image.open(BytesIO(image_data))
                logger.info(f"Converted bytes to PIL Image with format: {image_data.format}")
            else:
                logger.error(f"Invalid image data type: {type(image_data)}")
                raise ValueError(f"Invalid image data type: {type(image_data)}")
        
        # Generate a unique filename to prevent overwriting
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name, ext = os.path.splitext(filename)
        unique_filename = f"{base_name}_{timestamp}{ext}"
        logger.info(f"Generated unique filename: {unique_filename}")
        
        # Prepare result dictionary
        result = {
            'image_path': None,
            's3_key': ''
        }
        
        # If S3 storage is enabled and the handler is available, use it
        if s3_enabled and s3_handler:
            logger.info(f"Using S3 storage for image: {filename}")
            try:
                # Add detailed debugging to diagnose S3 upload
                logger.info(f"S3 handler type: {type(s3_handler)}")
                logger.info(f"Bucket name: {s3_handler.bucket_name if hasattr(s3_handler, 'bucket_name') else 'None'}")
                
                # Create a deep copy of the image to avoid potential closed file issues
                img_copy = image_data.copy()
                logger.info(f"Created image copy with size: {img_copy.size}")
                
                # Upload to S3, using direct upload from s3_storage module if we have issues
                try:
                    s3_key = s3_handler.upload_image(img_copy, organization_id)
                    logger.info(f"Image uploaded to S3 with key: {s3_key}")
                except Exception as handler_error:
                    # If the class-based upload failed, try the direct function
                    logger.warning(f"S3 handler upload failed: {str(handler_error)}, trying direct upload function")
                    img_copy = image_data.copy()  # Create a fresh copy
                    s3_key = s3_storage.upload_image_to_s3(img_copy, organization_id)
                    logger.info(f"Direct S3 upload successful with key: {s3_key}")
                
                result['s3_key'] = s3_key
                
                # Still save locally as a backup and for immediate display
                file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                image_data.save(file_path)
                relative_path = os.path.join('uploads', unique_filename)
                result['image_path'] = relative_path
                logger.info(f"Backup image saved locally to {file_path}")
                
                # Return full result with both local path and S3 key
                logger.info(f"Returning storage result with image_path={result['image_path']} and s3_key={result['s3_key']}")
                return result
            except Exception as s3_error:
                logger.error(f"Error using S3 storage, falling back to local: {str(s3_error)}")
                logger.error(traceback.format_exc())
                # Fall back to local storage on S3 error
        else:
            logger.warning(f"S3 storage skipped: USE_S3_STORAGE={USE_S3_STORAGE}, s3_handler available={s3_handler is not None}")
        
        # Save to local storage (either as primary or fallback)
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        image_data.save(file_path)
        relative_path = os.path.join('uploads', unique_filename)
        result['image_path'] = relative_path
        logger.info(f"Image saved locally to {file_path}")
        
        # Return result with local path only
        logger.info(f"Returning storage result with image_path={result['image_path']} and empty s3_key")
        return result
    
    except Exception as e:
        logger.error(f"Error saving image: {str(e)}")
        logger.error(traceback.format_exc())
        raise


@celery_app.task(
    bind=True,
    max_retries=2,  # Reduced since Gemini API has its own retry logic
    autoretry_for=(Exception,),
    retry_backoff=True,  # Enable exponential backoff
    retry_backoff_max=600,  # Max 10 minutes between retries
    retry_jitter=True  # Add random jitter to prevent thundering herd
)
def process_receipt_image(self, image_data_b64, original_filename, gemini_processor_fn, organization_id=None):
    """
    Process a receipt image with Gemini Vision API.
    
    This task uses exponential backoff retry strategy for infrastructure failures.
    Gemini API-specific errors are handled by the generate_with_retry function
    inside process_receipt_with_gemini.
    
    Args:
        image_data_b64: Base64 encoded image data
        original_filename: Original filename of the uploaded image
        gemini_processor_fn: Function name to process the image
        organization_id: Optional organization ID for S3 key path
    
    Returns:
        Dictionary containing extracted receipt data, image path, and S3 key
    """
    try:
        logger.info(f"Starting to process receipt image: {original_filename}")
        if organization_id:
            logger.info(f"Using organization_id: {organization_id} for S3 storage path")
        
        start_time = time.time()
        
        # Decode base64 image
        image_data = base64.b64decode(image_data_b64)
        image = Image.open(BytesIO(image_data))
        
        # Upload directly to S3 first using the standalone function for maximum reliability
        # This avoids any potential issues with class-based handlers and shared state
        logger.info(f"Attempting direct S3 upload with thumbnail generation...")
        s3_key = ""
        thumbnail_s3_key = ""
        try:
            # Make a copy of the image to ensure we don't have issues with closed files
            img_copy = image.copy()
            # Use the new direct upload function that also generates a thumbnail
            s3_key, thumbnail_s3_key = s3_direct_upload.upload_receipt_to_s3(img_copy, organization_id)
            if s3_key:
                logger.info(f"✅ Direct S3 upload successful with key: {s3_key}")
                if thumbnail_s3_key:
                    logger.info(f"✅ Thumbnail generated and uploaded with key: {thumbnail_s3_key}")
                else:
                    logger.warning("⚠️ Main image uploaded but thumbnail generation failed")
            else:
                # Fall back to the old upload method if the new one fails completely
                logger.warning("⚠️ New upload method failed, falling back to legacy method")
                s3_key = s3_storage.upload_image_to_s3(img_copy, organization_id)
                logger.info(f"✅ Legacy direct S3 upload successful with key: {s3_key}")
        except Exception as direct_s3_error:
            logger.error(f"❌ Direct S3 upload failed: {str(direct_s3_error)}")
            logger.error(traceback.format_exc())
        
        # Also save the image using the normal method to ensure it's saved locally
        storage_result = save_uploaded_image(image, original_filename, organization_id)
        
        # Log successful image storage
        image_path = storage_result.get('image_path', '') if storage_result else ''
        # If we got a successful direct S3 upload, use that key instead
        if not storage_result.get('s3_key') and s3_key:
            logger.info(f"Using directly uploaded S3 key: {s3_key} instead of storage result key")
            if storage_result:
                storage_result['s3_key'] = s3_key
        else:
            s3_key = storage_result.get('s3_key', '') if storage_result else ''
            
        logger.info(f"Before Gemini processing - Image saved with path: {image_path} and S3 key: {s3_key}")
        
        # Process with Gemini Vision API AFTER saving the image
        # Since we can't directly import the function (circular import issue),
        # we'll use a string identifier and reference the app module
        import app as app_module
        process_function = getattr(app_module, gemini_processor_fn)
        extracted_data = process_function(image)
        
        # Add the image path, S3 key, and thumbnail S3 key to the extracted data
        if storage_result:
            # Get the values with proper defaults
            image_path_value = storage_result.get('image_path', '')
            s3_key_value = storage_result.get('s3_key', '') if storage_result.get('s3_key') else s3_key
            
            # Set explicit values in the extracted data
            extracted_data['image_path'] = image_path_value
            extracted_data['s3_key'] = s3_key_value
            
            # Add thumbnail key if available
            if thumbnail_s3_key:
                extracted_data['thumbnail_s3_key'] = thumbnail_s3_key
                logger.info(f"Added thumbnail S3 key to extracted data: {thumbnail_s3_key}")
            
            # Log for debugging
            logger.info(f"Added storage information to extracted data: path='{image_path_value}', s3_key='{s3_key_value}', thumbnail_key='{thumbnail_s3_key}'")
            
            # Verify the data was set correctly
            logger.info(f"Verification - extracted_data now has: image_path='{extracted_data.get('image_path')}', s3_key='{extracted_data.get('s3_key')}', thumbnail_key='{extracted_data.get('thumbnail_s3_key', '')}'")
        else:
            # Set default values if storage_result is empty
            extracted_data['image_path'] = ''
            extracted_data['s3_key'] = s3_key if s3_key else ''
            if thumbnail_s3_key:
                extracted_data['thumbnail_s3_key'] = thumbnail_s3_key
            logger.info(f"Limited storage result, using direct upload values: s3_key='{s3_key}', thumbnail_key='{thumbnail_s3_key}'")
        
        # Add organization ID to the extracted data
        if organization_id:
            extracted_data['organization_id'] = organization_id
        
        # Log processing time
        processing_time = time.time() - start_time
        logger.info(f"Receipt processing completed in {processing_time:.2f} seconds")
        
        return extracted_data
    
    except Exception as e:
        logger.error(f"Error processing receipt image: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Log retry info (autoretry_for handles the actual retry)
        retry_count = self.request.retries
        logger.info(f"Task failed on attempt {retry_count + 1}. Autoretry will handle retry if applicable.")
        
        # Re-raise to trigger Celery's autoretry mechanism
        raise


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,  # Enable exponential backoff
    retry_backoff_max=300,  # Max 5 minutes between retries
    retry_jitter=True  # Add random jitter
)
def save_receipt_to_database(self, receipt_data):
    """
    Save the receipt data to the database with exponential backoff retry.
    
    Args:
        receipt_data: Dictionary containing receipt information
    
    Returns:
        Receipt ID of the saved receipt
    """
    try:
        logger.info("Starting to save receipt to database")
        start_time = time.time()
        
        # Extract receipt data
        vendor_name = receipt_data.get('vendor_name', 'Unknown Vendor')
        vendor_address = receipt_data.get('vendor_address', '')
        vendor_contact = receipt_data.get('vendor_contact', '')
        date_str = receipt_data.get('date', '')
        total_amount = float(receipt_data.get('total_amount', 0))
        image_path = receipt_data.get('image_path', '')
        s3_key = receipt_data.get('s3_key', '')
        thumbnail_s3_key = receipt_data.get('thumbnail_s3_key', '')
        expense_major_category = receipt_data.get('expense_major_category', '')
        expense_minor_category = receipt_data.get('expense_minor_category', '')
        service_charge = float(receipt_data.get('service_charge', 0))
        vat_tax = float(receipt_data.get('vat_tax', 0))
        sscl_tax = float(receipt_data.get('sscl_tax', 0))
        vat_registration_number = receipt_data.get('vat_registration_number', '')
        organization_id = receipt_data.get('organization_id')
        
        # Parse date
        date_obj = None
        if date_str:
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                try:
                    date_obj = datetime.strptime(date_str, '%d/%m/%Y').date()
                except ValueError:
                    logger.warning(f"Could not parse date: {date_str}")
        
        # Import models here to avoid circular imports
        from models import Receipt, ReceiptItem
        
        # Create receipt instance
        new_receipt = Receipt(
            vendor_name=vendor_name,
            vendor_address=vendor_address,
            vendor_contact=vendor_contact,
            date=date_obj,
            total_amount=total_amount,
            service_charge=service_charge,
            vat_registration_number=vat_registration_number,
            sscl_tax=sscl_tax,
            vat_tax=vat_tax,
            image_path=image_path,
            s3_key=s3_key,
            thumbnail_s3_key=thumbnail_s3_key,
            organization_id=organization_id,
            expense_major_category=expense_major_category,
            expense_minor_category=expense_minor_category
        )
        
        # Add receipt items
        for item_data in receipt_data.get('items', []):
            item_name = item_data.get('name', 'Unknown Item')
            item_quantity = float(item_data.get('quantity', 1))
            item_price = float(item_data.get('price', 0))
            tax_deductible = item_data.get('tax_deductible', False)
            
            new_item = ReceiptItem(
                name=item_name,
                quantity=item_quantity,
                price=item_price,
                tax_deductible=tax_deductible
            )
            new_receipt.items.append(new_item)
        
        # Import db here to avoid circular imports
        from app import db
        
        # Save to database
        db.session.add(new_receipt)
        db.session.commit()
        
        receipt_id = new_receipt.id
        logger.info(f"Receipt saved to database with ID: {receipt_id}")
        
        # Log processing time
        processing_time = time.time() - start_time
        logger.info(f"Database save completed in {processing_time:.2f} seconds")
        
        return receipt_id
    
    except Exception as e:
        logger.error(f"Error saving receipt to database: {str(e)}")
        logger.error(traceback.format_exc())
        
        # If the database session is still active but in a bad state
        try:
            from app import db
            db.session.rollback()
        except:
            logger.warning("Could not rollback database session")
        
        # Log retry info (autoretry_for handles the actual retry)
        retry_count = self.request.retries
        logger.info(f"Database save failed on attempt {retry_count + 1}. Autoretry will handle retry if applicable.")
        
        # Re-raise to trigger Celery's autoretry mechanism
        raise


@celery_app.task
def cleanup_temp_files(file_paths, min_age_hours=24):
    """
    Clean up temporary files that are older than the specified age.
    
    Args:
        file_paths: List of file paths to check and potentially remove
        min_age_hours: Minimum age in hours for files to be removed
    
    Returns:
        Number of files removed
    """
    try:
        logger.info(f"Starting cleanup of temporary files older than {min_age_hours} hours")
        removed_count = 0
        current_time = datetime.now()
        
        for file_path in file_paths:
            path = Path(file_path)
            if not path.exists():
                continue
                
            # Get file age
            file_mtime = datetime.fromtimestamp(path.stat().st_mtime)
            age_hours = (current_time - file_mtime).total_seconds() / 3600
            
            # Remove if older than min_age_hours
            if age_hours >= min_age_hours:
                path.unlink()
                removed_count += 1
                logger.info(f"Removed old file: {file_path}")
        
        logger.info(f"Cleanup completed. Removed {removed_count} files")
        return removed_count
    
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")
        logger.error(traceback.format_exc())
        return 0