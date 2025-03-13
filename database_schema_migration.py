"""
Database schema migration script to add the new columns for personal/business organization setup.
"""
from app import app, db
import logging
from sqlalchemy import Column, Integer, Boolean, String, Float, ForeignKey, text

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def execute_migration():
    """Execute the migration for the new schema changes."""
    logger.info("Starting schema migration")
    
    with app.app_context():
        connection = db.engine.connect()
        transaction = connection.begin()
        
        try:
            # 1. Add the new columns to the organization table
            logger.info("Adding new columns to organization table")
            
            # Check if columns already exist before adding
            check_is_personal = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='is_personal'"
            )).fetchone()
            
            if not check_is_personal:
                logger.info("Adding is_personal column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN is_personal BOOLEAN DEFAULT FALSE"
                ))
            
            check_tax_rate_type = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='tax_rate_type'"
            )).fetchone()
            
            if not check_tax_rate_type:
                logger.info("Adding tax_rate_type column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN tax_rate_type VARCHAR(20) DEFAULT 'business'"
                ))
            
            # Business tax rate fields
            check_corporate_tax_rate = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='corporate_tax_rate'"
            )).fetchone()
            
            if not check_corporate_tax_rate:
                logger.info("Adding corporate_tax_rate column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN corporate_tax_rate FLOAT DEFAULT 24.0"
                ))
            
            check_dividend_tax_rate = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='dividend_tax_rate'"
            )).fetchone()
            
            if not check_dividend_tax_rate:
                logger.info("Adding dividend_tax_rate column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN dividend_tax_rate FLOAT DEFAULT 14.0"
                ))
            
            # Personal tax rate fields
            check_employment_tax_rate = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='employment_tax_rate'"
            )).fetchone()
            
            if not check_employment_tax_rate:
                logger.info("Adding employment_tax_rate column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN employment_tax_rate FLOAT DEFAULT 24.0"
                ))
            
            check_investment_tax_rate = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='investment_tax_rate'"
            )).fetchone()
            
            if not check_investment_tax_rate:
                logger.info("Adding investment_tax_rate column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN investment_tax_rate FLOAT DEFAULT 14.0"
                ))
            
            check_business_income_tax_rate = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='business_income_tax_rate'"
            )).fetchone()
            
            if not check_business_income_tax_rate:
                logger.info("Adding business_income_tax_rate column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN business_income_tax_rate FLOAT DEFAULT 36.0"
                ))
            
            check_consulting_tax_rate = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='organization' AND column_name='consulting_tax_rate'"
            )).fetchone()
            
            if not check_consulting_tax_rate:
                logger.info("Adding consulting_tax_rate column to organization table")
                connection.execute(text(
                    "ALTER TABLE organization ADD COLUMN consulting_tax_rate FLOAT DEFAULT 15.0"
                ))
            
            # 2. Add personal_organization_id column to user table
            logger.info("Adding personal_organization_id column to user table")
            
            check_personal_organization_id = connection.execute(text(
                "SELECT column_name FROM information_schema.columns " +
                "WHERE table_name='user' AND column_name='personal_organization_id'"
            )).fetchone()
            
            if not check_personal_organization_id:
                logger.info("Adding personal_organization_id column to user table")
                connection.execute(text(
                    "ALTER TABLE \"user\" ADD COLUMN personal_organization_id INTEGER " +
                    "REFERENCES organization(id)"
                ))
            
            # Commit the transaction
            transaction.commit()
            logger.info("Schema migration completed successfully")
            
            return True
            
        except Exception as e:
            logger.error(f"Error in schema migration: {e}")
            transaction.rollback()
            return False
        finally:
            connection.close()

if __name__ == "__main__":
    success = execute_migration()
    if success:
        logger.info("Database schema update completed successfully")
    else:
        logger.error("Database schema update failed")