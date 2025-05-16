import unittest
from app import app, db
from flask import session, url_for
import json

class CSRFTest(unittest.TestCase):
    """Test suite for CSRF protection verification"""
    
    def setUp(self):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = True  # Keep CSRF enabled for testing
        app.config['SERVER_NAME'] = 'localhost'
        self.app = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()
        
    def tearDown(self):
        self.app_context.pop()
    
    def test_get_csrf_token(self):
        """Test that CSRF token is available in the session"""
        with self.app as client:
            client.get('/')
            self.assertIn('csrf_token', session)
            self.assertIsNotNone(session.get('csrf_token'))
    
    def test_post_without_csrf_token(self):
        """Test that POST requests without CSRF token are rejected"""
        response = self.app.post('/login', data={
            'email': 'test@example.com',
            'password': 'password'
        })
        self.assertEqual(response.status_code, 400)  # Bad Request due to missing CSRF token
    
    def test_state_changing_get_routes_blocked(self):
        """Test that state-changing GET routes now require POST with CSRF token"""
        # Test logout route (should be POST now)
        response = self.app.get('/logout')
        self.assertNotEqual(response.status_code, 302)  # Should not redirect immediately
        
        # Test friend invitation acceptance (should require confirmation)
        response = self.app.get('/accept-invitation/test-token')
        self.assertEqual(response.status_code, 200)  # Should show confirmation page
        self.assertIn(b'Accept Invitation', response.data)  # Check for confirmation button
        
        # Test organization invitation cancellation (should require POST)
        response = self.app.get('/organizations/1/cancel-invitation/1')
        self.assertNotEqual(response.status_code, 302)  # Should not redirect immediately
    
    def test_api_routes_csrf_protection(self):
        """Test that API routes are protected against CSRF"""
        # API request without CSRF token should fail
        response = self.app.post('/api/organizations', 
                                 data=json.dumps({'name': 'Test Org'}),
                                 content_type='application/json')
        self.assertIn(response.status_code, [400, 401, 403])  # One of these error codes
    
    def test_valid_csrf_token_accepted(self):
        """Test that valid CSRF tokens are accepted for POST requests"""
        with self.app as client:
            # First get a CSRF token
            response = client.get('/')
            csrf_token = session.get('csrf_token')
            
            # Then use it in a POST request
            response = client.post('/login', data={
                'email': 'test@example.com',
                'password': 'password',
                'csrf_token': csrf_token
            })
            # For this test, we're just checking that it's not rejected due to CSRF
            # It will fail login due to invalid credentials, but shouldn't be a 400
            self.assertNotEqual(response.status_code, 400)
            
if __name__ == '__main__':
    unittest.main()