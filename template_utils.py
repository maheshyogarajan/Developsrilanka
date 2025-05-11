from flask import current_app
import logging

def get_csrf_token():
    """
    Get the CSRF token for the current session.
    This is used to pass the token to JavaScript.
    """
    try:
        from flask_wtf.csrf import generate_csrf
        return generate_csrf()
    except ImportError:
        logging.error("Flask-WTF not installed. CSRF protection not available.")
        return ""