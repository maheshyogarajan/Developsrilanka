"""
Registration process logger for improved error tracking.
This module provides specialized logging for user registration events.
"""

import os
import logging
import traceback
from datetime import datetime

# Create a dedicated registration logger with its own handlers
logger = logging.getLogger('registration')
logger.setLevel(logging.DEBUG)

# Ensure logs directory exists
log_dir = 'logs'
os.makedirs(log_dir, exist_ok=True)

# Create file handler for registration logs
registration_log_file = os.path.join(log_dir, 'registration.log')
file_handler = logging.FileHandler(registration_log_file)
file_handler.setLevel(logging.DEBUG)

# Create formatter with detailed information
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
file_handler.setFormatter(formatter)

# Add handler to logger
logger.addHandler(file_handler)

def log_registration_start(email, registration_method='standard'):
    """
    Log the start of a registration attempt.
    
    Args:
        email: User's email address
        registration_method: Method of registration (standard, google, facebook)
    """
    logger.info(f"Registration attempt started: {email} via {registration_method}")
    
def log_registration_success(user_id, email, registration_method='standard'):
    """
    Log a successful registration.
    
    Args:
        user_id: ID of the created user
        email: User's email address
        registration_method: Method of registration (standard, google, facebook)
    """
    logger.info(f"Registration successful: ID={user_id}, Email={email}, Method={registration_method}")
    
def log_registration_error(email, error, registration_method='standard', include_traceback=True):
    """
    Log a registration error with detailed information.
    
    Args:
        email: User's email address
        error: Exception or error message
        registration_method: Method of registration (standard, google, facebook)
        include_traceback: Whether to include full traceback
    """
    error_message = str(error)
    error_type = type(error).__name__ if isinstance(error, Exception) else "Error"
    
    logger.error(f"Registration failed: Email={email}, Method={registration_method}")
    logger.error(f"Error type: {error_type}")
    logger.error(f"Error message: {error_message}")
    
    if include_traceback and isinstance(error, Exception):
        logger.error(f"Traceback: {traceback.format_exc()}")

def log_organization_creation(user_id, email, org_id=None, status="started"):
    """
    Log Personal Finances organization creation status.
    
    Args:
        user_id: User ID
        email: User's email
        org_id: Organization ID (if available)
        status: Current status (started, success, failed)
    """
    if status == "started":
        logger.info(f"Personal Finances organization creation started for user ID={user_id}, Email={email}")
    elif status == "success":
        logger.info(f"Personal Finances organization creation successful: User ID={user_id}, Org ID={org_id}")
    elif status == "failed":
        logger.error(f"Personal Finances organization creation failed for user ID={user_id}, Email={email}")