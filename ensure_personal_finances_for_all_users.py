"""
Migration script to ensure all users have a Personal Finances organization.
This script will:
1. Check all existing users
2. Create Personal Finances organizations for users who don't have one
3. Update session handling to use these organizations
"""

from app import app, db
from models import User, Organization, OrganizationUser, UserRole
from sqlalchemy import and_
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_personal_finances_org(user_id):
    """
    Create a Personal Finances organization for the specified user if one doesn't exist.
    
    Args:
        user_id: User ID to create organization for
        
    Returns:
        Tuple of (created_new, organization_id)
    """
    # Check if user already has a Personal Finances organization
    existing_org = db.session.query(Organization).join(
        OrganizationUser, Organization.id == OrganizationUser.organization_id
    ).filter(
        and_(
            OrganizationUser.user_id == user_id,
            Organization.name == "Personal Finances"
        )
    ).first()
    
    if existing_org:
        logger.info(f"User {user_id} already has Personal Finances organization (ID: {existing_org.id})")
        return False, existing_org.id
    
    # Create the organization
    try:
        # Get user for reference
        user = User.query.get(user_id)
        if not user:
            logger.error(f"User {user_id} not found")
            return False, None
            
        # Create new Personal Finances organization
        org = Organization(
            name="Personal Finances",
            type="personal",  # Using lowercase to match existing data
            logo_url=None,
            website=None,
            address=None,
            phone=None,
            email=user.email,  # Use user's email as contact
            tax_registration_number=None,
            vat_registered=False,
            country_code="LK",  # Default to Sri Lanka
            currency_code="LKR",  # Default to Sri Lankan Rupee
            timezone="Asia/Colombo",
            fiscal_year_start="01-01",
            created_by_user_id=user_id
        )
        
        db.session.add(org)
        db.session.flush()  # Get the org ID without committing transaction
        
        # Link user to organization with Owner role
        org_user = OrganizationUser(
            user_id=user_id,
            organization_id=org.id,
            role="owner",  # Using lowercase to match existing data
            is_default=True  # Make this the default organization
        )
        
        db.session.add(org_user)
        
        # Update user's current organization ID if not set
        if hasattr(user, 'current_organization_id') and not user.current_organization_id:
            user.current_organization_id = org.id
            
        logger.info(f"Created Personal Finances organization for user {user_id} (Org ID: {org.id})")
        return True, org.id
    
    except Exception as e:
        logger.error(f"Error creating Personal Finances for user {user_id}: {str(e)}")
        db.session.rollback()
        return False, None

def ensure_all_users_have_personal_finances():
    """
    Ensure all users have a Personal Finances organization.
    
    Returns:
        Tuple of (created_count, existing_count, error_count)
    """
    users = User.query.all()
    created_count = 0
    existing_count = 0
    error_count = 0
    
    for user in users:
        created, org_id = create_personal_finances_org(user.id)
        if created:
            created_count += 1
        elif org_id:
            existing_count += 1
        else:
            error_count += 1
    
    db.session.commit()
    logger.info(f"Summary: Created for {created_count}, Already existed for {existing_count}, Errors for {error_count}")
    return created_count, existing_count, error_count

def run_migration():
    """Run the migration."""
    with app.app_context():
        created, existing, errors = ensure_all_users_have_personal_finances()
        logger.info(f"Migration completed: {created} created, {existing} existing, {errors} errors")

if __name__ == "__main__":
    run_migration()