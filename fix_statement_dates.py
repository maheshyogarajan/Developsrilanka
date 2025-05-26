#!/usr/bin/env python3
"""
Fix bank statement dates that were incorrectly stored as year 1000
This script updates the database to correct the dates.
"""

from app import app, db
from enhanced_financial_models import BankStatement
from datetime import datetime, date
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_statement_dates():
    """Fix bank statement dates that have year 1000."""
    with app.app_context():
        # Find all statements with year 1000
        statements_to_fix = BankStatement.query.filter(
            db.or_(
                db.extract('year', BankStatement.statement_period_from) == 1000,
                db.extract('year', BankStatement.statement_period_to) == 1000
            )
        ).all()
        
        logger.info(f"Found {len(statements_to_fix)} statements with year 1000 dates")
        
        current_year = datetime.now().year
        fixed_count = 0
        
        for statement in statements_to_fix:
            try:
                # Fix the dates by updating year to current year
                if statement.statement_period_from and statement.statement_period_from.year == 1000:
                    statement.statement_period_from = statement.statement_period_from.replace(year=current_year)
                    logger.info(f"Fixed statement {statement.id} period_from to {statement.statement_period_from}")
                
                if statement.statement_period_to and statement.statement_period_to.year == 1000:
                    statement.statement_period_to = statement.statement_period_to.replace(year=current_year)
                    logger.info(f"Fixed statement {statement.id} period_to to {statement.statement_period_to}")
                
                fixed_count += 1
                
            except Exception as e:
                logger.error(f"Error fixing statement {statement.id}: {str(e)}")
                continue
        
        # Commit all changes
        try:
            db.session.commit()
            logger.info(f"Successfully fixed {fixed_count} bank statements")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error committing changes: {str(e)}")
            raise

if __name__ == "__main__":
    fix_statement_dates()
    print("Date fix completed!")