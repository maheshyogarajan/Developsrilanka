"""
Test the invitation email functionality in our application.
This script creates a request context to simulate the application environment.
"""
import logging
import os
import sys
from flask import Flask, request
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configure logging
logging.basicConfig(level=logging.INFO)

def send_test_invitation():
    """Send a test invitation email using direct SMTP connection."""
    
    # Gmail account credentials
    username = os.environ.get('GMAIL_USERNAME')
    password = os.environ.get('GMAIL_APP_PASSWORD')
    
    # Log configuration
    logging.info(f"Gmail username configured: {bool(username)}")
    logging.info(f"Gmail password configured: {bool(password)}")
    
    if not username or not password:
        logging.error("Gmail credentials are not configured.")
        return False
    
    try:
        # Set up the SMTP server
        logging.info("Connecting to Gmail SMTP server...")
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        
        # Login to the SMTP server
        logging.info("Logging in to Gmail SMTP server...")
        server.login(username, password)
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = username
        msg['To'] = "test@example.com"  # Replace with your test email
        msg['Subject'] = "FIESTA Invitation Test"
        
        # HTML message body 
        html = """
        <html>
        <body style="font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; background-color: #f9f9f9; padding: 15px;">
            <div style="background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; margin-bottom: 20px;">
                <!-- Header with gradient background -->
                <div style="background: linear-gradient(135deg, #004b87 0%, #00285a 100%); padding: 25px; text-align: center;">
                    <h1 style="color: white;">FIESTA</h1>
                    <p style="color: rgba(255,255,255,0.9); margin: 5px 0 0; font-size: 16px;">Empowering financial intelligence</p>
                </div>
                
                <!-- Main content -->
                <div style="padding: 30px;">
                    <h2 style="color: #004b87; margin-top: 0; margin-bottom: 20px; font-size: 22px;">Invitation Test Successful!</h2>
                    <p style="margin-bottom: 20px;">This test confirms that your email invitation system is working correctly!</p>
                    
                    <div style="background-color: #e6f7ff; padding: 15px; border-radius: 6px; border-left: 4px solid #004b87; margin: 25px 0;">
                        <p style="font-style: italic; margin: 0 0 10px;">"This is a test personal message that would appear in a real invitation."</p>
                        <p style="text-align: right; margin-bottom: 0; color: #4a5568;">- Test User</p>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="#" style="display: inline-block; background-color: #ff9e1b; color: white; 
                        padding: 12px 28px; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 16px;
                        transition: background-color 0.2s; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                            Test Button
                        </a>
                    </div>
                </div>
            </div>
            
            <!-- Footer -->
            <div style="text-align: center; color: #718096; font-size: 0.9em; margin-top: 20px;">
                <p>This is a test email. No action is required.</p>
                <p>&copy; 2025 FIESTA. All rights reserved.</p>
            </div>
        </body>
        </html>
        """
        
        # Attach HTML content
        msg.attach(MIMEText(html, 'html'))
        
        # Send the message
        logging.info("Sending test invitation email...")
        server.send_message(msg)
        server.quit()
        
        logging.info("✅ Test invitation email sent successfully!")
        return True
        
    except Exception as e:
        logging.error(f"❌ Failed to send test invitation email: {str(e)}")
        return False

if __name__ == "__main__":
    success = send_test_invitation()
    sys.exit(0 if success else 1)