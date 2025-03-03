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

def format_currency(amount, currency="$"):
    """
    Format a number as currency.
    
    Args:
        amount: The amount to format
        currency: The currency symbol to use
    
    Returns:
        Formatted currency string
    """
    try:
        num = float(amount)
        return f"{currency}{num:.2f}"
    except (ValueError, TypeError):
        return f"{currency}0.00"
