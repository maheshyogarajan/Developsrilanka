"""
Event listeners for SQLAlchemy models.
This module contains event listeners that automatically perform actions when database events occur.
"""

import logging
from datetime import datetime
from sqlalchemy import event

from app import db
from models import User, Organization, OrganizationUser, UserRole

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@event.listens_for(User, "after_insert")
def create_personal_finances_organization(mapper, connection, target):
    """Create a Personal Finances organization for newly registered users."""
    try:
        # Create Organization with complete fields matching the quick setup
        org = Organization()
        org.name = "Personal Finances"
        org.email = target.email  # Use the user's email
        org.primary_color = "#4a6da7"  # Default blue from quick setup
        org.secondary_color = "#f5f8ff"  # Light blue from quick setup
        org.created_at = datetime.utcnow()
        org.updated_at = datetime.utcnow()
        org.email_footer_text = "Thank you for your business! This is an automated message from my Personal Finances management system."
        
        db.session.add(org)
        db.session.flush()  # Get ID without committing
        
        # Create owner relationship with default flag
        org_user = OrganizationUser()
        org_user.user_id = target.id
        org_user.organization_id = org.id
        org_user.role = UserRole.OWNER.value
        org_user.is_default = True
        org_user.created_at = datetime.utcnow()
        org_user.updated_at = datetime.utcnow()
        
        db.session.add(org_user)
        
        # Update OnboardingProgress if it exists
        try:
            from onboarding_models import OnboardingProgress
            progress = OnboardingProgress.query.filter_by(user_id=target.id).first()
            if progress:
                progress.organization_created = True
        except ImportError:
            # OnboardingProgress might not be available at this point
            pass
            
        logger.info(f"Created Personal Finances organization for user {target.id}")
    except Exception as e:
        # Log the error but don't stop registration
        logger.error(f"Error creating Personal Finances organization: {str(e)}")
        # Note: We don't rollback here as it would affect the user creation transaction
        # The migration script will handle creating the organization if this fails