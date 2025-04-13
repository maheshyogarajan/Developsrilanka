"""
Routes for unified receipt and expense views.
This module combines the functionality of receipt_views and expense_views.
"""
import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, abort, send_file
from flask_login import login_required, current_user
from sqlalchemy import text, or_, and_, func
from sqlalchemy.orm import joinedload
from models import (
    Receipt, ReceiptItem, CompanyExpense, ClientExpense, Client, 
    ExpenseStatus, Organization, User, OrganizationUser
)
from app import db
from utils import format_currency

# Create blueprint
unified_view_bp = Blueprint('unified_view', __name__, template_folder='templates')

@unified_view_bp.route('/view/<int:receipt_id>')
@login_required
def view_unified_receipt(receipt_id):
    """
    Unified view for receipt and any associated expenses.
    
    This view combines:
    - Receipt details
    - Business expense details (if classified as business expense)
    - Client expense details (if allocated to client)
    - Receipt items
    - Receipt image
    
    All in a single cohesive view.
    """
    # Retrieve the receipt with organization filtering
    receipt = Receipt.query.filter_by(id=receipt_id).first_or_404()
    
    # Check if user has access to this receipt
    if receipt.user_id != current_user.id:
        # If receipt belongs to organization, check if user is a member of that organization
        if receipt.organization_id:
            org_user = OrganizationUser.query.filter_by(
                user_id=current_user.id,
                organization_id=receipt.organization_id
            ).first()
            if not org_user:
                abort(403)  # Forbidden access
        else:
            abort(403)  # Forbidden access
            
    # Get image URL using the standardized helper function
    from utils import get_receipt_image_url
    receipt.image_url = get_receipt_image_url(receipt, prefer_s3=True)
    
    # Get user's organizations for the form dropdowns
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    user_org_ids = [org.organization_id for org in user_orgs]
    organizations = Organization.query.filter(Organization.id.in_(user_org_ids)).all()
    
    # Load organization if applicable
    organization = None
    if receipt.organization_id:
        organization = Organization.query.get(receipt.organization_id)
    
    # Check if receipt is classified as a business expense
    company_expense = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
    is_company_expense = company_expense is not None
    
    # Check if receipt is allocated to a client
    client_expense = ClientExpense.query.filter_by(receipt_id=receipt_id).first()
    is_client_expense = client_expense is not None
    
    # Get user's role in the organization for permission checks
    user_org_role = None
    if receipt.organization_id:
        org_user = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=receipt.organization_id
        ).first()
        if org_user:
            user_org_role = org_user.role
    
    # Enhance expense object with related data if it exists
    if is_company_expense:
        # Add submitter name
        submitter = User.query.get(company_expense.user_id)
        company_expense.submitter_name = submitter.name if submitter else "Unknown"
        company_expense.submitter_email = submitter.email if submitter else "Unknown"
        
        # Add approver name if approved
        if company_expense.approved_by_user_id:
            approver = User.query.get(company_expense.approved_by_user_id)
            company_expense.approver_name = approver.name if approver else "Unknown"
        
        # Add reimburser name if reimbursed
        if company_expense.reimbursed_by_user_id:
            reimburser = User.query.get(company_expense.reimbursed_by_user_id)
            company_expense.reimburser_name = reimburser.name if reimburser else "Unknown"
        
        # Add client name if associated with a client
        if company_expense.client_id:
            client = Client.query.get(company_expense.client_id)
            company_expense.client_name = client.name if client else "Unknown Client"
    
    # Use standardized helper to get the image URL
    from utils import get_receipt_image_url
    receipt.image_url = get_receipt_image_url(receipt)
    
    # Get receipt items with tax deductible status
    receipt_items = ReceiptItem.query.filter_by(receipt_id=receipt_id).all()
    
    return render_template(
        'unified_receipt_view.html',
        receipt=receipt,
        organization=organization,
        organizations=organizations,  # Added for the dropdown
        expense=company_expense,
        is_company_expense=is_company_expense,
        client_expense=client_expense,
        is_client_expense=is_client_expense,
        user_org_role=user_org_role,
        items=receipt_items,
        current_user=current_user
    )

@unified_view_bp.route('/history')
@login_required
def history():
    """
    Unified history view for all receipts and expenses.
    
    Features:
    - Pagination
    - Advanced filtering options (organization, type, date, amount, etc.)
    - Sorting capabilities
    - Bulk actions
    """
    try:
        # Get user's organizations
        user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
        user_org_ids = [org.organization_id for org in user_orgs]
        
        # Get all organizations the user is a member of
        organizations = Organization.query.filter(Organization.id.in_(user_org_ids)).all()
        
        # Get default organization
        default_org = None
        for org in user_orgs:
            if org.is_default:
                default_org = Organization.query.get(org.organization_id)
                break
        
        # Set organization for filtered view
        selected_org_id = None
        if request.args.get('organization'):
            try:
                selected_org_id = int(request.args.get('organization'))
            except (ValueError, TypeError):
                selected_org_id = None
        elif default_org:
            selected_org_id = default_org.id
        
        # Get other filter parameters
        page = request.args.get('page', 1, type=int)
        selected_type = request.args.get('type', '')
        selected_status = request.args.get('status', '')
        selected_date_range = request.args.get('date_range', '')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        amount_min = request.args.get('amount_min', '', type=str)
        amount_max = request.args.get('amount_max', '', type=str)
        vendor = request.args.get('vendor', '')
        selected_category = request.args.get('category', '')
        tax_deductible = request.args.get('tax_deductible', '')
        sort_by = request.args.get('sort_by', 'date_desc')
        
        # Initialize query
        query = Receipt.query.filter(
            or_(
                Receipt.user_id == current_user.id,
                and_(
                    Receipt.organization_id.in_(user_org_ids)
                )
            )
        )
        
        # Add organization filter if selected
        if selected_org_id:
            query = query.filter(Receipt.organization_id == selected_org_id)
        
        # Add type filter
        if selected_type == 'business':
            # Only show receipts that are business expenses
            query = query.join(CompanyExpense, CompanyExpense.receipt_id == Receipt.id)
        elif selected_type == 'client':
            # Only show receipts that are client expenses
            query = query.join(ClientExpense, ClientExpense.receipt_id == Receipt.id)
        elif selected_type == 'unclassified':
            # Only show receipts that are not business or client expenses
            business_receipt_ids = db.session.query(CompanyExpense.receipt_id).all()
            client_receipt_ids = db.session.query(ClientExpense.receipt_id).all()
            classified_ids = [r[0] for r in business_receipt_ids] + [r[0] for r in client_receipt_ids]
            query = query.filter(~Receipt.id.in_(classified_ids) if classified_ids else True)
        
        # Add status filter (only applies to business expenses)
        if selected_status and selected_type == 'business':
            query = query.join(CompanyExpense, CompanyExpense.receipt_id == Receipt.id)
            query = query.filter(CompanyExpense.status == selected_status)
        
        # Add date filter
        if selected_date_range:
            if selected_date_range == 'custom' and start_date and end_date:
                try:
                    start = datetime.strptime(start_date, '%Y-%m-%d')
                    end = datetime.strptime(end_date, '%Y-%m-%d')
                    end = end + timedelta(days=1) - timedelta(seconds=1)  # End of day
                    query = query.filter(Receipt.date.between(start, end))
                except ValueError:
                    flash('Invalid date format', 'warning')
            elif selected_date_range.isdigit():
                days = int(selected_date_range)
                start_date = datetime.now() - timedelta(days=days)
                query = query.filter(Receipt.date >= start_date)
        
        # Add amount filter
        if amount_min:
            try:
                min_amount = float(amount_min)
                query = query.filter(Receipt.total_amount >= min_amount)
            except ValueError:
                flash('Invalid minimum amount', 'warning')
        
        if amount_max:
            try:
                max_amount = float(amount_max)
                query = query.filter(Receipt.total_amount <= max_amount)
            except ValueError:
                flash('Invalid maximum amount', 'warning')
        
        # Add vendor filter
        if vendor:
            query = query.filter(Receipt.vendor_name.ilike(f'%{vendor}%'))
        
        # Add category filter
        if selected_category:
            query = query.filter(
                or_(
                    Receipt.expense_major_category == selected_category,
                    Receipt.expense_minor_category == selected_category
                )
            )
        
        # Add tax deductible filter
        if tax_deductible == 'yes':
            query = query.filter(Receipt.tax_deductible_amount > 0)
        elif tax_deductible == 'no':
            query = query.filter(Receipt.tax_deductible_amount <= 0)
        
        # Add sorting
        if sort_by == 'date_desc':
            query = query.order_by(Receipt.date.desc())
        elif sort_by == 'date_asc':
            query = query.order_by(Receipt.date.asc())
        elif sort_by == 'amount_desc':
            query = query.order_by(Receipt.total_amount.desc())
        elif sort_by == 'amount_asc':
            query = query.order_by(Receipt.total_amount.asc())
        elif sort_by == 'vendor_asc':
            query = query.order_by(Receipt.vendor_name.asc())
        elif sort_by == 'vendor_desc':
            query = query.order_by(Receipt.vendor_name.desc())
        else:
            # Default sort
            query = query.order_by(Receipt.date.desc())
        
        # Get pagination parameters
        per_page = 24  # Number of receipts per page
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        receipts = pagination.items
        total_pages = pagination.pages
        
        # Get list of all unique categories for filter dropdown
        categories = db.session.query(Receipt.expense_major_category).filter(
            Receipt.expense_major_category != '',
            Receipt.expense_major_category.isnot(None)
        ).distinct().all()
        categories = [c[0] for c in categories]
        
        # Determine if filtering is active
        filtered = (
            selected_org_id is not None or
            selected_type or
            selected_status or
            selected_date_range or
            amount_min or
            amount_max or
            vendor or
            selected_category or
            tax_deductible
        )
        
        # Store filter parameters for pagination links
        filter_params = {
            'organization': selected_org_id,
            'type': selected_type,
            'status': selected_status,
            'date_range': selected_date_range,
            'start_date': start_date,
            'end_date': end_date,
            'amount_min': amount_min,
            'amount_max': amount_max,
            'vendor': vendor,
            'category': selected_category,
            'tax_deductible': tax_deductible,
            'sort_by': sort_by
        }
        
        # Remove None values for URL generation
        filter_params = {k: v for k, v in filter_params.items() if v}
        
        # Enhance receipt objects with associated business and client expenses
        enhanced_receipts = []
        for receipt in receipts:
            # Check for business expense
            business_expense = CompanyExpense.query.filter_by(receipt_id=receipt.id).first()
            if business_expense:
                receipt.is_business_expense = True
                receipt.business_expense = business_expense
                
                # Add client if one exists
                if business_expense.client_id:
                    business_expense.client = Client.query.get(business_expense.client_id)
            else:
                receipt.is_business_expense = False
            
            # Check for client expense
            client_expense = ClientExpense.query.filter_by(receipt_id=receipt.id).first()
            if client_expense:
                receipt.is_client_expense = True
                receipt.client_expense = client_expense
                
                # Add client
                if client_expense.client_id:
                    client_expense.client = Client.query.get(client_expense.client_id)
            else:
                receipt.is_client_expense = False
            
            # Add organization info
            if receipt.organization_id:
                receipt.organization = Organization.query.get(receipt.organization_id)
            
            # Use standardized helper to get the image URL
            from utils import get_receipt_image_url
            receipt.image_url = get_receipt_image_url(receipt)
            
            enhanced_receipts.append(receipt)
        
        # Create filtered parameter dictionaries for template
        filter_params_without_org = {k: v for k, v in filter_params.items() if k != 'organization'}
        filter_params_without_type = {k: v for k, v in filter_params.items() if k != 'type'}
        filter_params_without_search = {k: v for k, v in filter_params.items() if k != 'search'}
        filter_params_without_page = {k: v for k, v in filter_params.items() if k != 'page'}
        
        items = []
        for receipt in enhanced_receipts:
            if hasattr(receipt, 'is_company_expense') and receipt.is_company_expense:
                items.append({'type': 'expense', 'expense': receipt})
            else:
                items.append({'type': 'receipt', 'receipt': receipt})
        
        return render_template(
            'unified_history.html',
            items=items,
            organizations=organizations,
            selected_org_id=selected_org_id,
            selected_type=selected_type,
            search_query=vendor,
            has_filters=filtered,
            filter_params=filter_params,
            filter_params_without_org=filter_params_without_org,
            filter_params_without_type=filter_params_without_type,
            filter_params_without_search=filter_params_without_search,
            filter_params_without_page=filter_params_without_page,
            current_page=page,
            total_pages=total_pages,
            current_user=current_user
        )
    except Exception as e:
        # Log the error
        logging.error(f"Database error in history view: {str(e)}")
        flash("We encountered a temporary database issue. Please try again in a moment.", "error")
        # Render a simplified view with error message
        return render_template('unified_history.html', error=True, error_message="Database connection error", current_user=current_user)

@unified_view_bp.route('/update/<int:receipt_id>', methods=['POST'])
@login_required
def update_receipt(receipt_id):
    """
    Update receipt details and expense classification in a single form.
    This handles both receipt details and expense details simultaneously.
    """
    # Get receipt
    receipt = Receipt.query.get_or_404(receipt_id)
    
    # Ensure user has access to this receipt
    if receipt.user_id != current_user.id:
        if not receipt.organization_id:
            abort(403)  # User doesn't own this receipt and it's not associated with an organization
        
        # Check if user is part of this organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=receipt.organization_id
        ).first()
        
        if not user_org or user_org.role not in ['owner', 'admin', 'member']:
            abort(403)  # User doesn't have edit permission
    
    # Update receipt basic details
    receipt.vendor_name = request.form.get('vendor_name', receipt.vendor_name)
    receipt.receipt_number = request.form.get('receipt_number', receipt.receipt_number)
    
    try:
        if request.form.get('date'):
            receipt.date = datetime.strptime(request.form.get('date'), '%Y-%m-%d')
    except ValueError:
        flash('Invalid date format', 'warning')
    
    try:
        if request.form.get('total_amount'):
            receipt.total_amount = float(request.form.get('total_amount'))
    except ValueError:
        flash('Invalid amount format', 'warning')
    
    receipt.expense_major_category = request.form.get('expense_major_category', receipt.expense_major_category)
    receipt.expense_minor_category = request.form.get('expense_minor_category', receipt.expense_minor_category)
    
    # Handle receipt type change
    receipt_type = request.form.get('receipt_type')
    
    # Get existing expense records
    company_expense = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
    client_expense = ClientExpense.query.filter_by(receipt_id=receipt_id).first()
    
    if receipt_type == 'personal':
        # Convert to personal expense by removing business/client expense records
        if company_expense:
            db.session.delete(company_expense)
        if client_expense:
            db.session.delete(client_expense)
            
    elif receipt_type == 'business':
        # Handle business expense details
        if company_expense:
            # Update existing business expense
            company_expense.description = request.form.get('business_description', '')
            company_expense.is_reimbursable = 'is_reimbursable' in request.form
            
            if request.form.get('client_id'):
                company_expense.client_id = int(request.form.get('client_id'))
            else:
                company_expense.client_id = None
                
            company_expense.notes = request.form.get('notes', '')
            
        else:
            # Create new business expense
            new_expense = CompanyExpense(
                receipt_id=receipt_id,
                user_id=current_user.id,
                organization_id=receipt.organization_id,
                description=request.form.get('business_description', ''),
                is_reimbursable='is_reimbursable' in request.form,
                notes=request.form.get('notes', ''),
                status=ExpenseStatus.SUBMITTED.value
            )
            
            if request.form.get('client_id'):
                new_expense.client_id = int(request.form.get('client_id'))
                
            db.session.add(new_expense)
            
            # Remove client expense if it exists
            if client_expense:
                db.session.delete(client_expense)
    
    # Calculate tax deductible amount based on receipt items
    # Will be updated when items are saved
    
    db.session.commit()
    flash('Receipt updated successfully', 'success')
    return redirect(url_for('unified_view.view_unified_receipt', receipt_id=receipt_id))

@unified_view_bp.route('/update-items/<int:receipt_id>', methods=['POST'])
@login_required
def update_receipt_items(receipt_id):
    """
    Update receipt items and their tax deductible status.
    Also recalculates the receipt's total tax deductible amount.
    """
    # Get receipt
    receipt = Receipt.query.get_or_404(receipt_id)
    
    # Ensure user has access to this receipt
    if receipt.user_id != current_user.id:
        if not receipt.organization_id:
            abort(403)  # User doesn't own this receipt and it's not associated with an organization
        
        # Check if user is part of this organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=receipt.organization_id
        ).first()
        
        if not user_org or user_org.role not in ['owner', 'admin', 'member']:
            abort(403)  # User doesn't have edit permission
    
    # Get form data
    item_ids = request.form.getlist('item_ids[]')
    descriptions = request.form.getlist('descriptions[]')
    quantities = request.form.getlist('quantities[]')
    unit_prices = request.form.getlist('unit_prices[]')
    amounts = request.form.getlist('amounts[]')
    tax_deductible_ids = request.form.getlist('tax_deductible[]')
    
    # Update items
    total_tax_deductible = 0
    
    for i in range(len(item_ids)):
        if i < len(descriptions) and i < len(quantities) and i < len(unit_prices) and i < len(amounts):
            item_id = int(item_ids[i])
            item = ReceiptItem.query.get(item_id)
            
            if item and item.receipt_id == receipt_id:
                item.description = descriptions[i]
                
                try:
                    item.quantity = float(quantities[i]) if quantities[i] else None
                    item.unit_price = float(unit_prices[i]) if unit_prices[i] else None
                    item.amount = float(amounts[i]) if amounts[i] else None
                except ValueError:
                    flash(f'Invalid number format for item: {descriptions[i]}', 'warning')
                    continue
                
                # Set tax deductible status
                item.tax_deductible = str(item_id) in tax_deductible_ids
                
                # Calculate tax deductible amount
                if item.tax_deductible and item.amount:
                    total_tax_deductible += item.amount
    
    # Update receipt with total tax deductible amount
    receipt.tax_deductible_amount = total_tax_deductible
    
    db.session.commit()
    flash('Receipt items updated successfully', 'success')
    return redirect(url_for('unified_view.view_unified_receipt', receipt_id=receipt_id))

@unified_view_bp.route('/bulk-action', methods=['POST'])
@login_required
def bulk_action():
    """Handle bulk actions on receipts (delete, export)."""
    action = request.form.get('action')
    selected_receipts = request.form.getlist('selected_receipts')
    
    if not selected_receipts:
        flash('No receipts selected', 'warning')
        return redirect(url_for('unified_view.history'))
    
    # Convert string IDs to integers
    receipt_ids = []
    for id_str in selected_receipts:
        try:
            receipt_ids.append(int(id_str))
        except ValueError:
            continue
    
    if action == 'delete':
        # Verify user has permission to delete these receipts
        accessible_receipts = []
        for receipt_id in receipt_ids:
            receipt = Receipt.query.get(receipt_id)
            if not receipt:
                continue
                
            if receipt.user_id == current_user.id:
                accessible_receipts.append(receipt)
            elif receipt.organization_id:
                # Check if user is org owner or admin
                org_user = OrganizationUser.query.filter_by(
                    user_id=current_user.id,
                    organization_id=receipt.organization_id
                ).first()
                
                if org_user and org_user.role in ['owner', 'admin']:
                    accessible_receipts.append(receipt)
        
        if not accessible_receipts:
            flash('You do not have permission to delete the selected receipts', 'danger')
            return redirect(url_for('unified_view.history'))
        
        # Delete associated expenses first
        for receipt in accessible_receipts:
            # Delete company expenses
            company_expenses = CompanyExpense.query.filter_by(receipt_id=receipt.id).all()
            for expense in company_expenses:
                db.session.delete(expense)
            
            # Delete client expenses
            client_expenses = ClientExpense.query.filter_by(receipt_id=receipt.id).all()
            for expense in client_expenses:
                db.session.delete(expense)
            
            # Delete receipt items
            receipt_items = ReceiptItem.query.filter_by(receipt_id=receipt.id).all()
            for item in receipt_items:
                db.session.delete(item)
            
            # Finally delete the receipt itself
            db.session.delete(receipt)
        
        db.session.commit()
        flash(f'Successfully deleted {len(accessible_receipts)} receipt(s)', 'success')
        
    elif action == 'export':
        # TODO: Implement export functionality
        flash('Export functionality not yet implemented', 'info')
    
    return redirect(url_for('unified_view.history'))

@unified_view_bp.route('/api/organizations')
@login_required
def api_organizations():
    """
    API endpoint to get a list of organizations the current user belongs to.
    Used for organization dropdown in forms.
    """
    # Get user's organizations
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    user_org_ids = [org.organization_id for org in user_orgs]
    
    # Get organization details
    organizations = Organization.query.filter(Organization.id.in_(user_org_ids)).all()
    
    # Convert to simple dict format
    org_list = [{'id': org.id, 'name': org.name} for org in organizations]
    
    return jsonify(org_list)

@unified_view_bp.route('/api/organizations/<int:org_id>/clients')
@login_required
def api_organization_clients(org_id):
    """
    API endpoint to get a list of clients for a specific organization.
    Used for client dropdown in forms when an organization is selected.
    """
    # Ensure user has access to this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user:
        abort(403)  # User doesn't have access to this organization
    
    # Get clients for the organization
    clients = Client.query.filter_by(organization_id=org_id).all()
    
    # Convert to simple dict format
    client_list = [{'id': client.id, 'name': client.name} for client in clients]
    
    return jsonify(client_list)



@unified_view_bp.route('/export_excel')
@login_required
def export_excel():
    """
    Export receipts and expenses as Excel file.
    This combines the functionality of the original export routes.
    """
    # Get user's organizations
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    user_org_ids = [org.organization_id for org in user_orgs]
    
    # Get receipts and expenses for the user
    organization_filter = request.args.get('organization')
    if organization_filter:
        try:
            org_id = int(organization_filter)
            # Verify user has access to this organization
            if org_id not in user_org_ids:
                flash('Access denied to this organization', 'danger')
                return redirect(url_for('unified_view.history'))
                
            receipts = Receipt.query.filter_by(organization_id=org_id).all()
        except ValueError:
            flash('Invalid organization ID', 'danger')
            return redirect(url_for('unified_view.history'))
    else:
        # If no organization filter, get receipts for all user's organizations plus personal receipts
        receipts = Receipt.query.filter(
            (Receipt.user_id == current_user.id) | 
            (Receipt.organization_id.in_(user_org_ids))
        ).all()
    
    # Generate Excel file
    from io import BytesIO
    import xlsxwriter
    
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output)
    worksheet = workbook.add_worksheet('Receipts')
    
    # Define formats
    header_format = workbook.add_format({
        'bold': True,
        'bg_color': '#f8f9fa',
        'border': 1
    })
    
    date_format = workbook.add_format({
        'num_format': 'yyyy-mm-dd',
        'border': 1
    })
    
    number_format = workbook.add_format({
        'num_format': '#,##0.00',
        'border': 1
    })
    
    text_format = workbook.add_format({
        'border': 1
    })
    
    # Write headers
    headers = [
        'ID', 'Type', 'Date', 'Vendor', 'Receipt #', 'Category', 
        'Total Amount', 'Currency', 'Tax Amount', 'Tax Deductible', 
        'Organization', 'Client', 'Status', 'Notes'
    ]
    
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_format)
    
    # Set column widths
    worksheet.set_column(0, 0, 5)  # ID
    worksheet.set_column(1, 1, 10)  # Type
    worksheet.set_column(2, 2, 12)  # Date
    worksheet.set_column(3, 3, 25)  # Vendor
    worksheet.set_column(4, 4, 15)  # Receipt #
    worksheet.set_column(5, 5, 20)  # Category
    worksheet.set_column(6, 6, 15)  # Total Amount
    worksheet.set_column(7, 7, 10)  # Currency
    worksheet.set_column(8, 8, 12)  # Tax Amount
    worksheet.set_column(9, 9, 12)  # Tax Deductible
    worksheet.set_column(10, 10, 20)  # Organization
    worksheet.set_column(11, 11, 20)  # Client
    worksheet.set_column(12, 12, 12)  # Status
    worksheet.set_column(13, 13, 30)  # Notes
    
    # Write data
    row = 1
    for receipt in receipts:
        # Get organization name
        organization_name = ''
        if receipt.organization_id:
            org = Organization.query.get(receipt.organization_id)
            if org:
                organization_name = org.name
        
        # Get expense details
        expense_type = 'Personal'
        client_name = ''
        status = ''
        notes = ''
        
        company_expense = CompanyExpense.query.filter_by(receipt_id=receipt.id).first()
        if company_expense:
            expense_type = 'Business'
            status = company_expense.status
            notes = company_expense.notes
            
            if company_expense.client_id:
                client = Client.query.get(company_expense.client_id)
                if client:
                    client_name = client.name
        
        client_expense = ClientExpense.query.filter_by(receipt_id=receipt.id).first()
        if client_expense:
            expense_type = 'Client'
            if client_expense.client_id:
                client = Client.query.get(client_expense.client_id)
                if client:
                    client_name = client.name
        
        # Write row data
        worksheet.write(row, 0, receipt.id, text_format)
        worksheet.write(row, 1, expense_type, text_format)
        worksheet.write(row, 2, receipt.date, date_format if receipt.date else text_format)
        worksheet.write(row, 3, receipt.vendor_name, text_format)
        worksheet.write(row, 4, receipt.receipt_number, text_format)
        worksheet.write(row, 5, f"{receipt.expense_major_category} - {receipt.expense_minor_category}" 
                       if receipt.expense_minor_category else receipt.expense_major_category, text_format)
        worksheet.write(row, 6, receipt.total_amount, number_format)
        worksheet.write(row, 7, receipt.currency, text_format)
        worksheet.write(row, 8, receipt.tax_amount, number_format if receipt.tax_amount else text_format)
        worksheet.write(row, 9, receipt.tax_deductible_amount, number_format if receipt.tax_deductible_amount else text_format)
        worksheet.write(row, 10, organization_name, text_format)
        worksheet.write(row, 11, client_name, text_format)
        worksheet.write(row, 12, status, text_format)
        worksheet.write(row, 13, notes, text_format)
        
        row += 1
    
    # Add receipt items worksheet
    items_worksheet = workbook.add_worksheet('Receipt Items')
    
    # Write headers for items
    item_headers = [
        'Receipt ID', 'Description', 'Quantity', 'Unit Price', 
        'Amount', 'Tax Deductible', 'Vendor', 'Date'
    ]
    
    for col, header in enumerate(item_headers):
        items_worksheet.write(0, col, header, header_format)
    
    # Set column widths
    items_worksheet.set_column(0, 0, 10)  # Receipt ID
    items_worksheet.set_column(1, 1, 40)  # Description
    items_worksheet.set_column(2, 2, 10)  # Quantity
    items_worksheet.set_column(3, 3, 15)  # Unit Price
    items_worksheet.set_column(4, 4, 15)  # Amount
    items_worksheet.set_column(5, 5, 12)  # Tax Deductible
    items_worksheet.set_column(6, 6, 25)  # Vendor
    items_worksheet.set_column(7, 7, 12)  # Date
    
    # Write items data
    row = 1
    for receipt in receipts:
        items = ReceiptItem.query.filter_by(receipt_id=receipt.id).all()
        
        for item in items:
            items_worksheet.write(row, 0, receipt.id, text_format)
            items_worksheet.write(row, 1, item.description, text_format)
            items_worksheet.write(row, 2, item.quantity, number_format if item.quantity else text_format)
            items_worksheet.write(row, 3, item.unit_price, number_format if item.unit_price else text_format)
            items_worksheet.write(row, 4, item.amount, number_format if item.amount else text_format)
            items_worksheet.write(row, 5, 'Yes' if item.tax_deductible else 'No', text_format)
            items_worksheet.write(row, 6, receipt.vendor_name, text_format)
            items_worksheet.write(row, 7, receipt.date, date_format if receipt.date else text_format)
            
            row += 1
    
    workbook.close()
    
    # Prepare response
    output.seek(0)
    
    date_str = datetime.now().strftime('%Y%m%d')
    filename = f"receipts_export_{date_str}.xlsx"
    
    return send_file(
        output,
        download_name=filename,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def register_routes(app):
    """Register the unified receipt/expense view routes with the app."""
    app.register_blueprint(unified_view_bp, url_prefix='/receipts')
    # Register the format_currency function as a template filter with the name currencyformat
    app.add_template_filter(format_currency, 'currencyformat')
    # Also register it with its default name for backward compatibility
    app.add_template_filter(format_currency)
    logging.info('Unified receipt and expense view routes loaded successfully')