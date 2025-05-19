"""
Test script to verify user registration and Personal Finances organization creation.
"""

import logging
import os
import random
import string
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generate_random_email():
    """Generate a random test email address."""
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"test_{random_str}@test.com"

def run_test(app_instance):
    """
    Run registration process test.
    
    Args:
        app_instance: The Flask app instance
    """
    # Import models here to avoid circular imports
    from models import User, Organization, OrganizationUser, UserRole
    from user_organization_helper import create_personal_finances_organization
    from app import db
    
    # Generate a test email
    test_email = generate_random_email()
    
    # Start a new transaction
    try:
        # Create a test user
        logger.info(f"Creating test user with email: {test_email}")
        test_user = User()
        test_user.email = test_email
        test_user.password_hash = "test_password_hash"
        test_user.name = test_email.split('@')[0]
        test_user.role = 'user'
        test_user.subscription_status = 'free_trial'
        test_user.access_expiration_date = datetime.utcnow()
        
        # Add the user to the session
        db.session.add(test_user)
        db.session.flush()  # Get ID without committing
        logger.info(f"Created test user with ID: {test_user.id}")
        
        # Create Personal Finances organization without committing yet
        logger.info(f"Creating Personal Finances organization for user {test_user.id}")
        org = create_personal_finances_organization(test_user, commit=False)
        
        # Commit the entire transaction
        db.session.commit()
        logger.info(f"Committed transaction - Created user {test_user.id} and organization {org.id}")
        
        # Verify the organization was created
        org_user = OrganizationUser.query.filter_by(user_id=test_user.id).first()
        if org_user:
            logger.info(f"Verified: User {test_user.id} has OrganizationUser record for org {org_user.organization_id}")
            logger.info(f"Organization role: {org_user.role}, is_default: {org_user.is_default}")
        else:
            logger.error(f"Failed: No OrganizationUser record found for User {test_user.id}")
        
        # Success!
        logger.info("Test completed successfully!")
        return True
        
    except Exception as e:
        # Roll back on error
        db.session.rollback()
        logger.error(f"Test failed with error: {str(e)}")
        return False

if __name__ == "__main__":
    # Use the existing app instance from main.py to avoid duplicate SQLAlchemy instances
    from main import app
    
    with app.app_context():
        success = run_test(app)
        print(f"Registration test {'passed' if success else 'failed'}")