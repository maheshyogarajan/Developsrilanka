"""
Migration script to implement personal organizations.
This will:
1. Add the new fields to Organization model
2. Create personal organizations for users
3. Link users to their personal organizations
"""
from app import app, db
from models import User, Organization, OrganizationUser, UserRole
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reset_aborted_transactions():
    """Reset any aborted database transactions."""
    logger.info("Resetting any aborted database transactions...")
    try:
        # Get engine from app
        engine = db.engine
        connection = engine.connect()
        
        # Start a transaction
        trans = connection.begin()
        
        # Execute transactions reset
        connection.execute("ROLLBACK")
        
        # Commit
        trans.commit()
        
        # Close connection
        connection.close()
        logger.info("Successfully reset transactions")
    except Exception as e:
        logger.error(f"Error resetting transactions: {e}")
        if 'trans' in locals() and trans:
            trans.rollback()
        if 'connection' in locals() and connection:
            connection.close()

def migrate_to_personal_organizations():
    """
    Migrate to personal organizations:
    1. Create a personal organization for each user
    2. Set up organization-user relationships
    3. Mark these organizations as personal
    """
    logger.info("Starting migration to personal organizations...")
    try:
        # Get all users
        users = User.query.all()
        logger.info(f"Found {len(users)} users to migrate")
        
        count = 0
        for user in users:
            # Skip users who already have a personal organization
            if user.personal_organization_id is not None:
                logger.info(f"User {user.id} already has a personal organization, skipping")
                continue
                
            # Create a personal organization for the user
            org_name = f"{user.name}'s Personal Finances" if user.name else f"Personal Finances"
            personal_org = Organization(
                name=org_name,
                is_personal=True,
                tax_rate_type="personal",
                # Default personal tax rates
                employment_tax_rate=24.0,
                investment_tax_rate=14.0,
                business_income_tax_rate=36.0,
                consulting_tax_rate=15.0
            )
            
            # Add to session
            db.session.add(personal_org)
            db.session.flush()  # Get the ID
            
            # Link user to personal organization
            user.personal_organization_id = personal_org.id
            
            # Create organization user relationship with OWNER role
            org_user = OrganizationUser(
                user_id=user.id,
                organization_id=personal_org.id,
                role=UserRole.OWNER.value,
                is_default=True
            )
            db.session.add(org_user)
            
            count += 1
            if count % 10 == 0:
                logger.info(f"Processed {count} users")
        
        # Commit all changes
        db.session.commit()
        logger.info(f"Successfully created personal organizations for {count} users")
        
    except Exception as e:
        logger.error(f"Error in migration: {e}")
        db.session.rollback()
        raise

def migrate_existing_organizations_tax_settings():
    """
    Update existing organizations:
    1. Mark as business organizations (is_personal=False)
    2. Set tax_rate_type to 'business'
    3. Set default tax rates for business
    """
    logger.info("Updating existing organizations with tax settings...")
    try:
        # Get organizations that don't have is_personal set
        orgs = Organization.query.filter(Organization.is_personal.is_(None)).all()
        logger.info(f"Found {len(orgs)} organizations to update")
        
        for org in orgs:
            # Set business defaults
            org.is_personal = False
            org.tax_rate_type = "business"
            org.corporate_tax_rate = 24.0
            org.dividend_tax_rate = 14.0
            
        # Commit changes
        db.session.commit()
        logger.info(f"Successfully updated {len(orgs)} organizations")
        
    except Exception as e:
        logger.error(f"Error updating organizations: {e}")
        db.session.rollback()
        raise

if __name__ == "__main__":
    with app.app_context():
        logger.info("Starting migration script")
        # First reset any aborted transactions
        reset_aborted_transactions()
        # Migrate to personal organizations
        migrate_to_personal_organizations()
        # Update existing organizations
        migrate_existing_organizations_tax_settings()
        logger.info("Migration completed successfully")