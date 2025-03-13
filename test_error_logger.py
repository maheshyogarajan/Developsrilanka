"""
Test script to verify that the error logging system is working correctly.
This tests both backend and frontend error logging components.
"""

import requests
import json
import logging
from app import app
from error_logger import log_error, log_receipt_history_error, log_api_error, ErrorTypes

logging.basicConfig(level=logging.DEBUG)

# Test backend error logger
def test_backend_logger():
    print("\n=== Testing Backend Error Logger ===")
    
    # Test general error logging
    error_id = log_error(
        error="Test error message",
        error_type=ErrorTypes.UNKNOWN,
        component="test_script",
        additional_info={"test_run": True, "test_type": "backend_logger"}
    )
    print(f"✅ General error logged with ID: {error_id}")
    
    # Test receipt history error logging
    error_id = log_receipt_history_error(
        error="Test receipt history error",
        sub_component="test",
        additional_info={"test_run": True}
    )
    print(f"✅ Receipt history error logged with ID: {error_id}")
    
    # Test API error logging
    error_id = log_api_error(
        error="Test API error",
        api_name="test_api",
        endpoint="/test",
        method="GET",
        additional_info={"test_run": True}
    )
    print(f"✅ API error logged with ID: {error_id}")
    
    return True

# Test frontend-to-backend error logging bridge
def test_client_error_api():
    print("\n=== Testing Client Error API ===")
    
    # Create a test client
    client = app.test_client()
    
    # Prepare test error data
    test_error_data = {
        "error_id": "test-client-error-id-123",
        "timestamp": "2025-03-13T12:00:00.000Z",
        "error_type": ErrorTypes.CLIENT,
        "component": "test.client",
        "error_message": "Test client-side error",
        "additional_info": {
            "test_run": True,
            "test_type": "client_api"
        },
        "user_agent": "Test User Agent",
        "url": "http://localhost:5000/test"
    }
    
    # Send test error to API
    response = client.post(
        '/api/log_client_error',
        data=json.dumps(test_error_data),
        content_type='application/json'
    )
    
    # Check response
    print(f"Response status: {response.status_code}")
    response_data = json.loads(response.data)
    print(f"Response data: {response_data}")
    
    if response.status_code == 200 and response_data.get('success'):
        print(f"✅ Client error logged with ID: {response_data.get('error_id')}")
        return True
    else:
        print("❌ Failed to log client error")
        return False

# Run tests
if __name__ == "__main__":
    print("Starting error logger tests...")
    
    backend_success = test_backend_logger()
    client_api_success = test_client_error_api()
    
    if backend_success and client_api_success:
        print("\n✅ All error logger tests passed successfully!")
    else:
        print("\n❌ Some error logger tests failed.")