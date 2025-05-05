"""
Enhanced receipt classification routes.
This module provides an improved workflow for classifying receipts.
"""
import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import text, or_, and_, func
from models import (
    Receipt, ReceiptItem, CompanyExpense, ClientExpense, Client, 
    ExpenseStatus, Organization, OrganizationUser
)
from app import db
# Removed import that doesn't exist in the codebase

# Create blueprint - use a different name to avoid conflicts
classify_bp = Blueprint('enhanced_classify', __name__, template_folder='templates')

@classify_bp.route('/classify', defaults={'page': None, 'receipt_id': None})
@classify_bp.route('/classify/<string:page>/<int:receipt_id>')
@login_required
def classify_receipts(page=None, receipt_id=None):
    """
    Enhanced receipt classification workflow.
    
    Args:
        page (str): Optional navigation parameter ('next', 'prev', or 'skip')
        receipt_id (int): Optional receipt ID for navigation
    
    Features:
    - Batch processing of receipts
    - Tax deductible item selection
    - Progress tracking
    - Client allocation
    """
    # Get user's organizations
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    user_org_ids = [org.organization_id for org in user_orgs]
    
    # Get organizations the user is a member of
    organizations = Organization.query.filter(Organization.id.in_(user_org_ids)).all()
    
    # Get default organization
    default_org_id = None
    for org in user_orgs:
        if org.is_default:
            default_org_id = org.organization_id
            break
    
    # Get all clients for the default organization
    clients = []
    if default_org_id:
        clients = Client.query.filter_by(organization_id=default_org_id).order_by(Client.name).all()
    
    # Get unprocessed receipts (not classified as business or client expense)
    # First, get receipts that are already classified
    company_receipt_ids = db.session.query(CompanyExpense.receipt_id).all()
    client_receipt_ids = db.session.query(ClientExpense.receipt_id).all()
    classified_ids = [r[0] for r in company_receipt_ids] + [r[0] for r in client_receipt_ids]
    
    # Build query for unprocessed receipts
    query = Receipt.query.filter(
        or_(
            Receipt.user_id == current_user.id,
            and_(
                Receipt.organization_id.in_(user_org_ids)
            )
        )
    )
    
    # Filter out receipts that are already classified
    if classified_ids:
        query = query.filter(~Receipt.id.in_(classified_ids))
    
    # Order by date (newest first)
    query = query.order_by(Receipt.date.desc())
    
    # Get all unprocessed receipts for counting
    all_unprocessed = query.all()
    total_count = len(all_unprocessed)
    
    # Navigation logic
    current_receipt = None
    
    if page and receipt_id:
        # Find current position in the list
        current_position = 0
        for i, r in enumerate(all_unprocessed):
            if r.id == receipt_id:
                current_position = i
                break
        
        # Determine next receipt based on navigation action
        if page == 'next' or page == 'skip':
            if current_position < len(all_unprocessed) - 1:
                current_receipt = all_unprocessed[current_position + 1]
            elif len(all_unprocessed) > 0:
                current_receipt = all_unprocessed[0]
        elif page == 'prev':
            if current_position > 0:
                current_receipt = all_unprocessed[current_position - 1]
            elif len(all_unprocessed) > 0:
                current_receipt = all_unprocessed[-1]
    else:
        # No navigation params, get the first unprocessed receipt
        if all_unprocessed:
            current_receipt = all_unprocessed[0]
    
    # Calculate progress metrics
    current_position = 0
    if current_receipt:
        for i, r in enumerate(all_unprocessed):
            if r.id == current_receipt.id:
                current_position = i + 1
                break
    
    remaining_count = total_count
    progress_percentage = 0
    
    if total_count > 0:
        # Calculate how many receipts are classified vs. total
        total_receipts = db.session.query(func.count(Receipt.id)).filter(
            or_(
                Receipt.user_id == current_user.id,
                and_(
                    Receipt.organization_id.in_(user_org_ids)
                )
            )
        ).scalar()
        
        classified_count = len(classified_ids)
        remaining_count = total_count
        
        if total_receipts > 0:
            progress_percentage = int((classified_count / total_receipts) * 100)
    
    # Calculate how many receipts were processed today
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    processed_today = db.session.query(func.count(CompanyExpense.id)).filter(
        CompanyExpense.user_id == current_user.id,
        CompanyExpense.submitted_date >= today_start
    ).scalar()
    
    # If we have a current receipt, ensure it has image URL
    if current_receipt:
        if current_receipt.s3_key and hasattr(current_receipt, 's3_url') and current_receipt.s3_url:
            current_receipt.image_url = current_receipt.s3_url
        elif current_receipt.image_path:
            current_receipt.image_url = f"/static/{current_receipt.image_path}"
    
    return render_template(
        'enhanced_classify_receipts.html',
        receipt=current_receipt,
        organizations=organizations,
        clients=clients,
        total_count=total_count,
        current_position=current_position,
        remaining_count=remaining_count,
        progress_percentage=progress_percentage,
        processed_today=processed_today
    )

@classify_bp.route('/classify/submit/<int:receipt_id>', methods=['POST'])
@login_required
def classify_receipt_submit(receipt_id):
    """Process receipt classification form submission."""
    # Verify receipt exists and user has access
    receipt = Receipt.query.filter(
        Receipt.id == receipt_id,
        or_(
            Receipt.user_id == current_user.id,
            Receipt.organization_id.in_([org.organization_id for org in current_user.organizations])
        )
    ).first_or_404()
    
    # Check if receipt is already classified
    existing_company = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
    existing_client = ClientExpense.query.filter_by(receipt_id=receipt_id).first()
    
    if existing_company or existing_client:
        flash('This receipt has already been classified', 'warning')
        return redirect(url_for('enhanced_classify.classify_receipts'))
    
    # Get form data
    organization_id = request.form.get('organization_id')
    client_id = request.form.get('client_id')
    description = request.form.get('description')
    is_reimbursable = request.form.get('is_reimbursable') == 'true'
    notes = request.form.get('notes', '')
    tax_deductible_items = request.form.getlist('tax_deductible_items[]')
    
    try:
        # Convert to integers or None
        organization_id = int(organization_id) if organization_id else None
        client_id = int(client_id) if client_id and client_id.isdigit() else None
        
        # Verify organization exists and user is a member
        if organization_id:
            org_user = OrganizationUser.query.filter_by(
                user_id=current_user.id,
                organization_id=organization_id
            ).first()
            
            if not org_user:
                flash('You are not a member of the selected organization', 'danger')
                return redirect(url_for('enhanced_classify.classify_receipts', receipt_id=receipt_id))
        
        # Verify client belongs to the organization
        if client_id:
            client = Client.query.filter_by(id=client_id, organization_id=organization_id).first()
            if not client:
                flash('Selected client does not belong to the selected organization', 'danger')
                return redirect(url_for('enhanced_classify.classify_receipts', receipt_id=receipt_id))
        
        # Update receipt organization
        receipt.organization_id = organization_id
        
        # Update tax deductible status for items
        if receipt.items:
            for item in receipt.items:
                item.tax_deductible = str(item.id) in tax_deductible_items
            
            # Recalculate tax deductible amount
            tax_deductible_amount = 0
            for item in receipt.items:
                if item.tax_deductible:
                    tax_deductible_amount += item.price * item.quantity
            
            receipt.tax_deductible_amount = tax_deductible_amount
        
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
        
        flash('Receipt successfully classified as a business expense', 'success')
        
        # Redirect to next receipt
        return redirect(url_for('enhanced_classify.classify_receipts', page='next', receipt_id=receipt_id))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error classifying receipt: {str(e)}")
        flash(f'Error classifying receipt: {str(e)}', 'danger')
        return redirect(url_for('enhanced_classify.classify_receipts', receipt_id=receipt_id))

@classify_bp.route('/api/clients')
@login_required
def get_clients():
    """API endpoint to get clients for a specific organization."""
    organization_id = request.args.get('organization_id')
    
    if not organization_id:
        return jsonify({'error': 'No organization ID provided'}), 400
    
    try:
        organization_id = int(organization_id)
    except ValueError:
        return jsonify({'error': 'Invalid organization ID'}), 400
    
    # Verify user is a member of the organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=organization_id
    ).first()
    
    if not org_user:
        return jsonify({'error': 'You are not a member of this organization'}), 403
    
    # Get clients for the organization
    clients = Client.query.filter_by(organization_id=organization_id).order_by(Client.name).all()
    
    return jsonify({
        'clients': [{'id': c.id, 'name': c.name} for c in clients]
    })

def register_routes(app):
    """Register the enhanced receipt classification routes with the app."""
    app.register_blueprint(classify_bp, url_prefix='/enhanced')
    logging.info('Enhanced receipt classification routes registered')