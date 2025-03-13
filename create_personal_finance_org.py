"""
Script to create a comprehensive "Personal Finances" organization.
This follows Option 2 from our implementation plan.
"""
from datetime import datetime
from app import app, db
from models import Organization, User, OrganizationUser, UserRole
from flask_login import current_user
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_personal_finances_organization():
    """
    Create a comprehensive Personal Finances organization with complete details.
    
    Returns:
        The created organization object
    """
    with app.app_context():
        try:
            # Get the first user (admin/owner)
            user = User.query.first()
            if not user:
                logger.error("No users found in the database")
                return None
                
            # Check if "Personal Finances" organization already exists
            existing_org = Organization.query.filter_by(name="Personal Finances").first()
            if existing_org:
                logger.info(f"Organization 'Personal Finances' already exists with ID {existing_org.id}")
                return existing_org
                
            # Create new organization
            new_org = Organization(
                name="Personal Finances",
                email=user.email,  # Use user's email
                website=None,
                phone=None,  # Will be updated by user if needed
                address=None,  # Will be updated by user if needed
                tax_registration_number=None,  # Will be updated by user if needed
                primary_color="#4a6da7",  # Default blue
                secondary_color="#f5f8ff",  # Light blue
                email_footer_text="Thank you for your business! This is an automated message from my Personal Finances management system."
            )
            
            db.session.add(new_org)
            db.session.flush()  # Get ID without committing
            
            logger.info(f"Created new organization 'Personal Finances' with ID {new_org.id}")
            
            # Link user to the organization as owner
            org_user = OrganizationUser(
                user_id=user.id,
                organization_id=new_org.id,
                role=UserRole.OWNER.value,
                is_default=True  # Make this the default organization
            )
            
            db.session.add(org_user)
            db.session.commit()
            
            logger.info(f"Added user {user.name} (ID: {user.id}) as OWNER of 'Personal Finances'")
            
            # Update any existing default organization for this user to not be default
            other_defaults = OrganizationUser.query.filter(
                OrganizationUser.user_id == user.id,
                OrganizationUser.organization_id != new_org.id,
                OrganizationUser.is_default == True
            ).all()
            
            if other_defaults:
                for other in other_defaults:
                    other.is_default = False
                    other_org = Organization.query.get(other.organization_id)
                    logger.info(f"Removed default status from organization '{other_org.name}' (ID: {other_org.id})")
                
                db.session.commit()
            
            return new_org
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating Personal Finances organization: {str(e)}")
            return None

if __name__ == "__main__":
    # Create the organization
    org = create_personal_finances_organization()
    
    if org:
        # We need to fetch fresh data from the database
        with app.app_context():
            fresh_org = Organization.query.filter_by(name="Personal Finances").first()
            if fresh_org:
                print(f"\n✅ Successfully created 'Personal Finances' organization (ID: {fresh_org.id})")
                print("Details:")
                print(f"  Name: {fresh_org.name}")
                print(f"  Email: {fresh_org.email}")
                print(f"  Created: {fresh_org.created_at}")
                
                # Verify default organization
                user = User.query.first()
                default_org = None
                org_user = OrganizationUser.query.filter_by(user_id=user.id, is_default=True).first()
                if org_user:
                    default_org = Organization.query.get(org_user.organization_id)
                
                if default_org and default_org.id == fresh_org.id:
                    print(f"\n✅ 'Personal Finances' is now the default organization for {user.name}")
                else:
                    print(f"\n❌ Warning: 'Personal Finances' is NOT the default organization for {user.name}")
            else:
                print("\n❓ Organization was created but can't be retrieved. Please check the database.")
    else:
        print("\n❌ Failed to create 'Personal Finances' organization")