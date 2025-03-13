"""
Migration script - Part 1: Create organizations for all users.
This script is broken into smaller parts to avoid timeout issues.
"""
import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import db, app

def create_organizations_for_users():
    """
    Create organizations for users who don't have one:
    1. Find users without organizations
    2. Create an organization for each user
    3. Link the user to their organization as owner
    """
    try:
        # Find users without organizations
        users_without_org = db.session.execute(text("""
            SELECT u.id, u.email, u.name
            FROM "user" u
            LEFT JOIN organization_user ou ON u.id = ou.user_id
            WHERE ou.id IS NULL
        """)).fetchall()
        
        print(f"Found {len(users_without_org)} users without organizations")
        
        # Process users in smaller batches to avoid timeout
        for i, user in enumerate(users_without_org):
            user_id = user[0]
            user_email = user[1]
            user_name = user[2] or "Default User"
            
            # Create organization name based on user name or email
            org_name = f"{user_name}'s Organization"
            
            # Insert new organization
            result = db.session.execute(text("""
                INSERT INTO organization 
                (name, email, created_at, updated_at, primary_color, secondary_color)
                VALUES (:name, :email, :created_at, :updated_at, '#4a6da7', '#f5f8ff')
                RETURNING id
            """), {
                'name': org_name,
                'email': user_email,
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            })
            
            org_id = result.fetchone()[0]
            
            # Link user to organization as owner and set as default
            db.session.execute(text("""
                INSERT INTO organization_user
                (user_id, organization_id, role, is_default, created_at, updated_at)
                VALUES (:user_id, :org_id, 'owner', true, :created_at, :updated_at)
            """), {
                'user_id': user_id,
                'org_id': org_id,
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            })
            
            print(f"{i+1}/{len(users_without_org)}: Created organization '{org_name}' (ID: {org_id}) for user {user_email} (ID: {user_id})")
            
            # Commit in smaller batches (every 5 users) to avoid transaction timeout
            if (i+1) % 5 == 0:
                db.session.commit()
                print(f"Committed changes for batch {(i+1)//5}")
        
        # Final commit for any remaining changes
        db.session.commit()
        print("All organizations created successfully!")
            
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error during migration: {str(e)}")
        logging.error(f"Error during migration: {str(e)}")
        raise

if __name__ == "__main__":
    with app.app_context():
        create_organizations_for_users()