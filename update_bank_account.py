"""
Migration script to create bank_account table and add bank_account_id to invoice table.
"""
import logging
from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Table, MetaData, text

from app import db
from models import BankAccount

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_bank_account_table():
    """
    Create bank_account table and add bank_account_id column to invoice table.
    """
    logger.info("Starting bank_account migration...")
    
    try:
        # Create bank_account table if it doesn't exist
        inspector = db.inspect(db.engine)
        if 'bank_account' not in inspector.get_table_names():
            logger.info("Creating bank_account table")
            
            metadata = MetaData()
            bank_account_table = Table(
                'bank_account',
                metadata,
                Column('id', Integer, primary_key=True),
                Column('user_id', Integer, ForeignKey('user.id'), nullable=False),
                Column('account_name', String(255), nullable=False),
                Column('bank_name', String(255), nullable=False),
                Column('account_number', String(100), nullable=False),
                Column('branch_name', String(255), nullable=True),
                Column('swift_code', String(50), nullable=True),
                Column('iban', String(100), nullable=True),
                Column('is_default', Boolean, default=False),
                Column('created_at', DateTime, default=datetime.utcnow),
                Column('updated_at', DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
            )
            
            metadata.create_all(db.engine)
            logger.info("Successfully created bank_account table")
        else:
            logger.info("bank_account table already exists")
        
        # Check if invoice table exists
        if 'invoice' in inspector.get_table_names():
            # Check if bank_account_id column already exists in invoice table
            columns = [c['name'] for c in inspector.get_columns('invoice')]
            if 'bank_account_id' not in columns:
                logger.info("Adding bank_account_id column to invoice table")
                
                # Add bank_account_id column
                db.engine.execute(text(
                    "ALTER TABLE invoice ADD COLUMN bank_account_id INTEGER REFERENCES bank_account(id)"
                ))
                
                logger.info("Successfully added bank_account_id column to invoice table")
            else:
                logger.info("bank_account_id column already exists in invoice table")
        else:
            logger.info("invoice table not found, skipping column addition")
        
        logger.info("Bank account migration completed successfully")
        
        # Create a default bank account for all users with invoices
        create_default_accounts()
        
        return True
    except Exception as e:
        logger.error(f"Error during migration: {str(e)}")
        return False

def create_default_accounts():
    """Create a default bank account for all users who have invoices but no bank accounts."""
    try:
        # Find users who have invoices but no bank accounts
        result = db.session.execute(text("""
            SELECT DISTINCT u.id, u.name, u.email 
            FROM user u
            JOIN invoice i ON u.id = i.user_id
            LEFT JOIN bank_account ba ON u.id = ba.user_id
            WHERE ba.id IS NULL
        """))
        
        users = [(row[0], row[1] or row[2]) for row in result]
        
        for user_id, user_name in users:
            logger.info(f"Creating default bank account for user {user_name} (ID: {user_id})")
            
            new_account = BankAccount(
                user_id=user_id,
                account_name="Primary Account",
                bank_name="Default Bank",
                account_number="Please update",
                is_default=True
            )
            
            db.session.add(new_account)
        
        db.session.commit()
        logger.info(f"Created default bank accounts for {len(users)} users")
        
    except Exception as e:
        logger.error(f"Error creating default accounts: {str(e)}")
        db.session.rollback()

if __name__ == "__main__":
    # Import app context for standalone execution
    from app import app
    with app.app_context():
        result = create_bank_account_table()
        print(f"Migration {'successful' if result else 'failed'}")