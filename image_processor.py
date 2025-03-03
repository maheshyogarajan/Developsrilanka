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

from app import db
from celery_config import app as celery_app
from models import Receipt, ReceiptItem

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure storage directory exists
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def save_uploaded_image(image_data, filename):
    """
    Save the uploaded image to the server.
    
    Args:
        image_data: PIL Image or bytes
        filename: Name to save the file as
    
    Returns:
        Path to the saved image
    """
    try:
        # Ensure we're working with a PIL Image
        if not isinstance(image_data, Image.Image):
            if isinstance(image_data, bytes):
                image_data = Image.open(BytesIO(image_data))
            else:
                raise ValueError("Invalid image data type")
        
        # Generate a unique filename to prevent overwriting
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name, ext = os.path.splitext(filename)
        unique_filename = f"{base_name}_{timestamp}{ext}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        # Save the image
        image_data.save(file_path)
        relative_path = os.path.join('uploads', unique_filename)
        logger.info(f"Image saved to {file_path}")
        return relative_path
    
    except Exception as e:
        logger.error(f"Error saving image: {str(e)}")
        logger.error(traceback.format_exc())
        raise


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def process_receipt_image(self, image_data_b64, original_filename, gemini_processor_fn):
    """
    Process a receipt image with Gemini Vision API.
    
    Args:
        image_data_b64: Base64 encoded image data
        original_filename: Original filename of the uploaded image
        gemini_processor_fn: Function name to process the image
    
    Returns:
        Dictionary containing extracted receipt data and image path
    """
    try:
        logger.info(f"Starting to process receipt image: {original_filename}")
        start_time = time.time()
        
        # Decode base64 image
        image_data = base64.b64decode(image_data_b64)
        image = Image.open(BytesIO(image_data))
        
        # Save the image to disk
        image_path = save_uploaded_image(image, original_filename)
        
        # Process with Gemini Vision API
        # Since we can't directly import the function (circular import issue),
        # we'll use a string identifier and reference the app module
        import app as app_module
        process_function = getattr(app_module, gemini_processor_fn)
        extracted_data = process_function(image)
        
        # Add the image path to the extracted data
        extracted_data['image_path'] = image_path
        
        # Log processing time
        processing_time = time.time() - start_time
        logger.info(f"Receipt processing completed in {processing_time:.2f} seconds")
        
        return extracted_data
    
    except Exception as e:
        logger.error(f"Error processing receipt image: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Retry the task
        retry_count = self.request.retries
        logger.info(f"Retrying task, attempt {retry_count + 1} of {self.max_retries + 1}")
        
        raise self.retry(exc=e)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def save_receipt_to_database(self, receipt_data):
    """
    Save the receipt data to the database.
    
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
        expense_major_category = receipt_data.get('expense_major_category', '')
        expense_minor_category = receipt_data.get('expense_minor_category', '')
        service_charge = float(receipt_data.get('service_charge', 0))
        vat_tax = float(receipt_data.get('vat_tax', 0))
        sscl_tax = float(receipt_data.get('sscl_tax', 0))
        vat_registration_number = receipt_data.get('vat_registration_number', '')
        
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
        db.session.rollback()
        
        # Retry the task
        retry_count = self.request.retries
        logger.info(f"Retrying task, attempt {retry_count + 1} of {self.max_retries + 1}")
        
        raise self.retry(exc=e)


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