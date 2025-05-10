"""
Custom decorators for role-based access control.
"""

from functools import wraps
from flask import flash, redirect, url_for, request, jsonify
from flask_login import current_user

def ajax_login_required(f):
    """
    AJAX-aware login required decorator.
    Returns JSON response for AJAX requests, normal redirect for other requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or '/api/' in request.path:
                return jsonify({'error': 'Authentication required', 'redirect': url_for('login')}), 401
            
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
            
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """
    Decorator to require admin role for a route.
    If the user is not an admin, they will be redirected to the home page.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or '/api/' in request.path:
                return jsonify({'error': 'Authentication required', 'redirect': url_for('login')}), 401
            
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        
        if not current_user.is_admin():
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or '/api/' in request.path:
                return jsonify({'error': 'Permission denied', 'message': 'Admin privileges required'}), 403
                
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('home'))
            
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    """
    Decorator factory to require a specific role for a route.
    If the user does not have the required role, they will be redirected to the home page.
    
    Args:
        role: String role name required for this route
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or '/api/' in request.path:
                    return jsonify({'error': 'Authentication required', 'redirect': url_for('login')}), 401
                
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('login'))
            
            if not current_user.has_role(role):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or '/api/' in request.path:
                    return jsonify({'error': 'Permission denied', 'message': f'{role} privileges required'}), 403
                
                flash(f'You need {role} permissions to access this page.', 'danger')
                return redirect(url_for('home'))
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator