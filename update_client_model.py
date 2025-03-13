"""
Migration script to add company_name and contact_person fields to Client model.
"""
import logging
from app import app, db
from sqlalchemy import text

def add_client_fields():
    """
    Add company_name and contact_person columns to the client table.
    """
    try:
        with app.app_context():
            # Check if columns already exist
            with db.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'client' 
                    AND column_name IN ('company_name', 'contact_person')
                """))
                existing_columns = [row[0] for row in result]
                
                print(f"Existing columns: {existing_columns}")
                
                # Add company_name if it doesn't exist
                if 'company_name' not in existing_columns:
                    print("Adding company_name column to client table...")
                    conn.execute(text("""
                        ALTER TABLE client 
                        ADD COLUMN company_name VARCHAR(255)
                    """))
                
                # Add contact_person if it doesn't exist
                if 'contact_person' not in existing_columns:
                    print("Adding contact_person column to client table...")
                    conn.execute(text("""
                        ALTER TABLE client 
                        ADD COLUMN contact_person VARCHAR(255)
                    """))
                
                conn.commit()
            
            print("Client table updated successfully!")
    except Exception as e:
        logging.error(f"Error updating client table: {str(e)}")
        print(f"Error: {str(e)}")
        
if __name__ == "__main__":
    add_client_fields()