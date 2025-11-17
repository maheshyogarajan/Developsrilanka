"""
User-friendly error messages for Gemini API failures.

Maps technical error categories to actionable, non-technical messages
that guide users on what to do when receipt scanning fails.
"""

ERROR_UI_MESSAGES = {
    'RATE_LIMIT': {
        'title': 'Processing Temporarily Limited',
        'message': 'Our AI service is experiencing high demand. Please wait a moment and try again.',
        'icon': 'clock',
        'retry_in': 90,
        'suggestions': [
            'Wait 1-2 minutes before trying again',
            'Your receipt image is saved and ready to retry'
        ],
        'category_description': 'Too many requests in a short time period'
    },
    
    'TIMEOUT': {
        'title': 'Processing Took Too Long',
        'message': 'The image took longer than expected to process. This usually happens with very large or complex images.',
        'icon': 'alert-circle',
        'retry_in': None,
        'suggestions': [
            'Try taking a clearer photo with better lighting',
            'Crop the image to show only the receipt',
            'Keep file size under 5MB',
            'Ensure text is sharp and readable'
        ],
        'category_description': 'Processing exceeded timeout limits'
    },
    
    'CIRCUIT_BREAKER': {
        'title': 'Service Temporarily Paused',
        'message': 'We detected technical issues and temporarily paused receipt scanning to protect the system. Service will automatically resume in a few minutes.',
        'icon': 'shield',
        'retry_in': 300,
        'suggestions': [
            'Your receipt is saved - no need to upload again',
            'The service will be available again in 5 minutes',
            'This is a safety measure to ensure reliable service'
        ],
        'category_description': 'Too many failures triggered circuit breaker protection'
    },
    
    'MODEL_OVERLOAD': {
        'title': 'AI Service Overloaded',
        'message': 'The AI model is currently overloaded. We\'re automatically trying a backup model...',
        'icon': 'cpu',
        'retry_in': 30,
        'auto_retry': True,
        'suggestions': [
            'Please wait while we try a backup AI model',
            'This usually resolves automatically in seconds'
        ],
        'category_description': 'Primary AI model returned 503 service unavailable'
    },
    
    'INVALID_REQUEST': {
        'title': 'Image Cannot Be Processed',
        'message': 'This image format or content cannot be analyzed. Please upload a clear photo of a receipt.',
        'icon': 'x-circle',
        'retry_in': None,
        'suggestions': [
            'Use JPEG or PNG image format',
            'Ensure the image shows a receipt or invoice',
            'Make sure text is readable',
            'Avoid extreme angles or distortion',
            'Check that the file is not corrupted'
        ],
        'category_description': 'Invalid image format or unreadable content'
    },
    
    'AUTH_ERROR': {
        'title': 'Service Configuration Issue',
        'message': 'There\'s a problem with the AI service configuration. Please contact support if this persists.',
        'icon': 'key',
        'retry_in': None,
        'suggestions': [
            'This is likely a temporary service issue',
            'Contact support if you see this repeatedly'
        ],
        'category_description': 'API key or authentication problem'
    },
    
    'PERMISSION_ERROR': {
        'title': 'Service Access Issue',
        'message': 'There\'s an issue accessing the AI service. Please contact support.',
        'icon': 'lock',
        'retry_in': None,
        'suggestions': [
            'This is a configuration or permissions issue',
            'Contact support for assistance'
        ],
        'category_description': 'API permissions or access denied'
    },
    
    'SERVER_ERROR': {
        'title': 'Service Error',
        'message': 'Our AI service encountered an error. Please try again in a few moments.',
        'icon': 'server',
        'retry_in': 60,
        'suggestions': [
            'This is a temporary service issue',
            'Try again in 1-2 minutes',
            'Contact support if this persists'
        ],
        'category_description': 'Internal server error from AI service'
    },
    
    'UNKNOWN': {
        'title': 'Processing Error',
        'message': 'Something unexpected happened while processing your receipt. Please try again.',
        'icon': 'alert-triangle',
        'retry_in': 60,
        'suggestions': [
            'Try uploading the receipt again',
            'If this happens repeatedly, try a different image',
            'Contact support if the problem continues'
        ],
        'category_description': 'Unclassified error'
    }
}


def get_error_message(error_category):
    """
    Get user-friendly error message for a given error category.
    
    Args:
        error_category: Error category from GeminiErrorLogger (e.g., 'RATE_LIMIT')
    
    Returns:
        Dictionary with title, message, icon, retry_in, suggestions
    """
    return ERROR_UI_MESSAGES.get(error_category, ERROR_UI_MESSAGES['UNKNOWN'])


def format_error_response(error_category, include_technical_details=False):
    """
    Format error message as JSON response for API endpoints.
    
    Args:
        error_category: Error category from GeminiErrorLogger
        include_technical_details: Whether to include technical category info
    
    Returns:
        Dictionary formatted for JSON response
    """
    error_ui = get_error_message(error_category)
    
    response = {
        'success': False,
        'error': {
            'title': error_ui['title'],
            'message': error_ui['message'],
            'icon': error_ui['icon'],
            'suggestions': error_ui.get('suggestions', []),
            'retry_in': error_ui.get('retry_in'),
            'auto_retry': error_ui.get('auto_retry', False)
        }
    }
    
    if include_technical_details:
        response['error']['category'] = error_category
        response['error']['technical_description'] = error_ui.get('category_description')
    
    return response
