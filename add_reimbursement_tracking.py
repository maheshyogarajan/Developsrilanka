"""
Migration script to add reimbursement tracking fields to the company_expense table.
This allows tracking how reimbursements are made and their reference numbers.
"""
import os
import sys
from sqlalchemy import create_engine, Column, String, text
from sqlalchemy.ext.declarative import declarative_base

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the database connection and app
from app import db, app
from models import CompanyExpense

def add_reimbursement_tracking_fields():
    """
    Add reimbursement tracking fields to the company_expense table.
    """
    try:
        with app.app_context():
            # Check if the columns already exist
            inspector = db.inspect(db.engine)
            columns = inspector.get_columns('company_expense')
            column_names = [col['name'] for col in columns]
            
            # Add reimbursement_method column if it doesn't exist
            if 'reimbursement_method' not in column_names:
                db.session.execute(text('ALTER TABLE company_expense ADD COLUMN reimbursement_method VARCHAR(50)'))
                db.session.commit()
                print("Added reimbursement_method column to company_expense table")
            else:
                print("reimbursement_method column already exists")
            
            # Add reimbursement_reference column if it doesn't exist
            if 'reimbursement_reference' not in column_names:
                db.session.execute(text('ALTER TABLE company_expense ADD COLUMN reimbursement_reference VARCHAR(100)'))
                db.session.commit()
                print("Added reimbursement_reference column to company_expense table")
            else:
                print("reimbursement_reference column already exists")
            
            print("Successfully completed reimbursement tracking fields migration")
            return True
    except Exception as e:
        print(f"Error adding reimbursement tracking fields: {str(e)}")
        return False

if __name__ == "__main__":
    add_reimbursement_tracking_fields()