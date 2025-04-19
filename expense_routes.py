"""
Routes for company expense management and reimbursement.
"""
from datetime import datetime
import logging

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from models import CompanyExpense, ExpenseStatus, Receipt, User, Organization, Client, OrganizationUser
from decorators import role_required
from utils import check_organization_permission

expense_bp = Blueprint('expense', __name__)

@expense_bp.route('/expenses')
@login_required
def expenses():
    """Render the company expenses page showing list of user's submitted expenses."""
    
    # Get the user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    # Get user's role in this organization
    role_result = db.session.execute(text("""
        SELECT role FROM organization_user
        WHERE user_id = :user_id AND organization_id = :org_id
        LIMIT 1
    """), {'user_id': current_user.id, 'org_id': organization_id}).first()
    
    user_org_role = role_result[0] if role_result else None
    
    # Get all expenses for this organization if admin/owner, otherwise just the user's
    if user_org_role in ['owner', 'admin']:
        # Admin view - get all expenses for the organization
        expense_results = db.session.execute(text("""
            SELECT e.id, e.receipt_id, e.user_id, e.description, e.status,
                   e.submitted_date, e.approval_date, e.reimbursed_date, e.notes,
                   r.vendor_name, r.date, r.total_amount, r.image_path,
                   u.name as submitter_name, e.client_id, c.name as client_name,
                   e.is_reimbursable, r.created_at as date_scanned
            FROM company_expense e
            JOIN receipt r ON e.receipt_id = r.id
            JOIN "user" u ON e.user_id = u.id
            LEFT JOIN client c ON e.client_id = c.id
            WHERE e.organization_id = :org_id
            ORDER BY e.submitted_date DESC
        """), {'org_id': organization_id})
    else:
        # Regular user view - get only their expenses
        expense_results = db.session.execute(text("""
            SELECT e.id, e.receipt_id, e.user_id, e.description, e.status,
                   e.submitted_date, e.approval_date, e.reimbursed_date, e.notes,
                   r.vendor_name, r.date, r.total_amount, r.image_path,
                   u.name as submitter_name, e.client_id, c.name as client_name,
                   e.is_reimbursable, r.created_at as date_scanned
            FROM company_expense e
            JOIN receipt r ON e.receipt_id = r.id
            JOIN "user" u ON e.user_id = u.id
            LEFT JOIN client c ON e.client_id = c.id
            WHERE e.user_id = :user_id AND e.organization_id = :org_id
            ORDER BY e.submitted_date DESC
        """), {'user_id': current_user.id, 'org_id': organization_id})
    
    expenses = []
    for row in expense_results:
        expense = {
            'id': row[0],
            'receipt_id': row[1],
            'user_id': row[2],
            'description': row[3],
            'status': row[4],
            'submitted_date': row[5],
            'approval_date': row[6],
            'reimbursed_date': row[7],
            'notes': row[8],
            'vendor_name': row[9],
            'receipt_date': row[10],
            'total_amount': row[11],
            'image_path': row[12],
            'submitter_name': row[13],
            'client_id': row[14],
            'client_name': row[15],
            'is_reimbursable': row[16],
            'date_scanned': row[17] if len(row) > 17 else None
        }
        expenses.append(expense)
    
    # Get organization info
    org_result = db.session.execute(text("""
        SELECT id, name, logo_path FROM organization
        WHERE id = :org_id
        LIMIT 1
    """), {'org_id': organization_id}).first()
    
    organization = {
        'id': org_result[0],
        'name': org_result[1],
        'logo_path': org_result[2]
    } if org_result else None
    
    # Get stats for each status type
    submitted_count = sum(1 for e in expenses if e['status'] == ExpenseStatus.SUBMITTED.value)
    approved_count = sum(1 for e in expenses if e['status'] == ExpenseStatus.APPROVED.value)
    reimbursed_count = sum(1 for e in expenses if e['status'] == ExpenseStatus.REIMBURSED.value)
    rejected_count = sum(1 for e in expenses if e['status'] == ExpenseStatus.REJECTED.value)
    
    # Calculate total amounts by status
    submitted_amount = sum(e['total_amount'] for e in expenses if e['status'] == ExpenseStatus.SUBMITTED.value)
    approved_amount = sum(e['total_amount'] for e in expenses if e['status'] == ExpenseStatus.APPROVED.value)
    reimbursed_amount = sum(e['total_amount'] for e in expenses if e['status'] == ExpenseStatus.REIMBURSED.value)
    
    stats = {
        'submitted': {'count': submitted_count, 'amount': submitted_amount},
        'approved': {'count': approved_count, 'amount': approved_amount},
        'reimbursed': {'count': reimbursed_count, 'amount': reimbursed_amount},
        'rejected': {'count': rejected_count, 'amount': 0}
    }
    
    return render_template('expenses.html', 
                           expenses=expenses, 
                           organization=organization,
                           user_org_role=user_org_role,
                           stats=stats,
                           expense_statuses=[(status.name, status.value) for status in ExpenseStatus])

@expense_bp.route('/expenses/submit', methods=['GET', 'POST'])
@login_required
def submit_expense():
    """Submit a receipt as a company expense for reimbursement."""
    
    # Get the user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    if not organization_id:
        flash('You need to be part of an organization to submit expenses', 'warning')
        return redirect(url_for('organizations'))
    
    if request.method == 'POST':
        try:
            receipt_id = request.form.get('receipt_id')
            description = request.form.get('description', '')
            notes = request.form.get('notes', '')
            is_reimbursable = request.form.get('is_reimbursable') == 'true'
            client_id = request.form.get('client_id')
            
            # Convert client_id to int or None
            if client_id and client_id.isdigit():
                client_id = int(client_id)
            else:
                client_id = None
            
            # Verify the receipt belongs to the user
            receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
            if not receipt:
                flash('Receipt not found or access denied', 'danger')
                return redirect(url_for('expense.expenses'))
            
            # If client_id is provided, verify it belongs to the organization
            if client_id:
                client = Client.query.filter_by(id=client_id, organization_id=organization_id).first()
                if not client:
                    flash('Selected client not found or does not belong to your organization', 'danger')
                    return redirect(url_for('expense.expenses'))
            
            # Create the company expense
            company_expense = CompanyExpense(
                receipt_id=receipt_id,
                user_id=current_user.id,
                organization_id=organization_id,
                client_id=client_id,
                description=description,
                status=ExpenseStatus.SUBMITTED.value,
                is_reimbursable=is_reimbursable,
                notes=notes,
                submitted_date=datetime.utcnow()
            )
            
            db.session.add(company_expense)
            db.session.commit()
            
            flash('Expense submitted successfully for reimbursement', 'success')
            return redirect(url_for('expense.expenses'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error submitting expense: {str(e)}")
            flash(f'Error submitting expense: {str(e)}', 'danger')
            return redirect(url_for('expense.expenses'))
    
    # GET request - render form
    # Get user's receipts that haven't been submitted as expenses yet
    receipts_results = db.session.execute(text("""
        SELECT r.id, r.vendor_name, r.date, r.total_amount, r.image_path, 
               r.expense_major_category, r.expense_minor_category, r.s3_key, r.organization_id
        FROM receipt r
        LEFT JOIN company_expense e ON r.id = e.receipt_id
        WHERE r.user_id = :user_id AND e.id IS NULL
        ORDER BY r.date DESC
    """), {'user_id': current_user.id})
    
    receipts = []
    for row in receipts_results:
        receipt = {
            'id': row[0],
            'vendor_name': row[1],
            'date': row[2],
            'total_amount': row[3],
            'image_path': row[4],
            'expense_major_category': row[5],
            'expense_minor_category': row[6],
            's3_key': row[7] if len(row) > 7 else None,
            'organization_id': row[8] if len(row) > 8 else None
        }
        
        # Generate S3 image URL if needed
        # We'll use image_path directly in templates instead of S3 URLs
        receipt['image_url'] = None
        receipts.append(receipt)
    
    if not receipts:
        flash('No receipts available for expense submission. Please scan receipts first.', 'info')
        return redirect(url_for('index'))
    
    # Get clients for the organization
    clients = Client.query.filter_by(organization_id=organization_id).order_by(Client.name).all()
    
    return render_template('submit_expense.html', receipts=receipts, clients=clients)

@expense_bp.route('/expenses/<int:expense_id>')
@login_required
def view_expense(expense_id):
    """View a specific expense."""
    # Get the expense first
    expense_obj = CompanyExpense.query.get_or_404(expense_id)
    
    # Check if user has permission to view this expense
    # Allow if: user is the submitter, or user is owner/admin of the expense's organization
    if expense_obj.user_id != current_user.id:
        # Check role in the expense's organization
        from utils import check_organization_permission
        if not check_organization_permission(current_user.id, expense_obj.organization_id, ['owner', 'admin']):
            abort(403)  # User doesn't have permission in this organization
    
    # Get the related receipt
    receipt = Receipt.query.get_or_404(expense_obj.receipt_id)
    
    # Get user information for submitter, approver, and reimburser
    submitter = User.query.get(expense_obj.user_id)
    approver = User.query.get(expense_obj.approved_by_user_id) if expense_obj.approved_by_user_id else None
    reimburser = User.query.get(expense_obj.reimbursed_by_user_id) if expense_obj.reimbursed_by_user_id else None
    
    # Get client information if associated with this expense
    client = Client.query.get(expense_obj.client_id) if expense_obj.client_id else None
    
    # Use the standardized helper to get the image URL
    from utils import get_receipt_image_url
    
    # Get receipt data for debugging
    receipt_dict = receipt.to_dict()
    
    # Debug: Log the receipt info to see what we're working with
    logging.debug(f"Receipt data for expense ID {expense_id}: s3_key={receipt.s3_key}, image_path={receipt.image_path}")
    
    # Use standardized helper to generate image URL
    receipt_image_url = get_receipt_image_url(receipt)
    logging.debug(f"Using standardized image URL for expense ID {expense_id}: {receipt_image_url}")
    
    logging.debug(f"Generated receipt_image_url: {receipt_image_url}")
    
    expense = {
        'id': expense_obj.id,
        'receipt_id': expense_obj.receipt_id,
        'user_id': expense_obj.user_id,
        'organization_id': expense_obj.organization_id,
        'client_id': expense_obj.client_id,
        'description': expense_obj.description,
        'status': expense_obj.status,
        'submitted_date': expense_obj.submitted_date,
        'approval_date': expense_obj.approval_date,
        'reimbursed_date': expense_obj.reimbursed_date,
        'approved_by_user_id': expense_obj.approved_by_user_id,
        'reimbursed_by_user_id': expense_obj.reimbursed_by_user_id,
        'notes': expense_obj.notes,
        'vendor_name': receipt.vendor_name,
        'receipt_date': receipt.date,
        'total_amount': receipt.total_amount,
        'image_path': receipt.image_path,
        's3_key': receipt.s3_key,
        'image_url': receipt_image_url,
        'vendor_address': receipt.vendor_address,
        'vendor_contact': receipt.vendor_contact,
        'vat_registration_number': receipt.vat_registration_number,
        'submitter_name': submitter.name if submitter else 'Unknown',
        'submitter_email': submitter.email if submitter else '',
        'approver_name': approver.name if approver else None,
        'reimburser_name': reimburser.name if reimburser else None,
        'client_name': client.name if client else None,
        'client_email': client.email if client else None,
        'is_reimbursable': getattr(expense_obj, 'is_reimbursable', True),  # Add support for is_reimbursable field
        'date_scanned': receipt.created_at  # Add date scanned field
    }
    
    # Get receipt items
    items_result = db.session.execute(text("""
        SELECT id, name, quantity, price, tax_deductible
        FROM receipt_item
        WHERE receipt_id = :receipt_id
        ORDER BY id
    """), {'receipt_id': expense['receipt_id']})
    
    receipt_items = []
    for row in items_result:
        item = {
            'id': row[0],
            'name': row[1],
            'quantity': row[2],
            'price': row[3],
            'tax_deductible': row[4]
        }
        receipt_items.append(item)
    
    expense['receipt_items'] = receipt_items
    
    # Get organization info using the expense's organization_id
    organization_id = expense_obj.organization_id
    org_result = db.session.execute(text("""
        SELECT id, name, logo_path FROM organization
        WHERE id = :org_id
        LIMIT 1
    """), {'org_id': organization_id}).first()
    
    organization = {
        'id': org_result[0],
        'name': org_result[1],
        'logo_path': org_result[2]
    } if org_result else None
    
    # Get user's role in this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=organization_id
    ).first()
    user_org_role = org_user.role if org_user else None
    
    return render_template('view_expense.html', 
                           expense=expense, 
                           organization=organization,
                           user_org_role=user_org_role)

@expense_bp.route('/expenses/<int:expense_id>/approve', methods=['POST'])
@login_required
def approve_expense(expense_id):
    """Approve an expense for reimbursement (admin/owner only)."""
    try:
        # First get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        
        # Get organization ID from the expense
        organization_id = expense.organization_id
        
        # Use the helper function to check permission in the expense's organization
        from utils import check_organization_permission
        if not check_organization_permission(current_user.id, organization_id, ['owner', 'admin']):
            abort(403)  # User doesn't have permission in this organization
        
        # Can only approve submitted expenses
        if expense.status != ExpenseStatus.SUBMITTED.value:
            flash('Only submitted expenses can be approved', 'warning')
            return redirect(url_for('expense.view_expense', expense_id=expense_id))
        
        # Update the expense status
        expense.status = ExpenseStatus.APPROVED.value
        expense.approval_date = datetime.utcnow()
        expense.approved_by_user_id = current_user.id
        
        db.session.commit()
        
        flash('Expense approved successfully', 'success')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error approving expense: {str(e)}")
        flash(f'Error approving expense: {str(e)}', 'danger')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))

@expense_bp.route('/expenses/<int:expense_id>/reject', methods=['POST'])
@login_required
def reject_expense(expense_id):
    """Reject an expense (admin/owner only)."""
    try:
        # First get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        
        # Get organization ID from the expense
        organization_id = expense.organization_id
        
        # Use the helper function to check permission in the expense's organization
        from utils import check_organization_permission
        if not check_organization_permission(current_user.id, organization_id, ['owner', 'admin']):
            abort(403)  # User doesn't have permission in this organization
        
        # Can reject submitted or approved expenses
        if expense.status not in [ExpenseStatus.SUBMITTED.value, ExpenseStatus.APPROVED.value]:
            flash('Only submitted or approved expenses can be rejected', 'warning')
            return redirect(url_for('expense.view_expense', expense_id=expense_id))
        
        # Add rejection reason from form
        rejection_reason = request.form.get('rejection_reason', '')
        if rejection_reason:
            expense.notes = f"REJECTED: {rejection_reason}\n\nOriginal notes: {expense.notes or ''}"
        
        # Update the expense status
        expense.status = ExpenseStatus.REJECTED.value
        
        db.session.commit()
        
        flash('Expense rejected successfully', 'success')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error rejecting expense: {str(e)}")
        flash(f'Error rejecting expense: {str(e)}', 'danger')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))

@expense_bp.route('/expenses/<int:expense_id>/cancel', methods=['POST'])
@login_required
def cancel_expense(expense_id):
    """Cancel a submitted expense (submitter only)."""
    # Get the expense
    expense = CompanyExpense.query.get_or_404(expense_id)
    
    # Verify the expense belongs to the current user
    if expense.user_id != current_user.id:
        abort(403)
    
    # Can only cancel submitted expenses
    if expense.status != ExpenseStatus.SUBMITTED.value:
        flash('Only submitted expenses can be cancelled', 'warning')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))
    
    try:
        # Update the expense status
        expense.status = ExpenseStatus.CANCELLED.value
        expense.notes = expense.notes + '\n[CANCELLED BY USER]' if expense.notes else '[CANCELLED BY USER]'
        
        db.session.commit()
        
        flash('Expense cancelled successfully', 'success')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error cancelling expense: {str(e)}")
        flash(f'Error cancelling expense: {str(e)}', 'danger')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))

@expense_bp.route('/expenses/<int:expense_id>/reimburse', methods=['POST'])
@login_required
def mark_reimbursed(expense_id):
    """Mark an expense as reimbursed (admin/owner only)."""
    try:
        # First get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        
        # Get organization ID from the expense
        organization_id = expense.organization_id
        
        # Use the helper function to check permission in the expense's organization
        from utils import check_organization_permission
        if not check_organization_permission(current_user.id, organization_id, ['owner', 'admin']):
            abort(403)  # User doesn't have permission in this organization
        
        # Can only reimburse approved expenses
        if expense.status != ExpenseStatus.APPROVED.value:
            flash('Only approved expenses can be reimbursed', 'warning')
            return redirect(url_for('expense.view_expense', expense_id=expense_id))
        
        # Update the expense status
        expense.status = ExpenseStatus.REIMBURSED.value
        expense.reimbursed_date = datetime.utcnow()
        expense.reimbursed_by_user_id = current_user.id
        
        db.session.commit()
        
        flash('Expense marked as reimbursed successfully', 'success')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error reimbursing expense: {str(e)}")
        flash(f'Error reimbursing expense: {str(e)}', 'danger')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))

@expense_bp.route('/expenses/<int:expense_id>/update-ajax', methods=['POST'])
@login_required
def update_expense_ajax(expense_id):
    import logging
    """Update an expense via AJAX."""
    # Get the expense
    expense = CompanyExpense.query.get_or_404(expense_id)
    
    # Log the received data for debugging
    logging.info(f"Update expense {expense_id} request data: {request.json}")
    
    # Verify the expense belongs to the current user or user has proper permissions
    if expense.user_id != current_user.id and not check_organization_permission(current_user.id, expense.organization_id, ['owner', 'admin']):
        logging.warning(f"Permission denied for user {current_user.id} to update expense {expense_id}")
        return jsonify({'success': False, 'error': 'You do not have permission to edit this expense'}), 403
    
    # Verify expense is still in submitted status
    if expense.status != ExpenseStatus.SUBMITTED.value:
        logging.warning(f"Cannot edit expense {expense_id} because it has status {expense.status}")
        return jsonify({'success': False, 'error': 'This expense cannot be edited because it has been processed'}), 400
    
    # Get JSON data
    data = request.json
    
    try:
        # Update the expense fields
        if 'description' in data:
            expense.description = data['description']
        
        if 'is_reimbursable' in data:
            expense.is_reimbursable = data['is_reimbursable']
            
        if 'notes' in data:
            expense.notes = data['notes']
            
        if 'vendor_name' in data:
            # Get the associated receipt and update its vendor_name
            receipt = Receipt.query.get(expense.receipt_id)
            if receipt:
                receipt.vendor_name = data['vendor_name']
            else:
                logging.error(f"Receipt not found for expense {expense_id}")
                return jsonify({'success': False, 'error': 'Receipt not found'}), 404
            
        # Handle organization and client updates (with validation)
        if 'organization_id' in data:
            if data['organization_id']:
                org_id = int(data['organization_id'])
                org = Organization.query.get(org_id)
                if not org:
                    return jsonify({'success': False, 'error': 'Invalid organization selected'}), 400
                expense.organization_id = org_id
                
                # Reset client if organization changed
                if 'client_id' not in data:
                    expense.client_id = None
            else:
                expense.organization_id = None
                expense.client_id = None
                
        if 'client_id' in data:
            if data['client_id']:
                client_id = int(data['client_id'])
                client = Client.query.get(client_id)
                if not client or (expense.organization_id and client.organization_id != expense.organization_id):
                    return jsonify({'success': False, 'error': 'Invalid client selected for this organization'}), 400
                expense.client_id = client_id
            else:
                expense.client_id = None
        
        # Save changes
        db.session.commit()
        
        # Get updated data for response
        organization_name = None
        if expense.organization_id:
            org = Organization.query.get(expense.organization_id)
            organization_name = org.name if org else None
            
        client_name = None
        if expense.client_id:
            client = Client.query.get(expense.client_id)
            client_name = client.name if client else None
            
        # Get the receipt again to include its vendor_name in the response
        receipt = Receipt.query.get(expense.receipt_id)
        receipt_vendor_name = receipt.vendor_name if receipt else ""
        
        return jsonify({
            'success': True, 
            'message': 'Expense updated successfully',
            'expense': {
                'description': expense.description,
                'organization_id': expense.organization_id,
                'organization_name': organization_name,
                'client_id': expense.client_id,
                'client_name': client_name,
                'is_reimbursable': expense.is_reimbursable,
                'notes': expense.notes,
                'vendor_name': receipt_vendor_name  # Use the receipt's vendor_name
            }
        })
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating expense {expense_id}: {str(e)}")
        logging.error(f"Request data: {data}")
        return jsonify({
            'success': False, 
            'error': str(e),
            'request_data': data,  # Echo back the data for debugging
            'field_names': list(data.keys()) if data else []
        }), 500


@expense_bp.route('/receipts/<int:receipt_id>/create-expense', methods=['GET', 'POST'])
@login_required
def create_expense_from_receipt(receipt_id):
    """Create a company expense directly from a receipt with organization selection."""
    # Verify the receipt belongs to the user
    receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first_or_404()
    
    # Use standardized helper to get the image URL
    from utils import get_receipt_image_url
    
    # Generate standardized image URL
    receipt.image_url = get_receipt_image_url(receipt)
    logging.debug(f"Using standardized image URL for receipt ID {receipt_id}: {receipt.image_url}")
    
    # Check if there's already an expense for this receipt
    existing_expense = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
    if existing_expense:
        # Allow viewing/editing existing expense instead of creating a new one
        flash('This receipt has already been submitted as a company expense. You can edit the existing expense.', 'info')
        return redirect(url_for('expense.view_expense', expense_id=existing_expense.id))
    
    # Get all user's organizations
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    
    # Check if user belongs to any organizations
    if not user_orgs:
        flash('You need to be part of an organization to submit expenses', 'warning')
        return redirect(url_for('organizations'))
    
    # Get organization IDs and objects
    user_org_ids = [org.organization_id for org in user_orgs]
    organizations = Organization.query.filter(Organization.id.in_(user_org_ids)).all()
    
    # Get default organization ID
    default_org_id = None
    for org_user in user_orgs:
        if org_user.is_default:
            default_org_id = org_user.organization_id
            break
    
    # If no default organization, use the first one
    if not default_org_id and organizations:
        default_org_id = organizations[0].id
    
    if request.method == 'POST':
        try:
            description = request.form.get('description', '')
            notes = request.form.get('notes', '')
            is_reimbursable = request.form.get('is_reimbursable') == 'true'
            
            # Get organization ID from form 
            organization_id = request.form.get('organization_id')
            
            # Validate organization ID
            if not organization_id or not organization_id.isdigit():
                flash('Please select a valid organization', 'danger')
                return redirect(url_for('create_expense_from_receipt', receipt_id=receipt_id))
                
            organization_id = int(organization_id)
            
            # Verify user has access to this organization
            org_user = OrganizationUser.query.filter_by(
                user_id=current_user.id,
                organization_id=organization_id
            ).first()
            
            if not org_user:
                flash('You do not have access to the selected organization', 'danger')
                return redirect(url_for('create_expense_from_receipt', receipt_id=receipt_id))
                
            # Get client ID from form
            client_id = request.form.get('client_id')
            
            # Convert client_id to int or None
            if client_id and client_id.isdigit():
                client_id = int(client_id)
                
                # If client_id is provided, verify it belongs to the organization
                if client_id:
                    client = Client.query.filter_by(id=client_id, organization_id=organization_id).first()
                    if not client:
                        flash('Selected client not found or does not belong to the selected organization', 'danger')
                        return redirect(url_for('create_expense_from_receipt', receipt_id=receipt_id))
            else:
                client_id = None
            
            # Create the company expense
            company_expense = CompanyExpense(
                receipt_id=receipt_id,
                user_id=current_user.id,
                organization_id=organization_id,
                client_id=client_id,
                description=description,
                status=ExpenseStatus.SUBMITTED.value,
                is_reimbursable=is_reimbursable,
                notes=notes,
                submitted_date=datetime.utcnow()
            )
            
            db.session.add(company_expense)
            db.session.commit()
            
            flash('Expense submitted successfully for reimbursement', 'success')
            return redirect(url_for('expense.expenses'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error submitting expense: {str(e)}")
            flash(f'Error submitting expense: {str(e)}', 'danger')
            return redirect(url_for('view_receipt', receipt_id=receipt_id))
    
    # For initial page load, get clients for the default organization
    clients = Client.query.filter_by(organization_id=default_org_id).order_by(Client.name).all() if default_org_id else []
    
    return render_template(
        'create_expense_from_receipt.html',
        receipt=receipt,
        clients=clients,
        organizations=organizations,
        default_org_id=default_org_id
    )

def register_routes(app):
    """Register the expense routes with the app."""
    app.register_blueprint(expense_bp, url_prefix='')
    app.jinja_env.globals.update(_now=datetime.utcnow)
    logging.info('Expense management routes loaded successfully')