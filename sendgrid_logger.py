"""
SendGrid API Call Logger

This module provides detailed logging for all SendGrid API interactions
to help troubleshoot email delivery issues.
"""

import logging
import os
import json
import traceback
from datetime import datetime

# Configure dedicated logger for SendGrid API calls
logger = logging.getLogger('sendgrid_api')
logger.setLevel(logging.DEBUG)

# Create file handler for logging to a dedicated file
log_dir = 'logs'
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'sendgrid_api.log')
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)

# Create console handler for immediate feedback
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create formatter and attach to handlers
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

def log_api_request(recipient_email, sender_email, sender_name, subject, api_key_length=5):
    """
    Log details about a SendGrid API request before it's sent.
    
    Args:
        recipient_email: Email address of recipient
        sender_email: Email address of sender
        sender_name: Display name of sender
        subject: Email subject
        api_key_length: Length of API key prefix to log (for identification, not security)
    """
    api_key = os.environ.get('SENDGRID_API_KEY', '')
    api_key_prefix = api_key[:api_key_length] + '...' if api_key else 'Not Set'
    
    log_message = f"""
=== SENDGRID API REQUEST ===
TIMESTAMP: {datetime.utcnow().isoformat()}
TO: {recipient_email}
FROM: {sender_name} <{sender_email}>
SUBJECT: {subject}
API KEY PREFIX: {api_key_prefix}
==========================
"""
    logger.info(log_message)
    # Also print to standard output for immediate feedback during debugging
    print(log_message)

def log_api_response(status_code, response_body, response_headers):
    """
    Log details about a SendGrid API response.
    
    Args:
        status_code: HTTP status code from the response
        response_body: Body of the response
        response_headers: Headers from the response
    """
    log_message = f"""
=== SENDGRID API RESPONSE ===
TIMESTAMP: {datetime.utcnow().isoformat()}
STATUS CODE: {status_code}
RESPONSE BODY: {response_body}
RESPONSE HEADERS: {json.dumps(dict(response_headers), indent=2)}
============================
"""
    logger.info(log_message)
    # Also print to standard output for immediate feedback during debugging
    print(log_message)

def log_api_error(error, recipient_email=None, error_type=None, detailed_traceback=True):
    """
    Log details about a SendGrid API error.
    
    Args:
        error: The error object or message
        recipient_email: Email address of intended recipient (if known)
        error_type: Type of error (if known)
        detailed_traceback: Whether to include detailed traceback
    """
    error_message = str(error)
    error_type = error_type or type(error).__name__
    
    log_message = f"""
=== SENDGRID API ERROR ===
TIMESTAMP: {datetime.utcnow().isoformat()}
RECIPIENT: {recipient_email or 'Unknown'}
ERROR TYPE: {error_type}
ERROR MESSAGE: {error_message}
"""
    
    if detailed_traceback:
        log_message += f"""
TRACEBACK: 
{traceback.format_exc()}
"""
    
    log_message += """======================="""
    
    logger.error(log_message)
    # Also print to standard output for immediate feedback during debugging
    print(log_message)

def log_email_content(html_content, text_content=None, max_length=500):
    """
    Log the content of an email (with truncation for very large emails).
    
    Args:
        html_content: HTML content of the email
        text_content: Plain text content of the email (if available)
        max_length: Maximum length to log before truncating
    """
    html_preview = html_content[:max_length] + '...' if len(html_content) > max_length else html_content
    text_preview = None
    if text_content:
        text_preview = text_content[:max_length] + '...' if len(text_content) > max_length else text_content
    
    log_message = f"""
=== SENDGRID EMAIL CONTENT ===
TIMESTAMP: {datetime.utcnow().isoformat()}
HTML CONTENT PREVIEW: 
{html_preview}

"""
    if text_preview:
        log_message += f"""
TEXT CONTENT PREVIEW:
{text_preview}
"""
    
    log_message += """============================"""
    
    logger.debug(log_message)