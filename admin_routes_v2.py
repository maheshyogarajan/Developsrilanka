"""
Enhanced admin routes for the application v2.
These routes are protected by the admin_required decorator.
"""

import os
import json
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import current_user
from sqlalchemy import func, desc, and_, or_
import pandas as pd
from io import BytesIO
import random

from app import app, db
from models import User, Receipt, ReceiptItem, UserIncome
from decorators import admin_required
from image_processor import cleanup_temp_files

# Mock data structure for application settings
# In a real application, these would be stored in the database
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

# Enhanced Admin Dashboard
@app.route('/admin/v2')
@admin_required
def admin_panel_home():
    """Enhanced admin dashboard with overview of the application."""
    try:
        # Get counts for the dashboard
        user_count = User.query.count()
        receipt_count = Receipt.query.count()
        
        # Calculate total amount processed
        total_amount_result = db.session.query(func.sum(Receipt.total_amount)).scalar()
        total_amount = total_amount_result if total_amount_result else 0.0
        
        # System health metrics
        error_count = random.randint(0, 5)  # Mock data - would be real in production
        system_status = "Healthy" if error_count < 3 else "Warning"
        
        # Recent system activity logs (mock data for now)
        recent_logs = []
        log_events = [
            "User login", "Receipt scanned", "Tax calculation", "User registration", 
            "Settings updated", "Categories updated", "Export completed"
        ]
        log_statuses = [
            {"status": "Success", "color": "success"},
            {"status": "Warning", "color": "warning"},
            {"status": "Error", "color": "danger"},
            {"status": "Info", "color": "info"}
        ]
        
        # Get some users for log entries
        users = User.query.order_by(func.random()).limit(5).all()
        user_names = [user.name for user in users] if users else ["Anonymous"]
        
        # Generate mock log entries
        for i in range(10):
            status_data = random.choice(log_statuses)
            # Higher probability of success entries
            if random.random() < 0.7:
                status_data = log_statuses[0]
                
            time_delta = timedelta(
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
                seconds=random.randint(0, 59)
            )
            
            recent_logs.append({
                "event": random.choice(log_events),
                "user": random.choice(user_names),
                "timestamp": datetime.utcnow() - time_delta,
                "status": status_data["status"],
                "status_color": status_data["color"]
            })
        
        # Sort logs by timestamp (most recent first)
        recent_logs.sort(key=lambda x: x["timestamp"], reverse=True)
        
        # Activity chart data (mock data for demonstration)
        days = 7
        user_activity_dates = [(datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
        user_activity_dates.reverse()  # Oldest date first
        
        user_activity_new = [random.randint(1, 10) for _ in range(days)]
        user_activity_active = [random.randint(5, 30) for _ in range(days)]
        user_activity_receipts = [random.randint(10, 50) for _ in range(days)]
        
        # Category data for pie chart (mock data)
        category_labels = [
            "Office Supplies", "Travel", "Meals", "Utilities", 
            "Software", "Hardware"
        ]
        category_values = [
            random.randint(5, 30),
            random.randint(10, 40),
            random.randint(15, 35),
            random.randint(8, 25),
            random.randint(10, 30),
            random.randint(5, 20)
        ]
        
        # Growth percentages (mock data)
        user_growth_percent = random.uniform(1.5, 15.0)
        receipt_growth_percent = random.uniform(5.0, 25.0)
        amount_growth_percent = random.uniform(8.0, 30.0)
        
        # Settings update timestamp
        settings_updated = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        
        return render_template(
            'admin_v2/dashboard.html',
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
            now=datetime.utcnow()
        )
    except Exception as e:
        flash(f'Error loading admin dashboard: {str(e)}', 'danger')
        app.logger.error(f'Admin dashboard error: {str(e)}')
        return redirect(url_for('home'))

# User Management
@app.route('/admin/v2/users')
@admin_required
def admin_panel_users():
    """Enhanced user management page for admins."""
    try:
        # Get basic user stats
        total_users = User.query.count()
        active_users = User.query.filter(User.last_login >= (datetime.utcnow() - timedelta(days=30))).count()
        admin_users = User.query.filter(User.role == 'admin').count()
        
        # Get all users with pagination
        page = request.args.get('page', 1, type=int)
        per_page = 10
        
        users = User.query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        
        # Calculate user metrics (mock data for demonstration)
        metrics = {
            'total_users': total_users,
            'active_users': active_users,
            'inactive_users': total_users - active_users,
            'admin_users': admin_users,
            'standard_users': total_users - admin_users,
            'growth_rate': round(random.uniform(2.5, 15.0), 1),
            'average_receipts': round(random.uniform(8.5, 25.0), 1)
        }
        
        return render_template(
            'admin_v2/users.html',
            users=users,
            metrics=metrics
        )
    except Exception as e:
        flash(f'Error loading user management: {str(e)}', 'danger')
        app.logger.error(f'Admin user management error: {str(e)}')
        return redirect(url_for('admin_panel_home'))

# Update User Role 
@app.route('/admin/v2/users/update-role', methods=['POST'])
@admin_required
def admin_panel_update_user_role():
    """Update a user's role."""
    try:
        user_id = request.form.get('user_id')
        new_role = request.form.get('role')
        
        # Validate inputs
        if not user_id or not new_role:
            flash('Missing required parameters', 'danger')
            return redirect(url_for('admin_panel_users'))
        
        if new_role not in ['user', 'admin']:
            flash('Invalid role specified', 'danger')
            return redirect(url_for('admin_panel_users'))
            
        user = User.query.get(user_id)
        if not user:
            flash('User not found', 'danger')
            return redirect(url_for('admin_panel_users'))
            
        # Can't demote yourself
        if user.id == current_user.id and new_role != 'admin':
            flash('You cannot demote yourself from admin', 'danger')
            return redirect(url_for('admin_panel_users'))
            
        # Update the role
        user.role = new_role
        db.session.commit()
        
        flash(f'User {user.email} role updated to {new_role}', 'success')
        return redirect(url_for('admin_panel_users'))
    except Exception as e:
        flash(f'Error updating user role: {str(e)}', 'danger')
        app.logger.error(f'Admin update user role error: {str(e)}')
        return redirect(url_for('admin_panel_users'))

# Receipt Management
@app.route('/admin/v2/receipts')
@admin_required
def admin_panel_receipts():
    """Receipt data management page for admins."""
    try:
        # Get receipt statistics
        total_receipts = Receipt.query.count()
        total_amount = db.session.query(func.sum(Receipt.total_amount)).scalar() or 0
        avg_amount = db.session.query(func.avg(Receipt.total_amount)).scalar() or 0
        
        # Get top categories
        category_counts = db.session.query(
            Receipt.expense_major_category, 
            func.count(Receipt.id).label('count'),
            func.sum(Receipt.total_amount).label('total')
        ).group_by(Receipt.expense_major_category).order_by(desc('count')).limit(5).all()
        
        top_categories = []
        for category, count, amount in category_counts:
            if category:  # Skip None categories
                top_categories.append({
                    'category': category,
                    'count': count,
                    'amount': amount,
                    'percentage': (count / total_receipts * 100) if total_receipts > 0 else 0
                })
        
        # Get receipts with pagination
        page = request.args.get('page', 1, type=int)
        per_page = 10
        search_term = request.args.get('search', '')
        
        query = Receipt.query
        
        # Apply search filter if provided
        if search_term:
            query = query.filter(
                or_(
                    Receipt.vendor_name.ilike(f'%{search_term}%'),
                    Receipt.expense_major_category.ilike(f'%{search_term}%'),
                    Receipt.expense_minor_category.ilike(f'%{search_term}%')
                )
            )
        
        # Get receipts with user information
        receipts = query.join(User, Receipt.user_id == User.id, isouter=True)\
            .add_columns(User.name, User.email)\
            .order_by(Receipt.created_at.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        
        return render_template(
            'admin_v2/receipts.html',
            receipts=receipts,
            total_receipts=total_receipts,
            total_amount=total_amount,
            avg_amount=avg_amount,
            top_categories=top_categories,
            search_term=search_term
        )
    except Exception as e:
        flash(f'Error loading receipt data: {str(e)}', 'danger')
        app.logger.error(f'Admin receipt management error: {str(e)}')
        return redirect(url_for('admin_panel_home'))

# System Statistics
@app.route('/admin/v2/statistics')
@admin_required
def admin_panel_statistics():
    """Enhanced system statistics page for admins."""
    try:
        # Time ranges for statistics
        time_range = request.args.get('range', 'month')
        
        if time_range == 'week':
            start_date = datetime.utcnow() - timedelta(days=7)
            date_format = '%a'  # Day of week abbreviation
        elif time_range == 'month':
            start_date = datetime.utcnow() - timedelta(days=30)
            date_format = '%d'  # Day of month
        elif time_range == 'year':
            start_date = datetime.utcnow() - timedelta(days=365)
            date_format = '%b'  # Month abbreviation
        else:
            start_date = datetime.utcnow() - timedelta(days=30)
            date_format = '%d'
        
        # User registration statistics
        user_registrations = db.session.query(
            func.date_trunc('day', User.created_at).label('day'),
            func.count(User.id).label('count')
        ).filter(User.created_at >= start_date)\
        .group_by('day')\
        .order_by('day')\
        .all()
        
        # Convert to format for charting
        registration_dates = []
        registration_counts = []
        
        # Fill in missing dates with zeros
        current_date = start_date
        end_date = datetime.utcnow()
        
        reg_dict = {day.strftime('%Y-%m-%d'): count for day, count in user_registrations}
        
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            chart_date = current_date.strftime(date_format)
            
            registration_dates.append(chart_date)
            registration_counts.append(reg_dict.get(date_str, 0))
            
            current_date += timedelta(days=1)
        
        # Receipt scanning statistics (mock data for demonstration)
        receipt_dates = registration_dates.copy()
        receipt_counts = [int(count * random.uniform(1.5, 3.0)) for count in registration_counts]
        
        # Tax savings statistics (mock data for demonstration)
        savings_dates = registration_dates.copy()
        savings_amounts = [float(count) * random.uniform(1000, 5000) for count in registration_counts]
        
        # Category distribution (mock data)
        category_labels = [
            "Office Supplies", "Travel", "Meals", "Utilities", 
            "Software", "Hardware"
        ]
        category_values = [
            random.randint(15, 30),
            random.randint(20, 40),
            random.randint(25, 45),
            random.randint(10, 25),
            random.randint(15, 30),
            random.randint(10, 20)
        ]
        
        # User engagement metrics (mock data)
        engagement_metrics = {
            'avg_receipts_per_user': round(random.uniform(5.0, 15.0), 1),
            'avg_session_duration': round(random.uniform(3.0, 10.0), 1),
            'avg_tax_savings': round(random.uniform(5000, 20000), 2),
            'returning_users': round(random.uniform(65.0, 85.0), 1)
        }
        
        return render_template(
            'admin_v2/statistics.html',
            time_range=time_range,
            registration_dates=registration_dates,
            registration_counts=registration_counts,
            receipt_dates=receipt_dates,
            receipt_counts=receipt_counts,
            savings_dates=savings_dates,
            savings_amounts=savings_amounts,
            category_labels=category_labels,
            category_values=category_values,
            engagement_metrics=engagement_metrics
        )
    except Exception as e:
        flash(f'Error loading system statistics: {str(e)}', 'danger')
        app.logger.error(f'Admin statistics error: {str(e)}')
        return redirect(url_for('admin_panel_home'))

# Application Settings
@app.route('/admin/v2/settings')
@admin_required
def admin_panel_settings():
    """Enhanced application settings page for admins."""
    try:
        # Get real database stats for context
        db_stats = {
            'user_count': User.query.count(),
            'receipt_count': Receipt.query.count(),
            'receipt_item_count': ReceiptItem.query.count(),
            'income_records': UserIncome.query.count()
        }
        
        return render_template(
            'admin_v2/settings.html',
            tax_settings=tax_settings,
            general_settings=general_settings,
            db_stats=db_stats
        )
    except Exception as e:
        flash(f'Error loading settings page: {str(e)}', 'danger')
        app.logger.error(f'Admin settings error: {str(e)}')
        return redirect(url_for('admin_panel_home'))

# Update Tax Settings
@app.route('/admin/v2/settings/tax', methods=['POST'])
@admin_required
def admin_panel_update_tax_settings():
    """Update tax rate settings."""
    try:
        # Get settings from form
        lkr_business_tax_rate = float(request.form.get('lkr_business_tax_rate', 36.0))
        usd_consulting_tax_rate = float(request.form.get('usd_consulting_tax_rate', 15.0))
        vat_rate = float(request.form.get('vat_rate', 8.0))
        sscl_rate = float(request.form.get('sscl_rate', 2.0))
        
        # Validate inputs (simple range check)
        if not (0 <= lkr_business_tax_rate <= 100):
            flash('LKR Business Tax Rate must be between 0 and 100', 'danger')
            return redirect(url_for('admin_panel_settings'))
            
        if not (0 <= usd_consulting_tax_rate <= 100):
            flash('USD Consulting Tax Rate must be between 0 and 100', 'danger')
            return redirect(url_for('admin_panel_settings'))
            
        if not (0 <= vat_rate <= 100):
            flash('VAT Rate must be between 0 and 100', 'danger')
            return redirect(url_for('admin_panel_settings'))
            
        if not (0 <= sscl_rate <= 100):
            flash('SSCL Rate must be between 0 and 100', 'danger')
            return redirect(url_for('admin_panel_settings'))
        
        # Update global settings (in real app, would save to database)
        tax_settings['lkr_business_tax_rate'] = lkr_business_tax_rate
        tax_settings['usd_consulting_tax_rate'] = usd_consulting_tax_rate
        tax_settings['vat_rate'] = vat_rate
        tax_settings['sscl_rate'] = sscl_rate
        
        flash('Tax settings updated successfully', 'success')
        return redirect(url_for('admin_panel_settings'))
        
    except Exception as e:
        flash(f'Error updating tax settings: {str(e)}', 'danger')
        app.logger.error(f'Admin update tax settings error: {str(e)}')
        return redirect(url_for('admin_panel_settings'))

# Update General Settings
@app.route('/admin/v2/settings/general', methods=['POST'])
@admin_required
def admin_panel_update_general_settings():
    """Update general application settings."""
    try:
        # Get settings from form
        app_name = request.form.get('app_name', 'Sri Lanka Tax Optimizer')
        items_per_page = int(request.form.get('items_per_page', 10))
        default_currency = request.form.get('default_currency', 'LKR')
        enable_registration = 'enable_registration' in request.form
        enable_ai_features = 'enable_ai_features' in request.form
        
        # Validate inputs
        if not app_name:
            flash('Application name cannot be empty', 'danger')
            return redirect(url_for('admin_panel_settings'))
            
        if not (5 <= items_per_page <= 100):
            flash('Items per page must be between 5 and 100', 'danger')
            return redirect(url_for('admin_panel_settings'))
            
        if default_currency not in ['LKR', 'USD']:
            flash('Default currency must be LKR or USD', 'danger')
            return redirect(url_for('admin_panel_settings'))
        
        # Update global settings (in real app, would save to database)
        general_settings['app_name'] = app_name
        general_settings['items_per_page'] = items_per_page
        general_settings['default_currency'] = default_currency
        general_settings['enable_registration'] = enable_registration
        general_settings['enable_ai_features'] = enable_ai_features
        
        flash('General settings updated successfully', 'success')
        return redirect(url_for('admin_panel_settings'))
        
    except Exception as e:
        flash(f'Error updating general settings: {str(e)}', 'danger')
        app.logger.error(f'Admin update general settings error: {str(e)}')
        return redirect(url_for('admin_panel_settings'))

# System Logs
@app.route('/admin/v2/logs')
@admin_required
def admin_panel_logs():
    """Enhanced system logs page for admins."""
    try:
        # Get log type filter
        log_type = request.args.get('type', 'all')
        
        # Mock log data for demonstration
        log_entries = []
        log_types = ['system', 'user', 'receipt', 'error', 'security']
        log_levels = ['info', 'warning', 'error', 'debug']
        log_messages = [
            "User logged in successfully",
            "Password reset requested",
            "Receipt processed successfully",
            "Failed login attempt",
            "Database query error",
            "API call limit reached",
            "File upload failed - invalid format",
            "Tax calculations completed",
            "System update applied",
            "User session expired",
            "Receipt categorization updated",
            "Temporary files cleaned up"
        ]
        
        # Generate mock log entries
        for i in range(100):
            entry_type = random.choice(log_types)
            
            # Skip if filtering by type
            if log_type != 'all' and log_type != entry_type:
                continue
                
            # Higher probability of info logs
            level_weights = [0.7, 0.15, 0.1, 0.05]
            level = random.choices(log_levels, weights=level_weights)[0]
            
            # Generate random timestamp within the last 7 days
            time_delta = timedelta(
                days=random.randint(0, 7),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
                seconds=random.randint(0, 59)
            )
            
            timestamp = datetime.utcnow() - time_delta
            
            # Generate a user (sometimes None for system logs)
            user = None
            if entry_type in ['user', 'receipt', 'security'] or random.random() > 0.3:
                user_id = random.randint(1, 10)
                user = f"user{user_id}@example.com"
            
            # Create log entry
            log_entries.append({
                'id': i + 1,
                'timestamp': timestamp,
                'type': entry_type,
                'level': level,
                'message': random.choice(log_messages),
                'user': user,
                'details': f"Details for log entry {i+1}. This would contain additional debugging information."
            })
        
        # Sort by timestamp (most recent first)
        log_entries.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = 20
        total = len(log_entries)
        
        start_idx = (page - 1) * per_page
        end_idx = min(start_idx + per_page, total)
        
        current_logs = log_entries[start_idx:end_idx]
        
        # Calculate log statistics
        log_stats = {
            'total_logs': total,
            'error_count': sum(1 for log in log_entries if log['level'] == 'error'),
            'warning_count': sum(1 for log in log_entries if log['level'] == 'warning'),
            'types': {type: sum(1 for log in log_entries if log['type'] == type) for type in log_types}
        }
        
        return render_template(
            'admin_v2/logs.html',
            logs=current_logs,
            log_type=log_type,
            page=page,
            per_page=per_page,
            total=total,
            total_pages=(total + per_page - 1) // per_page,
            log_stats=log_stats
        )
    except Exception as e:
        flash(f'Error loading system logs: {str(e)}', 'danger')
        app.logger.error(f'Admin logs error: {str(e)}')
        return redirect(url_for('admin_panel_home'))

# Clear System Logs
@app.route('/admin/v2/logs/clear', methods=['POST'])
@admin_required
def admin_panel_clear_logs():
    """Clear system logs of a specific type."""
    try:
        log_type = request.form.get('log_type', 'all')
        
        # In a real application, this would delete logs from the database
        # For now, we'll just show a success message
        
        if log_type == 'all':
            message = "All system logs have been cleared"
        else:
            message = f"All {log_type} logs have been cleared"
            
        flash(message, 'success')
        return redirect(url_for('admin_panel_logs'))
        
    except Exception as e:
        flash(f'Error clearing logs: {str(e)}', 'danger')
        app.logger.error(f'Admin clear logs error: {str(e)}')
        return redirect(url_for('admin_panel_logs'))

# Export System Logs
@app.route('/admin/v2/logs/export')
@admin_required
def admin_panel_export_logs():
    """Export system logs as CSV file."""
    try:
        log_type = request.args.get('type', 'all')
        
        # Mock log data for demonstration (similar to the logs page)
        log_entries = []
        log_types = ['system', 'user', 'receipt', 'error', 'security']
        log_levels = ['info', 'warning', 'error', 'debug']
        log_messages = [
            "User logged in successfully",
            "Password reset requested",
            "Receipt processed successfully",
            "Failed login attempt",
            "Database query error",
            "API call limit reached",
            "File upload failed - invalid format",
            "Tax calculations completed",
            "System update applied",
            "User session expired",
            "Receipt categorization updated",
            "Temporary files cleaned up"
        ]
        
        # Generate mock log entries
        for i in range(100):
            entry_type = random.choice(log_types)
            
            # Skip if filtering by type
            if log_type != 'all' and log_type != entry_type:
                continue
                
            level = random.choice(log_levels)
            
            # Generate random timestamp within the last 7 days
            time_delta = timedelta(
                days=random.randint(0, 7),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
                seconds=random.randint(0, 59)
            )
            
            timestamp = datetime.utcnow() - time_delta
            
            # Generate a user (sometimes None for system logs)
            user = None
            if entry_type in ['user', 'receipt', 'security'] or random.random() > 0.3:
                user_id = random.randint(1, 10)
                user = f"user{user_id}@example.com"
            
            # Create log entry
            log_entries.append({
                'id': i + 1,
                'timestamp': timestamp,
                'type': entry_type,
                'level': level,
                'message': random.choice(log_messages),
                'user': user,
                'details': f"Details for log entry {i+1}"
            })
        
        # Sort by timestamp (oldest first for chronological order in export)
        log_entries.sort(key=lambda x: x['timestamp'])
        
        # Create DataFrame for export
        df = pd.DataFrame(log_entries)
        
        # Format timestamp
        df['timestamp'] = df['timestamp'].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S'))
        
        # Create CSV
        output = BytesIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        # Generate filename
        filename = f"system_logs_{log_type}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='text/csv'
        )
        
    except Exception as e:
        flash(f'Error exporting logs: {str(e)}', 'danger')
        app.logger.error(f'Admin export logs error: {str(e)}')
        return redirect(url_for('admin_panel_logs'))

# Clean up temporary files
@app.route('/admin/v2/maintenance/cleanup', methods=['POST'])
@admin_required
def admin_panel_clear_temp_files():
    """Clear temporary files older than specified hours."""
    try:
        hours = int(request.form.get('hours', 24))
        
        # Validate input
        if hours < 1:
            flash('Hours must be at least 1', 'danger')
            return redirect(url_for('admin_panel_settings'))
        
        # Get all image paths from receipts
        receipt_images = db.session.query(Receipt.image_path).all()
        image_paths = [path[0] for path in receipt_images if path[0]]
        
        # Call cleanup function from image_processor
        files_removed = cleanup_temp_files(image_paths, min_age_hours=hours)
        
        flash(f'Successfully removed {files_removed} temporary files', 'success')
        return redirect(url_for('admin_panel_settings'))
        
    except Exception as e:
        flash(f'Error cleaning up temporary files: {str(e)}', 'danger')
        app.logger.error(f'Admin cleanup error: {str(e)}')
        return redirect(url_for('admin_panel_settings'))

# Trigger category update
@app.route('/admin/v2/maintenance/update-categories', methods=['POST'])
@admin_required
def admin_panel_update_categories():
    """Trigger the update of expense categories for all receipts."""
    try:
        # In a real implementation, this would:
        # 1. Queue a background task to process all receipts without categories
        # 2. Use the AI to categorize them
        
        # For now, just simulate success
        updated_count = random.randint(5, 20)
        
        flash(f'Successfully updated categories for {updated_count} receipts', 'success')
        return redirect(url_for('admin_panel_settings'))
        
    except Exception as e:
        flash(f'Error updating categories: {str(e)}', 'danger')
        app.logger.error(f'Admin update categories error: {str(e)}')
        return redirect(url_for('admin_panel_settings'))