"""
Migration script to create Personal Finances organization for existing users.

This script identifies users who don't have any organizations and creates a
Personal Finances organization for them, ensuring all users have at least
one organization to prevent null foreign key issues.
"""

import os
import sys
import logging
import argparse
from datetime import datetime

from app import app, db
from models import User, Organization, OrganizationUser, UserRole

# Set up logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_personal_finances_org(user_id):
    """
    Create a Personal Finances organization for a specific user.
    
    Args:
        user_id: The ID of the user to create the organization for
        
    Returns:
        Tuple of (success, organization_id)
    """
    user = User.query.get(user_id)
    if not user:
        logger.error(f"User not found: {user_id}")
        return False, None
    
    # Check if user already has a Personal Finances organization
    existing_org = Organization.query.join(
        OrganizationUser
    ).filter(
        OrganizationUser.user_id == user_id,
        db.text("LOWER(organization.name) = 'personal finances'")
    ).first()
    
    if existing_org:
        logger.info(f"User {user_id} already has Personal Finances organization: {existing_org.id}")
        return False, existing_org.id
    
    # Check if user has any default organization
    has_default = OrganizationUser.query.filter_by(
        user_id=user_id,
        is_default=True
    ).first()
    
    try:
        # Create Organization with proper fields
        org = Organization(
            name="Personal Finances",
            email=user.email,
            primary_color="#4a6da7",
            secondary_color="#f5f8ff",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            email_footer_text="Thank you for your business! This is an automated message from my Personal Finances management system."
        )
        
        db.session.add(org)
        db.session.flush()  # Get ID without committing
        
        # Create owner relationship
        org_user = OrganizationUser(
            user_id=user_id,
            organization_id=org.id,
            role=UserRole.OWNER.value,
            is_default=not has_default,  # Only set as default if user has no default org
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.session.add(org_user)
        
        # Update OnboardingProgress if it exists
        try:
            from onboarding_models import OnboardingProgress
            progress = OnboardingProgress.query.filter_by(user_id=user_id).first()
            if progress:
                progress.organization_created = True
        except ImportError:
            logger.warning("OnboardingProgress model not found, skipping progress update")
        
        logger.info(f"Created Personal Finances organization for user {user_id} (Org ID: {org.id})")
        return True, org.id
    except Exception as e:
        logger.error(f"Error creating Personal Finances for user {user_id}: {str(e)}")
        db.session.rollback()
        return False, None

def ensure_all_users_have_personal_finances(dry_run=False, batch_size=100):
    """
    Ensure all users have a Personal Finances organization.
    
    Args:
        dry_run: If True, don't make any actual changes to database
        batch_size: Number of users to process in a single transaction
    
    Returns:
        Tuple of (created_count, existing_count, error_count)
    """
    users = User.query.all()
    created_count = 0
    existing_count = 0
    error_count = 0
    
    if dry_run:
        logger.info("DRY RUN - No changes will be made to database")
        
    logger.info(f"Processing {len(users)} users...")
    
    # Process in batches
    for i in range(0, len(users), batch_size):
        batch = users[i:i+batch_size]
        logger.info(f"Processing batch {i//batch_size + 1} ({len(batch)} users)...")
        
        if not dry_run:
            # Start a transaction for this batch
            for user in batch:
                # Check if user has any organizations
                if not user.organizations:
                    logger.info(f"User {user.id} ({user.email}) has no organizations, creating Personal Finances")
                    
                    if dry_run:
                        logger.info(f"Would create Personal Finances for user {user.id}")
                        created_count += 1
                        continue
                        
                    created, org_id = create_personal_finances_org(user.id)
                    if created:
                        created_count += 1
                    elif org_id:
                        existing_count += 1
                    else:
                        error_count += 1
                else:
                    # User already has organizations
                    existing_count += 1
                    
            # Commit this batch if not dry run
            if not dry_run:
                try:
                    db.session.commit()
                    logger.info(f"Batch {i//batch_size + 1} committed successfully")
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error committing batch {i//batch_size + 1}: {str(e)}")
                    error_count += len(batch)
        else:
            # Dry run - just count the users without organizations
            for user in batch:
                if not user.organizations:
                    logger.info(f"Would create Personal Finances for user {user.id} ({user.email})")
                    created_count += 1
                else:
                    existing_count += 1
    
    logger.info(f"Summary: Created for {created_count}, Already existed for {existing_count}, Errors for {error_count}")
    return created_count, existing_count, error_count

def run_migration(dry_run=False, batch_size=100):
    """
    Run the migration to ensure all users have a Personal Finances organization.
    
    Args:
        dry_run: If True, don't make any actual changes to database
        batch_size: Number of users to process in a single transaction
    """
    with app.app_context():
        created, existing, errors = ensure_all_users_have_personal_finances(dry_run, batch_size)
        
        if dry_run:
            logger.info(f"DRY RUN results: Would create {created} organizations, {existing} already exist")
        else:
            logger.info(f"Migration completed: {created} created, {existing} existing, {errors} errors")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Create Personal Finances organizations for users')
    parser.add_argument('--dry-run', action='store_true', help='Run without making changes to database')
    parser.add_argument('--batch-size', type=int, default=100, help='Number of users to process in a single transaction')
    args = parser.parse_args()
    
    run_migration(dry_run=args.dry_run, batch_size=args.batch_size)