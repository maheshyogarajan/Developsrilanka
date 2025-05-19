"""
Helper module for user and organization management.
Provides utilities for creating and managing Personal Finances organizations.
"""

import logging
from datetime import datetime

from app import db
from models import Organization, OrganizationUser, UserRole

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_personal_finances_organization(user, commit=True):
    """
    Create a Personal Finances organization for a user.
    
    Args:
        user: The user to create the organization for
        commit: Whether to commit the transaction (default: True)
        
    Returns:
        The created Organization object, or existing one if already present
    """
    # Check if user already has a Personal Finances organization
    existing_org = db.session.query(Organization).join(
        OrganizationUser,
        OrganizationUser.organization_id == Organization.id
    ).filter(
        OrganizationUser.user_id == user.id,
        db.func.lower(Organization.name) == 'personal finances'
    ).first()
    
    if existing_org:
        logger.info(f"User {user.id} already has Personal Finances organization {existing_org.id}")
        return existing_org
        
    try:
        # Create Organization with all required fields
        org = Organization()
        org.name = "Personal Finances"
        org.email = user.email
        org.primary_color = "#4a6da7"
        org.secondary_color = "#f5f8ff"
        org.created_at = datetime.utcnow()
        org.updated_at = datetime.utcnow()
        org.email_footer_text = "Thank you for your business! This is an automated message from my Personal Finances management system."
        
        # Set additional fields if they exist in the model
        try:
            if hasattr(Organization, 'type'):
                setattr(org, 'type', 'personal')
            if hasattr(Organization, 'country_code'):
                setattr(org, 'country_code', 'LK')
            if hasattr(Organization, 'currency_code'):
                setattr(org, 'currency_code', 'LKR')
            if hasattr(Organization, 'subscription_type'):
                setattr(org, 'subscription_type', "free")
            if hasattr(Organization, 'invoice_counter'):
                setattr(org, 'invoice_counter', 0)
            if hasattr(Organization, 'receipt_counter'):
                setattr(org, 'receipt_counter', 0)
            if hasattr(Organization, 'timezone'):
                setattr(org, 'timezone', 'Asia/Colombo')
            if hasattr(Organization, 'fiscal_year_start'):
                setattr(org, 'fiscal_year_start', '01-01')
            if hasattr(Organization, 'created_by_user_id'):
                setattr(org, 'created_by_user_id', user.id)
        except Exception as e:
            logger.warning(f"Could not set optional fields: {str(e)}")
        
        db.session.add(org)
        db.session.flush()  # Get ID without committing
        
        # Create owner relationship
        org_user = OrganizationUser()
        org_user.user_id = user.id
        org_user.organization_id = org.id
        org_user.role = UserRole.OWNER.value
        org_user.is_default = True
        org_user.created_at = datetime.utcnow()
        org_user.updated_at = datetime.utcnow()
        
        db.session.add(org_user)
        
        # Only commit if requested - this allows the caller to control the transaction
        if commit:
            db.session.commit()
            
            # Update OnboardingProgress if it exists
            try:
                from onboarding_models import OnboardingProgress
                progress = OnboardingProgress.query.filter_by(user_id=user.id).first()
                if progress:
                    progress.organization_created = True
                    db.session.commit()
            except ImportError:
                # OnboardingProgress might not be available
                pass
                
        logger.info(f"Created Personal Finances organization {org.id} for user {user.id}")
        return org
    except Exception as e:
        # Only rollback if we're managing our own transaction
        if commit:
            db.session.rollback()
        logger.error(f"Error creating Personal Finances organization: {str(e)}")
        # Re-raise the exception to let the caller handle it
        raise