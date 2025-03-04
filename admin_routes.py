"""
Admin routes for the application.
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

# Admin Dashboard
@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard with overview of the application."""
    try:
        # Get counts for the dashboard
        user_count = User.query.count()
        receipt_count = Receipt.query.count()
        error_count = 0  # Mock for now
        settings_updated = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        
        return render_template('admin/dashboard.html',
                              user_count=user_count,
                              receipt_count=receipt_count,
                              error_count=error_count,
                              settings_updated=settings_updated)
    except Exception as e:
        app.logger.error(f"Error loading admin dashboard: {str(e)}")
        flash(f"Error loading dashboard: {str(e)}", "danger")
        return redirect(url_for('home'))

# User Management
@app.route('/admin/users')
@admin_required
def admin_users():
    """User management page for admins."""
    try:
        page = request.args.get('page', 1, type=int)
        search = request.args.get('search', '')
        per_page = 10
        
        # Build query with search filter if provided
        query = User.query
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    User.name.ilike(search_term),
                    User.email.ilike(search_term)
                )
            )
        
        # Get paginated users
        pagination = query.order_by(User.id).paginate(page=page, per_page=per_page, error_out=False)
        users = pagination.items
        total_users = pagination.total
        total_pages = pagination.pages
        
        return render_template('admin/users.html',
                              users=users,
                              page=page,
                              total_pages=total_pages,
                              total_users=total_users,
                              search=search)
    except Exception as e:
        app.logger.error(f"Error loading admin users page: {str(e)}")
        flash(f"Error loading users: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

# Update User Role
@app.route('/admin/users/update-role', methods=['POST'])
@admin_required
def admin_update_user_role():
    """Update a user's role."""
    try:
        user_id = request.form.get('user_id', type=int)
        role = request.form.get('role')
        
        if not user_id or not role:
            flash("User ID and role are required.", "danger")
            return redirect(url_for('admin_users'))
        
        # Validate role
        if role not in ['user', 'admin']:
            flash("Invalid role specified.", "danger")
            return redirect(url_for('admin_users'))
        
        # Get user and update role
        user = User.query.get(user_id)
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for('admin_users'))
        
        # Don't allow changing own role (to prevent locking out)
        if user.id == current_user.id:
            flash("You cannot change your own role.", "warning")
            return redirect(url_for('admin_users'))
        
        user.role = role
        db.session.commit()
        
        flash(f"Role for {user.name or user.email} updated to {role}.", "success")
        return redirect(url_for('admin_users'))
    except Exception as e:
        app.logger.error(f"Error updating user role: {str(e)}")
        flash(f"Error updating role: {str(e)}", "danger")
        return redirect(url_for('admin_users'))

# System Statistics
@app.route('/admin/statistics')
@admin_required
def admin_statistics():
    """System statistics page for admins."""
    try:
        # Get basic statistics
        total_receipts = Receipt.query.count()
        total_users = User.query.count()
        
        # Calculate active vs inactive users (mock data for now)
        active_user_count = total_users  # Simplification
        inactive_user_count = 0
        
        # Calculate receipt statistics
        receipts = Receipt.query.all()
        total_amount = sum(r.total_amount for r in receipts) if receipts else 0
        avg_receipt_amount = total_amount / total_receipts if total_receipts > 0 else 0
        
        # Count tax deductible receipts
        tax_deductible_count = sum(1 for r in receipts if any(item.tax_deductible for item in r.items))
        tax_deductible_percent = (tax_deductible_count / total_receipts * 100) if total_receipts > 0 else 0
        
        # Calculate total VAT and SSCL
        total_vat = sum(r.vat_tax for r in receipts if r.vat_tax is not None)
        total_sscl = sum(r.sscl_tax for r in receipts if r.sscl_tax is not None)
        
        # Find most common vendor
        vendor_counts = {}
        for r in receipts:
            vendor_counts[r.vendor_name] = vendor_counts.get(r.vendor_name, 0) + 1
        most_common_vendor = max(vendor_counts.items(), key=lambda x: x[1])[0] if vendor_counts else "None"
        
        # Generate user growth data (mock data for demo)
        # In real app, you'd query by creation date
        today = datetime.utcnow().date()
        user_growth_labels = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(30, -1, -1)]
        user_growth_data = [i % 5 for i in range(32)]  # Mock data
        
        # Generate category distribution data
        categories = db.session.query(
            Receipt.expense_major_category,
            func.count(Receipt.id)
        ).filter(
            Receipt.expense_major_category != None,
            Receipt.expense_major_category != ''
        ).group_by(Receipt.expense_major_category).all()
        
        category_labels = [c[0] for c in categories]
        category_data = [c[1] for c in categories]
        
        # Generate daily activity data (mock)
        daily_activity_labels = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(14, -1, -1)]
        daily_activity_data = [i % 8 + 1 for i in range(15)]  # Mock data
        
        return render_template('admin/statistics.html',
                              total_receipts=total_receipts,
                              avg_receipt_amount=avg_receipt_amount,
                              tax_deductible_count=tax_deductible_count,
                              tax_deductible_percent=round(tax_deductible_percent, 1),
                              total_vat=total_vat,
                              total_sscl=total_sscl,
                              most_common_vendor=most_common_vendor,
                              active_user_count=active_user_count,
                              inactive_user_count=inactive_user_count,
                              user_growth_labels=json.dumps(user_growth_labels),
                              user_growth_data=user_growth_data,
                              category_labels=json.dumps(category_labels),
                              category_data=category_data,
                              daily_activity_labels=json.dumps(daily_activity_labels),
                              daily_activity_data=daily_activity_data)
    except Exception as e:
        app.logger.error(f"Error loading admin statistics: {str(e)}")
        flash(f"Error loading statistics: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

# Application Settings
@app.route('/admin/settings')
@admin_required
def admin_settings():
    """Application settings page for admins."""
    message = request.args.get('message')
    message_type = request.args.get('message_type', 'info')
    
    return render_template('admin/settings.html',
                          tax_settings=tax_settings,
                          general_settings=general_settings,
                          message=message,
                          message_type=message_type)

# Update Tax Settings
@app.route('/admin/settings/update-tax', methods=['POST'])
@admin_required
def admin_update_tax_settings():
    """Update tax rate settings."""
    try:
        # Update global tax settings
        tax_settings['lkr_business_tax_rate'] = float(request.form.get('lkr_business_tax_rate', 36.0))
        tax_settings['usd_consulting_tax_rate'] = float(request.form.get('usd_consulting_tax_rate', 15.0))
        tax_settings['vat_rate'] = float(request.form.get('vat_rate', 8.0))
        tax_settings['sscl_rate'] = float(request.form.get('sscl_rate', 2.0))
        
        flash("Tax settings updated successfully.", "success")
        return redirect(url_for('admin_settings', message="Tax settings updated successfully.", message_type="success"))
    except Exception as e:
        app.logger.error(f"Error updating tax settings: {str(e)}")
        return redirect(url_for('admin_settings', message=f"Error updating tax settings: {str(e)}", message_type="danger"))

# Update General Settings
@app.route('/admin/settings/update-general', methods=['POST'])
@admin_required
def admin_update_general_settings():
    """Update general application settings."""
    try:
        # Update global general settings
        general_settings['app_name'] = request.form.get('app_name', 'Sri Lanka Tax Optimizer')
        general_settings['items_per_page'] = int(request.form.get('items_per_page', 10))
        general_settings['default_currency'] = request.form.get('default_currency', 'LKR')
        general_settings['enable_registration'] = 'enable_registration' in request.form
        general_settings['enable_ai_features'] = 'enable_ai_features' in request.form
        
        flash("General settings updated successfully.", "success")
        return redirect(url_for('admin_settings', message="General settings updated successfully.", message_type="success"))
    except Exception as e:
        app.logger.error(f"Error updating general settings: {str(e)}")
        return redirect(url_for('admin_settings', message=f"Error updating general settings: {str(e)}", message_type="danger"))

# Clear Temporary Files
@app.route('/admin/clear-temp-files', methods=['POST'])
@admin_required
def admin_clear_temp_files():
    """Clear temporary files older than 24 hours."""
    try:
        # Get all image paths from the database
        image_paths = [r.image_path for r in Receipt.query.all() if r.image_path]
        
        # Clean up temporary files
        files_removed = cleanup_temp_files(image_paths)
        
        flash(f"Successfully removed {files_removed} temporary files.", "success")
        return redirect(url_for('admin_settings', message=f"Successfully removed {files_removed} temporary files.", message_type="success"))
    except Exception as e:
        app.logger.error(f"Error clearing temporary files: {str(e)}")
        return redirect(url_for('admin_settings', message=f"Error clearing temporary files: {str(e)}", message_type="danger"))

# Update Categories
@app.route('/admin/update-categories', methods=['POST'])
@admin_required
def admin_update_categories():
    """Trigger the update of expense categories for all receipts."""
    try:
        # This would normally call the update_categories.py script
        flash("Category update process started. This may take some time.", "info")
        return redirect(url_for('admin_settings', message="Category update process started. This may take some time.", message_type="info"))
    except Exception as e:
        app.logger.error(f"Error updating categories: {str(e)}")
        return redirect(url_for('admin_settings', message=f"Error updating categories: {str(e)}", message_type="danger"))

# System Logs
@app.route('/admin/logs')
@admin_required
def admin_logs():
    """System logs page for admins."""
    try:
        page = request.args.get('page', 1, type=int)
        log_type = request.args.get('log_type', 'error')
        start_date = request.args.get('start_date', (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d'))
        end_date = request.args.get('end_date', datetime.utcnow().strftime('%Y-%m-%d'))
        per_page = 20
        
        # Mock log data for demonstration
        # In a real application, you would query a logs table
        logs = []
        total_logs = 0
        
        # Generate some sample log entries
        log_levels = ['INFO', 'WARNING', 'ERROR']
        sources = ['web', 'database', 'auth', 'api']
        messages = [
            'User login successful',
            'User login failed: invalid credentials',
            'Database connection lost',
            'Receipt processing failed',
            'API rate limit exceeded'
        ]
        
        # Create mock logs
        for i in range(40):
            level = log_levels[i % len(log_levels)]
            if log_type != 'all' and log_type.upper() != level:
                continue
                
            log_date = datetime.utcnow() - timedelta(days=i % 14, hours=i % 24)
            log_date_str = log_date.strftime('%Y-%m-%d')
            
            if log_date_str < start_date or log_date_str > end_date:
                continue
                
            logs.append({
                'timestamp': log_date,
                'level': level,
                'source': sources[i % len(sources)],
                'message': f"{messages[i % len(messages)]} (ID: {i})",
                'user_id': i % 5 + 1 if i % 3 != 0 else None,
                'ip_address': f"192.168.1.{i % 255}"
            })
            total_logs += 1
        
        # Sort logs by timestamp (newest first)
        logs.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # Paginate logs
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        logs_page = logs[start_idx:end_idx]
        total_pages = (total_logs + per_page - 1) // per_page if total_logs > 0 else 1
        
        return render_template('admin/logs.html',
                              logs=logs_page,
                              page=page,
                              total_pages=total_pages,
                              total_logs=total_logs,
                              log_type=log_type,
                              start_date=start_date,
                              end_date=end_date,
                              error_count=sum(1 for l in logs if l['level'] == 'ERROR'))
    except Exception as e:
        app.logger.error(f"Error loading admin logs: {str(e)}")
        flash(f"Error loading logs: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

# Clear Logs
@app.route('/admin/logs/clear', methods=['POST'])
@admin_required
def admin_clear_logs():
    """Clear system logs of a specific type."""
    log_type = request.form.get('log_type', 'all')
    
    # In a real app, this would delete logs from the database
    flash(f"Logs of type '{log_type}' cleared successfully.", "success")
    return redirect(url_for('admin_logs', log_type=log_type))

# Export Logs
@app.route('/admin/logs/export')
@admin_required
def admin_export_logs():
    """Export system logs as CSV file."""
    log_type = request.args.get('log_type', 'all')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    # In a real app, this would query logs from the database
    # For now, we'll create a simple CSV with mock data
    data = {
        'timestamp': [datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') for _ in range(5)],
        'level': ['INFO', 'ERROR', 'WARNING', 'INFO', 'ERROR'],
        'source': ['web', 'database', 'auth', 'api', 'web'],
        'message': [
            'User login successful',
            'Database connection lost',
            'User login failed: invalid credentials',
            'Receipt processing successful',
            'API rate limit exceeded'
        ],
        'user_id': [1, None, 2, 3, None],
        'ip_address': ['192.168.1.1', '192.168.1.2', '192.168.1.3', '192.168.1.4', '192.168.1.5']
    }
    
    df = pd.DataFrame(data)
    
    # Create a file-like object in memory
    buffer = BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    
    # Generate filename with current date
    filename = f"logs_{log_type}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='text/csv'
    )