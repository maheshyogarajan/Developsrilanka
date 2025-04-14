import logging
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from models import Receipt, Organization, Client, User, CompanyExpense, ExpenseStatus, ReceiptItem

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Create a Blueprint for the receipt classification routes
classify_bp = Blueprint('classify', __name__)

def register_routes(app):
    """Register routes with the Flask application."""
    app.register_blueprint(classify_bp)
    logger.info("Receipt classification routes registered")

@classify_bp.route('/classify/receipts', methods=['GET'])
@classify_bp.route('/classify/receipts/<page>/<int:receipt_id>', methods=['GET'])
@login_required
def classify_receipts(page=None, receipt_id=None):
    """
    Classify receipts as company expenses.
    
    Args:
        page (str): Optional navigation parameter ('next' or 'prev')
        receipt_id (int): Optional receipt ID for navigation
    """
    # Get user's organizations
    user_orgs = db.session.execute(text("""
        SELECT o.id, o.name, ou.is_default
        FROM organization o
        JOIN organization_user ou ON o.id = ou.organization_id
        WHERE ou.user_id = :user_id
        ORDER BY ou.is_default DESC, o.name
    """), {'user_id': current_user.id}).fetchall()
    
    organizations = [
        {'id': row[0], 'name': row[1], 'is_default': row[2]} 
        for row in user_orgs
    ]
    
    if not organizations:
        flash('You need to be part of an organization to classify receipts', 'warning')
        return redirect(url_for('organizations'))
    
    # Default to the user's default organization
    default_org_id = next((org['id'] for org in organizations if org['is_default']), organizations[0]['id']) if organizations else None
    
    # Get all client options (we'll filter by JS on the frontend based on organization selection)
    clients = Client.query.filter(
        Client.organization_id.in_([org['id'] for org in organizations])
    ).order_by(Client.name).all()
    
    # First, get unclassified receipts (not linked to any company expense)
    unclassified_query = text("""
        SELECT r.id
        FROM receipt r
        LEFT JOIN company_expense e ON r.id = e.receipt_id
        WHERE r.user_id = :user_id AND e.id IS NULL
        ORDER BY r.date DESC
    """)
    
    unclassified_receipt_ids = [row[0] for row in db.session.execute(unclassified_query, {'user_id': current_user.id}).fetchall()]
    
    # Then, get classified receipts
    classified_query = text("""
        SELECT r.id
        FROM receipt r
        JOIN company_expense e ON r.id = e.receipt_id
        WHERE r.user_id = :user_id
        ORDER BY r.date DESC
    """)
    
    classified_receipt_ids = [row[0] for row in db.session.execute(classified_query, {'user_id': current_user.id}).fetchall()]
    
    # Combine them with unclassified first
    all_receipt_ids = unclassified_receipt_ids + classified_receipt_ids
    
    if not all_receipt_ids:
        flash('No receipts found', 'info')
        return render_template('classify_receipts.html', 
                               receipts=None, 
                               organizations=organizations,
                               clients=clients)
    
    # Handle pagination
    current_index = 0
    
    if page and receipt_id:
        try:
            current_index = all_receipt_ids.index(receipt_id)
            if page == 'next' and current_index < len(all_receipt_ids) - 1:
                current_index += 1
            elif page == 'prev' and current_index > 0:
                current_index -= 1
        except ValueError:
            # Receipt ID not found, stay at index 0
            pass
    
    # Get current receipt
    current_receipt_id = all_receipt_ids[current_index]
    current_receipt = Receipt.query.get_or_404(current_receipt_id)
    
    # Import utility functions for image URLs
    from utils import get_receipt_image_url, get_receipt_thumbnail_url
    
    # Get the full-size image URL and thumbnail URL
    current_receipt.image_url = get_receipt_image_url(current_receipt)
    current_receipt.thumbnail_url = get_receipt_thumbnail_url(current_receipt)
    
    # Check if this receipt is already processed
    existing_expense = CompanyExpense.query.filter_by(receipt_id=current_receipt_id).first()
    current_receipt.is_processed = existing_expense is not None
    
    # Attach expense information to the receipt for the template
    if existing_expense:
        current_receipt.expense = existing_expense
        current_receipt.description = existing_expense.description
        current_receipt.organization_id = existing_expense.organization_id
        current_receipt.client_id = existing_expense.client_id
        current_receipt.is_reimbursable = existing_expense.is_reimbursable
        current_receipt.notes = existing_expense.notes
    
    # Get receipt items
    receipt_items = ReceiptItem.query.filter_by(receipt_id=current_receipt_id).all()
    
    return render_template('classify_receipts.html', 
                           receipts=all_receipt_ids,
                           current_receipt=current_receipt,
                           receipt_items=receipt_items,
                           current_index=current_index,
                           total_receipts=len(all_receipt_ids),
                           organizations=organizations,
                           clients=clients,
                           existing_expense=existing_expense)

@classify_bp.route('/classify/receipts/submit/<int:receipt_id>', methods=['POST'])
@login_required
def classify_receipt_submit(receipt_id):
    """Process receipt classification form submission."""
    try:
        # Validate the receipt belongs to the user
        receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
        if not receipt:
            flash('Receipt not found or access denied', 'danger')
            return redirect(url_for('classify.classify_receipts'))
        
        # Get form data
        organization_id = request.form.get('organization_id')
        if not organization_id or not organization_id.isdigit():
            flash('Invalid organization selected', 'danger')
            return redirect(url_for('classify.classify_receipts', page='next', receipt_id=receipt_id))
        
        organization_id = int(organization_id)
        
        # Verify user belongs to the organization
        user_org = db.session.execute(text("""
            SELECT role FROM organization_user
            WHERE user_id = :user_id AND organization_id = :org_id
            LIMIT 1
        """), {'user_id': current_user.id, 'org_id': organization_id}).first()
        
        if not user_org:
            flash('You do not have access to the selected organization', 'danger')
            return redirect(url_for('classify.classify_receipts', page='next', receipt_id=receipt_id))
        
        # Get remaining form data
        client_id = request.form.get('client_id')
        description = request.form.get('description', '').strip()
        is_reimbursable = request.form.get('is_reimbursable') == 'true'
        notes = request.form.get('notes', '').strip()
        
        # Process tax deductible items
        tax_deductible_items = {}
        for key, value in request.form.items():
            if key.startswith('tax_deductible_items[') and key.endswith(']'):
                # Extract the ID from the key (format: tax_deductible_items[item_id])
                item_id = int(key[len('tax_deductible_items['):-1])
                tax_deductible_items[item_id] = (value == 'true')
        
        # Convert client_id to int or None
        if client_id and client_id.isdigit():
            client_id = int(client_id)
            # Verify client belongs to organization
            client = Client.query.filter_by(id=client_id, organization_id=organization_id).first()
            if not client:
                flash('Selected client not found or does not belong to the selected organization', 'danger')
                return redirect(url_for('classify.classify_receipts', page='next', receipt_id=receipt_id))
        else:
            client_id = None
        
        # Check if this receipt is already processed
        existing_expense = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
        
        if existing_expense:
            # Update existing expense
            existing_expense.organization_id = organization_id
            existing_expense.client_id = client_id
            existing_expense.description = description
            existing_expense.is_reimbursable = is_reimbursable
            existing_expense.notes = notes
            existing_expense.submitted_date = datetime.utcnow()
            
            # Update tax deductible status for receipt items
            if tax_deductible_items:
                receipt_items = ReceiptItem.query.filter_by(receipt_id=receipt_id).all()
                for item in receipt_items:
                    if item.id in tax_deductible_items:
                        item.tax_deductible = tax_deductible_items[item.id]
                
            db.session.commit()
            flash('Expense updated successfully', 'success')
        else:
            # Create new company expense
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
            
            # Update tax deductible status for receipt items
            if tax_deductible_items:
                receipt_items = ReceiptItem.query.filter_by(receipt_id=receipt_id).all()
                for item in receipt_items:
                    if item.id in tax_deductible_items:
                        item.tax_deductible = tax_deductible_items[item.id]
            
            db.session.commit()
            flash('Receipt classified as an expense successfully', 'success')
        
        # Redirect to next receipt
        return redirect(url_for('classify.classify_receipts', page='next', receipt_id=receipt_id))
    
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error classifying receipt: {str(e)}")
        flash(f'Error classifying receipt: {str(e)}', 'danger')
        return redirect(url_for('classify.classify_receipts'))