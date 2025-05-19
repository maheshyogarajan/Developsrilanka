"""
Database Transaction Reset Script

This script contains functions to reset an aborted database transaction.
It provides a utility function that can be imported in any route to
recover from InFailedSqlTransaction errors.

Usage:
from reset_db_transaction import reset_db_session

# In a route handler
try:
    # Database operation
    db.session.commit()
except Exception as e:
    reset_db_session()
    # Handle error
"""

import logging
from sqlalchemy import text
from app import db
import psycopg2

def reset_db_session():
    """
    Reset the Flask-SQLAlchemy session to recover from aborted transactions.
    
    This function:
    1. Rolls back any pending transaction
    2. Removes the current session
    3. Creates a new session connection
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # First try SQLAlchemy's methods
        db.session.rollback()
        db.session.remove()
        logging.info("Database session has been reset via SQLAlchemy")
        
        # Force a simple query to check connection
        db.session.execute(text("SELECT 1"))
        
        return True
    except Exception as e:
        logging.error(f"Error resetting database session: {e}")
        return False

def hard_reset_connection():
    """
    A more drastic approach to reset the database connection
    when the regular reset doesn't work.
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Close all connections and create new ones
        db.engine.dispose()
        logging.info("Database engine connections have been disposed")
        
        # Force a simple query to verify connection
        db.session.execute(text("SELECT 1"))
        
        return True
    except Exception as e:
        logging.error(f"Error during hard reset of database connection: {e}")
        return False