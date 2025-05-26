#!/usr/bin/env python3
"""
Database migration for Auto-Reconciliation & Performance Optimization
Adds matching engine tables, ML training data, and performance indexes
"""

from app import app, db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def create_reconciliation_matching_tables():
    """Create tables and indexes for the matching engine system"""
    
    with app.app_context():
        try:
            # Add match status and score columns to existing tables
            logger.info("Adding match status and score columns...")
            db.session.execute(text("""
                ALTER TABLE financial_transaction 
                ADD COLUMN IF NOT EXISTS match_status VARCHAR(20) DEFAULT 'pending',
                ADD COLUMN IF NOT EXISTS match_score FLOAT DEFAULT 0.0,
                ADD COLUMN IF NOT EXISTS auto_matched BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS match_confidence VARCHAR(10) DEFAULT 'low',
                ADD COLUMN IF NOT EXISTS last_matched_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS processing_status VARCHAR(20) DEFAULT 'queued'
            """))
            
            # Create transaction matching pairs table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS transaction_match (
                    id SERIAL PRIMARY KEY,
                    bank_transaction_id INTEGER REFERENCES financial_transaction(id),
                    expense_transaction_id INTEGER REFERENCES company_expense(id),
                    match_type VARCHAR(20) NOT NULL, -- 'deterministic', 'ml_model', 'manual'
                    match_score FLOAT NOT NULL DEFAULT 0.0,
                    confidence_tier VARCHAR(10) NOT NULL, -- 'tier1', 'tier2', 'manual'
                    match_status VARCHAR(20) DEFAULT 'proposed', -- 'proposed', 'accepted', 'rejected'
                    match_rules_applied JSONB, -- Store which rules matched
                    ml_features JSONB, -- Store ML model features for retraining
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
                    feature_vector JSONB NOT NULL, -- Serialized feature data
                    label INTEGER NOT NULL, -- 0=no_match, 1=match
                    training_set VARCHAR(20) DEFAULT 'active', -- 'active', 'validation', 'test'
                    created_at TIMESTAMP DEFAULT NOW(),
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    model_version VARCHAR(50) DEFAULT 'v1.0'
                )
            """))
            
            # Create reconciliation cache table for Redis-backed caching
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS reconciliation_cache (
                    id SERIAL PRIMARY KEY,
                    cache_key VARCHAR(64) UNIQUE NOT NULL, -- SHA-256 hash
                    pdf_hash VARCHAR(64) NOT NULL,
                    extraction_result JSONB NOT NULL,
                    page_count INTEGER DEFAULT 0,
                    processing_time_ms INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP DEFAULT NOW() + INTERVAL '24 hours',
                    organization_id INTEGER REFERENCES organization(id) NOT NULL
                )
            """))
            
            # Create rate limiting table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS rate_limit_log (
                    id SERIAL PRIMARY KEY,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    user_id INTEGER REFERENCES "user"(id),
                    endpoint VARCHAR(100) NOT NULL,
                    request_count INTEGER DEFAULT 1,
                    window_start TIMESTAMP DEFAULT NOW(),
                    window_end TIMESTAMP DEFAULT NOW() + INTERVAL '1 minute',
                    rate_limit_exceeded BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            
            # Performance indexes for large-scale operations
            logger.info("Creating performance indexes...")
            
            # Composite indexes for transaction queries
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_financial_transaction_org_date 
                ON financial_transaction(organization_id, transaction_date);
                
                CREATE INDEX IF NOT EXISTS idx_financial_transaction_match_status 
                ON financial_transaction(match_status, organization_id);
                
                CREATE INDEX IF NOT EXISTS idx_financial_transaction_processing 
                ON financial_transaction(processing_status, organization_id);
            """))
            
            # Indexes for company expenses
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_company_expense_org_date 
                ON company_expense(organization_id, submitted_date);
                
                CREATE INDEX IF NOT EXISTS idx_company_expense_amount 
                ON company_expense(amount, organization_id);
            """))
            
            # Indexes for transaction matching
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_transaction_match_org_status 
                ON transaction_match(organization_id, match_status);
                
                CREATE INDEX IF NOT EXISTS idx_transaction_match_score 
                ON transaction_match(match_score DESC, organization_id);
                
                CREATE INDEX IF NOT EXISTS idx_transaction_match_type 
                ON transaction_match(match_type, confidence_tier);
            """))
            
            # Indexes for ML training data
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ml_training_org_set 
                ON ml_training_data(organization_id, training_set);
                
                CREATE INDEX IF NOT EXISTS idx_ml_training_label 
                ON ml_training_data(label, model_version);
            """))
            
            # Indexes for caching and rate limiting
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_reconciliation_cache_key 
                ON reconciliation_cache(cache_key);
                
                CREATE INDEX IF NOT EXISTS idx_reconciliation_cache_expires 
                ON reconciliation_cache(expires_at);
                
                CREATE INDEX IF NOT EXISTS idx_rate_limit_org_window 
                ON rate_limit_log(organization_id, window_start, window_end);
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
            logger.info("Successfully created reconciliation matching tables and indexes")
            
        except Exception as e:
            logger.error(f"Error creating reconciliation matching tables: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    create_reconciliation_matching_tables()
    print("Reconciliation matching tables created successfully!")