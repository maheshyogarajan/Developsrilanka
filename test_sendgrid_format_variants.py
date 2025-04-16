"""
Test script to identify SendGrid API email format issues
This script tests various from_email formats to identify compatibility issues
"""
import os
import sys
import json
import traceback
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def test_sendgrid_formats():
    """Test various SendGrid API sender formats to identify compatibility issues"""
    
    # Get API key
    sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
    
    if not sendgrid_api_key:
        print("ERROR: SendGrid API key not found in environment")
        return
        
    print(f"Using SendGrid API key: {sendgrid_api_key[:5]}...")
    
    # Test data
    organization_name = "Test Organization"
    sender_name = "DevelopSriLanka"
    sender_email = "noreply@developsrilanka.com"
    recipient = "test@example.com"
    
    # Test various formats
    test_cases = [
        {
            "name": "Simple email only",
            "from_format": sender_email,
            "has_name_attr": False,
            "description": "Just a plain email address"
        },
        {
            "name": "Tuple format",
            "from_format": (sender_email, f"{organization_name} via {sender_name}"),
            "has_name_attr": False,
            "description": "Tuple with (email, name) format"
        },
        {
            "name": "Email with separate name attribute",
            "from_format": sender_email,
            "has_name_attr": True,
            "description": "Email address with name set via message.from_email.name"
        },
        {
            "name": "Dictionary format",
            "from_format": {"email": sender_email, "name": f"{organization_name} via {sender_name}"},
            "has_name_attr": False,
            "description": "Dictionary with email and name keys"
        }
    ]
    
    # Run each test case
    results = []
    for i, test in enumerate(test_cases):
        print(f"\n[{i+1}] Testing format: {test['name']}")
        print(f"Description: {test['description']}")
        
        # Create message with the test format
        try:
            message = Mail(
                from_email=test["from_format"],
                to_emails=recipient,
                subject=f"SendGrid Format Test {i+1}: {test['name']}",
                html_content=f"<p>Testing SendGrid from_email format: {test['description']}</p>"
            )
            
            # Apply name attribute if specified
            if test["has_name_attr"]:
                message.from_email.name = f"{organization_name} via {sender_name}"
                print(f"Applied name attribute: '{organization_name} via {sender_name}'")
            
            # Print the constructed message details
            print(f"Format being tested: {repr(test['from_format'])}")
            
            # Try to send
            sg = SendGridAPIClient(sendgrid_api_key)
            response = sg.send(message)
            
            # Handle response
            success = response.status_code == 202
            result = {
                "format": test["name"],
                "success": success,
                "status_code": response.status_code,
                "response_headers": dict(response.headers)
            }
            
            print(f"SUCCESS: Status code {response.status_code}")
            results.append(result)
            
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {str(e)}")
            traceback_str = traceback.format_exc()
            print(f"Traceback: {traceback_str}")
            
            results.append({
                "format": test["name"],
                "success": False,
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
    
    # Print summary of results
    print("\n\n=== RESULTS SUMMARY ===")
    for result in results:
        status = "✅ WORKING" if result.get("success") else "❌ FAILED"
        print(f"{status}: {result['format']}")
        if result.get("error_message"):
            print(f"  Error: {result.get('error_type')}: {result.get('error_message')}")
        elif result.get("status_code"):
            print(f"  Status: {result.get('status_code')}")
    
if __name__ == "__main__":
    test_sendgrid_formats()