"""
Feature flag module to control application features.
This allows toggling features on and off without code changes.
"""
import os
from typing import Dict, Any

# Dictionary of feature flags and their default values
# These can be overridden by environment variables
DEFAULT_FLAGS: Dict[str, Any] = {
    # Enhanced CSRF protection with secure cookies and more POST requirements
    "CSRF_HARDENING_ENABLED": True,
    
    # Enable async processing with Celery (if False, falls back to sync)
    "ENABLE_ASYNC_PROCESSING": True,
    
    # Enable S3 integration for image storage
    "ENABLE_S3_STORAGE": True,
    
    # Enable Google OAuth login
    "ENABLE_GOOGLE_LOGIN": True,
    
    # Enable Facebook OAuth login
    "ENABLE_FACEBOOK_LOGIN": True,
    
    # Feature flags for specific modules or UI elements
    "ENABLE_ANALYTICS_DASHBOARD": True,
    "ENABLE_RECEIPT_CLASSIFICATION": True,
    "ENABLE_EXPENSE_PIPELINE": True,
    "ENABLE_CLIENT_MANAGEMENT": True,
    "ENABLE_TAX_CALCULATOR": True,
    
    # Development and debugging flags
    "DEBUG_MODE": os.environ.get("FLASK_ENV") == "development"
}

def get_feature_flag(flag_name: str) -> Any:
    """
    Get the value of a feature flag.
    
    Args:
        flag_name: Name of the feature flag to check
        
    Returns:
        Value of the feature flag (True/False/other)
    """
    # Check if the flag is set as an environment variable first
    env_flag = os.environ.get(flag_name)
    print(f"DEBUG: Feature flag {flag_name} from environment: {env_flag}")
    
    if env_flag is not None:
        # Convert string environment variables to appropriate types
        if env_flag.lower() in ["true", "1", "yes"]:
            print(f"DEBUG: Feature flag {flag_name} is enabled")
            return True
        elif env_flag.lower() in ["false", "0", "no"]:
            print(f"DEBUG: Feature flag {flag_name} is disabled")
            return False
        # Try to convert to int if it looks like a number
        elif env_flag.isdigit():
            return int(env_flag)
        return env_flag
    
    # Fall back to default values
    default_value = DEFAULT_FLAGS.get(flag_name, False)
    print(f"DEBUG: Feature flag {flag_name} using default value: {default_value}")
    return default_value

def is_feature_enabled(flag_name: str) -> bool:
    """
    Check if a feature flag is enabled.
    
    Args:
        flag_name: Name of the feature flag to check
        
    Returns:
        True if the feature is enabled, False otherwise
    """
    return bool(get_feature_flag(flag_name))