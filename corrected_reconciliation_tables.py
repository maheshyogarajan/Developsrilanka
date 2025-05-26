#!/usr/bin/env python3
"""
Corrected Database migration for Auto-Reconciliation & Performance Optimization
Works with actual database schema
"""

from app import app, db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def create_corrected_reconciliation_tables():
    """Create tables for the matching engine system with correct column references"""
    
    with app.app_context():
        try:
            # Add match status and score columns to existing tables
            logger.info("Adding match status and score columns...")
            db.session.execute(text("""
                ALTER TABLE financial_transaction 
                ADD COLUMN IF NOT EXISTS match_status VARCHAR(20) DEFAULT 'pending',
                ADD COLUMN IF NOT EXISTS match_score FLOAT DEFAULT 0.0,
                ADD COLUMN IF NOT EXISTS auto_matched BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS last_matched_at TIMESTAMP
            """))
            
            # Create transaction matching pairs table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS transaction_match (
                    id SERIAL PRIMARY KEY,
                    bank_transaction_id INTEGER REFERENCES financial_transaction(id),
                    expense_receipt_id INTEGER REFERENCES receipt(id),
                    match_type VARCHAR(20) NOT NULL,
                    match_score FLOAT NOT NULL DEFAULT 0.0,
                    confidence_tier VARCHAR(10) NOT NULL,
                    match_status VARCHAR(20) DEFAULT 'proposed',
                    match_rules_applied JSONB,
                    ml_features JSONB,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    reviewed_by INTEGER REFERENCES "user"(id),
                    reviewed_at TIMESTAMP,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL
                )
            """))
            
            # Create ML training data table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS ml_training_data (
                    id SERIAL PRIMARY KEY,
                    match_id INTEGER REFERENCES transaction_match(id),
                    feature_vector JSONB NOT NULL,
                    label INTEGER NOT NULL,
                    training_set VARCHAR(20) DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT NOW(),
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    model_version VARCHAR(50) DEFAULT 'v1.0'
                )
            """))
            
            # Performance indexes for large-scale operations
            logger.info("Creating performance indexes...")
            
            # Composite indexes for financial transactions
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_financial_transaction_org_date 
                ON financial_transaction(organization_id, transaction_date);
                
                CREATE INDEX IF NOT EXISTS idx_financial_transaction_match_status 
                ON financial_transaction(match_status, organization_id);
            """))
            
            # Indexes for receipts (using correct column names)
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_receipt_org_date 
                ON receipt(organization_id, created_at);
                
                CREATE INDEX IF NOT EXISTS idx_receipt_amount 
                ON receipt(total_amount, organization_id);
            """))
            
            # Indexes for transaction matching
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_transaction_match_org_status 
                ON transaction_match(organization_id, match_status);
                
                CREATE INDEX IF NOT EXISTS idx_transaction_match_score 
                ON transaction_match(match_score DESC, organization_id);
            """))
            
            # Indexes for ML training data
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ml_training_org_set 
                ON ml_training_data(organization_id, training_set);
            """))
            
            # Add triggers for automatic timestamp updates
            db.session.execute(text("""
                CREATE OR REPLACE FUNCTION update_updated_at_column()
                RETURNS TRIGGER AS $$
                BEGIN
                    NEW.updated_at = NOW();
                    RETURN NEW;
                END;
                $$ language 'plpgsql';
                
                DROP TRIGGER IF EXISTS update_transaction_match_updated_at ON transaction_match;
                CREATE TRIGGER update_transaction_match_updated_at 
                    BEFORE UPDATE ON transaction_match 
                    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
            """))
            
            db.session.commit()
            logger.info("Successfully created corrected reconciliation tables and indexes")
            
        except Exception as e:
            logger.error(f"Error creating reconciliation tables: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    create_corrected_reconciliation_tables()
    print("Corrected reconciliation tables created successfully!")