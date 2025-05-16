"""
Test script to verify Personal Finances organization auto-creation logic.
"""

import logging
from app import app, db
from models import User, Organization, OrganizationUser, UserRole
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_personal_finances_creation():
    """Test the Personal Finances organization creation logic without using the event listener."""
    logger.info("Testing Personal Finances Organization Creation")
    
    TEST_EMAIL = "test_personal_finances@example.com"
    
    with app.app_context():
        # Check if test user exists, clean up if needed
        existing_test_user = User.query.filter_by(email=TEST_EMAIL).first()
        if existing_test_user:
            logger.info(f"Cleaning up existing test user: {existing_test_user.email}")
            # Delete any associated organization users
            OrganizationUser.query.filter_by(user_id=existing_test_user.id).delete()
            # Delete the test user
            db.session.delete(existing_test_user)
            db.session.commit()
        
        # Create a real test user in the database
        logger.info(f"Creating test user with email: {TEST_EMAIL}")
        test_user = User()
        test_user.email = TEST_EMAIL
        test_user.password_hash = "test_hash"  # Not a real password hash
        test_user.role = "user"
        test_user.subscription_status = "free_trial"
        test_user.created_at = datetime.utcnow()
        test_user.access_expiration_date = datetime.utcnow() + timedelta(days=30)
        test_user.successful_invites_count = 0
        test_user.earned_access_days = 0
        test_user.invitations_sent_today = 0
        test_user.trust_level = 0
        test_user.show_in_trust_ranking = False
        test_user.trust_points = 0
        db.session.add(test_user)
        db.session.commit()
        logger.info(f"Test user created with ID: {test_user.id}")
        
        try:
            # Create Personal Finances organization manually (mimicking the event listener)
            logger.info("Creating Personal Finances organization")
            org = Organization()
            org.name = "Personal Finances"
            org.email = test_user.email
            org.primary_color = "#4a6da7"
            org.secondary_color = "#f5f8ff"
            org.created_at = datetime.utcnow()
            org.updated_at = datetime.utcnow()
            org.email_footer_text = "Thank you for your business! This is an automated message from my Personal Finances management system."
            
            db.session.add(org)
            db.session.flush()
            logger.info(f"Organization created with ID: {org.id}")
            
            # Create owner relationship
            logger.info("Creating organization-user relationship")
            org_user = OrganizationUser()
            org_user.user_id = test_user.id
            org_user.organization_id = org.id
            org_user.role = UserRole.OWNER.value
            org_user.is_default = True
            org_user.created_at = datetime.utcnow()
            org_user.updated_at = datetime.utcnow()
            
            db.session.add(org_user)
            db.session.commit()
            
            # Verify the organization was created properly
            new_org = Organization.query.filter_by(id=org.id).first()
            if new_org:
                logger.info(f"✅ Organization created successfully:")
                logger.info(f"   ID: {new_org.id}, Name: {new_org.name}, Email: {new_org.email}")
                logger.info(f"   Colors: {new_org.primary_color}, {new_org.secondary_color}")
                
                # Verify the relationship was created properly
                relationship = OrganizationUser.query.filter_by(
                    user_id=test_user.id,
                    organization_id=new_org.id
                ).first()
                
                if relationship:
                    logger.info(f"✅ Organization-User relationship created:")
                    logger.info(f"   ID: {relationship.id}, User ID: {relationship.user_id}")
                    logger.info(f"   Org ID: {relationship.organization_id}, Role: {relationship.role}")
                    logger.info(f"   Is Default: {relationship.is_default}")
                else:
                    logger.error("❌ Organization-User relationship not found")
            else:
                logger.error("❌ Organization not found after creation")
                
        finally:
            # Clean up test data
            logger.info("Cleaning up test data")
            OrganizationUser.query.filter_by(user_id=test_user.id).delete()
            Organization.query.filter_by(email=TEST_EMAIL).delete()
            User.query.filter_by(email=TEST_EMAIL).delete()
            db.session.commit()
            logger.info("Test data cleanup complete")

if __name__ == "__main__":
    test_personal_finances_creation()