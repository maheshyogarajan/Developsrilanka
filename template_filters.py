"""
Custom template filters for the Flask application.
"""

from app import app

@app.template_filter('currency')
def currency_filter(value, currency_symbol="Rs "):
    """
    Format a number as currency.
    
    Args:
        value: The value to format
        currency_symbol: Currency symbol to use (default: Rs for Sri Lankan Rupee)
    
    Returns:
        Formatted currency string
    """
    if value is None:
        return f"{currency_symbol}0.00"
    
    try:
        # Format value with thousand separator and 2 decimal places
        formatted_value = f"{float(value):,.2f}"
        return f"{currency_symbol}{formatted_value}"
    except (ValueError, TypeError):
        return f"{currency_symbol}0.00"

@app.template_filter('percent')
def percent_filter(value, decimals=1):
    """
    Format a number as percentage.
    
    Args:
        value: The value to format (e.g., 0.45 for 45%)
        decimals: Number of decimal places
    
    Returns:
        Formatted percentage string
    """
    if value is None:
        return "0%"
    
    try:
        # Format value as percentage with specified decimal places
        formatted_value = f"{float(value):.{decimals}f}%"
        return formatted_value
    except (ValueError, TypeError):
        return "0%"

@app.template_filter('datetime')
def datetime_filter(value, format="%Y-%m-%d %H:%M"):
    """
    Format a datetime object.
    
    Args:
        value: The datetime to format
        format: The format string to use
    
    Returns:
        Formatted datetime string
    """
    if value is None:
        return ""
    
    try:
        return value.strftime(format)
    except (ValueError, TypeError, AttributeError):
        return ""

@app.template_filter('truncate_text')
def truncate_text_filter(value, length=50):
    """
    Truncate text to a specific length.
    
    Args:
        value: The text to truncate
        length: Maximum length before truncation
    
    Returns:
        Truncated text with ellipsis if needed
    """
    if not value:
        return ""
    
    try:
        if len(value) > length:
            return value[:length] + "..."
        return value
    except (TypeError, AttributeError):
        return ""