"""
Routes for client expense allocation management.
"""
from datetime import datetime
import logging

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from models import ClientExpense, Receipt, Client, Organization
from decorators import role_required

client_expense_bp = Blueprint('client_expense', __name__)

@client_expense_bp.route('/receipts/<int:receipt_id>/allocate-to-client', methods=['GET', 'POST'])
@login_required
def allocate_to_client(receipt_id):
    """Allocate a receipt to a client."""
    # Verify the receipt belongs to the user
    receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first_or_404()
    
    # Check if there's already a client expense for this receipt
    existing_expense = ClientExpense.query.filter_by(receipt_id=receipt_id).first()
    if existing_expense:
        flash('This receipt has already been allocated to a client', 'warning')
        return redirect(url_for('client_expense.view_client_expense', expense_id=existing_expense.id))
    
    # Get the user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    if not organization_id:
        flash('You need to be part of an organization to allocate expenses to clients', 'warning')
        return redirect(url_for('organizations'))
    
    # Get all clients for this organization
    clients = Client.query.filter_by(organization_id=organization_id).all()
    
    if not clients:
        flash('You need to create at least one client before allocating expenses', 'warning')
        return redirect(url_for('create_client'))
        
    if request.method == 'POST':
        try:
            client_id = request.form.get('client_id', type=int)
            description = request.form.get('description', '')
            project_name = request.form.get('project_name', '')
            
            # Validate client belongs to the organization
            client = Client.query.filter_by(id=client_id, organization_id=organization_id).first()
            if not client:
                flash('Invalid client selected', 'danger')
                return redirect(url_for('client_expense.allocate_to_client', receipt_id=receipt_id))
            
            # Create the client expense
            client_expense = ClientExpense(
                receipt_id=receipt_id,
                client_id=client_id,
                user_id=current_user.id,
                organization_id=organization_id,
                description=description,
                project_name=project_name
            )
            
            db.session.add(client_expense)
            db.session.commit()
            
            flash('Receipt successfully allocated to client', 'success')
            return redirect(url_for('client_expense.client_expenses'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error allocating receipt to client: {str(e)}")
            flash(f'Error allocating receipt to client: {str(e)}', 'danger')
            return redirect(url_for('view_receipt', receipt_id=receipt_id))
    
    # GET request - render form
    return render_template('allocate_to_client.html', receipt=receipt, clients=clients)

@client_expense_bp.route('/client-expenses')
@login_required
def client_expenses():
    """Render the client expenses page showing list of expenses allocated to clients."""
    
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
    
    # Get all client expenses for this organization if admin/owner, otherwise just the user's
    if user_org_role in ['owner', 'admin']:
        # Admin view - get all expenses for the organization
        expenses = ClientExpense.query.filter_by(organization_id=organization_id).all()
    else:
        # User view - get only their expenses
        expenses = ClientExpense.query.filter_by(
            organization_id=organization_id, 
            user_id=current_user.id
        ).all()
    
    # Get all clients for this organization
    clients = Client.query.filter_by(organization_id=organization_id).all()
    
    # Format client expenses for display
    formatted_expenses = []
    for expense in expenses:
        receipt = Receipt.query.get(expense.receipt_id)
        client = Client.query.get(expense.client_id)
        
        formatted_expenses.append({
            'id': expense.id,
            'receipt_id': expense.receipt_id,
            'client_id': expense.client_id,
            'client_name': client.name if client else 'Unknown Client',
            'vendor_name': receipt.vendor_name if receipt else 'Unknown Vendor',
            'description': expense.description,
            'project_name': expense.project_name,
            'date': receipt.date if receipt else None,
            'total_amount': receipt.total_amount if receipt else 0.0,
            'created_at': expense.created_at
        })
    
    return render_template('client_expenses.html', 
                           expenses=formatted_expenses, 
                           clients=clients, 
                           is_admin=(user_org_role in ['owner', 'admin']))

@client_expense_bp.route('/client-expenses/<int:expense_id>')
@login_required
def view_client_expense(expense_id):
    """View a specific client expense."""
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
    
    # Verify the expense belongs to the user or user is admin/owner
    expense = ClientExpense.query.get_or_404(expense_id)
    
    if expense.organization_id != organization_id:
        abort(403)  # Forbidden
    
    if expense.user_id != current_user.id and user_org_role not in ['owner', 'admin']:
        abort(403)  # Forbidden
    
    receipt = Receipt.query.get_or_404(expense.receipt_id)
    client = Client.query.get_or_404(expense.client_id)
    
    return render_template('view_client_expense.html', 
                           expense=expense,
                           receipt=receipt,
                           client=client,
                           is_admin=(user_org_role in ['owner', 'admin']))

@client_expense_bp.route('/client-expenses/<int:expense_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_client_expense(expense_id):
    """Edit a client expense."""
    # Get the expense
    expense = ClientExpense.query.get_or_404(expense_id)
    
    # Verify the expense belongs to the user
    if expense.user_id != current_user.id:
        abort(403)  # Forbidden
    
    # Get the user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    if expense.organization_id != organization_id:
        abort(403)  # Forbidden
    
    # Get all clients for this organization
    clients = Client.query.filter_by(organization_id=organization_id).all()
    
    if request.method == 'POST':
        try:
            client_id = request.form.get('client_id', type=int)
            description = request.form.get('description', '')
            project_name = request.form.get('project_name', '')
            
            # Validate client belongs to the organization
            client = Client.query.filter_by(id=client_id, organization_id=organization_id).first()
            if not client:
                flash('Invalid client selected', 'danger')
                return redirect(url_for('client_expense.edit_client_expense', expense_id=expense_id))
            
            # Update the expense
            expense.client_id = client_id
            expense.description = description
            expense.project_name = project_name
            expense.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            flash('Client expense updated successfully', 'success')
            return redirect(url_for('client_expense.view_client_expense', expense_id=expense_id))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating client expense: {str(e)}")
            flash(f'Error updating client expense: {str(e)}', 'danger')
            return redirect(url_for('client_expense.edit_client_expense', expense_id=expense_id))
    
    # GET request - render form
    receipt = Receipt.query.get_or_404(expense.receipt_id)
    
    return render_template('edit_client_expense.html', 
                           expense=expense, 
                           receipt=receipt, 
                           clients=clients)

@client_expense_bp.route('/client-expenses/<int:expense_id>/delete', methods=['POST'])
@login_required
def delete_client_expense(expense_id):
    """Delete a client expense."""
    # Get the expense
    expense = ClientExpense.query.get_or_404(expense_id)
    
    # Verify the expense belongs to the user
    if expense.user_id != current_user.id:
        abort(403)  # Forbidden
    
    # Get the user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    if expense.organization_id != organization_id:
        abort(403)  # Forbidden
    
    try:
        db.session.delete(expense)
        db.session.commit()
        flash('Client expense deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting client expense: {str(e)}")
        flash(f'Error deleting client expense: {str(e)}', 'danger')
    
    return redirect(url_for('client_expense.client_expenses'))

@client_expense_bp.route('/clients/<int:client_id>/expenses')
@login_required
def client_expenses_by_client(client_id):
    """View all expenses for a specific client."""
    # Verify the client belongs to the user's organization
    client = Client.query.get_or_404(client_id)
    
    # Get the user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
    if client.organization_id != organization_id:
        abort(403)  # Forbidden
    
    # Get user's role in this organization
    role_result = db.session.execute(text("""
        SELECT role FROM organization_user
        WHERE user_id = :user_id AND organization_id = :org_id
        LIMIT 1
    """), {'user_id': current_user.id, 'org_id': organization_id}).first()
    
    user_org_role = role_result[0] if role_result else None
    
    # Get all expenses for this client
    if user_org_role in ['owner', 'admin']:
        # Admin view - get all expenses for this client
        expenses = ClientExpense.query.filter_by(
            organization_id=organization_id,
            client_id=client_id
        ).all()
    else:
        # User view - get only their expenses for this client
        expenses = ClientExpense.query.filter_by(
            organization_id=organization_id,
            client_id=client_id,
            user_id=current_user.id
        ).all()
    
    # Format client expenses for display
    formatted_expenses = []
    for expense in expenses:
        receipt = Receipt.query.get(expense.receipt_id)
        
        formatted_expenses.append({
            'id': expense.id,
            'receipt_id': expense.receipt_id,
            'vendor_name': receipt.vendor_name if receipt else 'Unknown Vendor',
            'description': expense.description,
            'project_name': expense.project_name,
            'date': receipt.date if receipt else None,
            'total_amount': receipt.total_amount if receipt else 0.0,
            'created_at': expense.created_at
        })
    
    return render_template('client_expenses_by_client.html', 
                           expenses=formatted_expenses, 
                           client=client,
                           is_admin=(user_org_role in ['owner', 'admin']))

def register_routes(app):
    """Register the client expense routes with the app."""
    app.register_blueprint(client_expense_bp, url_prefix='')
    app.add_url_rule('/receipts/<int:receipt_id>/allocate-to-client', 
                     endpoint='allocate_to_client', 
                     view_func=allocate_to_client)