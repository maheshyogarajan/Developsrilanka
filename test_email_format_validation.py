"""
Test script to verify email format validation.

This script tests the email address format validation functions to ensure
they correctly identify valid and invalid email addresses.
"""

import re

def test_email_format():
    """Test email format validation."""
    print("Testing email format validation...")
    
    # Email validation pattern
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    # Valid email addresses
    valid_emails = [
        "user@example.com",
        "user.name@example.com",
        "user-name@example.com",
        "user_name@example.com",
        "user+name@example.com",
        "user@example.co.uk",
        "user@subdomain.example.com",
    ]
    
    # Invalid email addresses
    invalid_emails = [
        "user@example.com ",  # Has trailing space
        " user@example.com",  # Has leading space
        "user@.com",          # Missing domain
        "user@example.",      # TLD missing
        "user@.example.com",  # Domain part starts with dot
        "user@example.c",     # TLD too short
        "user@example#com",   # Contains invalid character
        "user@exam ple.com",  # Contains space
        "user@example,com",   # Contains comma
        "user@example..com",  # Double dot
        "user..name@example.com", # Double dot in local part
        "@example.com",       # Missing local part
        "user@",              # Missing domain
        "user",               # No @ sign
    ]
    
    # Test valid emails
    print("\nTesting valid email addresses:")
    for email in valid_emails:
        result = bool(re.match(email_pattern, email))
        print(f"{'✓' if result else '✗'} {email} - {'Valid' if result else 'Invalid'}")
        
    # Test invalid emails
    print("\nTesting invalid email addresses:")
    for email in invalid_emails:
        result = bool(re.match(email_pattern, email))
        print(f"{'✗' if result else '✓'} {email} - {'Valid' if result else 'Invalid'}")

if __name__ == "__main__":
    test_email_format()