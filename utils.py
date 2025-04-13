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
    
    # Add debug logging
    logging.debug(f"Getting image URL for receipt {receipt.id}: s3_key={receipt.s3_key}, image_path={receipt.image_path}")
    
    # Approach 1: Use S3 URL if available and preferred
    if prefer_s3 and receipt.s3_key and hasattr(receipt, 's3_url'):
        s3_url = receipt.s3_url
        if s3_url:
            logging.debug(f"Using S3 URL for receipt {receipt.id}: {s3_url}")
            return s3_url
    
    # Approach 2: Use local image path if available
    if receipt.image_path:
        # Handle both formats: with or without 'static/' prefix
        clean_path = receipt.image_path.replace('static/', '')
        # Ensure path doesn't start with slash to avoid double slashes
        clean_path = clean_path.lstrip('/')
        # Create the full static URL
        image_url = f"/static/{clean_path}"
        logging.debug(f"Using local image path for receipt {receipt.id}: {image_url}")
        return image_url
    
    # Approach 3: Fall back to S3 URL if not already tried and available
    if not prefer_s3 and receipt.s3_key and hasattr(receipt, 's3_url'):
        s3_url = receipt.s3_url
        if s3_url:
            logging.debug(f"Falling back to S3 URL for receipt {receipt.id}: {s3_url}")
            return s3_url
    
    # No valid image URL found, return a placeholder image URL
    logging.warning(f"No valid image URL found for receipt {receipt.id}")
    return "/static/img/receipt-placeholder.svg"
