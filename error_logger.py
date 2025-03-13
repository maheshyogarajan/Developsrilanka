"""
Error logger module for centralized error handling and logging.

This module provides a consistent way to log errors across the application
with appropriate contextual information for easier debugging and tracking.
"""
import os
import logging
import traceback
import json
from datetime import datetime
from flask import request, session, current_app

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "DEBUG")
numeric_level = getattr(logging, log_level.upper(), logging.DEBUG)
logging.basicConfig(level=numeric_level)
logger = logging.getLogger(__name__)

# Setup file handler if environment variable is set
log_file = os.environ.get("ERROR_LOG_FILE")
if log_file:
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.ERROR)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

class ErrorTypes:
    """Error type constants for categorization"""
    DATABASE = "database_error"
    AUTHENTICATION = "authentication_error"
    AUTHORIZATION = "authorization_error"
    API = "api_error"
    VALIDATION = "validation_error"
    CLIENT = "client_error"
    SERVER = "server_error"
    FILE_OPERATION = "file_operation_error"
    RECEIPT_PROCESSING = "receipt_processing_error"
    RECEIPT_HISTORY = "receipt_history_error"
    THIRD_PARTY = "third_party_error"
    UNKNOWN = "unknown_error"

def get_request_details():
    """
    Get details about the current request context for error logs.
    
    Returns:
        dict: Request context information
    """
    if not request:
        return {"request_info": "No request context available"}
    
    # Basic request info
    request_details = {
        "url": request.url,
        "method": request.method,
        "endpoint": request.endpoint,
        "user_agent": request.user_agent.string,
        "remote_addr": request.remote_addr,
    }
    
    # Add session info (excluding sensitive data)
    if session:
        safe_session = {}
        for key, value in session.items():
            if key not in ["_csrf_token", "password", "token"]:
                safe_session[key] = value
        request_details["session"] = safe_session
    
    # Add request args
    if request.args:
        request_details["request_args"] = dict(request.args)
    
    # Add request form (excluding sensitive data)
    if request.form:
        safe_form = {}
        for key, value in request.form.items():
            if key not in ["password", "confirm_password", "current_password", "token"]:
                safe_form[key] = value
        request_details["form_data"] = safe_form
    
    # Add user info if authenticated
    if hasattr(request, "user") and request.user:
        request_details["user_id"] = getattr(request.user, "id", None)
        request_details["user_email"] = getattr(request.user, "email", None)
    
    return request_details

def log_error(error, error_type=ErrorTypes.UNKNOWN, 
              component="general", additional_info=None, 
              log_level="ERROR", capture_request=True):
    """
    Log an error with relevant context information.
    
    Args:
        error: Exception object or error string
        error_type: Type of error from ErrorTypes class
        component: Component where the error occurred (e.g., "receipt_history", "database")
        additional_info: Any additional information to log with the error
        log_level: Level to log the error at ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        capture_request: Whether to capture request context information
    
    Returns:
        str: Error ID for reference
    """
    error_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{hash(str(error))}"
    
    # Build error payload
    error_data = {
        "error_id": error_id,
        "timestamp": datetime.now().isoformat(),
        "error_type": error_type,
        "component": component,
        "error_message": str(error),
        "traceback": traceback.format_exc() if isinstance(error, Exception) else None
    }
    
    # Add request details if available and requested
    if capture_request:
        try:
            error_data["request_info"] = get_request_details()
        except Exception as e:
            error_data["request_info_error"] = str(e)
    
    # Add any additional information
    if additional_info:
        error_data["additional_info"] = additional_info
    
    # Format the error message for logging
    log_message = f"ERROR [{error_type}] in {component}: {str(error)} (ID: {error_id})"
    
    # Log at appropriate level
    log_func = getattr(logger, log_level.lower(), logger.error)
    log_func(log_message)
    
    # Also log detailed error data at DEBUG level
    if log_level.upper() != "DEBUG":
        logger.debug(f"Error details for {error_id}: {json.dumps(error_data, default=str)}")
    
    return error_id

def log_receipt_history_error(error, sub_component=None, additional_info=None):
    """
    Log receipt history specific errors.
    
    Args:
        error: Exception object or error string
        sub_component: Specific sub-component within receipt history
        additional_info: Any additional information to log with the error
    
    Returns:
        str: Error ID for reference
    """
    component = f"receipt_history.{sub_component}" if sub_component else "receipt_history"
    
    return log_error(
        error=error,
        error_type=ErrorTypes.RECEIPT_HISTORY,
        component=component,
        additional_info=additional_info
    )

def log_database_error(error, operation=None, model=None, additional_info=None):
    """
    Log database specific errors.
    
    Args:
        error: Exception object or error string
        operation: Database operation being performed (e.g., "query", "insert", "update")
        model: Model/table being operated on
        additional_info: Any additional information to log with the error
    
    Returns:
        str: Error ID for reference
    """
    info = additional_info or {}
    if operation:
        info["operation"] = operation
    if model:
        info["model"] = model
    
    return log_error(
        error=error,
        error_type=ErrorTypes.DATABASE,
        component="database",
        additional_info=info
    )

def log_api_error(error, api_name, endpoint=None, method=None, additional_info=None):
    """
    Log API specific errors.
    
    Args:
        error: Exception object or error string
        api_name: Name of the API (e.g., "Gemini", "internal")
        endpoint: API endpoint being called
        method: HTTP method used
        additional_info: Any additional information to log with the error
    
    Returns:
        str: Error ID for reference
    """
    info = additional_info or {}
    if endpoint:
        info["endpoint"] = endpoint
    if method:
        info["method"] = method
    
    return log_error(
        error=error,
        error_type=ErrorTypes.API,
        component=f"api.{api_name}",
        additional_info=info
    )

def handle_and_log_exception(e, error_type=ErrorTypes.UNKNOWN, component="general", 
                            additional_info=None, return_error_id=False):
    """
    Handle and log an exception with standard formatting.
    
    Args:
        e: Exception object
        error_type: Type of error from ErrorTypes class
        component: Component where the error occurred
        additional_info: Any additional information to log with the error
        return_error_id: Whether to return the error ID
        
    Returns:
        dict: Error response with error_id, error_message, and type
        or str: Just the error ID if return_error_id is True
    """
    error_id = log_error(
        error=e,
        error_type=error_type,
        component=component,
        additional_info=additional_info
    )
    
    if return_error_id:
        return error_id
    
    return {
        "error": True,
        "error_id": error_id,
        "error_message": str(e),
        "error_type": error_type
    }