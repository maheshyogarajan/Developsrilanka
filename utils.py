import base64
from io import BytesIO
import logging
from PIL import Image

def image_to_base64(image):
    """
    Convert a PIL Image to a base64 string.
    
    Args:
        image: PIL Image object
    
    Returns:
        Base64 encoded string of the image
    """
    buffered = BytesIO()
    image.save(buffered, format="JPEG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return f"data:image/jpeg;base64,{img_str}"

def format_currency(amount, currency="Rs "):
    """
    Format a number as currency.
    
    Args:
        amount: The amount to format
        currency: The currency symbol to use (default is Sri Lankan Rupee - Rs)
    
    Returns:
        Formatted currency string
    """
    try:
        num = float(amount)
        # Format with commas for thousands separator and 2 decimal places
        formatted_amount = "{:,.2f}".format(num)
        return f"{currency}{formatted_amount}"
    except (ValueError, TypeError):
        return f"{currency}0.00"

def get_receipt_image_url(receipt, prefer_s3=True):
    """
    Get the image URL for a receipt with consistent prioritization.
    This is a centralized helper to standardize image URL generation across the application.
    
    Args:
        receipt: Receipt model object
        prefer_s3: Whether to prioritize S3 URLs over local paths (default: True)
    
    Returns:
        str: URL to display the receipt image
    """
    image_url = None
    receipt_id = getattr(receipt, 'id', 'unknown')
    
    # Get path and S3 key with extensive validation
    s3_key = getattr(receipt, 's3_key', None)
    image_path = getattr(receipt, 'image_path', None)
    
    # Handle all invalid image_path cases
    if not image_path or image_path == 'None' or image_path == '' or image_path is None:
        image_path = None
    
    # Handle all invalid s3_key cases
    if not s3_key or s3_key == 'None' or s3_key == '' or s3_key is None:
        s3_key = None
    
    # Add enhanced debug logging
    logging.debug(f"Getting image URL for receipt {receipt_id}: s3_key='{s3_key}', image_path='{image_path}'")
    
    # Approach 1: Use S3 URL if available and preferred
    if prefer_s3 and s3_key and hasattr(receipt, 's3_url'):
        try:
            s3_url = receipt.s3_url
            if s3_url and s3_url != '' and s3_url != 'None':
                logging.debug(f"Using S3 URL for receipt {receipt_id}: {s3_url}")
                return s3_url
        except Exception as s3_error:
            logging.error(f"Error getting S3 URL for receipt {receipt_id}: {str(s3_error)}")
    
    # Approach 2: Use local image path if available
    if image_path:
        try:
            # Handle both formats: with or without 'static/' prefix
            clean_path = str(image_path).replace('static/', '')
            # Ensure path doesn't start with slash to avoid double slashes
            clean_path = clean_path.lstrip('/')
            
            # Validate that the path actually points to a file
            import os
            full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', clean_path)
            
            if os.path.exists(full_path) and os.path.isfile(full_path):
                # Create the full static URL
                image_url = f"/static/{clean_path}"
                logging.debug(f"Using local image path for receipt {receipt_id}: {image_url}")
                return image_url
            else:
                logging.warning(f"File not found for receipt {receipt_id}: {full_path}")
        except Exception as e:
            logging.error(f"Error formatting image path '{image_path}' for receipt {receipt_id}: {str(e)}")
    
    # Approach 3: Fall back to S3 URL if not already tried and available
    if not prefer_s3 and s3_key and hasattr(receipt, 's3_url'):
        try:
            s3_url = receipt.s3_url
            if s3_url and s3_url != '' and s3_url != 'None':
                logging.debug(f"Falling back to S3 URL for receipt {receipt_id}: {s3_url}")
                return s3_url
        except Exception as s3_error:
            logging.error(f"Error falling back to S3 URL for receipt {receipt_id}: {str(s3_error)}")
    
    # No valid image URL found, return a placeholder image URL
    logging.warning(f"No valid image URL found for receipt {receipt_id}, using placeholder")
    return "/static/img/receipt-placeholder.svg"
