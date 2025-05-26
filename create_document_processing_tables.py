"""
Database migration to add document processing audit trail tables.
Run this script to create the new tables for enhanced bank statement processing.
"""

from models import db
from document_processing_models import DocumentContext, ExtractionEvent

def create_tables():
    """Create the new document processing tables."""
    try:
        # Create all tables defined in document_processing_models
        db.create_all()
        
        # Add indexes for performance
        db.engine.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_context_statement 
            ON document_context(bank_statement_id);
            
            CREATE INDEX IF NOT EXISTS idx_extraction_event_context 
            ON extraction_event(document_context_id);
            
            CREATE INDEX IF NOT EXISTS idx_extraction_event_decision 
            ON extraction_event(decision);
            
            CREATE INDEX IF NOT EXISTS idx_extraction_event_stage 
            ON extraction_event(stage);
            
            CREATE INDEX IF NOT EXISTS idx_extraction_event_rule 
            ON extraction_event(rule_id);
        """)
        
        print("✅ Document processing tables created successfully")
        print("✅ Performance indexes added")
        return True
        
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        return False

if __name__ == "__main__":
    from app import app
    
    with app.app_context():
        success = create_tables()
        if success:
            print("\n🎉 Database migration completed!")
            print("Ready to proceed with enhanced document processing.")
        else:
            print("\n💥 Migration failed. Please check the error above.")