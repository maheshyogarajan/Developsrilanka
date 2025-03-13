"""
Migration script to create bank_account table and add bank_account_id to invoice table.
"""
import logging
from app import app, db
from sqlalchemy import text

def create_bank_account_table():
    """
    Create bank_account table and add bank_account_id column to invoice table.
    """
    try:
        with app.app_context():
            # Check if bank_account table already exists
            with db.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'bank_account'
                    )
                """))
                table_exists = result.scalar()
                
                # Create bank_account table if it doesn't exist
                if not table_exists:
                    print("Creating bank_account table...")
                    conn.execute(text("""
                        CREATE TABLE bank_account (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES "user" (id),
                            account_name VARCHAR(255) NOT NULL,
                            bank_name VARCHAR(255) NOT NULL,
                            account_number VARCHAR(100) NOT NULL,
                            branch_name VARCHAR(255),
                            swift_code VARCHAR(50),
                            iban VARCHAR(100),
                            is_default BOOLEAN DEFAULT FALSE,
                            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                        )
                    """))
                
                # Check if bank_account_id column already exists in invoice table
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'invoice' 
                    AND column_name = 'bank_account_id'
                """))
                column_exists = result.fetchone() is not None
                
                # Add bank_account_id column to invoice table if it doesn't exist
                if not column_exists:
                    print("Adding bank_account_id column to invoice table...")
                    conn.execute(text("""
                        ALTER TABLE invoice 
                        ADD COLUMN bank_account_id INTEGER REFERENCES bank_account (id)
                    """))
                
                conn.commit()
            
            print("Database tables updated successfully!")
    except Exception as e:
        logging.error(f"Error updating database tables: {str(e)}")
        print(f"Error: {str(e)}")
        
if __name__ == "__main__":
    create_bank_account_table()