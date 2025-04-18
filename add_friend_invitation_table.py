"""
Migration script to add friend_invitation table to the database.
This will allow tracking friend invitations sent by users.
"""

from app import app, db
from models import FriendInvitation

def add_friend_invitation_table():
    """
    Add the friend_invitation table to the database.
    """
    try:
        with app.app_context():
            # Check if the table exists already
            engine = db.engine
            inspector = db.inspect(engine)
            table_exists = 'friend_invitation' in inspector.get_table_names()
            
            if table_exists:
                print("friend_invitation table already exists. Skipping.")
                return
                
            # Create the table
            FriendInvitation.__table__.create(engine)
            print("✅ Successfully created friend_invitation table")
    except Exception as e:
        print(f"❌ Error creating friend_invitation table: {str(e)}")
        raise

if __name__ == "__main__":
    add_friend_invitation_table()