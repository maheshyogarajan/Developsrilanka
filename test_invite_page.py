"""
Test the invite friends page accessibility.
"""
import logging
import sys
from app import app, db
from models import User
from flask_login import LoginManager, login_user

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_invite_page():
    """Test access to the invite friends page with a logged-in user."""
    with app.test_client() as client:
        with app.app_context():
            # Get or create a test user
            test_user = User.query.filter_by(email='test@devsrilanka.com').first()
            
            if not test_user:
                logging.info("Creating test user")
                test_user = User(
                    email='test@devsrilanka.com',
                    name='Test User',
                    role='user'
                )
                db.session.add(test_user)
                db.session.commit()
                logging.info(f"Created test user with ID: {test_user.id}")
            else:
                logging.info(f"Using existing test user with ID: {test_user.id}")
            
            # Log the user in
            with client.session_transaction() as sess:
                # Simulate a login
                sess['_user_id'] = str(test_user.id)
            
            # Try to access the invite friends page
            logging.info("Testing access to invite-friends page...")
            response = client.get('/invite-friends')
            
            # Check the response
            if response.status_code == 200:
                logging.info("✅ Successfully accessed the invite-friends page!")
                return True
            else:
                logging.error(f"❌ Failed to access invite-friends page. Status code: {response.status_code}")
                return False

if __name__ == "__main__":
    try:
        success = test_invite_page()
        sys.exit(0 if success else 1)
    except Exception as e:
        logging.error(f"Error running test: {str(e)}")
        sys.exit(1)