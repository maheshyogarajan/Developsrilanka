"""
Utility functions for user management.
These functions handle common operations across different registration flows.
"""

from flask import session
from models import User, db
import logging

logger = logging.getLogger(__name__)

def create_personal_finances_for_new_user(user_id):
    """
    Create a Personal Finances organization for a newly registered user.
    
    Args:
        user_id: User ID to create organization for
        
    Returns:
        Tuple of (created_new, organization_id)
    """
    # Import here to avoid circular imports
    from ensure_personal_finances_for_all_users import create_personal_finances_org
    
    created, org_id = create_personal_finances_org(user_id)
    
    try:
        # Set as current organization in session if available
        if org_id:
            session['current_organization_id'] = org_id
            logger.info(f"Set current_organization_id in session to {org_id} for user {user_id}")
            
            # Update the user's current_organization_id if available
            if hasattr(User, 'current_organization_id'):
                user = User.query.get(user_id)
                if user:
                    user.current_organization_id = org_id
                    db.session.commit()
                    logger.info(f"Updated user.current_organization_id to {org_id} for user {user_id}")
    except Exception as e:
        logger.error(f"Error setting session for user {user_id}: {str(e)}")
        
    return created, org_id