"""
Migration script to add the client_id column to the company_expense table.
This will allow tracking which client an expense belongs to.
"""
import logging
from app import app, db
from sqlalchemy import text

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def add_client_id_column():
    """
    Add the client_id column to the company_expense table as a nullable foreign key.
    """
    try:
        # Check if the column already exists
        result = db.session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'company_expense' AND column_name = 'client_id';
        """))
        
        if result.first():
            logger.info("client_id column already exists in company_expense table")
            return True
        
        # Add the client_id column to the company_expense table
        db.session.execute(text("""
            ALTER TABLE company_expense
            ADD COLUMN client_id INTEGER;
        """))
        
        # Add a foreign key constraint after the column is created
        db.session.execute(text("""
            ALTER TABLE company_expense
            ADD CONSTRAINT fk_company_expense_client
            FOREIGN KEY (client_id)
            REFERENCES client(id);
        """))
        
        # Commit changes
        db.session.commit()
        logger.info("Successfully added client_id column to company_expense table")
        
        return True
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding client_id column to company_expense table: {str(e)}")
        return False

if __name__ == "__main__":
    # Run the migration within app context
    print("Starting migration to add client_id column to company_expense table...")
    with app.app_context():
        success = add_client_id_column()
        
        if success:
            print("Migration completed successfully")
        else:
            print("Migration failed. See logs for details")