import base64
from io import BytesIO
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
