"""
Migration script to add tracking columns to the friend_invitation table.
This will add accepted_at and accepted_by_user_id columns to track who accepted the invitation and when.
"""

from app import app, db
from datetime import datetime
from sqlalchemy import text

def add_tracking_columns():
    """
    Add the accepted_at and accepted_by_user_id columns to the friend_invitation table.
    """
    try:
        with app.app_context():
            # Check if the columns already exist
            engine = db.engine
            inspector = db.inspect(engine)
            columns = [column['name'] for column in inspector.get_columns('friend_invitation')]
            
            if 'accepted_at' in columns and 'accepted_by_user_id' in columns:
                print("Tracking columns already exist in friend_invitation table. Skipping.")
                return
                
            # Add the columns
            if 'accepted_at' not in columns:
                db.session.execute(text('ALTER TABLE friend_invitation ADD COLUMN accepted_at TIMESTAMP;'))
                print("✅ Successfully added accepted_at column to friend_invitation table")
            
            if 'accepted_by_user_id' not in columns:
                db.session.execute(text('ALTER TABLE friend_invitation ADD COLUMN accepted_by_user_id INTEGER REFERENCES "user"(id);'))
                print("✅ Successfully added accepted_by_user_id column to friend_invitation table")
            
            # Update values for existing accepted invitations
            db.session.execute(text("""
                UPDATE friend_invitation 
                SET accepted_at = created_at + INTERVAL '1 day',
                    accepted_by_user_id = (
                        SELECT id FROM "user" WHERE email = friend_invitation.email LIMIT 1
                    )
                WHERE accepted = true;
            """))
            print("✅ Successfully updated tracking data for existing accepted invitations")
            
            db.session.commit()
            print("✅ Successfully committed all changes")
            
    except Exception as e:
        print(f"❌ Error adding tracking columns to friend_invitation table: {str(e)}")
        raise

if __name__ == "__main__":
    add_tracking_columns()