"""
Test SendGrid API key permissions and status

This script checks if the SendGrid API key is working and has the necessary permissions.
"""

import os
import sys
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import json

def check_sendgrid_key():
    """Check SendGrid API key status."""
    print("=== SendGrid API Key Check ===")
    
    # Get API key from environment
    api_key = os.environ.get('SENDGRID_API_KEY')
    if not api_key:
        print("ERROR: SENDGRID_API_KEY environment variable is not set")
        return False
    
    # Show first few characters for identification
    api_key_prefix = api_key[:5] + '...' if api_key else 'Not Set'
    print(f"API Key Prefix: {api_key_prefix}")
    
    try:
        # Initialize SendGrid client
        sg = SendGridAPIClient(api_key)
        
        # Try to get account information (requires API key with user.read permission)
        try:
            print("\nTrying to get account information...")
            response = sg.client.user.profile.get()
            print(f"Response Status Code: {response.status_code}")
            if response.status_code == 200:
                print("✓ API key has user.read permission")
                print(f"Account username: {json.loads(response.body).get('username')}")
            else:
                print("✗ API key does not have user.read permission")
        except Exception as e:
            print(f"✗ API key does not have user.read permission: {str(e)}")
        
        # Check mail send permission with minimal API request
        try:
            print("\nChecking mail.send permission...")
            # Create a minimal test message
            message = Mail(
                from_email="noreply@developsrilanka.com",
                to_emails="test@example.com", 
                subject="SendGrid API Test",
                plain_text_content="This is just a test to verify API key permissions."
            )
            message.from_email.name = "FIESTA"
            
            # Get request body without actually sending
            request_body = message.get()
            print("✓ Can create mail message")
            
            # Try to send a test email to the validate endpoint to check permission
            # This endpoint validates the request without actually sending an email
            validate_endpoint = "mail/send"
            url_params = {"validate_only": "true"}
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            data = message.get()
            body = json.dumps(data).encode()
            
            response = sg._make_request(
                validate_endpoint,
                request_body=body,
                request_headers=headers,
                method="POST",
                params=url_params
            )
            
            if response.status_code == 200:
                print("✓ API key has mail.send permission")
                print("✓ Email validation successful")
            else:
                print(f"✗ Email validation failed: {response.status_code}")
                print(f"Response body: {response.body}")
                
        except Exception as e:
            print(f"✗ API key does not have mail.send permission: {str(e)}")
            
        # Try to actually send a test message (would actually send an email)
        print("\nWould you like to test sending an actual email? (y/n)")
        choice = input().strip().lower()
        
        if choice == 'y':
            try:
                print("\nTesting actual email sending...")
                test_email = input("Enter a test email address: ").strip()
                
                # Create a test message
                message = Mail(
                    from_email="noreply@developsrilanka.com",
                    to_emails=test_email,
                    subject="SendGrid API Test - " + api_key_prefix,
                    plain_text_content="This is a test email from the SendGrid API key checker script."
                )
                message.from_email.name = "FIESTA"
                
                response = sg.send(message)
                print(f"Response Status Code: {response.status_code}")
                print(f"Response Headers: {response.headers}")
                print(f"Response Body: {response.body}")
                
                if response.status_code in (200, 201, 202):
                    print(f"✓ Successfully sent test email to {test_email}")
                    print(f"✓ Message ID: {response.headers.get('X-Message-Id')}")
                    return True
                else:
                    print(f"✗ Failed to send test email: {response.status_code}")
                    return False
                    
            except Exception as e:
                print(f"✗ Failed to send test email: {str(e)}")
                return False
        else:
            print("Skipping actual email sending test.")
            
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to initialize SendGrid client: {str(e)}")
        return False
        
if __name__ == "__main__":
    check_sendgrid_key()