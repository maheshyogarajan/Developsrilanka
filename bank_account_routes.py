"""
Routes for bank account management.
"""
import logging
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import app, db
from models import BankAccount

@app.route('/bank-accounts')
@login_required
def bank_accounts():
    """Render the bank accounts page showing list of user's bank accounts."""
    accounts = BankAccount.query.filter_by(user_id=current_user.id).order_by(BankAccount.account_name).all()
    return render_template('bank_accounts.html', accounts=accounts)

@app.route('/bank-accounts/create', methods=['GET', 'POST'])
@login_required
def create_bank_account():
    """Create a new bank account."""
    if request.method == 'POST':
        try:
            # Get form data
            account_name = request.form.get('account_name')
            bank_name = request.form.get('bank_name')
            account_number = request.form.get('account_number')
            branch_name = request.form.get('branch_name', '')
            swift_code = request.form.get('swift_code', '')
            iban = request.form.get('iban', '')
            is_default = request.form.get('is_default') == 'on'
            
            # Validate required fields
            if not account_name or not bank_name or not account_number:
                flash('Please fill out all required fields.', 'danger')
                return redirect(url_for('create_bank_account'))
            
            # If this is set as default, unset others
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
                is_default=is_default,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.session.add(new_account)
            db.session.commit()
            
            flash('Bank account created successfully!', 'success')
            return redirect(url_for('bank_accounts'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating bank account: {str(e)}")
            flash(f'Error creating bank account: {str(e)}', 'danger')
    
    # GET request - render form
    return render_template('create_bank_account.html')

@app.route('/bank-accounts/<int:account_id>')
@login_required
def view_bank_account(account_id):
    """View a specific bank account."""
    account = BankAccount.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    return render_template('view_bank_account.html', account=account)

@app.route('/bank-accounts/<int:account_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_bank_account(account_id):
    """Edit an existing bank account."""
    account = BankAccount.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        try:
            # Get form data
            account.account_name = request.form.get('account_name')
            account.bank_name = request.form.get('bank_name')
            account.account_number = request.form.get('account_number')
            account.branch_name = request.form.get('branch_name', '')
            account.swift_code = request.form.get('swift_code', '')
            account.iban = request.form.get('iban', '')
            is_default = request.form.get('is_default') == 'on'
            
            # Validate required fields
            if not account.account_name or not account.bank_name or not account.account_number:
                flash('Please fill out all required fields.', 'danger')
                return redirect(url_for('edit_bank_account', account_id=account.id))
            
            # If this is set as default, unset others
            if is_default and not account.is_default:
                default_accounts = BankAccount.query.filter_by(user_id=current_user.id, is_default=True).all()
                for default_account in default_accounts:
                    default_account.is_default = False
            
            account.is_default = is_default
            account.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            flash('Bank account updated successfully!', 'success')
            return redirect(url_for('view_bank_account', account_id=account.id))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating bank account: {str(e)}")
            flash(f'Error updating bank account: {str(e)}', 'danger')
            return redirect(url_for('view_bank_account', account_id=account.id))
    
    # GET request - render form
    return render_template('edit_bank_account.html', account=account)

@app.route('/bank-accounts/<int:account_id>/delete', methods=['POST'])
@login_required
def delete_bank_account(account_id):
    """Delete a bank account."""
    account = BankAccount.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    
    # Check if account is used in any invoices
    has_invoices = db.session.query(db.exists().where(db.text(f"invoice.bank_account_id = {account_id}"))).scalar()
    
    if has_invoices:
        flash('Cannot delete bank account that is used in invoices. Remove it from invoices first.', 'warning')
        return redirect(url_for('view_bank_account', account_id=account.id))
    
    try:
        # If this is a default account, find another account to make default
        if account.is_default:
            other_account = BankAccount.query.filter(
                BankAccount.user_id == current_user.id,
                BankAccount.id != account.id
            ).first()
            
            if other_account:
                other_account.is_default = True
        
        db.session.delete(account)
        db.session.commit()
        
        flash('Bank account deleted successfully!', 'success')
        return redirect(url_for('bank_accounts'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting bank account: {str(e)}")
        flash(f'Error deleting bank account: {str(e)}', 'danger')
        return redirect(url_for('view_bank_account', account_id=account.id))

@app.route('/api/bank-accounts')
@login_required
def api_bank_accounts():
    """API endpoint to get all bank accounts for current user."""
    accounts = BankAccount.query.filter_by(user_id=current_user.id).order_by(BankAccount.account_name).all()
    return jsonify([account.to_dict() for account in accounts])

@app.route('/api/bank-accounts/default')
@login_required
def api_default_bank_account():
    """API endpoint to get default bank account for current user."""
    account = BankAccount.query.filter_by(user_id=current_user.id, is_default=True).first()
    if account:
        return jsonify(account.to_dict())
    return jsonify({})