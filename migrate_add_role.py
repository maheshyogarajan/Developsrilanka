"""
One-time script to add the role field to the user table.
This will set all existing users to 'user' role, except the first user who will be an 'admin'.
"""

from app import app, db
from sqlalchemy import text
import logging

def add_role_field_to_users():
    """
    Adds the role field to all existing users in the system.
    - First user will be set as 'admin'
    - All other users will be set as 'user'
    """
    try:
        with app.app_context():
            # Get all users
            from models import User
            
            # Create the schema if needed
            db.create_all()
            
            # Find the first user to set as admin
            first_user = User.query.order_by(User.id).first()
            
            if first_user:
                first_user.role = 'admin'
                db.session.commit()
                logging.info(f"User {first_user.email} (ID: {first_user.id}) set as admin")
            
            # Check status
            admin_users = User.query.filter_by(role='admin').count()
            regular_users = User.query.filter_by(role='user').count()
            
            logging.info(f"Migration complete: {admin_users} admin(s) and {regular_users} user(s) configured")
            
            return True
    except Exception as e:
        logging.error(f"Error adding role field: {str(e)}")
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = add_role_field_to_users()
    if success:
        print("Successfully added role field to users")
    else:
        print("Failed to add role field to users")