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

def get_receipt_image_url(receipt, prefer_s3=True, prefer_thumbnail=False):
    """
    Get the image URL for a receipt with consistent prioritization.
    This is a centralized helper to standardize image URL generation across the application.
    
    Args:
        receipt: Receipt model object
        prefer_s3: Whether to prioritize S3 URLs over local paths (default: True)
        prefer_thumbnail: Whether to prioritize thumbnail over full-size image (default: False)
    
    Returns:
        str: URL to display the receipt image
    """
    image_url = None
    receipt_id = getattr(receipt, 'id', 'unknown')
    
    # Get path and S3 key with extensive validation
    s3_key = getattr(receipt, 's3_key', None)
    thumbnail_s3_key = getattr(receipt, 'thumbnail_s3_key', None)
    image_path = getattr(receipt, 'image_path', None)
    
    # Handle all invalid image_path cases
    if not image_path or image_path == 'None' or image_path == '' or image_path is None:
        image_path = None
    
    # Handle all invalid s3_key cases
    if not s3_key or s3_key == 'None' or s3_key == '' or s3_key is None:
        s3_key = None
    # Handle the case where s3_key is a string representation of a tuple: "(key1,key2)"
    elif isinstance(s3_key, str) and s3_key.startswith('(') and s3_key.endswith(')') and ',' in s3_key:
        logging.warning(f"Found string representation of a tuple in s3_key: {s3_key}")
        try:
            # Extract the first key from the tuple representation
            parts = s3_key[1:-1].split(',')
            if parts and parts[0].strip():
                s3_key = parts[0].strip('"\'')
                logging.info(f"Extracted original s3_key from string tuple: {s3_key}")
                # If there's a second part, it might be the thumbnail key
                if len(parts) > 1 and parts[1].strip():
                    extracted_thumbnail = parts[1].strip('"\'')
                    if not thumbnail_s3_key:  # Only set if not already set
                        thumbnail_s3_key = extracted_thumbnail
                        logging.info(f"Extracted thumbnail_s3_key from string tuple: {thumbnail_s3_key}")
            else:
                logging.error(f"Failed to extract valid key from string tuple: {s3_key}")
                s3_key = None
        except Exception as e:
            logging.error(f"Error parsing string tuple for s3_key: {str(e)}")
            s3_key = None
        
    # Handle all invalid thumbnail_s3_key cases
    if not thumbnail_s3_key or thumbnail_s3_key == 'None' or thumbnail_s3_key == '' or thumbnail_s3_key is None:
        thumbnail_s3_key = None
    # Handle the case where thumbnail_s3_key is a string representation of a tuple
    elif isinstance(thumbnail_s3_key, str) and thumbnail_s3_key.startswith('(') and thumbnail_s3_key.endswith(')') and ',' in thumbnail_s3_key:
        logging.warning(f"Found string representation of a tuple in thumbnail_s3_key: {thumbnail_s3_key}")
        try:
            # Extract the second key from the tuple representation (it should be the thumbnail)
            parts = thumbnail_s3_key[1:-1].split(',')
            if len(parts) > 1 and parts[1].strip():
                thumbnail_s3_key = parts[1].strip('"\'')
                logging.info(f"Extracted thumbnail_s3_key from string tuple: {thumbnail_s3_key}")
            else:
                logging.error(f"Failed to extract valid thumbnail key from string tuple: {thumbnail_s3_key}")
                thumbnail_s3_key = None
        except Exception as e:
            logging.error(f"Error parsing string tuple for thumbnail_s3_key: {str(e)}")
            thumbnail_s3_key = None
    
    # Add enhanced debug logging
    logging.info(f"Getting image URL for receipt {receipt_id}: s3_key='{s3_key}', thumbnail_s3_key='{thumbnail_s3_key}', image_path='{image_path}'")
    
    # Approach 1: Use thumbnail URL if preferred and available
    if prefer_thumbnail and thumbnail_s3_key and hasattr(receipt, 'thumbnail_url'):
        try:
            logging.info(f"Trying to get thumbnail URL for receipt {receipt_id} with key '{thumbnail_s3_key}'")
            thumbnail_url = receipt.thumbnail_url
            if thumbnail_url and thumbnail_url != '' and thumbnail_url != 'None':
                logging.info(f"Successfully generated thumbnail URL for receipt {receipt_id}")
                return thumbnail_url
            else:
                logging.warning(f"Generated empty thumbnail URL for receipt {receipt_id} with key '{thumbnail_s3_key}'")
        except Exception as thumb_error:
            logging.error(f"Error getting thumbnail URL for receipt {receipt_id}: {str(thumb_error)}")
            import traceback
            logging.error(traceback.format_exc())
    
    # Approach 2: Use S3 URL if available and preferred
    if prefer_s3 and s3_key and hasattr(receipt, 's3_url'):
        try:
            logging.info(f"Trying to get S3 URL for receipt {receipt_id} with key '{s3_key}'")
            s3_url = receipt.s3_url
            if s3_url and s3_url != '' and s3_url != 'None':
                logging.info(f"Successfully generated S3 URL for receipt {receipt_id}: {s3_url}")
                return s3_url
            else:
                logging.warning(f"Generated empty S3 URL for receipt {receipt_id} with key '{s3_key}'")
        except Exception as s3_error:
            logging.error(f"Error getting S3 URL for receipt {receipt_id}: {str(s3_error)}")
            import traceback
            logging.error(traceback.format_exc())
    else:
        if not prefer_s3:
            logging.info(f"Skipping S3 URL for receipt {receipt_id} because prefer_s3=False")
        elif not s3_key:
            logging.info(f"No S3 key available for receipt {receipt_id}")
        elif not hasattr(receipt, 's3_url'):
            logging.warning(f"Receipt {receipt_id} has no s3_url property")
    
    # Approach 3: Use local image path if available
    if image_path:
        try:
            # Handle both formats: with or without 'static/' prefix
            clean_path = str(image_path).replace('static/', '')
            # Ensure path doesn't start with slash to avoid double slashes
            clean_path = clean_path.lstrip('/')
            
            # Validate that the path actually points to a file
            import os
            full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', clean_path)
            
            logging.info(f"Checking local image path for receipt {receipt_id}: {full_path}")
            
            if os.path.exists(full_path) and os.path.isfile(full_path):
                # Create the full static URL
                image_url = f"/static/{clean_path}"
                logging.info(f"Using local image path for receipt {receipt_id}: {image_url}")
                return image_url
            else:
                logging.warning(f"File not found for receipt {receipt_id}: {full_path}")
        except Exception as e:
            logging.error(f"Error formatting image path '{image_path}' for receipt {receipt_id}: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())
    
    # Approach 4: Fall back to S3 URL if not already tried and available
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
    
def get_receipt_thumbnail_url(receipt):
    """
    Helper function to specifically get the thumbnail URL for a receipt.
    If no thumbnail is available, falls back to a placeholder.
    
    Args:
        receipt: Receipt model object
        
    Returns:
        str: URL for the receipt thumbnail
    """
    # Use the main function with thumbnail preference
    return get_receipt_image_url(receipt, prefer_s3=True, prefer_thumbnail=True)

def can_view_organization_receipts(organization_id, user_id=None):
    """
    Check if a user can view all receipts in an organization based on their role.
    
    Args:
        organization_id: ID of the organization
        user_id: ID of the user (defaults to current_user.id)
        
    Returns:
        Boolean indicating if the user can view all organization receipts
    """
    from flask_login import current_user
    from models import OrganizationUser
    
    if user_id is None:
        if not hasattr(current_user, 'is_authenticated') or not current_user.is_authenticated:
            return False
        user_id = current_user.id
    
    org_user = OrganizationUser.query.filter_by(
        user_id=user_id,
        organization_id=organization_id
    ).first()
    
    if not org_user:
        return False
        
    # Owner and admin roles can view all receipts
    return org_user.role in ['owner', 'admin']
