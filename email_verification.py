"""
Email verification utilities for user account verification.
"""
import logging
import secrets
from datetime import datetime

from flask import url_for, render_template, current_app
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

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
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
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
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return serializer.loads(token, salt=salt, max_age=max_age)

def send_verification_email(user):
    """
    Send verification email to a user.
    
    Args:
        user: User object with email address
        
    Returns:
        Boolean indicating success or failure
    """
    # Generate token with unique salt
    token, salt = generate_verification_token(user.email)
    
    # Store token and salt in user record
    user.email_verification_token = token
    user.email_verification_salt = salt
    user.email_verification_sent_at = datetime.now()
    
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
        # Get mail from current_app
        mail = current_app.extensions.get('mail')
        if not mail:
            logger.error("Mail extension not found in current_app")
            return False
            
        from flask_mail import Message
        
        # Create a Flask-Mail message
        msg = Message(
            subject='Verify Your Email - Developsrilanka.com',
            recipients=[user.email],
            html=email_html,
            sender='noreply@developsrilanka.com'
        )
        
        # Send the email
        mail.send(msg)
        logger.info(f"Verification email sent to {user.email}")
        return True
            
    except Exception as e:
        logger.error(f"Email verification error: {str(e)}")
        return False