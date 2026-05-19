"""
Test script to diagnose SendGrid API issue
"""
import os
import json
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def test_sendgrid_connection():
    """Test SendGrid API connection with minimal email"""
    
    # Get API key
    sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
    
    if not sendgrid_api_key:
        print("ERROR: SendGrid API key not found in environment")
        return
    
    print(f"Using SendGrid API key: {sendgrid_api_key[:5]}...")
    
    # Create test message with the same format used in organization_routes.py
    message = Mail(
        from_email=('noreply@developsrilanka.com', 'Test Organization via FIESTA'),
        to_emails='test@example.com',
        subject='SendGrid Test Email with Organization Format',
        html_content='<p>This is a test email to check SendGrid API with organization format.</p>'
    )
    
    try:
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(message)
        
        # Log response
        print(f"SendGrid Response Status Code: {response.status_code}")
        if response.body:
            print(f"Response Body: {response.body}")
        print(f"Response Headers: {json.dumps(dict(response.headers))}")
        
        return True
    except Exception as e:
        print(f"SendGrid API ERROR: {str(e)}")
        return False

if __name__ == "__main__":
    test_sendgrid_connection()