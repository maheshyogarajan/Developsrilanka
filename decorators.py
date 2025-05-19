"""
Custom decorators for the application.
"""
import logging
from functools import wraps
import json

from flask import redirect, url_for, request, flash, jsonify
from flask_login import current_user
from models import OrganizationUser

logger = logging.getLogger(__name__)

def admin_required(f):
    """
    Decorator to require admin role for a route.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login', next=request.url))
        
        if not current_user.is_admin():
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('index'))
            
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    """
    Decorator to require a specific organization role for a route.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login', next=request.url))
            
            org_id = kwargs.get('org_id', request.args.get('org_id'))
            if not org_id:
                flash('Organization is required.', 'danger')
                return redirect(url_for('index'))
                
            org_user = OrganizationUser.query.filter_by(
                user_id=current_user.id, 
                organization_id=org_id
            ).first()
            
            if not org_user:
                flash('You do not have access to this organization.', 'danger')
                return redirect(url_for('index'))
                
            if org_user.role != role and org_user.role != 'owner' and current_user.role != 'admin':
                flash(f'You need {role} role for this action.', 'danger')
                return redirect(url_for('organization_dashboard', org_id=org_id))
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def ajax_login_required(f):
    """
    AJAX login required decorator for API calls.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify(success=False, error='login_required'), 401
        return f(*args, **kwargs)
    return decorated_function

def email_verified_required(f):
    """
    Decorator to require email verification for specific routes.
    Only restricts access to the decorated route if the user's email is not verified.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login', next=request.url))
        
        # Check if user has the is_email_verified attribute (for backward compatibility)
        if hasattr(current_user, 'is_email_verified') and not current_user.is_email_verified:
            flash('Email verification is required before scanning receipts. Please check your inbox or request a new verification email.', 'warning')
            return redirect(url_for('verify_email_reminder'))
            
        return f(*args, **kwargs)
    return decorated_function