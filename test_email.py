"""
Simple script to test sending emails via Flask-Mail.
"""
import logging
import os
import sys
from flask import Flask
from flask_mail import Mail, Message

# Configure logging
logging.basicConfig(level=logging.INFO)

# Create a simple Flask app
app = Flask(__name__)

# Configure Flask-Mail with Gmail settings
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('GMAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('GMAIL_APP_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('GMAIL_USERNAME')

# Initialize Flask-Mail
mail = Mail(app)

def test_email():
    """Test sending a simple email with Flask-Mail."""
    
    # Log mail configuration
    logging.info(f"Mail server: {app.config['MAIL_SERVER']}")
    logging.info(f"Mail port: {app.config['MAIL_PORT']}")
    logging.info(f"Mail username configured: {bool(app.config['MAIL_USERNAME'])}")
    logging.info(f"Mail password configured: {bool(app.config['MAIL_PASSWORD'])}")
    
    try:
        # Create a test message
        msg = Message(
            subject="Test Email from FIESTA",
            recipients=["test@example.com"],  # Replace with your test email
            html="""
            <html>
            <body>
                <h1>Test Email</h1>
                <p>This is a test email from FIESTA.</p>
                <p>If you're seeing this, email sending is working!</p>
            </body>
            </html>
            """
        )
        
        # Send the email
        logging.info("Attempting to send test email...")
        mail.send(msg)
        logging.info("✅ Email sent successfully!")
        return True
        
    except Exception as e:
        logging.error(f"❌ Failed to send email: {str(e)}")
        return False

if __name__ == "__main__":
    with app.app_context():
        success = test_email()
        sys.exit(0 if success else 1)