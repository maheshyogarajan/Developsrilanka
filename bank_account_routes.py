"""
Routes for bank account management.
"""
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from models import BankAccount, Invoice, User, Organization, OrganizationUser, UserRole

# Create a Blueprint for bank account routes
bank_account_bp = Blueprint('bank_account', __name__)

@bank_account_bp.route('/bank-accounts')
@login_required
def bank_accounts():
    """Render the bank accounts page showing list of user's bank accounts."""
    
    # Get the user's organizations
    user_orgs = db.session.query(
        Organization.id, 
        Organization.name,
        OrganizationUser.is_default
    ).join(
        OrganizationUser, 
        Organization.id == OrganizationUser.organization_id
    ).filter(
        OrganizationUser.user_id == current_user.id
    ).all()
    
    # Get organization filter from query parameters
    org_filter = request.args.get('organization')
    if org_filter and org_filter.isdigit():
        org_filter = int(org_filter)
    else:
        org_filter = None
    
    # Execute query to get bank accounts with organization information
    query = """
        SELECT ba.id, ba.user_id, ba.organization_id, ba.account_name, ba.bank_name, 
               ba.account_number, ba.branch_name, ba.swift_code, ba.iban, ba.is_default, 
               ba.created_at, ba.updated_at, org.name as organization_name
        FROM bank_account ba
        JOIN organization org ON ba.organization_id = org.id
        WHERE ba.user_id = :user_id
    """
    
    params = {'user_id': current_user.id}
    
    # Apply organization filter if provided
    if org_filter:
        query += " AND ba.organization_id = :org_id"
        params['org_id'] = org_filter
    
    # Execute the query
    result = db.session.execute(text(query), params)
    
    # Convert to list of dictionaries
    accounts = [
        {
            'id': row[0], 
            'user_id': row[1],
            'organization_id': row[2],
            'account_name': row[3],
            'bank_name': row[4], 
            'account_number': row[5],
            'branch_name': row[6],
            'swift_code': row[7],
            'iban': row[8],
            'is_default': row[9],
            'created_at': row[10],
            'updated_at': row[11],
            'organization_name': row[12]
        } for row in result
    ]
    
    return render_template('bank_accounts.html', 
                          accounts=accounts, 
                          organizations=user_orgs,
                          selected_org=org_filter)

@bank_account_bp.route('/bank-accounts/create', methods=['GET', 'POST'])
@login_required
def create_bank_account():
    """Create a new bank account."""
    # Get user's organizations
    organizations = db.session.query(
        Organization.id, 
        Organization.name,
        OrganizationUser.is_default
    ).join(
        OrganizationUser, 
        Organization.id == OrganizationUser.organization_id
    ).filter(
        OrganizationUser.user_id == current_user.id
    ).all()
    
    # If user has no organizations, redirect to create one
    if not organizations:
        flash('You need to create an organization before adding a bank account.', 'warning')
        return redirect(url_for('create_organization'))
    
    # Find default organization ID
    default_org_id = None
    for org in organizations:
        if org.is_default:
            default_org_id = org.id
            break
    
    # If no default organization is set but organizations exist
    if default_org_id is None and organizations:
        default_org_id = organizations[0].id
    
    # Check which organizations have no bank accounts yet (for auto-checking default)
    orgs_without_accounts = []
    for org in organizations:
        # Check if this organization has any bank accounts
        account_count = BankAccount.query.filter_by(organization_id=org.id).count()
        if account_count == 0:
            orgs_without_accounts.append(org.id)
    
    if request.method == 'POST':
        account_name = request.form.get('account_name')
        bank_name = request.form.get('bank_name')
        account_number = request.form.get('account_number')
        branch_name = request.form.get('branch_name')
        swift_code = request.form.get('swift_code')
        iban = request.form.get('iban')
        is_default = True if request.form.get('is_default') else False
        organization_id = request.form.get('organization_id')
        
        # Validate required fields
        if not account_name or not bank_name or not account_number or not organization_id:
            flash('Please fill in all required fields including organization.', 'danger')
            return render_template('create_bank_account.html', organizations=organizations, default_org_id=default_org_id, orgs_without_accounts=orgs_without_accounts)
        
        # Convert organization_id to integer
        try:
            organization_id = int(organization_id)
        except (ValueError, TypeError):
            flash('Invalid organization selected.', 'danger')
            return render_template('create_bank_account.html', organizations=organizations, default_org_id=default_org_id, orgs_without_accounts=orgs_without_accounts)
        
        # Verify user has access to this organization
        org_user = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=organization_id
        ).first()
        
        if not org_user:
            flash('You do not have access to the selected organization.', 'danger')
            return render_template('create_bank_account.html', organizations=organizations, default_org_id=default_org_id, orgs_without_accounts=orgs_without_accounts)
        
        # If this account is set as default, unset any existing default accounts for this organization
        if is_default:
            db.session.execute(text("""
                UPDATE bank_account 
                SET is_default = false 
                WHERE organization_id = :org_id AND is_default = true
            """), {'org_id': organization_id})

        # Create new bank account with organization association
        new_account = BankAccount(
            user_id=current_user.id,
            organization_id=organization_id, 
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
    
    return render_template('create_bank_account.html', organizations=organizations, default_org_id=default_org_id, orgs_without_accounts=orgs_without_accounts)

@bank_account_bp.route('/bank-accounts/<int:account_id>')
@login_required
def view_bank_account(account_id):
    """View a specific bank account."""
    # Query to get bank account with organization information
    query = """
        SELECT ba.id, ba.user_id, ba.organization_id, ba.account_name, ba.bank_name, 
               ba.account_number, ba.branch_name, ba.swift_code, ba.iban, ba.is_default, 
               ba.created_at, ba.updated_at, org.name as organization_name
        FROM bank_account ba
        JOIN organization org ON ba.organization_id = org.id
        WHERE ba.id = :account_id AND ba.user_id = :user_id
    """
    
    result = db.session.execute(text(query), {
        'account_id': account_id, 
        'user_id': current_user.id
    }).first()
    
    if not result:
        abort(404)
        
    account = {
        'id': result[0], 
        'user_id': result[1],
        'organization_id': result[2],
        'account_name': result[3],
        'bank_name': result[4], 
        'account_number': result[5],
        'branch_name': result[6],
        'swift_code': result[7],
        'iban': result[8],
        'is_default': result[9],
        'created_at': result[10],
        'updated_at': result[11],
        'organization_name': result[12]
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
    
    # Get user's organizations for the dropdown
    organizations = db.session.query(
        Organization.id, 
        Organization.name,
        OrganizationUser.is_default
    ).join(
        OrganizationUser, 
        Organization.id == OrganizationUser.organization_id
    ).filter(
        OrganizationUser.user_id == current_user.id
    ).all()
    
    if request.method == 'POST':
        account_name = request.form.get('account_name')
        bank_name = request.form.get('bank_name')
        account_number = request.form.get('account_number')
        branch_name = request.form.get('branch_name')
        swift_code = request.form.get('swift_code')
        iban = request.form.get('iban')
        is_default = True if request.form.get('is_default') else False
        organization_id = request.form.get('organization_id')
        
        # Validate required fields
        if not account_name or not bank_name or not account_number or not organization_id:
            flash('Please fill in all required fields including organization.', 'danger')
            return render_template('edit_bank_account.html', account=account, organizations=organizations)
        
        # Convert organization_id to integer
        try:
            organization_id = int(organization_id)
        except (ValueError, TypeError):
            flash('Invalid organization selected.', 'danger')
            return render_template('edit_bank_account.html', account=account, organizations=organizations)
        
        # Verify user has access to this organization
        org_user = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=organization_id
        ).first()
        
        if not org_user:
            flash('You do not have access to the selected organization.', 'danger')
            return render_template('edit_bank_account.html', account=account, organizations=organizations)
        
        # If this account is set as default, unset any existing default accounts for this organization
        if is_default:
            db.session.execute(text("""
                UPDATE bank_account 
                SET is_default = false 
                WHERE organization_id = :org_id AND is_default = true AND id != :account_id
            """), {'org_id': organization_id, 'account_id': account.id})
        
        # Use raw SQL to update account details including organization_id
        db.session.execute(text("""
            UPDATE bank_account 
            SET account_name = :account_name,
                bank_name = :bank_name,
                account_number = :account_number,
                branch_name = :branch_name,
                swift_code = :swift_code,
                iban = :iban,
                is_default = :is_default,
                organization_id = :organization_id,
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
            'organization_id': organization_id,
            'updated_at': datetime.utcnow(),
            'id': account.id
        })
        
        db.session.commit()
        
        flash('Bank account updated successfully.', 'success')
        return redirect(url_for('view_bank_account', account_id=account.id))
    
    return render_template('edit_bank_account.html', account=account, organizations=organizations)

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
    # Get organization filter from query parameters
    org_filter = request.args.get('organization')
    if org_filter and org_filter.isdigit():
        org_filter = int(org_filter)

    # Prepare the SQL query with organization_id
    query = """
        SELECT id, user_id, organization_id, account_name, bank_name, account_number, 
               branch_name, swift_code, iban, is_default, created_at, updated_at 
        FROM bank_account 
        WHERE user_id = :user_id
    """
    
    params = {'user_id': current_user.id}
    
    # Apply organization filter if provided
    if org_filter:
        query += " AND organization_id = :org_id"
        params['org_id'] = org_filter
    
    # Execute the query
    result = db.session.execute(text(query), params)
    
    # Convert to list of dictionaries
    accounts = [
        {
            'id': row[0], 
            'user_id': row[1],
            'organization_id': row[2],
            'account_name': row[3],
            'bank_name': row[4], 
            'account_number': row[5],
            'branch_name': row[6],
            'swift_code': row[7],
            'iban': row[8],
            'is_default': row[9],
            'created_at': row[10],
            'updated_at': row[11]
        } for row in result
    ]
    
    return jsonify(accounts)

@bank_account_bp.route('/api/bank-accounts/default')
@login_required
def api_default_bank_account():
    """API endpoint to get default bank account for current user."""
    # Get organization filter from query parameters
    org_filter = request.args.get('organization')
    if org_filter and org_filter.isdigit():
        org_filter = int(org_filter)
    
    # Prepare the SQL query with organization_id
    query = """
        SELECT id, user_id, organization_id, account_name, bank_name, account_number, 
               branch_name, swift_code, iban, is_default, created_at, updated_at 
        FROM bank_account 
        WHERE user_id = :user_id AND is_default = true
    """
    
    params = {'user_id': current_user.id}
    
    # Apply organization filter if provided
    if org_filter:
        query += " AND organization_id = :org_id"
        params['org_id'] = org_filter
    
    query += " LIMIT 1"
    
    # Execute the query
    result = db.session.execute(text(query), params).first()
    
    if result:
        account = {
            'id': result[0], 
            'user_id': result[1],
            'organization_id': result[2],
            'account_name': result[3],
            'bank_name': result[4], 
            'account_number': result[5],
            'branch_name': result[6],
            'swift_code': result[7],
            'iban': result[8],
            'is_default': result[9],
            'created_at': result[10],
            'updated_at': result[11]
        }
        return jsonify(account)
    return jsonify({})

@bank_account_bp.route('/organization/<int:org_id>/bank-accounts')
@login_required
def redirect_to_main_bank_accounts(org_id):
    """Redirect organization bank accounts page to the main bank accounts page with organization filter."""
    # Check if the organization exists and user has access to it
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user:
        flash('You do not have access to this organization.', 'danger')
        return redirect(url_for('bank_accounts'))
    
    # Redirect to the main bank accounts page with organization filter
    return redirect(url_for('bank_accounts', organization=org_id))

@bank_account_bp.route('/organization/<int:org_id>/bank-accounts/create', methods=['GET', 'POST'])
@login_required
def redirect_to_create_bank_account(org_id):
    """Redirect organization bank account creation to the main create bank account page."""
    # Check if the organization exists and user has access to it
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user:
        flash('You do not have access to this organization.', 'danger')
        return redirect(url_for('bank_accounts'))
    
    # Redirect to the main create bank account page with organization pre-selected
    return redirect(url_for('create_bank_account', organization=org_id))
    
@bank_account_bp.route('/organization/<int:org_id>/bank-accounts/create-old', methods=['GET', 'POST'])
@login_required
def create_organization_bank_account(org_id):
    """Create a new bank account for an organization (owner only)."""
    # Check if user is allowed to manage this organization's bank accounts
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user or org_user.role != UserRole.OWNER.value:
        flash('You must be an organization owner to add bank accounts.', 'danger')
        return redirect(url_for('organizations'))
    
    # Get organization details
    organization = Organization.query.get_or_404(org_id)
    
    # Check if organization has any bank accounts
    has_existing_accounts = db.session.execute(text(
        "SELECT COUNT(*) FROM bank_account WHERE organization_id = :org_id"
    ), {'org_id': org_id}).scalar() > 0
    
    if request.method == 'POST':
        account_name = request.form.get('account_name')
        bank_name = request.form.get('bank_name')
        account_number = request.form.get('account_number')
        branch_name = request.form.get('branch_name')
        swift_code = request.form.get('swift_code')
        iban = request.form.get('iban')
        is_default = True if request.form.get('is_default') else False
        
        # If this is the first account for the organization, make it default
        if not has_existing_accounts:
            is_default = True
        
        # Validate required fields
        if not account_name or not bank_name or not account_number:
            flash('Please fill in all required fields.', 'danger')
            return render_template('create_organization_bank_account.html', organization=organization, has_existing_accounts=has_existing_accounts)
        
        # If this account is set as default, unset any existing default accounts
        if is_default:
            db.session.execute(text("""
                UPDATE bank_account 
                SET is_default = false 
                WHERE organization_id = :org_id AND is_default = true
            """), {'org_id': org_id})
        
        # Create new bank account for organization
        new_account = BankAccount(
            user_id=current_user.id,  # For audit purposes, track who created it
            organization_id=org_id,
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
        
        flash('Organization bank account added successfully.', 'success')
        return redirect(url_for('organization_bank_accounts', org_id=org_id))
    
    return render_template('create_organization_bank_account.html', organization=organization, has_existing_accounts=has_existing_accounts)

@bank_account_bp.route('/organization/<int:org_id>/bank-accounts/<int:account_id>/edit', methods=['GET', 'POST'])
@login_required
def redirect_to_edit_bank_account(org_id, account_id):
    """Redirect organization bank account editing to the main edit bank account page."""
    # Check if the organization exists and user has access to it
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user:
        flash('You do not have access to this organization.', 'danger')
        return redirect(url_for('bank_accounts'))
    
    # Get the bank account, ensuring it belongs to this organization
    result = db.session.execute(text("""
        SELECT id FROM bank_account 
        WHERE id = :account_id AND organization_id = :org_id
    """), {'account_id': account_id, 'org_id': org_id}).first()
    
    if not result:
        flash('Bank account not found.', 'danger')
        return redirect(url_for('bank_accounts', organization=org_id))
    
    # Redirect to the main edit bank account page
    return redirect(url_for('edit_bank_account', account_id=account_id))
    
@bank_account_bp.route('/organization/<int:org_id>/bank-accounts/<int:account_id>/edit-old', methods=['GET', 'POST'])
@login_required
def edit_organization_bank_account(org_id, account_id):
    """Edit an organization bank account (owner only)."""
    # Check if user is allowed to manage this organization's bank accounts
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user or org_user.role != UserRole.OWNER.value:
        flash('You must be an organization owner to edit bank accounts.', 'danger')
        return redirect(url_for('organizations'))
    
    # Get organization details
    organization = Organization.query.get_or_404(org_id)
    
    # Get the bank account, ensuring it belongs to this organization
    result = db.session.execute(text("""
        SELECT id, user_id, organization_id, account_name, bank_name, account_number, 
               branch_name, swift_code, iban, is_default, created_at, updated_at 
        FROM bank_account 
        WHERE id = :account_id AND organization_id = :org_id
    """), {'account_id': account_id, 'org_id': org_id}).first()
    
    if not result:
        abort(404)
    
    # Create a proper account object
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
            return render_template('edit_organization_bank_account.html', account=account, organization=organization)
        
        # If this account is set as default, unset any existing default accounts
        if is_default and not account.is_default:
            db.session.execute(text("""
                UPDATE bank_account 
                SET is_default = false 
                WHERE organization_id = :org_id AND id != :account_id AND is_default = true
            """), {'org_id': org_id, 'account_id': account_id})
        
        # Update the account details
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
            WHERE id = :id AND organization_id = :org_id
        """), {
            'account_name': account_name,
            'bank_name': bank_name,
            'account_number': account_number,
            'branch_name': branch_name,
            'swift_code': swift_code,
            'iban': iban,
            'is_default': is_default,
            'updated_at': datetime.utcnow(),
            'id': account.id,
            'org_id': org_id
        })
        
        db.session.commit()
        
        flash('Organization bank account updated successfully.', 'success')
        return redirect(url_for('organization_bank_accounts', org_id=org_id))
    
    return render_template('edit_organization_bank_account.html', account=account, organization=organization)

@bank_account_bp.route('/organization/<int:org_id>/bank-accounts/<int:account_id>/delete', methods=['POST'])
@login_required
def redirect_to_delete_bank_account(org_id, account_id):
    """Redirect organization bank account deletion to the main delete bank account page."""
    # Check if the organization exists and user has access to it
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user:
        flash('You do not have access to this organization.', 'danger')
        return redirect(url_for('bank_accounts'))
    
    # Get the bank account, ensuring it belongs to this organization
    result = db.session.execute(text("""
        SELECT id FROM bank_account 
        WHERE id = :account_id AND organization_id = :org_id
    """), {'account_id': account_id, 'org_id': org_id}).first()
    
    if not result:
        flash('Bank account not found.', 'danger')
        return redirect(url_for('bank_accounts', organization=org_id))
    
    # Redirect to the main delete bank account endpoint
    return redirect(url_for('delete_bank_account', account_id=account_id))
    
@bank_account_bp.route('/organization/<int:org_id>/bank-accounts/<int:account_id>/delete-old', methods=['POST'])
@login_required
def delete_organization_bank_account(org_id, account_id):
    """Delete an organization bank account (owner only)."""
    # Check if user is allowed to manage this organization's bank accounts
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user or org_user.role != UserRole.OWNER.value:
        flash('You must be an organization owner to delete bank accounts.', 'danger')
        return redirect(url_for('organizations'))
    
    # Get the bank account, ensuring it belongs to this organization
    result = db.session.execute(text("""
        SELECT id, organization_id, is_default 
        FROM bank_account 
        WHERE id = :account_id AND organization_id = :org_id
    """), {'account_id': account_id, 'org_id': org_id}).first()
    
    if not result:
        abort(404)
    
    # Check if account is used in any invoices
    invoices_using_account = Invoice.query.filter_by(bank_account_id=account_id).count()
    if invoices_using_account > 0:
        flash(f'Cannot delete this account as it is used in {invoices_using_account} invoice(s).', 'danger')
        return redirect(url_for('organization_bank_accounts', org_id=org_id))
    
    # If this was the default account, set another one as default if available
    if result[2]:  # is_default
        other_account = db.session.execute(text("""
            SELECT id FROM bank_account 
            WHERE organization_id = :org_id AND id != :account_id 
            LIMIT 1
        """), {'org_id': org_id, 'account_id': account_id}).first()
        
        if other_account:
            db.session.execute(text("""
                UPDATE bank_account 
                SET is_default = true 
                WHERE id = :id
            """), {'id': other_account[0]})
    
    # Delete the account
    db.session.execute(text("""
        DELETE FROM bank_account 
        WHERE id = :id AND organization_id = :org_id
    """), {'id': account_id, 'org_id': org_id})
    
    db.session.commit()
    
    flash('Organization bank account deleted successfully.', 'success')
    return redirect(url_for('organization_bank_accounts', org_id=org_id))

@bank_account_bp.route('/api/organizations/<int:org_id>/bank-accounts')
@login_required
def api_organization_bank_accounts(org_id):
    """API endpoint to get all bank accounts for an organization."""
    # Check if user has access to this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user:
        return jsonify({'error': 'Access denied'}), 403
    
    # Get all bank accounts for this organization
    result = db.session.execute(text("""
        SELECT id, account_name, bank_name, account_number, 
               branch_name, swift_code, iban, is_default
        FROM bank_account 
        WHERE organization_id = :org_id
        ORDER BY is_default DESC, account_name ASC
    """), {'org_id': org_id})
    
    # Convert to list of dictionaries
    accounts = []
    for row in result:
        accounts.append({
            'id': row[0],
            'account_name': row[1],
            'bank_name': row[2],
            'account_number': row[3],
            'branch_name': row[4],
            'swift_code': row[5],
            'iban': row[6],
            'is_default': row[7]
        })
    
    return jsonify({'bank_accounts': accounts})

@bank_account_bp.route('/api/organizations/<int:org_id>/bank-accounts/default')
@login_required
def api_organization_default_bank_account(org_id):
    """API endpoint to get default bank account for an organization."""
    # Check if user has access to this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user:
        return jsonify({'error': 'Access denied'}), 403
    
    # Get the default bank account for this organization
    result = db.session.execute(text("""
        SELECT id, account_name, bank_name, account_number, 
               branch_name, swift_code, iban, is_default
        FROM bank_account 
        WHERE organization_id = :org_id AND is_default = true
        LIMIT 1
    """), {'org_id': org_id})
    
    row = result.first()
    if row:
        account = {
            'id': row[0],
            'account_name': row[1],
            'bank_name': row[2],
            'account_number': row[3],
            'branch_name': row[4],
            'swift_code': row[5],
            'iban': row[6],
            'is_default': row[7]
        }
        return jsonify({'bank_account': account})
    
    return jsonify({'bank_account': {}})

# Old function removed to avoid duplication

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
    
    # Add redirect routes to handle old organization bank account URLs
    app.route('/organization/<int:org_id>/bank-accounts')(redirect_to_main_bank_accounts)
    app.route('/organization/<int:org_id>/bank-accounts/create', methods=['GET', 'POST'])(redirect_to_create_bank_account)
    app.route('/organization/<int:org_id>/bank-accounts/<int:account_id>/edit', methods=['GET', 'POST'])(redirect_to_edit_bank_account)
    app.route('/organization/<int:org_id>/bank-accounts/<int:account_id>/delete', methods=['POST'])(redirect_to_delete_bank_account)
    app.route('/api/organizations/<int:org_id>/bank-accounts')(api_organization_bank_accounts)
    app.route('/api/organizations/<int:org_id>/bank-accounts/default')(api_organization_default_bank_account)