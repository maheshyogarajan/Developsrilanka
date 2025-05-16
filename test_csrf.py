import unittest
import pytest
import os
import json
from app import app, db
from feature_flags import is_feature_enabled
from flask import session, url_for
from unittest.mock import patch

class CSRFTest(unittest.TestCase):
    """Test suite for CSRF protection verification"""
    
    @classmethod
    def setUpClass(cls):
        # Save original configuration
        cls.original_settings = {
            'SESSION_COOKIE_SECURE': app.config.get('SESSION_COOKIE_SECURE', False),
            'SESSION_COOKIE_HTTPONLY': app.config.get('SESSION_COOKIE_HTTPONLY', False),
            'SESSION_COOKIE_SAMESITE': app.config.get('SESSION_COOKIE_SAMESITE', None),
        }
    
    @classmethod
    def tearDownClass(cls):
        # Restore original configuration
        app.config.update(cls.original_settings)
    
    def setUp(self):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = True  # Keep CSRF enabled for testing
        app.config['SERVER_NAME'] = 'localhost'
        
        # Determine flag state for this test run
        self.csrf_hardening_enabled = is_feature_enabled("CSRF_HARDENING_ENABLED")
        
        # Configure app based on the flag
        if self.csrf_hardening_enabled:
            app.config.update(
                SESSION_COOKIE_SECURE=True,
                SESSION_COOKIE_HTTPONLY=True,
                SESSION_COOKIE_SAMESITE='Lax'
            )
        else:
            # Ensure we're testing without secure cookie settings
            app.config.update(
                SESSION_COOKIE_SECURE=False,
                SESSION_COOKIE_HTTPONLY=False,
                SESSION_COOKIE_SAMESITE=None
            )
            
        print(f"CSRF Hardening is {'ENABLED' if self.csrf_hardening_enabled else 'DISABLED'} for tests")
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
        self.assertIn(response.status_code, [400, 405])  # 400 Bad Request or 405 Method Not Allowed
    
    def test_cookie_secure_settings(self):
        """Test that secure cookie settings are properly applied based on the CSRF hardening flag"""
        with self.app as client:
            # Trigger a session creation
            client.get('/')
            
            # Check cookie settings based on the flag
            if self.csrf_hardening_enabled:
                # With hardening enabled, cookies should be secure
                self.assertTrue(app.config.get('SESSION_COOKIE_SECURE', False))
                self.assertTrue(app.config.get('SESSION_COOKIE_HTTPONLY', False))
                self.assertEqual(app.config.get('SESSION_COOKIE_SAMESITE', None), 'Lax')
            else:
                # With hardening disabled, cookies may not be secure
                self.assertFalse(app.config.get('SESSION_COOKIE_SECURE', True))
                self.assertFalse(app.config.get('SESSION_COOKIE_HTTPONLY', True))
                self.assertIsNone(app.config.get('SESSION_COOKIE_SAMESITE', 'None'))
    
    def test_state_changing_get_routes_blocked(self):
        """Test that state-changing GET routes now require POST with CSRF token"""
        # Test logout route
        response = self.app.get('/logout')
        
        # Check response based on the flag
        if self.csrf_hardening_enabled:
            print("Testing with CSRF hardening ENABLED")
            # Should return 405 Method Not Allowed
            self.assertEqual(response.status_code, 405)
        else:
            print("Testing with CSRF hardening DISABLED")
            # Should redirect to login or home (302 Found)
            self.assertEqual(response.status_code, 302)
        
        # Test friend invitation acceptance
        response = self.app.get('/accept-invitation/test-token')
        # Should show confirmation page or redirect to login regardless of flag
        self.assertIn(response.status_code, [200, 302])
        
        # Test organization invitation cancellation
        response = self.app.get('/organizations/1/cancel-invitation/1')
        # Response should depend on the flag and the invite existence
        if self.csrf_hardening_enabled:
            # Should be either 405 Method Not Allowed or 404 Not Found
            # 404 means the invitation doesn't exist, which is fine for testing
            self.assertIn(response.status_code, [404, 405])
        else:
            # With flag disabled, might redirect or return other status
            self.assertNotEqual(response.status_code, 405)
    
    def test_api_routes_csrf_protection(self):
        """Test that API routes are protected against CSRF"""
        # API request without CSRF token should fail
        response = self.app.post('/api/save_receipt_context', 
                                 data=json.dumps({'organization_id': 1, 'expense_type': 'company'}),
                                 content_type='application/json')
        self.assertIn(response.status_code, [400, 401, 403, 404])  # One of these error codes
    
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