#!/usr/bin/env python3
"""
Create audit timeline table for comprehensive audit trail tracking
"""

from app import app, db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def create_audit_timeline_table():
    """Create audit timeline table for comprehensive tracking"""
    
    with app.app_context():
        try:
            # Create audit timeline table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS audit_timeline (
                    id SERIAL PRIMARY KEY,
                    event_id VARCHAR(32) UNIQUE NOT NULL,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    event_type VARCHAR(50) NOT NULL,
                    entity_type VARCHAR(50) NOT NULL,
                    entity_id INTEGER NOT NULL,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    user_id INTEGER REFERENCES "user"(id),
                    action VARCHAR(50) NOT NULL,
                    changes JSONB,
                    metadata JSONB,
                    confidence_score FLOAT,
                    risk_level VARCHAR(20) DEFAULT 'medium',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            
            # Create indexes for performance
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_audit_timeline_org_timestamp 
                ON audit_timeline(organization_id, timestamp DESC);
                
                CREATE INDEX IF NOT EXISTS idx_audit_timeline_entity 
                ON audit_timeline(entity_type, entity_id);
                
                CREATE INDEX IF NOT EXISTS idx_audit_timeline_event_type 
                ON audit_timeline(event_type, organization_id);
                
                CREATE INDEX IF NOT EXISTS idx_audit_timeline_risk_level 
                ON audit_timeline(risk_level, organization_id);
                
                CREATE INDEX IF NOT EXISTS idx_audit_timeline_user 
                ON audit_timeline(user_id, timestamp DESC);
            """))
            
            db.session.commit()
            logger.info("Successfully created audit timeline table and indexes")
            
        except Exception as e:
            logger.error(f"Error creating audit timeline table: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    create_audit_timeline_table()
    print("Audit timeline table created successfully!")