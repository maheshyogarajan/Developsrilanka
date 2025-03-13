"""
Migration script to add organization_id column to the client table.
"""
import logging
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import db, app

def add_organization_id_to_client():
    """
    Add organization_id column to the client table.
    """
    try:
        # Check if the organization_id column already exists
        result = db.session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'client' AND column_name = 'organization_id'
        """))
        
        if not result.first():
            # Add the organization_id column to client table
            db.session.execute(text("""
                ALTER TABLE client 
                ADD COLUMN organization_id INTEGER
            """))
            
            print("Added organization_id column to client table")
            
            # Add a foreign key constraint to client table
            db.session.execute(text("""
                ALTER TABLE client
                ADD CONSTRAINT fk_client_organization
                FOREIGN KEY (organization_id)
                REFERENCES organization(id)
            """))
            
            print("Added foreign key constraint to client.organization_id")
            
            # Update existing records in client table - set organization_id to user's default organization
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
            
            print("Updated organization_id for existing client records")
            
            db.session.commit()
            print("Migration completed successfully!")
        else:
            print("The organization_id column already exists in the client table")
            
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error during migration: {str(e)}")
        logging.error(f"Error during migration: {str(e)}")
        raise

if __name__ == "__main__":
    with app.app_context():
        add_organization_id_to_client()