"""
Test script to verify the scan route safety check redirect.
"""

import logging
from app import app
from flask import session
from flask_login import login_user
from models import User

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FlaskClientTestCase:
    """Test case for testing Flask routes using the test client."""
    
    def setUp(self):
        """Set up the test environment."""
        self.client = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()
        # Don't use CSRF protection for tests
        app.config['WTF_CSRF_ENABLED'] = False
        
    def tearDown(self):
        """Clean up the test environment."""
        self.app_context.pop()
    
    def test_scan_safety_check(self):
        """Test that users without organizations are redirected to Getting Started."""
        logger.info("Testing scan route safety check...")
        
        # Create a test client
        with app.test_client() as client:
            # Set up a user with no organizations in the session
            with client.session_transaction() as sess:
                sess['_user_id'] = '999'  # No such user should exist
                sess['_fresh'] = True
            
            # Try to access the scan route
            response = client.get('/scan', follow_redirects=False)
            
            # Check if redirection happens
            if response.status_code == 302:
                logger.info(f"✅ Redirect occurred as expected: {response.location}")
                
                # Verify redirect is to the getting_started.wizard route
                if 'getting_started' in response.location:
                    logger.info("✅ Redirected to Getting Started wizard")
                else:
                    logger.error(f"❌ Redirected to unexpected location: {response.location}")
            else:
                logger.error(f"❌ No redirect occurred, status code: {response.status_code}")

# Run the test
if __name__ == "__main__":
    test_case = FlaskClientTestCase()
    test_case.setUp()
    try:
        test_case.test_scan_safety_check()
    finally:
        test_case.tearDown()