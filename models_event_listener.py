"""
SQLAlchemy event listeners for model events like user creation.
This ensures automatic creation of Personal Finances organization for new users.
"""

import logging
from sqlalchemy import event

from app import db
from models import User

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@event.listens_for(User, 'after_insert')
def create_personal_finances_after_user_created(mapper, connection, user):
    """
    Create a Personal Finances organization for a user after they are created.
    
    Args:
        mapper: SQLAlchemy mapper
        connection: SQLAlchemy connection
        user: The User instance that was created
    """
    # Import here to avoid circular imports
    from user_organization_helper import create_personal_finances_organization
    
    try:
        # Use the helper function to create the organization
        create_personal_finances_organization(user)
        logger.info(f"Created Personal Finances organization for new user {user.id} via event listener")
    except Exception as e:
        logger.error(f"Error creating Personal Finances organization via event listener: {str(e)}")

# Load the event listeners
def load_event_listeners():
    """
    Load all event listeners.
    This function should be called when the application starts.
    """
    logger.info("Model event listeners loaded successfully")