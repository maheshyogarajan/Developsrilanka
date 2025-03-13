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
            
        # Get user's default organization
        default_org = db.session.execute(text(
            'SELECT organization_id FROM organization_user '
            'WHERE user_id = :user_id AND is_default = true LIMIT 1'
        ), {'user_id': current_user.id}).first()
        
        if not default_org:
            flash('You need to have a default organization to create a bank account.', 'danger')
            return render_template('create_bank_account.html')

        # Create new bank account with organization association
        new_account = BankAccount(
            user_id=current_user.id,
            organization_id=default_org.organization_id, 
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
    # Get the account using raw SQL to avoid organization_id issues
    result = db.session.execute(text(
        'SELECT id, user_id, organization_id, account_name, bank_name, account_number, '
        'branch_name, swift_code, iban, is_default, created_at, updated_at '
        'FROM bank_account WHERE id = :account_id AND user_id = :user_id'
    ), {'account_id': account_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
        
    # Create an account object with properties to mimic original implementation
    class AccountObject:
        def __init__(self, data):
            self.id = data[0]
            self.user_id = data[1]
            self.organization_id = data[2]
            self.account_name = data[3]
            self.bank_name = data[4]
            self.account_number = data[5]
            self.branch_name = data[6]
            self.swift_code = data[7]
            self.iban = data[8]
            self.is_default = data[9]
            self.created_at = data[10]
            self.updated_at = data[11]
    
    account = AccountObject(result)
    
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
            # Clear default flag from any other accounts for this user
            db.session.execute(text(
                'UPDATE bank_account SET is_default = false '
                'WHERE user_id = :user_id AND id != :account_id AND is_default = true'
            ), {'user_id': current_user.id, 'account_id': account.id})
        
        # Use raw SQL to update account details while preserving organization_id
        db.session.execute(text("""
            UPDATE bank_account 
            SET account_name = :account_name,
                bank_name = :bank_name,
                account_number = :account_number,
                branch_name = :branch_name,
                swift_code = :swift_code,
                iban = :iban,
                is_default = :is_default,
                updated_at = :updated_at
            WHERE id = :id
        """), {
            'account_name': account_name,
            'bank_name': bank_name,
            'account_number': account_number,
            'branch_name': branch_name,
            'swift_code': swift_code,
            'iban': iban,
            'is_default': is_default,
            'updated_at': datetime.utcnow(),
            'id': account.id
        })
        
        db.session.commit()
        
        flash('Bank account updated successfully.', 'success')
        return redirect(url_for('view_bank_account', account_id=account.id))
    
    return render_template('edit_bank_account.html', account=account)

@bank_account_bp.route('/bank-accounts/<int:account_id>/delete', methods=['POST'])
@login_required
def delete_bank_account(account_id):
    """Delete a bank account."""
    # Get the account using raw SQL to avoid organization_id issues
    result = db.session.execute(text(
        'SELECT id, user_id, organization_id, is_default '
        'FROM bank_account WHERE id = :account_id AND user_id = :user_id'
    ), {'account_id': account_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
        
    # Create a simple object with needed properties
    class AccountObject:
        def __init__(self, data):
            self.id = data[0]
            self.user_id = data[1]
            self.organization_id = data[2]
            self.is_default = data[3]
    
    account = AccountObject(result)
    
    # Check if account is used in any invoices
    invoices_using_account = Invoice.query.filter_by(bank_account_id=account.id).count()
    if invoices_using_account > 0:
        flash(f'Cannot delete this account as it is used in {invoices_using_account} invoice(s).', 'danger')
        return redirect(url_for('view_bank_account', account_id=account.id))
    
    # If deleting the default account, set another one as default if available
    if account.is_default:
        # Find another account to set as default
        other_account = db.session.execute(text(
            'SELECT id FROM bank_account WHERE user_id = :user_id AND id != :account_id LIMIT 1'
        ), {'user_id': current_user.id, 'account_id': account.id}).first()
        
        if other_account:
            # Set the other account as default
            db.session.execute(text(
                'UPDATE bank_account SET is_default = true WHERE id = :id'
            ), {'id': other_account.id})
    
    # Use raw SQL to delete the account
    db.session.execute(text(
        'DELETE FROM bank_account WHERE id = :id'
    ), {'id': account.id})
    
    db.session.commit()
    
    flash('Bank account deleted successfully.', 'success')
    return redirect(url_for('bank_accounts'))

@bank_account_bp.route('/api/bank-accounts')
@login_required
def api_bank_accounts():
    """API endpoint to get all bank accounts for current user."""
    # Temporarily use raw SQL to avoid the organization_id column issue
    
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