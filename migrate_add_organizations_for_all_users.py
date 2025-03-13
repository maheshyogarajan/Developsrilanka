"""
Migration script to ensure all users have a default organization.
This will create organizations for users who don't have one and link them as owners.
"""
import logging
import uuid
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import db, app

def migrate_organizations_for_all_users():
    """
    Migrate the database to ensure all users have an organization:
    1. Find users without organizations
    2. Create organizations for each of these users
    3. Link users to their organizations as owners
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
        
        for user in users_without_org:
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
            
            print(f"Created organization '{org_name}' (ID: {org_id}) for user {user_email} (ID: {user_id})")
        
        # Commit all changes
        db.session.commit()
        print("Migration completed successfully!")
        
        # Now update all data records that are missing organization_id
        
        # Update receipts
        db.session.execute(text("""
            UPDATE receipt r
            SET organization_id = (
                SELECT ou.organization_id
                FROM organization_user ou
                WHERE ou.user_id = r.user_id AND ou.is_default = true
                LIMIT 1
            )
            WHERE r.organization_id IS NULL
            AND r.user_id IS NOT NULL
        """))
        print("Updated organization_id for receipts")
        
        # Update user_income
        db.session.execute(text("""
            UPDATE user_income ui
            SET organization_id = (
                SELECT ou.organization_id
                FROM organization_user ou
                WHERE ou.user_id = ui.user_id AND ou.is_default = true
                LIMIT 1
            )
            WHERE ui.organization_id IS NULL
        """))
        print("Updated organization_id for user_income")
        
        # Update bank_accounts
        db.session.execute(text("""
            UPDATE bank_account ba
            SET organization_id = (
                SELECT ou.organization_id
                FROM organization_user ou
                WHERE ou.user_id = ba.user_id AND ou.is_default = true
                LIMIT 1
            )
            WHERE ba.organization_id IS NULL
        """))
        print("Updated organization_id for bank_accounts")
        
        # Update clients
        db.session.execute(text("""
            UPDATE client c
            SET organization_id = (
                SELECT ou.organization_id
                FROM organization_user ou
                WHERE ou.user_id = c.user_id AND ou.is_default = true
                LIMIT 1
            )
            WHERE c.organization_id IS NULL
        """))
        print("Updated organization_id for clients")
        
        # Update invoices
        db.session.execute(text("""
            UPDATE invoice i
            SET organization_id = (
                SELECT ou.organization_id
                FROM organization_user ou
                WHERE ou.user_id = i.user_id AND ou.is_default = true
                LIMIT 1
            )
            WHERE i.organization_id IS NULL
        """))
        print("Updated organization_id for invoices")
        
        # Commit all changes
        db.session.commit()
        print("Data migration completed successfully!")
            
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error during migration: {str(e)}")
        logging.error(f"Error during migration: {str(e)}")
        raise

if __name__ == "__main__":
    with app.app_context():
        migrate_organizations_for_all_users()