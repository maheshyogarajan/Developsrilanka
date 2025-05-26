#!/usr/bin/env python3
"""
Database migration to add PDF coordinate tracking for storage-lineage overlay
"""

from app import app, db
from sqlalchemy import text

def add_pdf_lineage_columns():
    """Add columns for PDF coordinate tracking and confidence scoring"""
    
    with app.app_context():
        try:
            # Add bbox (bounding box) column to store PDF coordinates as JSONB
            db.session.execute(text("""
                ALTER TABLE financial_transaction 
                ADD COLUMN IF NOT EXISTS bbox JSONB,
                ADD COLUMN IF NOT EXISTS page_idx INTEGER DEFAULT 0,
                ADD COLUMN IF NOT EXISTS extraction_confidence FLOAT DEFAULT 0.0,
                ADD COLUMN IF NOT EXISTS source_text TEXT
            """))
            
            # Add extraction event table for detailed lineage tracking
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS extraction_event (
                    id SERIAL PRIMARY KEY,
                    transaction_id INTEGER REFERENCES financial_transaction(id),
                    bank_statement_id INTEGER REFERENCES bank_statement(id),
                    extraction_type VARCHAR(50) NOT NULL, -- 'amount', 'date', 'description'
                    bbox JSONB, -- {x0, top, x1, bottom}
                    page_idx INTEGER DEFAULT 0,
                    confidence FLOAT DEFAULT 0.0,
                    source_text TEXT,
                    parser_method VARCHAR(100), -- 'regex', 'table_detection', 'ml_model'
                    rule_id VARCHAR(100),
                    created_at TIMESTAMP DEFAULT NOW(),
                    organization_id INTEGER REFERENCES organization(id)
                )
            """))
            
            # Add indexes for performance
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_extraction_event_transaction 
                ON extraction_event(transaction_id);
                
                CREATE INDEX IF NOT EXISTS idx_extraction_event_statement 
                ON extraction_event(bank_statement_id);
                
                CREATE INDEX IF NOT EXISTS idx_extraction_event_org 
                ON extraction_event(organization_id);
                
                CREATE INDEX IF NOT EXISTS idx_financial_transaction_bbox 
                ON financial_transaction USING GIN (bbox);
            """))
            
            db.session.commit()
            print("✅ Successfully added PDF lineage tracking columns and indexes")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    add_pdf_lineage_columns()