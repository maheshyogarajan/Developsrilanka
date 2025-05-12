"""
Migration script to fix Personal Finances organizations missing email and footer text.
This addresses issues where organizations created through quick setup are missing fields
that other parts of the application expect to be present.
"""
import logging
import sys
from datetime import datetime
from app import app, db
from models import Organization, OrganizationUser, User
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_personal_finances_organizations():
    """
    Find all Personal Finances organizations with missing email or footer text
    and update them with the owner's email and default footer text.
    """
    with app.app_context():
        try:
            # Find all Personal Finances organizations
            orgs = Organization.query.filter(
                Organization.name == "Personal Finances",
                (Organization.email.is_(None) | Organization.email_footer_text.is_(None))
            ).all()
            
            if not orgs:
                logger.info("No Personal Finances organizations found with missing fields.")
                return 0
                
            logger.info(f"Found {len(orgs)} Personal Finances organizations with missing fields.")
            fixed_count = 0
            
            for org in orgs:
                # Find the owner of this organization
                org_user = OrganizationUser.query.filter_by(
                    organization_id=org.id,
                    role='owner'  # Assuming 'owner' is the role value for owners
                ).first()
                
                if not org_user:
                    logger.warning(f"No owner found for organization {org.id}. Skipping.")
                    continue
                
                # Get the owner's email
                user = User.query.get(org_user.user_id)
                if not user or not user.email:
                    logger.warning(f"Owner {org_user.user_id} has no email for organization {org.id}. Skipping.")
                    continue
                
                # Update the organization with missing fields
                if org.email is None:
                    org.email = user.email
                    logger.info(f"Set email to {user.email} for organization {org.id}")
                
                if org.email_footer_text is None:
                    org.email_footer_text = "Thank you for your business! This is an automated message from my Personal Finances management system."
                    logger.info(f"Set default footer text for organization {org.id}")
                
                # Update the modified timestamp
                if hasattr(org, 'updated_at'):
                    org.updated_at = datetime.utcnow()
                
                fixed_count += 1
                
            # Commit all changes
            db.session.commit()
            logger.info(f"Fixed {fixed_count} Personal Finances organizations.")
            return fixed_count
                
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error fixing Personal Finances organizations: {str(e)}")
            logger.error(f"Stack trace: {sys.exc_info()}")
            return -1

def run_migration():
    """
    Run the migration to fix Personal Finances organizations.
    """
    print("Starting migration to fix Personal Finances organizations...")
    fixed = fix_personal_finances_organizations()
    
    if fixed > 0:
        print(f"✅ Successfully fixed {fixed} Personal Finances organizations.")
    elif fixed == 0:
        print("ℹ️ No Personal Finances organizations needed fixing.")
    else:
        print("❌ Error occurred during migration. Check logs for details.")
        
if __name__ == "__main__":
    run_migration()