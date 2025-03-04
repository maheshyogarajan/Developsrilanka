"""
One-time script to reset any aborted database transactions.
This should be run when the application is having database transaction issues.
"""
import os
import sys
import logging
from sqlalchemy import create_engine, text

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reset_db_transactions():
    """
    Reset any aborted database transactions.
    """
    database_url = os.environ.get("DATABASE_URL")
    
    if not database_url:
        logger.error("DATABASE_URL environment variable not found!")
        return False
    
    # Fix potential "postgres://" vs "postgresql://" issue
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    
    try:
        # Create an engine with the database URL
        engine = create_engine(database_url)
        
        # Create a connection
        with engine.connect() as conn:
            # Check if there are any in-doubt transactions
            in_doubt_result = conn.execute(text("SELECT * FROM pg_prepared_xacts"))
            in_doubt_transactions = in_doubt_result.fetchall()
            
            if in_doubt_transactions:
                logger.info(f"Found {len(in_doubt_transactions)} in-doubt transactions")
                for tx in in_doubt_transactions:
                    logger.info(f"Rolling back in-doubt transaction: {tx}")
                    conn.execute(text(f"ROLLBACK PREPARED '{tx[0]}'"))
            else:
                logger.info("No in-doubt transactions found")
                
            # Execute rollback to clear any active transactions
            conn.execute(text("ROLLBACK"))
            logger.info("Successfully executed ROLLBACK")
            
            # Test a simple query to verify database is working
            test_result = conn.execute(text("SELECT 1 as test"))
            if test_result.fetchone()[0] == 1:
                logger.info("Database is working correctly")
                return True
            else:
                logger.error("Database test query failed")
                return False
    
    except Exception as e:
        logger.error(f"Error resetting database transactions: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting database transaction reset...")
    success = reset_db_transactions()
    if success:
        logger.info("Successfully reset database transactions")
        sys.exit(0)
    else:
        logger.error("Failed to reset database transactions")
        sys.exit(1)