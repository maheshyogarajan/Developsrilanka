"""
Test script to validate SendGrid API fix for organization invitations
"""
import os
import json
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def test_sendgrid_organization_format():
    """Test SendGrid API with organization formatting using our fix"""
    
    # Get API key
    sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
    
    if not sendgrid_api_key:
        print("ERROR: SendGrid API key not found in environment")
        return
    
    print(f"Using SendGrid API key: {sendgrid_api_key[:5]}...")
    
    # Simulate organization invitation
    sender_email = 'noreply@developsrilanka.com'
    sender_name = 'FIESTA'
    organization_name = 'Test Organization'
    
    # Create message with the FIXED format
    message = Mail(
        from_email=sender_email,  # Just the email address
        to_emails='test@example.com',
        subject='Fixed Organization Invitation Test',
        html_content='<p>This is a test of the fixed organization invitation format.</p>'
    )
    
    # Set the name separately
    message.from_email.name = f"{organization_name} via {sender_name}"
    
    try:
        print(f"Sending test email from: {organization_name} via {sender_name} <{sender_email}>")
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(message)
        
        # Log response
        print(f"SendGrid Response Status Code: {response.status_code}")
        if response.body:
            print(f"Response Body: {response.body}")
        print(f"Response Headers: {json.dumps(dict(response.headers))}")
        
        if response.status_code == 202:
            print("SUCCESS: Email sent with fixed format!")
        else:
            print(f"FAILED with status code: {response.status_code}")
        
        return True
    except Exception as e:
        print(f"SendGrid API ERROR: {str(e)}")
        return False

if __name__ == "__main__":
    test_sendgrid_organization_format()