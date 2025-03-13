"""
Test script to verify the email invitation functionality.
This script logs in as a test user and sends an invitation email.
"""
import os
import sys
import logging
import requests
from flask import Flask, current_app
from app import app, db, send_invitation_email
from models import User

logging.basicConfig(level=logging.INFO)

def test_send_invitation():
    """
    Test sending an invitation email directly using the send_invitation_email function.
    This bypasses the need for authentication through the web interface.
    """
    with app.app_context():
        logging.info("Starting email invitation test")
        
        # Get or create a test user
        test_user = User.query.filter_by(email='test@devsrilanka.com').first()
        
        if not test_user:
            logging.info("Creating test user")
            test_user = User(
                email='test@devsrilanka.com',
                name='Test User',
                role='user'
            )
            db.session.add(test_user)
            db.session.commit()
            logging.info(f"Created test user with ID: {test_user.id}")
        else:
            logging.info(f"Using existing test user with ID: {test_user.id}")
        
        # Test recipient
        test_recipient = "test_recipient@example.com"
        
        # Test message
        test_message = "This is a test invitation message. Please join our platform!"
        
        # Send the invitation
        logging.info(f"Attempting to send invitation to: {test_recipient}")
        
        # Check mail configuration before sending
        logging.info(f"Mail username configured: {bool(app.config.get('MAIL_USERNAME'))}")
        logging.info(f"Mail password configured: {bool(app.config.get('MAIL_PASSWORD'))}")
        
        # Attempt to send the invitation email
        result = send_invitation_email(test_recipient, test_user, test_message)
        
        if result:
            logging.info("✅ Email invitation sent successfully!")
        else:
            logging.error("❌ Failed to send email invitation")
            
        return result

if __name__ == "__main__":
    success = test_send_invitation()
    sys.exit(0 if success else 1)