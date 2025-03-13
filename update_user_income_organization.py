"""
Migration script to add organization_id column to the user_income table.
"""
import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import db

def add_organization_id_to_user_income():
    """
    Add organization_id column to the user_income table.
    """
    try:
        # Check if the organization_id column already exists
        result = db.session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'user_income' AND column_name = 'organization_id'
        """))
        
        if not result.first():
            # Add the organization_id column
            db.session.execute(text("""
                ALTER TABLE user_income 
                ADD COLUMN organization_id INTEGER
            """))
            
            print("Added organization_id column to user_income table")
            
            # Add a foreign key constraint
            db.session.execute(text("""
                ALTER TABLE user_income
                ADD CONSTRAINT fk_user_income_organization
                FOREIGN KEY (organization_id)
                REFERENCES organization(id)
            """))
            
            print("Added foreign key constraint to user_income.organization_id")
            
            # Update existing records - set organization_id to user's default organization
            db.session.execute(text("""
                UPDATE user_income ui
                SET organization_id = (
                    SELECT ou.organization_id
                    FROM organization_user ou
                    WHERE ou.user_id = ui.user_id AND ou.is_default = true
                    LIMIT 1
                )
            """))
            
            print("Updated organization_id for existing user_income records")
            
            db.session.commit()
            print("Migration completed successfully!")
            
        else:
            print("The organization_id column already exists in the user_income table")
            
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error during migration: {str(e)}")
        logging.error(f"Error during migration: {str(e)}")
        raise

if __name__ == "__main__":
    from app import app
    with app.app_context():
        add_organization_id_to_user_income()