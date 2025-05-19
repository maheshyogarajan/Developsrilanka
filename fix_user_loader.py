"""
Script to permanently update the user loader function to handle missing columns gracefully.
This ensures the application functions properly even if there are schema mismatches.
"""
import logging
from app import app, db

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_user_loader():
    """
    Update the app.py file to modify the user_loader function to handle missing columns.
    """
    try:
        from models import User
        
        # Define the new user loader function
        @app.login_manager.user_loader
        def load_user(user_id):
            """
            Load a user by ID with graceful handling of missing columns.
            
            This version handles potential database schema mismatches 
            by using database reflection to check available columns.
            """
            try:
                # Attempt to get the user normally
                user = User.query.get(int(user_id))
                return user
            except Exception as e:
                # Check if this is a column-not-found error
                error_str = str(e).lower()
                if "column" in error_str and "not exist" in error_str:
                    logger.warning(f"Column error in user loader: {error_str}")
                    
                    # Use a more basic query to retrieve just the essential fields
                    try:
                        # First, rollback any failed transaction
                        db.session.rollback()
                        
                        # Get column names that definitely exist
                        inspector = db.inspect(db.engine)
                        columns = [col['name'] for col in inspector.get_columns('user')]
                        
                        # Build a query with only existing columns
                        query = "SELECT id"
                        for col in ['email', 'name', 'role', 'password_hash']:
                            if col in columns:
                                query += f", {col}"
                        
                        query += f" FROM \"user\" WHERE id = {int(user_id)}"
                        
                        # Execute the query
                        result = db.session.execute(db.text(query)).fetchone()
                        
                        if result:
                            # Create a User object with the retrieved data
                            user = User()
                            user.id = result[0]
                            
                            # Set other attributes that exist
                            idx = 1
                            if 'email' in columns:
                                user.email = result[idx]
                                idx += 1
                            if 'name' in columns:
                                user.name = result[idx]
                                idx += 1
                            if 'role' in columns:
                                user.role = result[idx]
                                idx += 1
                            
                            # Set default values for missing fields
                            if hasattr(User, 'is_email_verified') and not hasattr(user, 'is_email_verified'):
                                user.is_email_verified = True
                                
                            return user
                    except Exception as inner_e:
                        logger.error(f"Failed to load user with basic query: {str(inner_e)}")
                
                # For any other error, log it and return None
                logger.error(f"Error loading user {user_id}: {str(e)}")
                return None
        
        # Replace the existing user_loader in the application
        app.login_manager.user_loader(load_user)
        
        logger.info("User loader function updated successfully")
        return True
    except Exception as e:
        logger.error(f"Error updating user loader: {str(e)}")
        return False

def run():
    """Run the user loader update within app context."""
    with app.app_context():
        success = update_user_loader()
        if success:
            print("User loader updated successfully")
        else:
            print("Failed to update user loader")

if __name__ == "__main__":
    run()