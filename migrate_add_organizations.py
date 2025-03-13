"""
Migration script to add organization tables and update existing tables with organization_id.
This will set up the multi-organization structure and migrate existing data.
"""
import os
import sys
import logging
from datetime import datetime, timedelta
import uuid

from sqlalchemy import Table, Column, Integer, String, ForeignKey, DateTime, Boolean, Text, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from flask import Flask

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a minimal app context
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# Import database and models
from app import db
from models import User, Receipt, Client, Invoice, BankAccount, UserIncome, Organization, OrganizationUser, UserRole

def migrate_to_organizations():
    """
    Migrate the database to support organizations:
    1. Create organization for each existing user
    2. Link users to their organizations
    3. Update all existing records with the appropriate organization_id
    """
    logger.info("Starting organization migration...")
    
    with app.app_context():
        # Check if we've already migrated
        inspector = inspect(db.engine)
        if not inspector.has_table('organization'):
            logger.error("Organization table doesn't exist. Run db.create_all() first.")
            return
            
        # Get all existing users
        users = User.query.all()
        logger.info(f"Found {len(users)} users to migrate")
        
        for user in users:
            logger.info(f"Processing user: {user.email}")
            
            # Create a new organization for this user
            org_name = f"{user.name or user.email.split('@')[0]}'s Organization"
            new_org = Organization(
                name=org_name,
                email=user.email,
                created_at=user.created_at or datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.session.add(new_org)
            db.session.flush()  # Get the new ID
            
            logger.info(f"Created organization: {org_name} (ID: {new_org.id})")
            
            # Link user to organization
            org_user = OrganizationUser(
                user_id=user.id,
                organization_id=new_org.id,
                role=UserRole.OWNER.value,
                is_default=True,  # Set as default organization
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.session.add(org_user)
            
            # Update user's receipts
            receipts = Receipt.query.filter_by(user_id=user.id).all()
            for receipt in receipts:
                receipt.organization_id = new_org.id
                logger.info(f"Updated receipt ID: {receipt.id}")
            
            # Update user's clients
            clients = Client.query.filter_by(user_id=user.id).all()
            for client in clients:
                client.organization_id = new_org.id
                logger.info(f"Updated client ID: {client.id}")
            
            # Update user's invoices
            invoices = Invoice.query.filter_by(user_id=user.id).all()
            for invoice in invoices:
                invoice.organization_id = new_org.id
                logger.info(f"Updated invoice ID: {invoice.id}")
            
            # Update user's bank accounts
            bank_accounts = BankAccount.query.filter_by(user_id=user.id).all()
            for bank_account in bank_accounts:
                bank_account.organization_id = new_org.id
                logger.info(f"Updated bank account ID: {bank_account.id}")
            
            # Update user's income details
            income = UserIncome.query.filter_by(user_id=user.id).first()
            if income:
                income.organization_id = new_org.id
                logger.info(f"Updated income record ID: {income.id}")
        
        # Commit all changes
        db.session.commit()
        logger.info("Organization migration completed successfully!")

if __name__ == "__main__":
    migrate_to_organizations()