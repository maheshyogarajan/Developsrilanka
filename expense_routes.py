"""
Routes for company expense management and reimbursement.
"""
from datetime import datetime
import logging

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from models import CompanyExpense, ExpenseStatus, Receipt, User, Organization
from decorators import role_required

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
                   u.name as submitter_name
            FROM company_expense e
            JOIN receipt r ON e.receipt_id = r.id
            JOIN user u ON e.user_id = u.id
            WHERE e.organization_id = :org_id
            ORDER BY e.submitted_date DESC
        """), {'org_id': organization_id})
    else:
        # Regular user view - get only their expenses
        expense_results = db.session.execute(text("""
            SELECT e.id, e.receipt_id, e.user_id, e.description, e.status,
                   e.submitted_date, e.approval_date, e.reimbursed_date, e.notes,
                   r.vendor_name, r.date, r.total_amount, r.image_path,
                   u.name as submitter_name
            FROM company_expense e
            JOIN receipt r ON e.receipt_id = r.id
            JOIN user u ON e.user_id = u.id
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
            
            # Verify the receipt belongs to the user
            receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
            if not receipt:
                flash('Receipt not found or access denied', 'danger')
                return redirect(url_for('expense.expenses'))
            
            # Create the company expense
            company_expense = CompanyExpense(
                receipt_id=receipt_id,
                user_id=current_user.id,
                organization_id=organization_id,
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
               r.expense_major_category, r.expense_minor_category
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
            'expense_minor_category': row[6]
        }
        receipts.append(receipt)
    
    if not receipts:
        flash('No receipts available for expense submission. Please scan receipts first.', 'info')
        return redirect(url_for('index'))
    
    return render_template('submit_expense.html', receipts=receipts)

@expense_bp.route('/expenses/<int:expense_id>')
@login_required
def view_expense(expense_id):
    """View a specific expense."""
    # Get user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    # Get user's role in organization
    role_result = db.session.execute(text("""
        SELECT role FROM organization_user
        WHERE user_id = :user_id AND organization_id = :org_id
        LIMIT 1
    """), {'user_id': current_user.id, 'org_id': organization_id}).first()
    
    user_org_role = role_result[0] if role_result else None
    
    # Get the expense with detailed information
    expense_result = db.session.execute(text("""
        SELECT e.id, e.receipt_id, e.user_id, e.organization_id, e.description, 
               e.status, e.submitted_date, e.approval_date, e.reimbursed_date,
               e.approved_by_user_id, e.reimbursed_by_user_id, e.notes,
               r.vendor_name, r.date, r.total_amount, r.image_path,
               r.vendor_address, r.vendor_contact, r.vat_registration_number,
               u.name as submitter_name, u.email as submitter_email,
               a.name as approver_name, rb.name as reimburser_name
        FROM company_expense e
        JOIN receipt r ON e.receipt_id = r.id
        JOIN user u ON e.user_id = u.id
        LEFT JOIN user a ON e.approved_by_user_id = a.id
        LEFT JOIN user rb ON e.reimbursed_by_user_id = rb.id
        WHERE e.id = :expense_id
        LIMIT 1
    """), {'expense_id': expense_id}).first()
    
    if not expense_result:
        abort(404)
    
    # Check if user has permission to view this expense
    # Allow if: user is the submitter, or user is owner/admin of the organization
    if expense_result[2] != current_user.id and user_org_role not in ['owner', 'admin']:
        abort(403)
    
    expense = {
        'id': expense_result[0],
        'receipt_id': expense_result[1],
        'user_id': expense_result[2],
        'organization_id': expense_result[3],
        'description': expense_result[4],
        'status': expense_result[5],
        'submitted_date': expense_result[6],
        'approval_date': expense_result[7],
        'reimbursed_date': expense_result[8],
        'approved_by_user_id': expense_result[9],
        'reimbursed_by_user_id': expense_result[10],
        'notes': expense_result[11],
        'vendor_name': expense_result[12],
        'receipt_date': expense_result[13],
        'total_amount': expense_result[14],
        'image_path': expense_result[15],
        'vendor_address': expense_result[16],
        'vendor_contact': expense_result[17],
        'vat_registration_number': expense_result[18],
        'submitter_name': expense_result[19],
        'submitter_email': expense_result[20],
        'approver_name': expense_result[21],
        'reimburser_name': expense_result[22]
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
    
    return render_template('view_expense.html', 
                           expense=expense, 
                           organization=organization,
                           user_org_role=user_org_role)

@expense_bp.route('/expenses/<int:expense_id>/approve', methods=['POST'])
@login_required
def approve_expense(expense_id):
    """Approve an expense for reimbursement (admin/owner only)."""
    # Get user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    # Get user's role in organization
    role_result = db.session.execute(text("""
        SELECT role FROM organization_user
        WHERE user_id = :user_id AND organization_id = :org_id
        LIMIT 1
    """), {'user_id': current_user.id, 'org_id': organization_id}).first()
    
    user_org_role = role_result[0] if role_result else None
    
    # Check if user has permission to approve expenses
    if user_org_role not in ['owner', 'admin']:
        abort(403)
    
    try:
        # Get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        
        # Verify the expense belongs to user's organization
        if expense.organization_id != organization_id:
            abort(403)
        
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
    # Get user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    # Get user's role in organization
    role_result = db.session.execute(text("""
        SELECT role FROM organization_user
        WHERE user_id = :user_id AND organization_id = :org_id
        LIMIT 1
    """), {'user_id': current_user.id, 'org_id': organization_id}).first()
    
    user_org_role = role_result[0] if role_result else None
    
    # Check if user has permission to reject expenses
    if user_org_role not in ['owner', 'admin']:
        abort(403)
    
    try:
        # Get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        
        # Verify the expense belongs to user's organization
        if expense.organization_id != organization_id:
            abort(403)
        
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

@expense_bp.route('/expenses/<int:expense_id>/reimburse', methods=['POST'])
@login_required
def reimburse_expense(expense_id):
    """Mark an expense as reimbursed (admin/owner only)."""
    # Get user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    # Get user's role in organization
    role_result = db.session.execute(text("""
        SELECT role FROM organization_user
        WHERE user_id = :user_id AND organization_id = :org_id
        LIMIT 1
    """), {'user_id': current_user.id, 'org_id': organization_id}).first()
    
    user_org_role = role_result[0] if role_result else None
    
    # Check if user has permission to mark expenses as reimbursed
    if user_org_role not in ['owner', 'admin']:
        abort(403)
    
    try:
        # Get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        
        # Verify the expense belongs to user's organization
        if expense.organization_id != organization_id:
            abort(403)
        
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

@expense_bp.route('/expenses/<int:expense_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    """Edit an expense (submitter only, and only if not approved/reimbursed yet)."""
    # Get the expense
    expense = CompanyExpense.query.get_or_404(expense_id)
    
    # Verify the expense belongs to the current user
    if expense.user_id != current_user.id:
        abort(403)
    
    # Can only edit submitted expenses
    if expense.status != ExpenseStatus.SUBMITTED.value:
        flash('Only submitted expenses can be edited', 'warning')
        return redirect(url_for('expense.view_expense', expense_id=expense_id))
    
    if request.method == 'POST':
        try:
            # Update expense description and notes
            expense.description = request.form.get('description', '')
            expense.notes = request.form.get('notes', '')
            expense.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            flash('Expense updated successfully', 'success')
            return redirect(url_for('expense.view_expense', expense_id=expense_id))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating expense: {str(e)}")
            flash(f'Error updating expense: {str(e)}', 'danger')
            return redirect(url_for('expense.edit_expense', expense_id=expense_id))
    
    # GET request - render form
    # Get receipt details
    receipt = Receipt.query.get_or_404(expense.receipt_id)
    
    return render_template('edit_expense.html', expense=expense, receipt=receipt)

@expense_bp.route('/receipts/<int:receipt_id>/create-expense', methods=['GET', 'POST'])
@login_required
def create_expense_from_receipt(receipt_id):
    """Create a company expense directly from a receipt."""
    # Verify the receipt belongs to the user
    receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first_or_404()
    
    # Check if there's already an expense for this receipt
    existing_expense = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
    if existing_expense:
        flash('This receipt has already been submitted as an expense', 'warning')
        return redirect(url_for('expense.view_expense', expense_id=existing_expense.id))
    
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
            description = request.form.get('description', '')
            notes = request.form.get('notes', '')
            is_reimbursable = request.form.get('is_reimbursable') == 'true'
            
            # Create the company expense
            company_expense = CompanyExpense(
                receipt_id=receipt_id,
                user_id=current_user.id,
                organization_id=organization_id,
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
    
    # GET request - render form
    return render_template('create_expense_from_receipt.html', receipt=receipt)

def register_routes(app):
    """Register the expense routes with the app."""
    app.register_blueprint(expense_bp, url_prefix='')
    app.jinja_env.globals.update(_now=datetime.utcnow)
    logging.info('Expense management routes loaded successfully')