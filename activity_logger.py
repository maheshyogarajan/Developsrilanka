"""
Activity logging service for tracking user actions and system events.
Provides real-time activity tracking for the admin dashboard.
"""

import logging
from datetime import datetime
from flask import request
from flask_login import current_user

from app import db
from models import AuditLog

logger = logging.getLogger(__name__)


class ActivityLogger:
    """Service for logging user and system activities."""
    
    # Activity types
    USER_LOGIN = 'user_login'
    USER_LOGOUT = 'user_logout'
    USER_REGISTER = 'user_register'
    RECEIPT_UPLOAD = 'receipt_upload'
    RECEIPT_SCAN = 'receipt_scan'
    RECEIPT_SAVE = 'receipt_save'
    RECEIPT_DELETE = 'receipt_delete'
    RECEIPT_EXPORT = 'receipt_export'
    SETTINGS_UPDATE = 'settings_update'
    PASSWORD_RESET = 'password_reset'
    ADMIN_ACTION = 'admin_action'
    API_ERROR = 'api_error'
    GEMINI_SUCCESS = 'gemini_success'
    GEMINI_ERROR = 'gemini_error'
    
    @staticmethod
    def log_activity(
        entity_type: str,
        entity_id: int,
        action: str,
        changed_fields: dict = None,
        user_id: int = None
    ):
        """
        Log an activity to the audit_log table.
        
        Args:
            entity_type: Type of entity (user, receipt, settings, gemini_api)
            entity_id: ID of the entity being acted upon
            action: The action performed (login, upload, scan, error, etc.)
            changed_fields: Dictionary of changes or metadata
            user_id: User ID (uses current_user if not provided)
        """
        try:
            if user_id is None and current_user and hasattr(current_user, 'id'):
                user_id = current_user.id
            
            ip_address = None
            user_agent = None
            
            try:
                ip_address = request.remote_addr
                user_agent = request.user_agent.string[:500] if request.user_agent else None
            except RuntimeError:
                pass
            
            audit_log = AuditLog(
                entity_type=entity_type,
                entity_id=entity_id or 0,
                action=action,
                changed_fields=changed_fields or {},
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                timestamp=datetime.utcnow()
            )
            
            db.session.add(audit_log)
            db.session.commit()
            
            logger.debug(f"Activity logged: {entity_type}/{action} by user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log activity: {str(e)}")
            db.session.rollback()
            return False
    
    @classmethod
    def log_login(cls, user_id: int, success: bool = True, error_message: str = None):
        """Log a user login attempt."""
        return cls.log_activity(
            entity_type='user',
            entity_id=user_id,
            action=cls.USER_LOGIN if success else 'login_failed',
            changed_fields={
                'success': success,
                'error': error_message
            } if error_message else {'success': success},
            user_id=user_id
        )
    
    @classmethod
    def log_logout(cls, user_id: int):
        """Log a user logout."""
        return cls.log_activity(
            entity_type='user',
            entity_id=user_id,
            action=cls.USER_LOGOUT,
            user_id=user_id
        )
    
    @classmethod
    def log_registration(cls, user_id: int, email: str):
        """Log a new user registration."""
        return cls.log_activity(
            entity_type='user',
            entity_id=user_id,
            action=cls.USER_REGISTER,
            changed_fields={'email': email}
        )
    
    @classmethod
    def log_receipt_scan(cls, receipt_id=None, success: bool = True, model_used: str = None, 
                         error_category: str = None, processing_time: float = None,
                         extra: dict = None):
        """Log a receipt scanning attempt (Gemini API or GLM-OCR call).
        
        ``extra`` carries provider-specific metadata that we want preserved
        on the audit row (e.g. the underlying GLM model name when
        ``model_used='glm-ocr'``, or Stage B reasoner model on fallback).
        """
        fields = {
            'success': success,
            'model': model_used,
            'error_category': error_category,
            'processing_time_ms': int(processing_time * 1000) if processing_time else None
        }
        if extra:
            for k, v in extra.items():
                if k not in fields:
                    fields[k] = v
        return cls.log_activity(
            entity_type='gemini_api',
            entity_id=receipt_id or 0,
            action=cls.GEMINI_SUCCESS if success else cls.GEMINI_ERROR,
            changed_fields=fields
        )
    
    @classmethod
    def log_receipt_save(cls, receipt_id: int, vendor_name: str = None, amount: float = None):
        """Log a receipt being saved."""
        return cls.log_activity(
            entity_type='receipt',
            entity_id=receipt_id,
            action=cls.RECEIPT_SAVE,
            changed_fields={
                'vendor': vendor_name,
                'amount': amount
            }
        )
    
    @classmethod
    def log_receipt_delete(cls, receipt_id: int):
        """Log a receipt being deleted."""
        return cls.log_activity(
            entity_type='receipt',
            entity_id=receipt_id,
            action=cls.RECEIPT_DELETE
        )
    
    @classmethod
    def log_admin_action(cls, action_type: str, target_id: int = None, details: dict = None):
        """Log an admin action."""
        return cls.log_activity(
            entity_type='admin',
            entity_id=target_id or 0,
            action=action_type,
            changed_fields=details or {}
        )


def get_recent_activities(limit: int = 20, activity_types: list = None):
    """
    Get recent activities from the audit log.
    
    Args:
        limit: Maximum number of activities to return
        activity_types: Optional list of entity types to filter by
        
    Returns:
        List of activity dictionaries
    """
    from models import User
    
    query = AuditLog.query
    
    if activity_types:
        query = query.filter(AuditLog.entity_type.in_(activity_types))
    
    logs = query.order_by(AuditLog.timestamp.desc()).limit(limit).all()
    
    activities = []
    for log in logs:
        user_name = 'System'
        user_email = None
        if log.user_id:
            user = User.query.get(log.user_id)
            if user:
                user_name = user.name or user.email
                user_email = user.email
        
        status = 'success'
        status_color = 'success'
        
        if 'error' in log.action.lower() or 'failed' in log.action.lower():
            status = 'error'
            status_color = 'danger'
        elif 'warning' in str(log.changed_fields).lower():
            status = 'warning'
            status_color = 'warning'
        
        action_display = log.action.replace('_', ' ').title()
        
        activities.append({
            'id': log.id,
            'event': action_display,
            'entity_type': log.entity_type,
            'entity_id': log.entity_id,
            'user': user_name,
            'user_email': user_email,
            'timestamp': log.timestamp,
            'status': status,
            'status_color': status_color,
            'details': log.changed_fields,
            'ip_address': log.ip_address
        })
    
    return activities


def get_activity_stats(days: int = 7):
    """
    Get activity statistics for the dashboard.
    
    Args:
        days: Number of days to look back
        
    Returns:
        Dictionary of activity statistics
    """
    from datetime import timedelta
    from sqlalchemy import func
    
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    total_activities = AuditLog.query.filter(
        AuditLog.timestamp >= cutoff_date
    ).count()
    
    action_counts = db.session.query(
        AuditLog.action,
        func.count(AuditLog.id).label('count')
    ).filter(
        AuditLog.timestamp >= cutoff_date
    ).group_by(AuditLog.action).all()
    
    action_breakdown = {action: count for action, count in action_counts}
    
    gemini_success = action_breakdown.get('gemini_success', 0)
    gemini_error = action_breakdown.get('gemini_error', 0)
    total_scans = gemini_success + gemini_error
    
    return {
        'total_activities': total_activities,
        'action_breakdown': action_breakdown,
        'login_count': action_breakdown.get('user_login', 0),
        'registration_count': action_breakdown.get('user_register', 0),
        'receipt_scans': total_scans,
        'scan_success_rate': round(gemini_success / max(total_scans, 1) * 100, 1),
        'error_count': sum(1 for a in action_breakdown.keys() if 'error' in a.lower() or 'failed' in a.lower())
    }


def get_gemini_api_stats(days: int = 7):
    """
    Get Gemini API usage statistics.
    
    Args:
        days: Number of days to look back
        
    Returns:
        Dictionary of API statistics
    """
    from datetime import timedelta
    from sqlalchemy import func
    
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    gemini_logs = AuditLog.query.filter(
        AuditLog.timestamp >= cutoff_date,
        AuditLog.action.in_(['gemini_success', 'gemini_error'])
    ).all()
    
    total_calls = len(gemini_logs)
    success_count = sum(1 for log in gemini_logs if log.action == 'gemini_success')
    error_count = total_calls - success_count
    
    error_categories = {}
    model_usage = {}
    total_processing_time = 0
    processing_count = 0
    
    for log in gemini_logs:
        if log.changed_fields:
            if log.action == 'gemini_error':
                category = log.changed_fields.get('error_category', 'unknown')
                error_categories[category] = error_categories.get(category, 0) + 1
            
            model = log.changed_fields.get('model')
            if model:
                model_usage[model] = model_usage.get(model, 0) + 1
            
            proc_time = log.changed_fields.get('processing_time_ms')
            if proc_time:
                total_processing_time += proc_time
                processing_count += 1
    
    return {
        'total_calls': total_calls,
        'success_count': success_count,
        'error_count': error_count,
        'success_rate': round(success_count / max(total_calls, 1) * 100, 1),
        'error_categories': error_categories,
        'model_usage': model_usage,
        'avg_processing_time_ms': round(total_processing_time / max(processing_count, 1)),
        'days_analyzed': days
    }
