"""
Test script for SendGrid API logging

This script tests our custom SendGrid API logger without sending actual emails.
"""

import os
from sendgrid_logger import (
    log_api_request, 
    log_api_response, 
    log_api_error, 
    log_email_content
)

def test_sendgrid_logger():
    """Test the SendGrid API logger functions."""
    print("Testing SendGrid API logger...")
    
    # Test logging a request
    print("\nTesting API request logging:")
    log_api_request(
        recipient_email="test@example.com",
        sender_email="noreply@developsrilanka.com",
        sender_name="Test Organization via DevelopSriLanka",
        subject="Test Subject"
    )
    
    # Test logging email content
    print("\nTesting email content logging:")
    sample_html = """
    <html>
    <body>
        <h1>Test Email</h1>
        <p>This is a test email for logging purposes.</p>
        <a href="https://example.com">Test Link</a>
    </body>
    </html>
    """
    log_email_content(html_content=sample_html)
    
    # Test logging a response
    print("\nTesting API response logging:")
    sample_headers = {
        "X-Message-Id": "test-message-id-12345",
        "Content-Type": "application/json",
        "Date": "Wed, 16 Apr 2025 13:30:00 GMT"
    }
    log_api_response(
        status_code=202,
        response_body="",
        response_headers=sample_headers
    )
    
    # Test logging an error
    print("\nTesting API error logging:")
    try:
        raise Exception("Test error message for SendGrid API")
    except Exception as e:
        log_api_error(
            error=e,
            recipient_email="test@example.com",
            error_type="TestException"
        )
    
    print("\nSendGrid API logger test completed!")
    print(f"Log file available at: {os.path.abspath('logs/sendgrid_api.log')}")

if __name__ == "__main__":
    test_sendgrid_logger()