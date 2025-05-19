"""
Email verification utilities for user account verification.
"""
import logging
import secrets
import os
from datetime import datetime

from flask import url_for, render_template, current_app
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

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
    Send verification email to a user using SendGrid.
    
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
    
    # Import SendGrid logger for detailed logging
    from sendgrid_logger import log_api_request
    
    # Log and send
    try:
        sender_email = "info@developsrilanka.com"
        sender_name = "Team Developsrilanka.com"
        subject = "Verify Your Email - Developsrilanka.com"
        
        # Log email sending details
        logger.info(f"Preparing to send verification email with SendGrid:")
        logger.info(f"From: {sender_name} <{sender_email}>")
        logger.info(f"To: {user.email}")
        logger.info(f"Subject: {subject}")
        
        # Use detailed SendGrid logger to log the API request
        log_api_request(
            recipient_email=user.email, 
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject
        )
        
        # Get the SendGrid API key
        sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
        if not sendgrid_api_key:
            logger.error("SendGrid API key not found in environment")
            return False
        
        # Create SendGrid mail message
        message = Mail(
            from_email=Email(sender_email),
            to_emails=To(user.email),
            subject=subject,
            html_content=Content("text/html", email_html)
        )
        
        # Set the friendly display name separately for better compatibility
        message.from_email.name = sender_name
        
        # Send the email using SendGrid
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(message)
        
        # Check if the email was sent successfully
        status_code = response.status_code
        if status_code >= 200 and status_code < 300:
            logger.info(f"Verification email sent to {user.email}")
            return True
        else:
            logger.error(f"Failed to send verification email: {status_code}")
            # Handle response body safely
            response_body = getattr(response, 'body', None)
            if response_body:
                logger.error(f"Response body: {response_body}")
            return False
            
    except Exception as e:
        logger.error(f"Email verification error: {str(e)}")
        return False