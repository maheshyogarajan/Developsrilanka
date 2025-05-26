#!/usr/bin/env python3
"""
Create security and performance monitoring tables
"""

from app import app, db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def create_security_tables():
    """Create security logging and monitoring tables"""
    
    with app.app_context():
        try:
            # Create security log table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS security_log (
                    id SERIAL PRIMARY KEY,
                    event_type VARCHAR(100) NOT NULL,
                    details JSONB,
                    risk_level VARCHAR(20) DEFAULT 'medium',
                    ip_address INET,
                    user_agent TEXT,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    organization_id INTEGER REFERENCES organization(id),
                    user_id INTEGER REFERENCES "user"(id)
                )
            """))
            
            # Create performance monitoring table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id SERIAL PRIMARY KEY,
                    endpoint VARCHAR(200) NOT NULL,
                    method VARCHAR(10) NOT NULL,
                    response_time_ms INTEGER NOT NULL,
                    status_code INTEGER NOT NULL,
                    organization_id INTEGER REFERENCES organization(id),
                    user_id INTEGER REFERENCES "user"(id),
                    timestamp TIMESTAMP DEFAULT NOW(),
                    request_size_bytes INTEGER,
                    response_size_bytes INTEGER
                )
            """))
            
            # Create indexes for performance
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_security_log_timestamp 
                ON security_log(timestamp DESC);
                
                CREATE INDEX IF NOT EXISTS idx_security_log_risk_level 
                ON security_log(risk_level, organization_id);
                
                CREATE INDEX IF NOT EXISTS idx_performance_metrics_endpoint 
                ON performance_metrics(endpoint, timestamp DESC);
                
                CREATE INDEX IF NOT EXISTS idx_performance_metrics_org 
                ON performance_metrics(organization_id, timestamp DESC);
            """))
            
            db.session.commit()
            logger.info("Successfully created security and performance monitoring tables")
            
        except Exception as e:
            logger.error(f"Error creating security tables: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    create_security_tables()
    print("Security and performance monitoring tables created successfully!")