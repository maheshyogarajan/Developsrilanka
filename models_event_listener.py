"""
SQLAlchemy event listeners for model events.
NOTE: The automatic creation of Personal Finances organizations has been moved to app.py
to avoid transaction conflicts with the existing organization creation process.
"""

import logging
from sqlalchemy import event

from app import db
from models import User

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The event listener for Personal Finances organization creation has been removed
# to avoid transaction conflicts. Organization creation is now handled
# explicitly in app.py during user registration.

# Load the event listeners
def load_event_listeners():
    """
    Load all event listeners.
    This function should be called when the application starts.
    """
    logger.info("Model event listeners loaded successfully")