"""
Migration script to add organization tables and update existing tables with organization_id.
This will set up the multi-organization structure and migrate existing data.
"""
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta

from sqlalchemy import create_engine, inspect, Column, Integer, ForeignKey, Table, MetaData
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app import db
from models import User, Organization, OrganizationUser, Receipt, Client, Invoice, BankAccount, UserIncome, UserRole

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_to_organizations():
    """
    Migrate the database to support organizations:
    1. Create organization for each existing user
    2. Link users to their organizations
    3. Update all existing records with the appropriate organization_id
    """
    try:
        # Get all existing users
        users = User.query.all()
        logger.info(f"Found {len(users)} users to migrate to organizations")
        
        for user in users:
            # Create an organization for this user
            organization = Organization(
                name=f"{user.name}'s Organization" if user.name else f"Organization {user.id}",
                email=user.email,
                created_at=user.created_at
            )
            db.session.add(organization)
            # Flush to get the organization ID
            db.session.flush()
            
            # Create organization user link with owner role
            org_user = OrganizationUser(
                user_id=user.id,
                organization_id=organization.id,
                role=UserRole.OWNER.value,
                is_default=True,
                created_at=datetime.utcnow()
            )
            db.session.add(org_user)
            
            # Update all user's receipts
            receipts = Receipt.query.filter_by(user_id=user.id).all()
            for receipt in receipts:
                receipt.organization_id = organization.id
            
            # Update all user's clients
            clients = Client.query.filter_by(user_id=user.id).all()
            for client in clients:
                client.organization_id = organization.id
            
            # Update all user's invoices
            invoices = Invoice.query.filter_by(user_id=user.id).all()
            for invoice in invoices:
                invoice.organization_id = organization.id
            
            # Update all user's bank accounts
            bank_accounts = BankAccount.query.filter_by(user_id=user.id).all()
            for bank_account in bank_accounts:
                bank_account.organization_id = organization.id
            
            # Update user's income details
            income = UserIncome.query.filter_by(user_id=user.id).first()
            if income:
                income.organization_id = organization.id
            
            logger.info(f"Created organization for user {user.email} and updated {len(receipts)} receipts, {len(clients)} clients, {len(invoices)} invoices, {len(bank_accounts)} bank accounts")
        
        # Commit all changes
        db.session.commit()
        logger.info("✅ Successfully migrated to organizations")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error during organization migration: {str(e)}")
        db.session.rollback()
        return False

if __name__ == "__main__":
    # Import Flask app and establish application context
    from app import app
    
    with app.app_context():
        success = migrate_to_organizations()
        sys.exit(0 if success else 1)