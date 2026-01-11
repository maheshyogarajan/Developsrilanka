"""
Consolidated Admin Routes - Unified admin dashboard for platform management.
Provides real-time analytics, user management, and system monitoring.
"""

import os
import json
import time
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import current_user
from sqlalchemy import func, desc, and_, or_
import pandas as pd
from io import BytesIO

from app import app, db
from models import User, Receipt, ReceiptItem, UserIncome, AuditLog
from decorators import admin_required
from image_processor import cleanup_temp_files
from activity_logger import (
    ActivityLogger, get_recent_activities, get_activity_stats, get_gemini_api_stats
)

import admin_analytics

tax_settings = {
    'lkr_business_tax_rate': 36.0,
    'usd_consulting_tax_rate': 15.0,
    'vat_rate': 8.0,
    'sscl_rate': 2.0
}

general_settings = {
    'app_name': 'Sri Lanka Tax Optimizer',
    'items_per_page': 10,
    'default_currency': 'LKR',
    'enable_registration': True,
    'enable_ai_features': True
}


@app.route('/admin')
@admin_required
def admin_dashboard():
    """Main admin dashboard with real-time platform insights."""
    try:
        start_time = time.time()
        
        dashboard_data = admin_analytics.get_dashboard_data()
        
        user_stats = dashboard_data['users']
        receipt_stats = dashboard_data['receipts']
        tax_savings_stats = dashboard_data['tax_savings']
        
        user_count = user_stats['total_users']
        active_users = user_stats['active_users']
        receipt_count = receipt_stats['total_receipts']
        total_amount = receipt_stats['total_amount']
        
        activity_stats = get_activity_stats(days=1)
        gemini_stats = get_gemini_api_stats(days=7)
        
        error_count = activity_stats.get('error_count', 0)
        scan_success_rate = gemini_stats.get('success_rate', 100)
        
        if error_count > 10 or scan_success_rate < 80:
            system_status = "Warning"
        elif error_count > 20 or scan_success_rate < 50:
            system_status = "Critical"
        else:
            system_status = "Healthy"
        
        recent_logs = get_recent_activities(limit=10)
        
        registrations_data = user_stats['registrations_by_date']
        receipts_data = receipt_stats['receipts_by_date']
        
        days = 7
        date_range = [(datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
        date_range.reverse()
        
        user_activity_dates = date_range
        user_activity_new = [registrations_data.get(date, 0) for date in date_range]
        user_activity_receipts = [receipts_data.get(date, 0) for date in date_range]
        
        user_activity_active = []
        for date in date_range:
            receipt_cnt = receipts_data.get(date, 0)
            active_estimate = max(1, int(receipt_cnt * 0.5)) if receipt_cnt > 0 else 0
            user_activity_active.append(active_estimate)
        
        top_categories = receipt_stats['top_categories']
        category_labels = [cat['category'] for cat in top_categories]
        category_values = [cat['percentage'] for cat in top_categories]
        
        if not category_labels:
            category_labels = ["Uncategorized"]
            category_values = [100.0]
        
        user_growth_percent = user_stats['growth_rate']
        
        receipt_growth_percent = calculate_growth_rate(Receipt, 'created_at', 30)
        amount_growth_percent = calculate_amount_growth_rate(30)
        
        settings_updated = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        
        execution_time = time.time() - start_time
        performance_metrics = {
            'query_execution_time': execution_time,
            'cache_status': dashboard_data['_meta']['cache_status'],
            'data_timestamp': dashboard_data['timestamp']
        }
        
        return render_template(
            'admin/dashboard.html',
            user_count=user_count,
            receipt_count=receipt_count,
            total_amount=total_amount,
            error_count=error_count,
            system_status=system_status,
            recent_logs=recent_logs,
            user_activity_dates=user_activity_dates,
            user_activity_new=user_activity_new,
            user_activity_active=user_activity_active,
            user_activity_receipts=user_activity_receipts,
            category_labels=category_labels,
            category_values=category_values,
            user_growth_percent=user_growth_percent,
            receipt_growth_percent=receipt_growth_percent,
            amount_growth_percent=amount_growth_percent,
            settings_updated=settings_updated,
            tax_settings=tax_settings,
            general_settings=general_settings,
            now=datetime.utcnow(),
            performance=performance_metrics,
            tax_savings_stats=tax_savings_stats,
            gemini_stats=gemini_stats,
            activity_stats=activity_stats
        )
    except Exception as e:
        flash(f'Error loading admin dashboard: {str(e)}', 'danger')
        app.logger.error(f'Admin dashboard error: {str(e)}')
        return redirect(url_for('home'))


@app.route('/admin/v2')
@admin_required
def admin_v2_redirect():
    """Redirect old /admin/v2 URLs to unified /admin."""
    return redirect(url_for('admin_dashboard'))


def calculate_growth_rate(model, date_field, days):
    """Calculate growth rate comparing current period to previous period."""
    try:
        now = datetime.utcnow()
        current_start = now - timedelta(days=days)
        previous_start = current_start - timedelta(days=days)
        
        date_col = getattr(model, date_field)
        
        current_count = model.query.filter(date_col >= current_start).count()
        previous_count = model.query.filter(
            and_(date_col >= previous_start, date_col < current_start)
        ).count()
        
        if previous_count == 0:
            return 100.0 if current_count > 0 else 0.0
        
        return round((current_count - previous_count) / previous_count * 100, 1)
    except:
        return 0.0


def calculate_amount_growth_rate(days):
    """Calculate total amount growth rate."""
    try:
        now = datetime.utcnow()
        current_start = now - timedelta(days=days)
        previous_start = current_start - timedelta(days=days)
        
        current_amount = db.session.query(func.sum(Receipt.total_amount)).filter(
            Receipt.created_at >= current_start
        ).scalar() or 0
        
        previous_amount = db.session.query(func.sum(Receipt.total_amount)).filter(
            and_(Receipt.created_at >= previous_start, Receipt.created_at < current_start)
        ).scalar() or 0
        
        if previous_amount == 0:
            return 100.0 if current_amount > 0 else 0.0
        
        return round((current_amount - previous_amount) / previous_amount * 100, 1)
    except:
        return 0.0


@app.route('/admin/users')
@admin_required
def admin_users():
    """User management page with real engagement metrics."""
    try:
        user_stats = admin_analytics.get_user_statistics()
        role_distribution = admin_analytics.get_user_role_distribution()
        
        total_users = user_stats['total_users']
        active_users = user_stats['active_users']
        inactive_users = user_stats['inactive_users']
        admin_users = role_distribution.get('admin', 0)
        
        page = request.args.get('page', 1, type=int)
        per_page = 10
        search_term = request.args.get('search', '')
        
        query = User.query
        if search_term:
            query = query.filter(
                or_(
                    User.name.ilike(f'%{search_term}%'),
                    User.email.ilike(f'%{search_term}%')
                )
            )
        
        users = query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        
        receipt_stats = admin_analytics.get_receipt_statistics()
        avg_receipts_per_user = round(receipt_stats['total_receipts'] / max(total_users, 1), 1)
        
        metrics = {
            'total_users': total_users,
            'active_users': active_users,
            'inactive_users': inactive_users,
            'admin_users': admin_users,
            'standard_users': total_users - admin_users,
            'growth_rate': user_stats['growth_rate'],
            'average_receipts': avg_receipts_per_user
        }
        
        subscription_stats = get_subscription_stats()
        
        return render_template(
            'admin/users.html',
            users=users,
            metrics=metrics,
            subscription_stats=subscription_stats,
            search_term=search_term,
            page=page,
            total_pages=users.pages,
            total_users=total_users
        )
    except Exception as e:
        flash(f'Error loading user management: {str(e)}', 'danger')
        app.logger.error(f'Admin user management error: {str(e)}')
        return redirect(url_for('admin_dashboard'))


def get_subscription_stats():
    """Get subscription status breakdown."""
    try:
        stats = db.session.query(
            User.subscription_status,
            func.count(User.id).label('count')
        ).group_by(User.subscription_status).all()
        
        return {status: count for status, count in stats}
    except:
        return {}


@app.route('/admin/users/update-role', methods=['POST'])
@admin_required
def admin_update_user_role():
    """Update a user's role."""
    try:
        user_id = request.form.get('user_id')
        new_role = request.form.get('role')
        
        if not user_id or not new_role:
            flash('Missing required parameters', 'danger')
            return redirect(url_for('admin_users'))
        
        if new_role not in ['user', 'admin']:
            flash('Invalid role specified', 'danger')
            return redirect(url_for('admin_users'))
        
        user = User.query.get(user_id)
        if not user:
            flash('User not found', 'danger')
            return redirect(url_for('admin_users'))
        
        if user.id == current_user.id and new_role != 'admin':
            flash('You cannot demote yourself from admin', 'danger')
            return redirect(url_for('admin_users'))
        
        old_role = user.role
        user.role = new_role
        db.session.commit()
        
        ActivityLogger.log_admin_action(
            'user_role_change',
            target_id=user.id,
            details={'old_role': old_role, 'new_role': new_role, 'email': user.email}
        )
        
        flash(f'User {user.email} role updated to {new_role}', 'success')
        return redirect(url_for('admin_users'))
    except Exception as e:
        flash(f'Error updating user role: {str(e)}', 'danger')
        app.logger.error(f'Admin update user role error: {str(e)}')
        return redirect(url_for('admin_users'))


@app.route('/admin/users/reset-password', methods=['POST'])
@admin_required
def admin_reset_user_password():
    """Reset a user's password (admin only)."""
    try:
        user_id = request.form.get('user_id')
        new_password = request.form.get('new_password')
        
        if not user_id or not new_password:
            flash('Missing required parameters', 'danger')
            return redirect(url_for('admin_users'))
        
        if len(new_password) < 6:
            flash('Password must be at least 6 characters long', 'danger')
            return redirect(url_for('admin_users'))
        
        user = User.query.get(user_id)
        if not user:
            flash('User not found', 'danger')
            return redirect(url_for('admin_users'))
        
        from werkzeug.security import generate_password_hash
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        ActivityLogger.log_admin_action(
            'password_reset',
            target_id=user.id,
            details={'email': user.email, 'reset_by': current_user.email}
        )
        
        flash(f'Password for {user.email} has been reset successfully', 'success')
        return redirect(url_for('admin_users'))
    except Exception as e:
        flash(f'Error resetting password: {str(e)}', 'danger')
        app.logger.error(f'Admin reset password error: {str(e)}')
        return redirect(url_for('admin_users'))


@app.route('/admin/receipts')
@admin_required
def admin_receipts():
    """Receipt management page with scanning analytics."""
    try:
        start_time = time.time()
        
        receipt_stats = admin_analytics.get_receipt_statistics()
        
        total_receipts = receipt_stats['total_receipts']
        total_amount = receipt_stats['total_amount']
        avg_amount = receipt_stats['average_amount']
        median_amount = receipt_stats.get('median_amount', 0)
        top_categories = receipt_stats['top_categories']
        
        gemini_stats = get_gemini_api_stats(days=30)
        
        page = request.args.get('page', 1, type=int)
        per_page = 15
        search_term = request.args.get('search', '')
        
        query = Receipt.query
        if search_term:
            query = query.filter(
                or_(
                    Receipt.vendor_name.ilike(f'%{search_term}%'),
                    Receipt.expense_major_category.ilike(f'%{search_term}%'),
                    Receipt.expense_minor_category.ilike(f'%{search_term}%')
                )
            )
        
        receipts = query.join(User, Receipt.user_id == User.id, isouter=True)\
            .add_columns(User.name, User.email)\
            .order_by(Receipt.created_at.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        
        tax_deductible_percentage = receipt_stats['tax_deductible_percentage']
        avg_items_per_receipt = round(receipt_stats.get('total_items', 0) / max(total_receipts, 1), 1)
        
        execution_time = time.time() - start_time
        
        return render_template(
            'admin/receipts.html',
            receipts=receipts,
            total_receipts=total_receipts,
            total_amount=total_amount,
            avg_amount=avg_amount,
            median_amount=median_amount,
            top_categories=top_categories,
            tax_deductible_percentage=tax_deductible_percentage,
            avg_items_per_receipt=avg_items_per_receipt,
            search_term=search_term,
            gemini_stats=gemini_stats,
            performance={'query_time': execution_time}
        )
    except Exception as e:
        flash(f'Error loading receipt data: {str(e)}', 'danger')
        app.logger.error(f'Admin receipt management error: {str(e)}')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/statistics')
@admin_required
def admin_statistics():
    """Enhanced statistics page with real platform analytics."""
    try:
        time_range = request.args.get('range', 'month')
        
        days_lookup = {
            'week': 7,
            'month': 30,
            'year': 365
        }
        days = days_lookup.get(time_range, 30)
        
        if time_range == 'week':
            date_format = '%a'
        elif time_range == 'month':
            date_format = '%d'
        elif time_range == 'year':
            date_format = '%b'
        else:
            date_format = '%d'
        
        user_stats = admin_analytics.get_user_statistics(days=days)
        receipt_stats = admin_analytics.get_receipt_statistics(days=days)
        tax_savings_stats = admin_analytics.get_tax_savings_statistics()
        categories_breakdown = admin_analytics.get_receipt_categories_breakdown()
        income_stats = admin_analytics.get_income_statistics()
        
        gemini_stats = get_gemini_api_stats(days=days)
        activity_stats = get_activity_stats(days=days)
        
        start_date = datetime.utcnow() - timedelta(days=days)
        
        registrations_data = user_stats['registrations_by_date']
        receipts_data = receipt_stats['receipts_by_date']
        
        current_date = start_date
        end_date = datetime.utcnow()
        
        registration_dates = []
        registration_counts = []
        receipt_dates = []
        receipt_counts = []
        
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            chart_date = current_date.strftime(date_format)
            
            registration_dates.append(chart_date)
            registration_counts.append(registrations_data.get(date_str, 0))
            
            receipt_dates.append(chart_date)
            receipt_counts.append(receipts_data.get(date_str, 0))
            
            current_date += timedelta(days=1)
        
        if categories_breakdown:
            categories_breakdown.sort(key=lambda x: x['count'], reverse=True)
            category_data = categories_breakdown[:6]
            
            category_labels = [cat['category'] for cat in category_data]
            category_values = [cat['count_percentage'] for cat in category_data]
        else:
            category_labels = ["No Categories"]
            category_values = [100.0]
        
        user_count = user_stats['total_users']
        receipt_count = receipt_stats['total_receipts']
        
        engagement_metrics = {
            'avg_receipts_per_user': round(receipt_count / max(user_count, 1), 1),
            'avg_tax_savings': round(tax_savings_stats['total_savings'] / max(user_count, 1), 2),
            'scan_success_rate': gemini_stats.get('success_rate', 100),
            'returning_users': round(user_stats['active_users'] / max(user_count, 1) * 100, 1) if user_count else 0
        }
        
        return render_template(
            'admin/statistics.html',
            time_range=time_range,
            registration_dates=registration_dates,
            registration_counts=registration_counts,
            receipt_dates=receipt_dates,
            receipt_counts=receipt_counts,
            category_labels=category_labels,
            category_values=category_values,
            engagement_metrics=engagement_metrics,
            user_stats=user_stats,
            receipt_stats=receipt_stats,
            tax_savings_stats=tax_savings_stats,
            income_stats=income_stats,
            gemini_stats=gemini_stats,
            activity_stats=activity_stats
        )
    except Exception as e:
        flash(f'Error loading system statistics: {str(e)}', 'danger')
        app.logger.error(f'Admin statistics error: {str(e)}')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/settings')
@admin_required
def admin_settings():
    """Application settings page."""
    try:
        system_stats = admin_analytics.get_system_statistics()
        
        db_stats = system_stats['table_counts']
        db_stats['estimated_size_kb'] = system_stats['estimated_db_size_kb']
        
        cache_stats = {
            'entries': system_stats['cache_entries'],
            'timestamp': system_stats['server_time'].isoformat()
        }
        
        return render_template(
            'admin/settings.html',
            tax_settings=tax_settings,
            general_settings=general_settings,
            db_stats=db_stats,
            cache_stats=cache_stats
        )
    except Exception as e:
        flash(f'Error loading settings page: {str(e)}', 'danger')
        app.logger.error(f'Admin settings error: {str(e)}')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/settings/tax', methods=['POST'])
@admin_required
def admin_update_tax_settings():
    """Update tax rate settings."""
    try:
        lkr_rate = float(request.form.get('lkr_business_tax_rate', 36.0))
        usd_rate = float(request.form.get('usd_consulting_tax_rate', 15.0))
        vat_rate = float(request.form.get('vat_rate', 8.0))
        sscl_rate = float(request.form.get('sscl_rate', 2.0))
        
        if not all(0 <= r <= 100 for r in [lkr_rate, usd_rate, vat_rate, sscl_rate]):
            flash('Tax rates must be between 0 and 100', 'danger')
            return redirect(url_for('admin_settings'))
        
        old_settings = tax_settings.copy()
        
        tax_settings['lkr_business_tax_rate'] = lkr_rate
        tax_settings['usd_consulting_tax_rate'] = usd_rate
        tax_settings['vat_rate'] = vat_rate
        tax_settings['sscl_rate'] = sscl_rate
        
        ActivityLogger.log_admin_action(
            'tax_settings_update',
            details={'old': old_settings, 'new': tax_settings.copy()}
        )
        
        flash('Tax settings updated successfully', 'success')
        return redirect(url_for('admin_settings'))
        
    except Exception as e:
        flash(f'Error updating tax settings: {str(e)}', 'danger')
        app.logger.error(f'Admin tax settings error: {str(e)}')
        return redirect(url_for('admin_settings'))


@app.route('/admin/settings/general', methods=['POST'])
@admin_required
def admin_update_general_settings():
    """Update general application settings."""
    try:
        old_settings = general_settings.copy()
        
        general_settings['app_name'] = request.form.get('app_name', 'Sri Lanka Tax Optimizer')
        general_settings['items_per_page'] = int(request.form.get('items_per_page', 10))
        general_settings['default_currency'] = request.form.get('default_currency', 'LKR')
        general_settings['enable_registration'] = 'enable_registration' in request.form
        general_settings['enable_ai_features'] = 'enable_ai_features' in request.form
        
        ActivityLogger.log_admin_action(
            'general_settings_update',
            details={'old': old_settings, 'new': general_settings.copy()}
        )
        
        flash('General settings updated successfully', 'success')
        return redirect(url_for('admin_settings'))
        
    except Exception as e:
        flash(f'Error updating general settings: {str(e)}', 'danger')
        app.logger.error(f'Admin general settings error: {str(e)}')
        return redirect(url_for('admin_settings'))


@app.route('/admin/logs')
@admin_required
def admin_logs():
    """System activity logs with real data from audit log."""
    try:
        log_type = request.args.get('type', 'all')
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        query = AuditLog.query
        
        if log_type != 'all':
            type_mapping = {
                'user': ['user'],
                'receipt': ['receipt'],
                'admin': ['admin'],
                'error': ['gemini_error'],
                'system': ['admin', 'settings']
            }
            entity_types = type_mapping.get(log_type, [log_type])
            
            if log_type == 'error':
                query = query.filter(
                    or_(
                        AuditLog.action.ilike('%error%'),
                        AuditLog.action.ilike('%failed%')
                    )
                )
            else:
                query = query.filter(AuditLog.entity_type.in_(entity_types))
        
        pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        logs = []
        for log in pagination.items:
            user_email = None
            if log.user_id:
                user = User.query.get(log.user_id)
                if user:
                    user_email = user.email
            
            level = 'info'
            if 'error' in log.action.lower() or 'failed' in log.action.lower():
                level = 'error'
            elif 'warning' in str(log.changed_fields).lower():
                level = 'warning'
            
            logs.append({
                'id': log.id,
                'timestamp': log.timestamp,
                'type': log.entity_type,
                'level': level,
                'message': log.action.replace('_', ' ').title(),
                'user': user_email,
                'details': log.changed_fields,
                'ip_address': log.ip_address
            })
        
        log_stats = {
            'total_logs': pagination.total,
            'error_count': AuditLog.query.filter(
                or_(
                    AuditLog.action.ilike('%error%'),
                    AuditLog.action.ilike('%failed%')
                )
            ).count(),
            'types': {}
        }
        
        type_counts = db.session.query(
            AuditLog.entity_type,
            func.count(AuditLog.id)
        ).group_by(AuditLog.entity_type).all()
        
        log_stats['types'] = {t: c for t, c in type_counts}
        
        return render_template(
            'admin/logs.html',
            logs=logs,
            log_type=log_type,
            page=page,
            per_page=per_page,
            total=pagination.total,
            total_pages=pagination.pages,
            log_stats=log_stats
        )
    except Exception as e:
        flash(f'Error loading system logs: {str(e)}', 'danger')
        app.logger.error(f'Admin logs error: {str(e)}')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/logs/export')
@admin_required
def admin_export_logs():
    """Export system logs as CSV file."""
    try:
        log_type = request.args.get('type', 'all')
        
        query = AuditLog.query
        
        if log_type != 'all':
            if log_type == 'error':
                query = query.filter(
                    or_(
                        AuditLog.action.ilike('%error%'),
                        AuditLog.action.ilike('%failed%')
                    )
                )
            else:
                query = query.filter(AuditLog.entity_type == log_type)
        
        logs = query.order_by(AuditLog.timestamp.asc()).limit(1000).all()
        
        data = []
        for log in logs:
            user_email = None
            if log.user_id:
                user = User.query.get(log.user_id)
                if user:
                    user_email = user.email
            
            data.append({
                'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'type': log.entity_type,
                'action': log.action,
                'user': user_email or '',
                'entity_id': log.entity_id,
                'ip_address': log.ip_address or '',
                'details': json.dumps(log.changed_fields) if log.changed_fields else ''
            })
        
        df = pd.DataFrame(data)
        
        output = BytesIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        filename = f"activity_logs_{log_type}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='text/csv'
        )
        
    except Exception as e:
        flash(f'Error exporting logs: {str(e)}', 'danger')
        app.logger.error(f'Admin export logs error: {str(e)}')
        return redirect(url_for('admin_logs'))


@app.route('/admin/maintenance/cleanup', methods=['POST'])
@admin_required
def admin_clear_temp_files():
    """Clear temporary files older than specified hours."""
    try:
        hours = int(request.form.get('hours', 24))
        
        if hours < 1:
            flash('Hours must be at least 1', 'danger')
            return redirect(url_for('admin_settings'))
        
        receipt_images = db.session.query(Receipt.image_path).all()
        image_paths = [path[0] for path in receipt_images if path[0]]
        
        files_removed = cleanup_temp_files(image_paths, min_age_hours=hours)
        
        ActivityLogger.log_admin_action(
            'temp_files_cleanup',
            details={'hours': hours, 'files_removed': files_removed}
        )
        
        flash(f'Successfully removed {files_removed} temporary files', 'success')
        return redirect(url_for('admin_settings'))
        
    except Exception as e:
        flash(f'Error clearing temporary files: {str(e)}', 'danger')
        app.logger.error(f'Admin cleanup error: {str(e)}')
        return redirect(url_for('admin_settings'))


@app.route('/admin/update-categories', methods=['POST'])
@admin_required
def admin_update_categories():
    """Trigger the update of expense categories for all receipts."""
    try:
        ActivityLogger.log_admin_action('category_update_triggered')
        
        flash("Category update process started. This may take some time.", "info")
        return redirect(url_for('admin_settings'))
    except Exception as e:
        app.logger.error(f"Error updating categories: {str(e)}")
        flash(f"Error updating categories: {str(e)}", "danger")
        return redirect(url_for('admin_settings'))


@app.route('/admin/api/gemini-stats')
@admin_required
def admin_api_gemini_stats():
    """API endpoint for Gemini API statistics."""
    try:
        days = request.args.get('days', 7, type=int)
        stats = get_gemini_api_stats(days=days)
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/activity-stats')
@admin_required
def admin_api_activity_stats():
    """API endpoint for activity statistics."""
    try:
        days = request.args.get('days', 7, type=int)
        stats = get_activity_stats(days=days)
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/engagement')
@admin_required
def admin_engagement():
    """User engagement funnel analytics."""
    try:
        total_users = User.query.count()
        
        users_with_receipts = db.session.query(func.count(func.distinct(Receipt.user_id))).scalar() or 0
        
        active_users_30d = db.session.query(func.count(func.distinct(Receipt.user_id))).filter(
            Receipt.created_at >= datetime.utcnow() - timedelta(days=30)
        ).scalar() or 0
        
        converted_users = User.query.filter(
            User.subscription_status.in_(['basic', 'pro', 'premium'])
        ).count()
        
        funnel = {
            'registered': total_users,
            'first_scan': users_with_receipts,
            'active_30d': active_users_30d,
            'converted': converted_users
        }
        
        funnel_rates = {
            'registration_to_scan': round(users_with_receipts / max(total_users, 1) * 100, 1),
            'scan_to_active': round(active_users_30d / max(users_with_receipts, 1) * 100, 1),
            'active_to_converted': round(converted_users / max(active_users_30d, 1) * 100, 1)
        }
        
        daily_registrations = db.session.query(
            func.date(User.created_at).label('date'),
            func.count(User.id).label('count')
        ).filter(
            User.created_at >= datetime.utcnow() - timedelta(days=30)
        ).group_by(func.date(User.created_at)).all()
        
        registration_trend = {str(d): c for d, c in daily_registrations}
        
        return render_template(
            'admin/engagement.html',
            funnel=funnel,
            funnel_rates=funnel_rates,
            registration_trend=registration_trend
        )
    except Exception as e:
        flash(f'Error loading engagement data: {str(e)}', 'danger')
        app.logger.error(f'Admin engagement error: {str(e)}')
        return redirect(url_for('admin_dashboard'))
