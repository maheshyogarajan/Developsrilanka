"""
One-time script to add the role field to the user table.
This will set all existing users to 'user' role, except the first user who will be an 'admin'.
"""

from app import app, db
from sqlalchemy import text, Column, String
import logging

def add_role_field_to_users():
    """
    Adds the role field to all existing users in the system.
    - First user will be set as 'admin'
    - All other users will be set as 'user'
    """
    try:
        with app.app_context():
            # First, check if the role column exists
            engine = db.engine
            inspector = db.inspect(engine)
            columns = [column['name'] for column in inspector.get_columns('user')]
            
            # If role column doesn't exist, add it
            if 'role' not in columns:
                logging.info("Role column does not exist, adding it...")
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE \"user\" ADD COLUMN role VARCHAR(20) DEFAULT 'user' NOT NULL"))
                    conn.commit()
                logging.info("Role column added successfully")
            else:
                logging.info("Role column already exists")
            
            # Get all users and update their roles
            from models import User
            
            # Create any missing schema elements
            db.create_all()
            
            # Find the first user to set as admin
            first_user = User.query.order_by(User.id).first()
            
            if first_user:
                # Execute a raw SQL update to avoid ORM issues
                with engine.connect() as conn:
                    conn.execute(
                        text("UPDATE \"user\" SET role = 'admin' WHERE id = :user_id"),
                        {"user_id": first_user.id}
                    )
                    conn.commit()
                logging.info(f"User ID: {first_user.id} set as admin")
                
                # Set all other users as 'user' role
                with engine.connect() as conn:
                    conn.execute(
                        text("UPDATE \"user\" SET role = 'user' WHERE id != :user_id"),
                        {"user_id": first_user.id}
                    )
                    conn.commit()
                logging.info("All other users set with 'user' role")
            
            # Check status using raw SQL to avoid ORM issues
            with engine.connect() as conn:
                admin_count = conn.execute(text("SELECT COUNT(*) FROM \"user\" WHERE role = 'admin'")).scalar()
                user_count = conn.execute(text("SELECT COUNT(*) FROM \"user\" WHERE role = 'user'")).scalar()
                conn.commit()
            
            logging.info(f"Migration complete: {admin_count} admin(s) and {user_count} user(s) configured")
            
            return True
    except Exception as e:
        logging.error(f"Error adding role field: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = add_role_field_to_users()
    if success:
        print("Successfully added role field to users")
    else:
        print("Failed to add role field to users")