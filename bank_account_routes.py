"""
Routes for bank account management.
"""
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from models import BankAccount, Invoice

# Create a Blueprint for bank account routes
bank_account_bp = Blueprint('bank_account', __name__)

@bank_account_bp.route('/bank-accounts')
@login_required
def bank_accounts():
    """Render the bank accounts page showing list of user's bank accounts."""
    
    # Execute raw SQL that doesn't reference the new column
    result = db.session.execute(text(
        'SELECT id, user_id, account_name, bank_name, account_number, '
        'branch_name, swift_code, iban, is_default, created_at, updated_at '
        'FROM bank_account WHERE user_id = :user_id'
    ), {'user_id': current_user.id})
    
    # Convert to list of dictionaries
    accounts = [
        {
            'id': row[0], 
            'user_id': row[1],
            'account_name': row[2],
            'bank_name': row[3], 
            'account_number': row[4],
            'branch_name': row[5],
            'swift_code': row[6],
            'iban': row[7],
            'is_default': row[8],
            'created_at': row[9],
            'updated_at': row[10]
        } for row in result
    ]
    
    return render_template('bank_accounts.html', accounts=accounts)

@bank_account_bp.route('/bank-accounts/create', methods=['GET', 'POST'])
@login_required
def create_bank_account():
    """Create a new bank account."""
    if request.method == 'POST':
        account_name = request.form.get('account_name')
        bank_name = request.form.get('bank_name')
        account_number = request.form.get('account_number')
        branch_name = request.form.get('branch_name')
        swift_code = request.form.get('swift_code')
        iban = request.form.get('iban')
        is_default = True if request.form.get('is_default') else False
        
        # Validate required fields
        if not account_name or not bank_name or not account_number:
            flash('Please fill in all required fields.', 'danger')
            return render_template('create_bank_account.html')
        
        # If this account is set as default, unset any existing default accounts
        if is_default:
            default_accounts = BankAccount.query.filter_by(user_id=current_user.id, is_default=True).all()
            for account in default_accounts:
                account.is_default = False
            
        # Create new bank account
        new_account = BankAccount(
            user_id=current_user.id,
            account_name=account_name,
            bank_name=bank_name,
            account_number=account_number,
            branch_name=branch_name,
            swift_code=swift_code,
            iban=iban,
            is_default=is_default
        )
        
        db.session.add(new_account)
        db.session.commit()
        
        flash('Bank account added successfully.', 'success')
        return redirect(url_for('bank_accounts'))
    
    return render_template('create_bank_account.html')

@bank_account_bp.route('/bank-accounts/<int:account_id>')
@login_required
def view_bank_account(account_id):
    """View a specific bank account."""
    # Temporarily use raw SQL to avoid the organization_id column issue
    
    # Execute raw SQL that doesn't reference the new column
    result = db.session.execute(text(
        'SELECT id, user_id, account_name, bank_name, account_number, '
        'branch_name, swift_code, iban, is_default, created_at, updated_at '
        'FROM bank_account WHERE id = :account_id AND user_id = :user_id'
    ), {'account_id': account_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
        
    account = {
        'id': result[0], 
        'user_id': result[1],
        'account_name': result[2],
        'bank_name': result[3], 
        'account_number': result[4],
        'branch_name': result[5],
        'swift_code': result[6],
        'iban': result[7],
        'is_default': result[8],
        'created_at': result[9],
        'updated_at': result[10]
    }
    return render_template('view_bank_account.html', account=account)

@bank_account_bp.route('/bank-accounts/<int:account_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_bank_account(account_id):
    """Edit an existing bank account."""
    account = BankAccount.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        account_name = request.form.get('account_name')
        bank_name = request.form.get('bank_name')
        account_number = request.form.get('account_number')
        branch_name = request.form.get('branch_name')
        swift_code = request.form.get('swift_code')
        iban = request.form.get('iban')
        is_default = True if request.form.get('is_default') else False
        
        # Validate required fields
        if not account_name or not bank_name or not account_number:
            flash('Please fill in all required fields.', 'danger')
            return render_template('edit_bank_account.html', account=account)
        
        # If this account is set as default, unset any existing default accounts
        if is_default and not account.is_default:
            default_accounts = BankAccount.query.filter_by(user_id=current_user.id, is_default=True).all()
            for default_account in default_accounts:
                default_account.is_default = False
        
        # Update account details
        account.account_name = account_name
        account.bank_name = bank_name
        account.account_number = account_number
        account.branch_name = branch_name
        account.swift_code = swift_code
        account.iban = iban
        account.is_default = is_default
        account.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        flash('Bank account updated successfully.', 'success')
        return redirect(url_for('view_bank_account', account_id=account.id))
    
    return render_template('edit_bank_account.html', account=account)

@bank_account_bp.route('/bank-accounts/<int:account_id>/delete', methods=['POST'])
@login_required
def delete_bank_account(account_id):
    """Delete a bank account."""
    account = BankAccount.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    
    # Check if account is used in any invoices
    invoices_using_account = Invoice.query.filter_by(bank_account_id=account.id).count()
    if invoices_using_account > 0:
        flash(f'Cannot delete this account as it is used in {invoices_using_account} invoice(s).', 'danger')
        return redirect(url_for('view_bank_account', account_id=account.id))
    
    # If deleting the default account, set another one as default if available
    if account.is_default:
        other_account = BankAccount.query.filter(
            BankAccount.user_id == current_user.id,
            BankAccount.id != account.id
        ).first()
        if other_account:
            other_account.is_default = True
    
    db.session.delete(account)
    db.session.commit()
    
    flash('Bank account deleted successfully.', 'success')
    return redirect(url_for('bank_accounts'))

@bank_account_bp.route('/api/bank-accounts')
@login_required
def api_bank_accounts():
    """API endpoint to get all bank accounts for current user."""
    # Temporarily use raw SQL to avoid the organization_id column issue
    from sqlalchemy import text
    
    # Execute raw SQL that doesn't reference the new column
    result = db.session.execute(text(
        'SELECT id, user_id, account_name, bank_name, account_number, '
        'branch_name, swift_code, iban, is_default, created_at, updated_at '
        'FROM bank_account WHERE user_id = :user_id'
    ), {'user_id': current_user.id})
    
    # Convert to list of dictionaries
    accounts = [
        {
            'id': row[0], 
            'user_id': row[1],
            'account_name': row[2],
            'bank_name': row[3], 
            'account_number': row[4],
            'branch_name': row[5],
            'swift_code': row[6],
            'iban': row[7],
            'is_default': row[8],
            'created_at': row[9],
            'updated_at': row[10]
        } for row in result
    ]
    
    return jsonify(accounts)

@bank_account_bp.route('/api/bank-accounts/default')
@login_required
def api_default_bank_account():
    """API endpoint to get default bank account for current user."""
    # Temporarily use raw SQL to avoid the organization_id column issue
    from sqlalchemy import text
    
    # Execute raw SQL that doesn't reference the new column
    result = db.session.execute(text(
        'SELECT id, user_id, account_name, bank_name, account_number, '
        'branch_name, swift_code, iban, is_default, created_at, updated_at '
        'FROM bank_account WHERE user_id = :user_id AND is_default = true LIMIT 1'
    ), {'user_id': current_user.id}).first()
    
    if result:
        account = {
            'id': result[0], 
            'user_id': result[1],
            'account_name': result[2],
            'bank_name': result[3], 
            'account_number': result[4],
            'branch_name': result[5],
            'swift_code': result[6],
            'iban': result[7],
            'is_default': result[8],
            'created_at': result[9],
            'updated_at': result[10]
        }
        return jsonify(account)
    return jsonify({})

# These are the routes to be registered in app.py
def register_routes(app):
    """Register the bank account routes with the app."""
    # Register the routes directly on the app instead of using blueprint
    app.route('/bank-accounts')(bank_accounts)
    app.route('/bank-accounts/create', methods=['GET', 'POST'])(create_bank_account)
    app.route('/bank-accounts/<int:account_id>')(view_bank_account)
    app.route('/bank-accounts/<int:account_id>/edit', methods=['GET', 'POST'])(edit_bank_account)
    app.route('/bank-accounts/<int:account_id>/delete', methods=['POST'])(delete_bank_account)
    app.route('/api/bank-accounts')(api_bank_accounts)
    app.route('/api/bank-accounts/default')(api_default_bank_account)