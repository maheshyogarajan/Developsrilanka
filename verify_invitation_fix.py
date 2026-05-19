"""
Test invitation email with the new implementation
"""
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def test_invitation_email():
    """Test the invitation email with our fixed implementation"""
    
    # Get API key
    sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
    
    if not sendgrid_api_key:
        print("ERROR: SendGrid API key not found in environment")
        return
    
    print(f"Using SendGrid API key: {sendgrid_api_key[:5]}...")
    
    # Test data
    organization_name = "Personal Finances"  # Use a real organization name
    sender_name = "FIESTA"
    sender_email = "noreply@developsrilanka.com"
    recipient = "test@example.com"
    
    # Using our fixed implementation exactly as it is in the code
    message = Mail(
        from_email=sender_email,  # Just use plain email address
        to_emails=recipient,
        subject="Invitation to join Personal Finances on FIESTA",
        html_content="<p>This is a test of the organization invitation email.</p>"
    )
    
    # Set the friendly display name separately as in our fixed code
    message.from_email.name = f"{organization_name} via {sender_name}"
    
    try:
        print(f"Sending test invitation email:")
        print(f"From: {organization_name} via {sender_name} <{sender_email}>")
        print(f"To: {recipient}")
        
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(message)
        
        # Print detailed response
        print(f"\nSendGrid Response:")
        print(f"Status Code: {response.status_code}")
        if hasattr(response, 'body') and response.body:
            print(f"Response Body: {response.body}")
        print(f"Response Headers: {dict(response.headers)}")
        
        if response.status_code == 202:
            print("\n✅ SUCCESS: Organization invitation email was sent successfully!")
        else:
            print(f"\n❌ ERROR: Email failed with status code {response.status_code}")
        
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {str(e)}")

if __name__ == "__main__":
    test_invitation_email()