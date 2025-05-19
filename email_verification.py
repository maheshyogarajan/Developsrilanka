"""
Email verification utilities for user account verification.
"""
import logging
import secrets
from datetime import datetime

from flask import url_for, render_template
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from app import app, db

logger = logging.getLogger(__name__)

def generate_verification_token(email):
    """
    Generate a secure, time-limited verification token.
    
    Args:
        email: User's email address
        
    Returns:
        Tuple of (token, salt) where salt is needed for verification
    """
    # Create a unique salt for each token generation to increase security
    unique_salt = f"email-verification-{secrets.token_hex(8)}"
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return serializer.dumps(email, salt=unique_salt), unique_salt

def verify_token(token, salt, max_age=86400):
    """
    Verify an email verification token.
    
    Args:
        token: The token to verify
        salt: The salt used when generating the token
        max_age: Maximum age in seconds (default: 24 hours)
        
    Returns:
        The email if valid, None otherwise
        
    Raises:
        SignatureExpired: If the token is expired
        BadSignature: If the token is invalid
    """
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return serializer.loads(token, salt=salt, max_age=max_age)

def send_verification_email(user):
    """
    Send verification email to a user.
    
    Args:
        user: User object with email address
        
    Returns:
        Boolean indicating success or failure
    """
    # Import here to avoid circular imports
    from sendgrid_logger import log_email_attempt, log_email_success, log_email_error
    
    # Generate token with unique salt
    token, salt = generate_verification_token(user.email)
    
    # Store token and salt in user record
    user.email_verification_token = token
    user.email_verification_salt = salt
    user.email_verification_sent_at = datetime.utcnow()
    
    # Don't commit here - let the caller handle the transaction
    
    # Check if the template exists
    try:
        verify_url = url_for('verify_email', token=token, _external=True)
        email_html = render_template('emails/verify_email.html', 
                                   verify_url=verify_url, 
                                   user=user,
                                   expiry_hours=24)
    except Exception as e:
        logger.error(f"Email template error: {str(e)}")
        return False
    
    # Log and send
    try:
        # Use the existing email sending function
        from app import send_email
        
        log_email_attempt(user.email, 'email_verification')
        
        success = send_email(
            to_email=user.email,
            from_email='noreply@developsrilanka.com',
            subject='Verify Your Email - Receipt Scanner',
            html_content=email_html
        )
        
        if success:
            log_email_success(user.email, 'email_verification')
            return True
        else:
            log_email_error(user.email, 'email_verification', 'Failed to send email')
            return False
            
    except Exception as e:
        log_email_error(user.email, 'email_verification', str(e))
        logger.error(f"Email verification error: {str(e)}")
        return False