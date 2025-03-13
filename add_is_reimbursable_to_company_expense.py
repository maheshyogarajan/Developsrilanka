"""
Migration script to add the is_reimbursable column to the company_expense table.
This will allow tracking whether expenses should be reimbursed to users.
"""
from app import db
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
import logging

def add_is_reimbursable_column():
    """
    Add the is_reimbursable column to the company_expense table and set all existing expenses to reimbursable by default.
    """
    try:
        # Create a connection to execute raw SQL
        conn = db.engine.connect()
        
        # Check if the column already exists
        check_query = """
        SELECT EXISTS (
            SELECT FROM information_schema.columns 
            WHERE table_name = 'company_expense' AND column_name = 'is_reimbursable'
        );
        """
        result = conn.execute(sa.text(check_query)).scalar()
        
        if result:
            logging.info("Column 'is_reimbursable' already exists in company_expense table.")
            return
        
        logging.info("Adding 'is_reimbursable' column to company_expense table.")
        
        # Add the column
        add_column_query = """
        ALTER TABLE company_expense
        ADD COLUMN is_reimbursable BOOLEAN NOT NULL DEFAULT True;
        """
        conn.execute(sa.text(add_column_query))
        
        # Commit the transaction
        db.session.commit()
        
        logging.info("Added 'is_reimbursable' column to company_expense table successfully.")
        
    except SQLAlchemyError as e:
        db.session.rollback()
        logging.error(f"Error adding 'is_reimbursable' column: {str(e)}")
        raise
    except Exception as e:
        db.session.rollback()
        logging.error(f"Unexpected error: {str(e)}")
        raise

if __name__ == "__main__":
    from app import app
    with app.app_context():
        add_is_reimbursable_column()
        print("Migration completed successfully.")