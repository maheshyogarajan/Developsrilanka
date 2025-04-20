"""
Expense reporting and summarization module.
Provides aggregated views of expenses and reimbursements with filtering capabilities.
"""

from flask import Blueprint, render_template, request, jsonify, current_app, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import func, extract, desc, and_, or_
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta
import calendar
from dateutil.relativedelta import relativedelta
import json

from app import db
from models import (
    Receipt, ReceiptItem, CompanyExpense, ExpenseStatus, 
    Organization, OrganizationUser, User, Client, UserRole
)
from decorators import role_required, admin_required

# Create the blueprint for expense reports with proper URL prefix
expense_reports_bp = Blueprint('expense_reports', __name__, url_prefix='/reports')

@expense_reports_bp.route('/expenses')
@login_required
def expense_summary():
    """
    Display a comprehensive summary of expenses with advanced filtering.
    
    This view provides visualizations and data tables for expense tracking
    with filtering by date range, organization, client, status, and category.
    """
    try:
        # Get query parameters for filtering
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        organization_id = request.args.get('organization_id', type=int)
        client_id = request.args.get('client_id', type=int)
        status = request.args.get('status', '')  # Ensure empty string instead of None
        category = request.args.get('category', '')  # Ensure empty string instead of None
        reimbursable_str = request.args.get('reimbursable')
        
        # Log received filter parameters for debugging
        current_app.logger.info(f"Received raw filter parameters: start_date={start_date_str}, end_date={end_date_str}, " 
                               f"organization_id={organization_id}, client_id={client_id}, "
                               f"status=[{status}], category=[{category}], reimbursable={reimbursable_str}")
        
        # Print request args and form data for debugging
        current_app.logger.info(f"Request args: {request.args}")
        current_app.logger.info(f"Request form: {request.form}")
        
        # Convert reimbursable string parameter to boolean if present
        reimbursable = None
        if reimbursable_str == '1':
            reimbursable = True
        elif reimbursable_str == '0':
            reimbursable = False
        
        # Get current date and set default date range to current month if not provided
        today = datetime.today()
        start_of_month = datetime(today.year, today.month, 1)
        end_of_month = start_of_month + relativedelta(months=1, days=-1)
        
        # Parse date parameters
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else start_of_month
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else end_of_month
        except ValueError:
            # Handle invalid date format
            start_date = start_of_month
            end_date = end_of_month
        
        # Get organizations the user has access to
        user_organizations = db.session.query(Organization)\
            .join(OrganizationUser)\
            .filter(OrganizationUser.user_id == current_user.id)\
            .all()
        
        # Prepare the filter conditions
        filters = []
        
        # Always filter by date range
        filters.append(Receipt.date.between(start_date, end_date))
        
        # If organization is specified, filter by organization
        if organization_id:
            # Check if user has access to the organization
            if any(org.id == organization_id for org in user_organizations):
                filters.append(CompanyExpense.organization_id == organization_id)
            else:
                # Redirect to default view if user doesn't have access
                return redirect(url_for('expense_reports.expense_summary'))
        else:
            # Filter for expenses in user's organizations
            org_ids = [org.id for org in user_organizations]
            filters.append(CompanyExpense.organization_id.in_(org_ids))
        
        # Filter by client if specified
        if client_id:
            filters.append(CompanyExpense.client_id == client_id)
        
        # Filter by status if specified
        if status:
            # Normalize status to lowercase for case-insensitive comparison
            status_lower = status.lower()
            filters.append(func.lower(CompanyExpense.status) == status_lower)
            current_app.logger.debug(f"Adding normalized status filter: {status_lower}")
        
        # Filter by receipt category if specified
        if category:
            # Normalize category to lowercase for case-insensitive comparison
            category_lower = category.lower()
            filters.append(func.lower(Receipt.expense_major_category) == category_lower)
            current_app.logger.debug(f"Adding normalized category filter: {category_lower}")
            
        # Filter by reimbursable status if specified
        if reimbursable is not None:
            filters.append(CompanyExpense.is_reimbursable == reimbursable)
            current_app.logger.debug(f"Adding reimbursable filter: {reimbursable}")
        
        # Get all expense categories for filter dropdown
        expense_categories = db.session.query(Receipt.expense_major_category)\
            .filter(Receipt.expense_major_category.isnot(None))\
            .distinct()\
            .all()
        expense_categories = [cat[0] for cat in expense_categories if cat[0]]
        
        # Get the expense statuses for filter dropdown
        expense_statuses = [status.value for status in ExpenseStatus]
        
        # Log selected status and available options for debugging
        current_app.logger.debug(f"Selected status value: '{status}', type: {type(status).__name__}")
        current_app.logger.debug(f"Status options: {expense_statuses}")
        current_app.logger.debug(f"Selected category value: '{category}', type: {type(category).__name__}")
        current_app.logger.debug(f"Category options: {expense_categories}")
        
        # Get clients for filter dropdown (based on selected organization or all user's organizations)
        if organization_id:
            clients = db.session.query(Client)\
                .filter(Client.organization_id == organization_id)\
                .all()
        else:
            clients = db.session.query(Client)\
                .filter(Client.organization_id.in_([org.id for org in user_organizations]))\
                .all()
        
        # Fetch expenses based on filters
        expenses = db.session.query(CompanyExpense)\
            .join(Receipt, CompanyExpense.receipt_id == Receipt.id)\
            .filter(*filters)\
            .options(
                joinedload(CompanyExpense.receipt),
                joinedload(CompanyExpense.submitter),
                joinedload(CompanyExpense.client)
            )\
            .order_by(desc(Receipt.date))\
            .all()
        
        # Calculate totals and prepare data for charts
        total_amount = sum(expense.receipt.total_amount for expense in expenses if expense.receipt)
        
        # Group by category for chart data
        category_data = {}
        for expense in expenses:
            if expense.receipt and expense.receipt.expense_major_category:
                category = expense.receipt.expense_major_category
                if category in category_data:
                    category_data[category] += expense.receipt.total_amount
                else:
                    category_data[category] = expense.receipt.total_amount
        
        # Group by status for chart data
        status_data = {}
        for expense in expenses:
            status = expense.status
            if status in status_data:
                status_data[status] += expense.receipt.total_amount if expense.receipt else 0
            else:
                status_data[status] = expense.receipt.total_amount if expense.receipt else 0
        
        # Group by month for trend chart
        monthly_data = {}
        for expense in expenses:
            if expense.receipt and expense.receipt.date:
                month_key = expense.receipt.date.strftime('%Y-%m')
                month_name = expense.receipt.date.strftime('%b %Y')
                if month_key in monthly_data:
                    monthly_data[month_key]['amount'] += expense.receipt.total_amount
                    monthly_data[month_key]['count'] += 1
                else:
                    monthly_data[month_key] = {
                        'label': month_name,
                        'amount': expense.receipt.total_amount,
                        'count': 1
                    }
        
        # Sort monthly data by date
        sorted_monthly_data = dict(sorted(monthly_data.items()))
        
        # Group by submitter for user comparison
        submitter_data = {}
        for expense in expenses:
            if expense.submitter:
                submitter_name = expense.submitter.name
                if submitter_name in submitter_data:
                    submitter_data[submitter_name] += expense.receipt.total_amount if expense.receipt else 0
                else:
                    submitter_data[submitter_name] = expense.receipt.total_amount if expense.receipt else 0
        
        # Calculate reimbursable vs. non-reimbursable amounts
        reimbursable_amount = sum(expense.receipt.total_amount for expense in expenses 
                                  if expense.receipt and expense.is_reimbursable)
        non_reimbursable_amount = total_amount - reimbursable_amount
        
        # Calculate average expense amount
        avg_expense_amount = total_amount / len(expenses) if expenses else 0
        
        # Calculate pending reimbursement amount (approved but not reimbursed)
        pending_reimbursement = sum(expense.receipt.total_amount for expense in expenses 
                                    if expense.receipt and expense.is_reimbursable 
                                    and expense.status == ExpenseStatus.APPROVED.value)
        
        return render_template(
            'expense_report_fixed.html',
            expenses=expenses,
            total_amount=total_amount,
            reimbursable_amount=reimbursable_amount,
            non_reimbursable_amount=non_reimbursable_amount,
            avg_expense_amount=avg_expense_amount,
            pending_reimbursement=pending_reimbursement,
            category_data=json.dumps(category_data),
            status_data=json.dumps(status_data),
            monthly_data=json.dumps(list(sorted_monthly_data.values())),
            submitter_data=json.dumps(submitter_data),
            expense_categories=expense_categories,
            expense_statuses=expense_statuses,
            user_organizations=user_organizations,
            selected_organization_id=organization_id,
            clients=clients,
            selected_client_id=client_id,
            selected_status=status,
            selected_category=category,
            selected_reimbursable=reimbursable,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )
    except Exception as e:
        # Log the error and return a friendly error page
        current_app.logger.error(f"Error rendering expense summary: {str(e)}")
        return render_template('error.html', 
                               error_code=500, 
                               error_message="An error occurred while generating the expense report.")

@expense_reports_bp.route('/reimbursements')
@login_required
def reimbursement_summary():
    """
    Display a focused summary of reimbursable expenses and their status.
    
    This view helps track outstanding reimbursements, approved amounts,
    and historical reimbursement data with filtering capabilities.
    """
    try:
        # Get query parameters for filtering
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        organization_id = request.args.get('organization_id', type=int)
        status = request.args.get('status')
        
        # Get current date and set default date range to current month if not provided
        today = datetime.today()
        start_of_month = datetime(today.year, today.month, 1)
        end_of_month = start_of_month + relativedelta(months=1, days=-1)
        
        # Parse date parameters
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else start_of_month
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else end_of_month
        except ValueError:
            # Handle invalid date format
            start_date = start_of_month
            end_date = end_of_month
        
        # Get organizations the user has access to
        user_organizations = db.session.query(Organization)\
            .join(OrganizationUser)\
            .filter(OrganizationUser.user_id == current_user.id)\
            .all()
        
        # Prepare the filter conditions
        filters = []
        
        # Always filter by date range
        filters.append(Receipt.date.between(start_date, end_date))
        
        # Only include reimbursable expenses
        filters.append(CompanyExpense.is_reimbursable == True)
        
        # If organization is specified, filter by organization
        if organization_id:
            # Check if user has access to the organization
            if any(org.id == organization_id for org in user_organizations):
                filters.append(CompanyExpense.organization_id == organization_id)
            else:
                # Redirect to default view if user doesn't have access
                return redirect(url_for('expense_reports.reimbursement_summary'))
        else:
            # Filter for expenses in user's organizations
            org_ids = [org.id for org in user_organizations]
            filters.append(CompanyExpense.organization_id.in_(org_ids))
        
        # Filter by status if specified
        if status:
            filters.append(CompanyExpense.status == status)
        
        # Get the expense statuses for filter dropdown
        expense_statuses = [status.value for status in ExpenseStatus]
        
        # Fetch reimbursable expenses based on filters
        reimbursable_expenses = db.session.query(CompanyExpense)\
            .join(Receipt, CompanyExpense.receipt_id == Receipt.id)\
            .filter(*filters)\
            .options(
                joinedload(CompanyExpense.receipt),
                joinedload(CompanyExpense.submitter),
                joinedload(CompanyExpense.client),
                joinedload(CompanyExpense.approver),
                joinedload(CompanyExpense.reimburser)
            )\
            .order_by(desc(Receipt.date))\
            .all()
        
        # Group expenses by status for summary
        status_summary = {}
        for status in expense_statuses:
            status_expenses = [e for e in reimbursable_expenses if e.status == status]
            status_summary[status] = {
                'count': len(status_expenses),
                'amount': sum(e.receipt.total_amount for e in status_expenses if e.receipt)
            }
        
        # Group by submitter for user breakdown
        submitter_summary = {}
        for expense in reimbursable_expenses:
            if expense.submitter:
                submitter_id = expense.submitter.id
                submitter_name = expense.submitter.name
                if submitter_id not in submitter_summary:
                    submitter_summary[submitter_id] = {
                        'name': submitter_name,
                        'total': 0,
                        'approved': 0,
                        'reimbursed': 0,
                        'pending': 0
                    }
                
                amount = expense.receipt.total_amount if expense.receipt else 0
                submitter_summary[submitter_id]['total'] += amount
                
                if expense.status == ExpenseStatus.APPROVED.value:
                    submitter_summary[submitter_id]['approved'] += amount
                elif expense.status == ExpenseStatus.REIMBURSED.value:
                    submitter_summary[submitter_id]['reimbursed'] += amount
                elif expense.status == ExpenseStatus.SUBMITTED.value:
                    submitter_summary[submitter_id]['pending'] += amount
        
        # Get reimbursement timeline data (when expenses were reimbursed)
        reimbursement_timeline = {}
        for expense in reimbursable_expenses:
            if expense.status == ExpenseStatus.REIMBURSED.value and expense.reimbursed_date:
                month_key = expense.reimbursed_date.strftime('%Y-%m')
                month_name = expense.reimbursed_date.strftime('%b %Y')
                if month_key in reimbursement_timeline:
                    reimbursement_timeline[month_key]['amount'] += expense.receipt.total_amount if expense.receipt else 0
                    reimbursement_timeline[month_key]['count'] += 1
                else:
                    reimbursement_timeline[month_key] = {
                        'label': month_name,
                        'amount': expense.receipt.total_amount if expense.receipt else 0,
                        'count': 1
                    }
        
        # Sort timeline data by date
        sorted_timeline = dict(sorted(reimbursement_timeline.items()))
        
        # Calculate totals
        total_reimbursable = sum(e.receipt.total_amount for e in reimbursable_expenses if e.receipt)
        total_reimbursed = sum(e.receipt.total_amount for e in reimbursable_expenses 
                               if e.receipt and e.status == ExpenseStatus.REIMBURSED.value)
        total_approved = sum(e.receipt.total_amount for e in reimbursable_expenses 
                             if e.receipt and e.status == ExpenseStatus.APPROVED.value)
        total_pending = sum(e.receipt.total_amount for e in reimbursable_expenses 
                            if e.receipt and e.status == ExpenseStatus.SUBMITTED.value)
        
        # Calculate average reimbursement time (from submission to reimbursement)
        reimbursement_times = []
        for expense in reimbursable_expenses:
            if expense.status == ExpenseStatus.REIMBURSED.value and expense.reimbursed_date and expense.submitted_date:
                time_diff = (expense.reimbursed_date - expense.submitted_date).days
                reimbursement_times.append(time_diff)
        
        avg_reimbursement_time = sum(reimbursement_times) / len(reimbursement_times) if reimbursement_times else 0
        
        return render_template(
            'reimbursement_summary.html',
            reimbursable_expenses=reimbursable_expenses,
            total_reimbursable=total_reimbursable,
            total_reimbursed=total_reimbursed,
            total_approved=total_approved,
            total_pending=total_pending,
            avg_reimbursement_time=avg_reimbursement_time,
            status_summary=status_summary,
            submitter_summary=submitter_summary,
            reimbursement_timeline=json.dumps(list(sorted_timeline.values())),
            expense_statuses=expense_statuses,
            user_organizations=user_organizations,
            selected_organization_id=organization_id,
            selected_status=status,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )
    except Exception as e:
        # Log the error and return a friendly error page
        current_app.logger.error(f"Error rendering reimbursement summary: {str(e)}")
        return render_template('error.html', 
                               error_code=500, 
                               error_message="An error occurred while generating the reimbursement report.")

@expense_reports_bp.route('/user-expenses')
@login_required
def user_expense_summary():
    """
    Display a personal summary of current user's expenses and reimbursements.
    
    This view provides insights into the user's expense patterns,
    reimbursement status, and historical expense data.
    """
    try:
        # Get query parameters for filtering
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        organization_id = request.args.get('organization_id', type=int)
        
        # Get current date and set default date range to current month if not provided
        today = datetime.today()
        start_of_month = datetime(today.year, today.month, 1)
        end_of_month = start_of_month + relativedelta(months=1, days=-1)
        
        # Parse date parameters
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else start_of_month
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else end_of_month
        except ValueError:
            # Handle invalid date format
            start_date = start_of_month
            end_date = end_of_month
        
        # Get organizations the user has access to
        user_organizations = db.session.query(Organization)\
            .join(OrganizationUser)\
            .filter(OrganizationUser.user_id == current_user.id)\
            .all()
        
        # Prepare the filter conditions
        filters = []
        
        # Always filter by date range
        filters.append(Receipt.date.between(start_date, end_date))
        
        # Always filter by current user
        filters.append(CompanyExpense.user_id == current_user.id)
        
        # If organization is specified, filter by organization
        if organization_id:
            # Check if user has access to the organization
            if any(org.id == organization_id for org in user_organizations):
                filters.append(CompanyExpense.organization_id == organization_id)
            else:
                # Redirect to default view if user doesn't have access
                return redirect(url_for('expense_reports.user_expense_summary'))
        
        # Fetch user's expenses based on filters
        user_expenses = db.session.query(CompanyExpense)\
            .join(Receipt, CompanyExpense.receipt_id == Receipt.id)\
            .filter(*filters)\
            .options(
                joinedload(CompanyExpense.receipt),
                joinedload(CompanyExpense.client)
            )\
            .order_by(desc(Receipt.date))\
            .all()
        
        # Group expenses by status
        status_summary = {}
        for status in [s.value for s in ExpenseStatus]:
            status_expenses = [e for e in user_expenses if e.status == status]
            status_summary[status] = {
                'count': len(status_expenses),
                'amount': sum(e.receipt.total_amount for e in status_expenses if e.receipt)
            }
        
        # Group expenses by category
        category_summary = {}
        for expense in user_expenses:
            if expense.receipt and expense.receipt.expense_major_category:
                category = expense.receipt.expense_major_category
                if category in category_summary:
                    category_summary[category] += expense.receipt.total_amount
                else:
                    category_summary[category] = expense.receipt.total_amount
        
        # Group expenses by month for trend
        monthly_trend = {}
        for expense in user_expenses:
            if expense.receipt and expense.receipt.date:
                month_key = expense.receipt.date.strftime('%Y-%m')
                month_name = expense.receipt.date.strftime('%b %Y')
                if month_key in monthly_trend:
                    monthly_trend[month_key]['amount'] += expense.receipt.total_amount
                    monthly_trend[month_key]['count'] += 1
                else:
                    monthly_trend[month_key] = {
                        'label': month_name,
                        'amount': expense.receipt.total_amount,
                        'count': 1
                    }
        
        # Sort monthly data by date
        sorted_monthly_trend = dict(sorted(monthly_trend.items()))
        
        # Calculate reimbursement statistics
        reimbursable_expenses = [e for e in user_expenses if e.is_reimbursable]
        non_reimbursable_expenses = [e for e in user_expenses if not e.is_reimbursable]
        
        total_reimbursable = sum(e.receipt.total_amount for e in reimbursable_expenses if e.receipt)
        total_non_reimbursable = sum(e.receipt.total_amount for e in non_reimbursable_expenses if e.receipt)
        
        # Calculate amounts by status for reimbursable expenses
        reimbursed_amount = sum(e.receipt.total_amount for e in reimbursable_expenses 
                               if e.receipt and e.status == ExpenseStatus.REIMBURSED.value)
        approved_amount = sum(e.receipt.total_amount for e in reimbursable_expenses 
                             if e.receipt and e.status == ExpenseStatus.APPROVED.value)
        pending_amount = sum(e.receipt.total_amount for e in reimbursable_expenses 
                            if e.receipt and e.status == ExpenseStatus.SUBMITTED.value)
        rejected_amount = sum(e.receipt.total_amount for e in reimbursable_expenses 
                             if e.receipt and e.status == ExpenseStatus.REJECTED.value)
        
        return render_template(
            'user_expense_summary.html',
            user_expenses=user_expenses,
            status_summary=status_summary,
            category_summary=json.dumps(category_summary),
            monthly_trend=json.dumps(list(sorted_monthly_trend.values())),
            total_reimbursable=total_reimbursable,
            total_non_reimbursable=total_non_reimbursable,
            reimbursed_amount=reimbursed_amount,
            approved_amount=approved_amount,
            pending_amount=pending_amount,
            rejected_amount=rejected_amount,
            user_organizations=user_organizations,
            selected_organization_id=organization_id,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )
    except Exception as e:
        # Log the error and return a friendly error page
        current_app.logger.error(f"Error rendering user expense summary: {str(e)}")
        return render_template('error.html', 
                               error_code=500, 
                               error_message="An error occurred while generating the user expense report.")

@expense_reports_bp.route('/expense-categories')
@login_required
def expense_categories():
    """
    Display a breakdown of expenses by category with visualization.
    
    This view helps understand spending patterns by expense category,
    with drill-down to subcategories and time-based analysis.
    """
    try:
        # Get query parameters for filtering
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        organization_id = request.args.get('organization_id', type=int)
        
        # Get current date and set default date range to current month if not provided
        today = datetime.today()
        start_of_month = datetime(today.year, today.month, 1)
        end_of_month = start_of_month + relativedelta(months=1, days=-1)
        
        # Parse date parameters
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else start_of_month
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else end_of_month
        except ValueError:
            # Handle invalid date format
            start_date = start_of_month
            end_date = end_of_month
        
        # Get organizations the user has access to
        user_organizations = db.session.query(Organization)\
            .join(OrganizationUser)\
            .filter(OrganizationUser.user_id == current_user.id)\
            .all()
        
        # Prepare the filter conditions
        filters = []
        
        # Always filter by date range
        filters.append(Receipt.date.between(start_date, end_date))
        
        # If organization is specified, filter by organization
        if organization_id:
            # Check if user has access to the organization
            if any(org.id == organization_id for org in user_organizations):
                filters.append(CompanyExpense.organization_id == organization_id)
            else:
                # Redirect to default view if user doesn't have access
                return redirect(url_for('expense_reports.expense_categories'))
        else:
            # Filter for expenses in user's organizations
            org_ids = [org.id for org in user_organizations]
            filters.append(CompanyExpense.organization_id.in_(org_ids))
        
        # Fetch expenses with their receipt data
        expenses = db.session.query(CompanyExpense)\
            .join(Receipt, CompanyExpense.receipt_id == Receipt.id)\
            .filter(*filters)\
            .options(
                joinedload(CompanyExpense.receipt),
                joinedload(CompanyExpense.receipt).joinedload(Receipt.items)
            )\
            .all()
        
        # Analyze expenses by major category
        major_categories = {}
        for expense in expenses:
            if expense.receipt and expense.receipt.expense_major_category:
                category = expense.receipt.expense_major_category
                if category in major_categories:
                    major_categories[category]['total'] += expense.receipt.total_amount
                    major_categories[category]['count'] += 1
                else:
                    major_categories[category] = {
                        'total': expense.receipt.total_amount,
                        'count': 1,
                        'subcategories': {},
                        'tax_deductible': 0
                    }
                
                # Track tax deductible amounts by category
                for item in expense.receipt.items:
                    if item.tax_deductible:
                        deduct_amount = item.price * item.quantity
                        major_categories[category]['tax_deductible'] += deduct_amount
                
                # Track minor categories
                if expense.receipt.expense_minor_category:
                    subcategory = expense.receipt.expense_minor_category
                    if subcategory in major_categories[category]['subcategories']:
                        major_categories[category]['subcategories'][subcategory]['total'] += expense.receipt.total_amount
                        major_categories[category]['subcategories'][subcategory]['count'] += 1
                    else:
                        major_categories[category]['subcategories'][subcategory] = {
                            'total': expense.receipt.total_amount,
                            'count': 1
                        }
        
        # Sort categories by total amount
        sorted_categories = {k: v for k, v in sorted(
            major_categories.items(), 
            key=lambda item: item[1]['total'], 
            reverse=True
        )}
        
        # Calculate totals
        total_amount = sum(cat['total'] for cat in major_categories.values())
        total_tax_deductible = sum(cat['tax_deductible'] for cat in major_categories.values())
        
        # Prepare chart data
        category_chart_data = [{
            'category': category,
            'total': data['total'],
            'percentage': (data['total'] / total_amount * 100) if total_amount > 0 else 0
        } for category, data in sorted_categories.items()]
        
        # Calculate month-by-month category data for trend analysis
        monthly_category_data = {}
        for expense in expenses:
            if expense.receipt and expense.receipt.date and expense.receipt.expense_major_category:
                month_key = expense.receipt.date.strftime('%Y-%m')
                month_name = expense.receipt.date.strftime('%b %Y')
                category = expense.receipt.expense_major_category
                
                if month_key not in monthly_category_data:
                    monthly_category_data[month_key] = {
                        'label': month_name,
                        'categories': {}
                    }
                
                if category in monthly_category_data[month_key]['categories']:
                    monthly_category_data[month_key]['categories'][category] += expense.receipt.total_amount
                else:
                    monthly_category_data[month_key]['categories'][category] = expense.receipt.total_amount
        
        # Sort monthly data and prepare for chart
        sorted_monthly_data = dict(sorted(monthly_category_data.items()))
        
        # Get all unique categories across all months
        all_categories = set()
        for month_data in sorted_monthly_data.values():
            all_categories.update(month_data['categories'].keys())
        
        # Prepare data for stacked area chart
        category_trend_data = {
            'labels': [data['label'] for data in sorted_monthly_data.values()],
            'datasets': []
        }
        
        for category in all_categories:
            dataset = {
                'label': category,
                'data': []
            }
            
            for month_key, month_data in sorted_monthly_data.items():
                dataset['data'].append(month_data['categories'].get(category, 0))
            
            category_trend_data['datasets'].append(dataset)
        
        return render_template(
            'expense_categories.html',
            major_categories=sorted_categories,
            total_amount=total_amount,
            total_tax_deductible=total_tax_deductible,
            category_chart_data=json.dumps(category_chart_data),
            category_trend_data=json.dumps(category_trend_data),
            user_organizations=user_organizations,
            selected_organization_id=organization_id,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )
    except Exception as e:
        # Log the error and return a friendly error page
        current_app.logger.error(f"Error rendering expense categories: {str(e)}")
        return render_template('error.html', 
                               error_code=500, 
                               error_message="An error occurred while generating the expense categories report.")

@expense_reports_bp.route('/tax-deductible')
@login_required
def tax_deductible_summary():
    """
    Display a summary of tax deductible expenses for accounting purposes.
    
    This view focuses on tracking tax deductible expenses and items,
    with appropriate filtering and summaries for tax season.
    """
    try:
        # Get query parameters for filtering
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        organization_id = request.args.get('organization_id', type=int)
        
        # If no specific range is provided, default to current year
        today = datetime.today()
        start_of_year = datetime(today.year, 1, 1)
        end_of_year = datetime(today.year, 12, 31)
        
        # Parse date parameters
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else start_of_year
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else end_of_year
        except ValueError:
            # Handle invalid date format
            start_date = start_of_year
            end_date = end_of_year
        
        # Get organizations the user has access to
        user_organizations = db.session.query(Organization)\
            .join(OrganizationUser)\
            .filter(OrganizationUser.user_id == current_user.id)\
            .all()
        
        # Prepare the filter conditions
        filters = []
        
        # Always filter by date range
        filters.append(Receipt.date.between(start_date, end_date))
        
        # If organization is specified, filter by organization
        if organization_id:
            # Check if user has access to the organization
            if any(org.id == organization_id for org in user_organizations):
                filters.append(CompanyExpense.organization_id == organization_id)
            else:
                # Redirect to default view if user doesn't have access
                return redirect(url_for('expense_reports.tax_deductible_summary'))
        else:
            # Filter for expenses in user's organizations
            org_ids = [org.id for org in user_organizations]
            filters.append(CompanyExpense.organization_id.in_(org_ids))
        
        # Fetch expenses with their receipt data and receipt items
        expenses = db.session.query(CompanyExpense)\
            .join(Receipt, CompanyExpense.receipt_id == Receipt.id)\
            .filter(*filters)\
            .options(
                joinedload(CompanyExpense.receipt),
                joinedload(CompanyExpense.receipt).joinedload(Receipt.items)
            )\
            .order_by(Receipt.date)\
            .all()
        
        # Collect tax deductible items and calculate totals
        deductible_items = []
        total_expense_amount = 0
        total_deductible_amount = 0
        
        for expense in expenses:
            if expense.receipt:
                receipt = expense.receipt
                total_expense_amount += receipt.total_amount
                
                # Process items for tax deductibility
                for item in receipt.items:
                    if item.tax_deductible:
                        item_amount = item.price * item.quantity
                        total_deductible_amount += item_amount
                        
                        deductible_items.append({
                            'expense_id': expense.id,
                            'receipt_id': receipt.id,
                            'date': receipt.date,
                            'vendor': receipt.vendor_name,
                            'description': item.name,
                            'amount': item_amount,
                            'category': receipt.expense_major_category,
                            'subcategory': receipt.expense_minor_category
                        })
        
        # Group by category for chart
        category_deductible = {}
        for item in deductible_items:
            category = item['category'] or 'Uncategorized'
            if category in category_deductible:
                category_deductible[category] += item['amount']
            else:
                category_deductible[category] = item['amount']
        
        # Group by month for trend analysis
        monthly_deductible = {}
        for item in deductible_items:
            if item['date']:
                month_key = item['date'].strftime('%Y-%m')
                month_name = item['date'].strftime('%b %Y')
                if month_key in monthly_deductible:
                    monthly_deductible[month_key]['amount'] += item['amount']
                    monthly_deductible[month_key]['count'] += 1
                else:
                    monthly_deductible[month_key] = {
                        'label': month_name,
                        'amount': item['amount'],
                        'count': 1
                    }
        
        # Sort monthly data by date
        sorted_monthly_deductible = dict(sorted(monthly_deductible.items()))
        
        # Calculate percentages
        deductible_percentage = (total_deductible_amount / total_expense_amount * 100) if total_expense_amount > 0 else 0
        
        return render_template(
            'tax_deductible_summary.html',
            deductible_items=deductible_items,
            total_expense_amount=total_expense_amount,
            total_deductible_amount=total_deductible_amount,
            deductible_percentage=deductible_percentage,
            category_deductible=json.dumps(category_deductible),
            monthly_deductible=json.dumps(list(sorted_monthly_deductible.values())),
            user_organizations=user_organizations,
            selected_organization_id=organization_id,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )
    except Exception as e:
        # Log the error and return a friendly error page
        current_app.logger.error(f"Error rendering tax deductible summary: {str(e)}")
        return render_template('error.html', 
                               error_code=500, 
                               error_message="An error occurred while generating the tax deductible expense report.")

# API endpoints for AJAX data retrieval

@expense_reports_bp.route('/api/expenses/summary')
@login_required
def api_expense_summary():
    """API endpoint to get expense summary data for charts."""
    try:
        # Similar filtering logic as the main page
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        organization_id = request.args.get('organization_id', type=int)
        
        # Default date range (current month)
        today = datetime.today()
        start_of_month = datetime(today.year, today.month, 1)
        end_of_month = start_of_month + relativedelta(months=1, days=-1)
        
        # Parse date parameters
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else start_of_month
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else end_of_month
        except ValueError:
            # Handle invalid date format
            start_date = start_of_month
            end_date = end_of_month
        
        # Get organizations the user has access to
        user_organizations = db.session.query(Organization)\
            .join(OrganizationUser)\
            .filter(OrganizationUser.user_id == current_user.id)\
            .all()
        
        # Prepare the filter conditions
        filters = []
        
        # Always filter by date range
        filters.append(Receipt.date.between(start_date, end_date))
        
        # If organization is specified, filter by organization
        if organization_id:
            # Check if user has access to the organization
            if any(org.id == organization_id for org in user_organizations):
                filters.append(CompanyExpense.organization_id == organization_id)
            else:
                return jsonify({'error': 'Access denied to specified organization'}), 403
        else:
            # Filter for expenses in user's organizations
            org_ids = [org.id for org in user_organizations]
            filters.append(CompanyExpense.organization_id.in_(org_ids))
        
        # Fetch expenses based on filters
        expenses = db.session.query(CompanyExpense)\
            .join(Receipt, CompanyExpense.receipt_id == Receipt.id)\
            .filter(*filters)\
            .options(
                joinedload(CompanyExpense.receipt)
            )\
            .all()
        
        # Calculate status breakdown
        status_data = {}
        for expense in expenses:
            status = expense.status
            if status in status_data:
                status_data[status] += expense.receipt.total_amount if expense.receipt else 0
            else:
                status_data[status] = expense.receipt.total_amount if expense.receipt else 0
        
        # Calculate category breakdown
        category_data = {}
        for expense in expenses:
            if expense.receipt and expense.receipt.expense_major_category:
                category = expense.receipt.expense_major_category
                if category in category_data:
                    category_data[category] += expense.receipt.total_amount
                else:
                    category_data[category] = expense.receipt.total_amount
        
        # Monthly trend
        monthly_data = {}
        for expense in expenses:
            if expense.receipt and expense.receipt.date:
                month_key = expense.receipt.date.strftime('%Y-%m')
                month_name = expense.receipt.date.strftime('%b %Y')
                if month_key in monthly_data:
                    monthly_data[month_key]['amount'] += expense.receipt.total_amount
                    monthly_data[month_key]['count'] += 1
                else:
                    monthly_data[month_key] = {
                        'label': month_name,
                        'amount': expense.receipt.total_amount,
                        'count': 1
                    }
        
        # Sort monthly data by date
        sorted_monthly_data = dict(sorted(monthly_data.items()))
        
        # Return JSON data for charts
        return jsonify({
            'status_data': status_data,
            'category_data': category_data,
            'monthly_data': list(sorted_monthly_data.values())
        })
    except Exception as e:
        # Log the error and return a JSON error response
        current_app.logger.error(f"Error generating expense summary API data: {str(e)}")
        return jsonify({'error': 'An error occurred while generating expense data'}), 500

@expense_reports_bp.route('/expenses/print')
@login_required
def print_expense_report():
    """
    Display a printable version of the expense report with receipt images.
    
    This view provides a clean, print-friendly report that shows each expense
    with its receipt image, designed for printing reimbursement requests.
    """
    try:
        # Get query parameters for filtering - same as in expense_summary
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        organization_id = request.args.get('organization_id', type=int)
        client_id = request.args.get('client_id', type=int)
        status = request.args.get('status', '')
        category = request.args.get('category', '')
        reimbursable_str = request.args.get('reimbursable')
        
        # Log received filter parameters for debugging
        current_app.logger.info(f"Print report - Received filter parameters: start_date={start_date_str}, end_date={end_date_str}, " 
                               f"organization_id={organization_id}, client_id={client_id}, "
                               f"status=[{status}], category=[{category}], reimbursable={reimbursable_str}")
        
        # Convert reimbursable string parameter to boolean if present
        reimbursable = None
        if reimbursable_str == '1':
            reimbursable = True
        elif reimbursable_str == '0':
            reimbursable = False
        
        # Get current date and set default date range to current month if not provided
        today = datetime.today()
        start_of_month = datetime(today.year, today.month, 1)
        end_of_month = start_of_month + relativedelta(months=1, days=-1)
        
        # Parse date parameters
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else start_of_month
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else end_of_month
        except ValueError:
            # Handle invalid date format
            start_date = start_of_month
            end_date = end_of_month
        
        # Get organizations the user has access to
        user_organizations = db.session.query(Organization)\
            .join(OrganizationUser)\
            .filter(OrganizationUser.user_id == current_user.id)\
            .all()
        
        # Prepare the filter conditions
        filters = []
        
        # Always filter by date range
        filters.append(Receipt.date.between(start_date, end_date))
        
        # If organization is specified, filter by organization
        if organization_id:
            # Check if user has access to the organization
            if any(org.id == organization_id for org in user_organizations):
                filters.append(CompanyExpense.organization_id == organization_id)
            else:
                # Redirect to default view if user doesn't have access
                return redirect(url_for('expense_reports.expense_summary'))
        else:
            # Filter for expenses in user's organizations
            org_ids = [org.id for org in user_organizations]
            filters.append(CompanyExpense.organization_id.in_(org_ids))
        
        # Filter by client if specified
        if client_id:
            filters.append(CompanyExpense.client_id == client_id)
        
        # Filter by status if specified
        if status:
            # Normalize status to lowercase for case-insensitive comparison
            status_lower = status.lower()
            filters.append(func.lower(CompanyExpense.status) == status_lower)
        
        # Filter by receipt category if specified
        if category:
            # Normalize category to lowercase for case-insensitive comparison
            category_lower = category.lower()
            filters.append(func.lower(Receipt.expense_major_category) == category_lower)
            
        # Filter by reimbursable status if specified
        if reimbursable is not None:
            filters.append(CompanyExpense.is_reimbursable == reimbursable)
        
        # Fetch expenses with full details including receipt images
        expenses = db.session.query(CompanyExpense)\
            .join(Receipt, CompanyExpense.receipt_id == Receipt.id)\
            .filter(*filters)\
            .options(
                joinedload(CompanyExpense.receipt).joinedload(Receipt.items),
                joinedload(CompanyExpense.submitter),
                joinedload(CompanyExpense.client),
                joinedload(CompanyExpense.organization)
            )\
            .order_by(desc(Receipt.date))\
            .all()
        
        # Calculate totals
        total_amount = sum(expense.receipt.total_amount for expense in expenses if expense.receipt)
        reimbursable_amount = sum(expense.receipt.total_amount for expense in expenses 
                              if expense.receipt and expense.is_reimbursable)
        
        # Set the report title based on filters
        report_title = "Expense Report"
        if organization_id:
            org_name = next((org.name for org in user_organizations if org.id == organization_id), None)
            if org_name:
                report_title += f" - {org_name}"
        
        if client_id:
            client = next((expense.client.name for expense in expenses if expense.client and expense.client.id == client_id), None)
            if client:
                report_title += f" - {client}"
                
        if status:
            report_title += f" - {status.title()} Expenses"
            
        if reimbursable is True:
            report_title += " - Reimbursable Only"
        elif reimbursable is False:
            report_title += " - Non-Reimbursable Only"
        
        report_title += f" ({start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')})"
        
        # Add a debug message to verify template usage
        current_app.logger.info(f"DEBUG: Using print_expense_report.html - Redesigned Template V2")
        
        return render_template(
            'print_expense_report.html',
            expenses=expenses,
            total_amount=total_amount,
            reimbursable_amount=reimbursable_amount,
            report_title=report_title,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d'),
            user=current_user
        )
    except Exception as e:
        # Log the error and return a friendly error page
        import traceback
        error_traceback = traceback.format_exc()
        current_app.logger.error(f"Error rendering print expense report: {str(e)}")
        current_app.logger.error(f"Traceback: {error_traceback}")
        return render_template('error.html', 
                               error_code=500, 
                               error_message="An error occurred while generating the print-friendly expense report.")

def register_routes(app):
    """Register expense report routes with the app."""
    app.register_blueprint(expense_reports_bp)
    # Add link to reports in the app's nav menu
    app.jinja_env.globals.update(expense_reports_enabled=True)