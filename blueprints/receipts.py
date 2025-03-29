import logging
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import text, or_, and_

from app import db
from models import (
    Receipt, Organization, Client, User, CompanyExpense, ExpenseStatus, ReceiptItem,
    OrganizationUser
)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Create a Blueprint for the receipt routes
receipts_bp = Blueprint('receipts', __name__)

def register_routes(app):
    """Register routes with the Flask application."""
    app.register_blueprint(receipts_bp)
    logger.info("Receipt routes registered")

@receipts_bp.route('/receipt_history')
@login_required
def receipt_history():
    """Render the receipt history page."""
    return render_template('receipt_history.html')

@receipts_bp.route('/list_receipts', methods=['GET'])
@login_required
def list_receipts():
    """Get a list of all saved receipts for the current user, with organization-aware filtering."""
    import traceback
    from error_logger import log_receipt_history_error, log_database_error, handle_and_log_exception, ErrorTypes
    
    try:
        # Add detailed logging to troubleshoot
        user_id = current_user.id
        logger.debug(f"list_receipts: Processing for user_id={user_id}")
        
        # Check if we should show all organizations or filter by a specific one
        # Enhanced parameter handling with better type conversion
        show_all_param = request.args.get('show_all')
        org_id_param = request.args.get('organization_id')
        
        # Convert show_all parameter properly (handle strings and booleans)
        show_all_organizations = False
        if show_all_param:
            if isinstance(show_all_param, str):
                show_all_organizations = show_all_param.lower() == 'true'
            else:
                show_all_organizations = bool(show_all_param)
        
        # Handle organization_id parameter
        selected_org_id = None
        if org_id_param:
            if org_id_param.lower() == 'all':
                show_all_organizations = True
            else:
                try:
                    selected_org_id = org_id_param
                    # Validate that this organization belongs to the user
                    user_org_ids = [str(org.organization_id) for org in OrganizationUser.query.filter_by(user_id=user_id).all()]
                    if selected_org_id not in user_org_ids:
                        logger.warning(f"Organization ID {selected_org_id} does not belong to user {user_id}")
                        selected_org_id = None
                except Exception as org_error:
                    logger.error(f"Error validating organization ID: {str(org_error)}")
                    selected_org_id = None
        
        # Get the user's default organization
        default_org = current_user.get_default_organization()
        
        # Start building the query
        # If show_all_organizations, show receipts from all organizations the user belongs to
        # Otherwise, filter by the selected organization or default organization
        
        # First get all organization IDs the user belongs to
        user_orgs = OrganizationUser.query.filter_by(user_id=user_id).all()
        user_org_ids = [org.organization_id for org in user_orgs]
        
        if show_all_organizations:
            # Show receipts from all user's organizations
            query = Receipt.query.filter(
                or_(
                    Receipt.organization_id.in_(user_org_ids),
                    and_(
                        Receipt.user_id == user_id,
                        or_(
                            Receipt.organization_id.is_(None),
                            Receipt.organization_id == 0
                        )
                    )
                )
            )
        elif selected_org_id:
            # Show receipts for the selected organization
            query = Receipt.query.filter(
                or_(
                    Receipt.organization_id == selected_org_id,
                    and_(
                        Receipt.user_id == user_id,
                        Receipt.organization_id == selected_org_id
                    )
                )
            )
        elif default_org:
            # Show receipts for the default organization
            default_org_id = default_org.id
            query = Receipt.query.filter(
                or_(
                    Receipt.organization_id == default_org_id,
                    and_(
                        Receipt.user_id == user_id,
                        or_(
                            Receipt.organization_id.is_(None),
                            Receipt.organization_id == 0
                        )
                    )
                )
            )
        else:
            # Fallback to just user's receipts if no organization context
            query = Receipt.query.filter_by(user_id=user_id)
        
        # Order by date, newest first
        query = query.order_by(Receipt.date.desc(), Receipt.id.desc())
        
        # Execute the query
        receipts = query.all()
        
        # Prepare a list of receipt data
        receipt_list = []
        for receipt in receipts:
            receipt_data = {
                'id': receipt.id,
                'date': receipt.date.strftime('%Y-%m-%d') if receipt.date else '',
                'vendor': receipt.vendor or 'Unknown Vendor',
                'total': float(receipt.total) if receipt.total else 0.0,
                'currency': receipt.currency or 'LKR',
                'description': receipt.description or '',
                'image_url': receipt.image_url if receipt.image_url else None,
                'items_count': len(receipt.items) if receipt.items else 0,
                'has_items': len(receipt.items) > 0 if receipt.items else False,
                'organization_id': receipt.organization_id,
                'organization_name': None,
                'customer_name': receipt.customer_name or '',
                'customer_phone': receipt.customer_phone or ''
            }
            
            # Get organization name if available
            if receipt.organization_id:
                organization = Organization.query.get(receipt.organization_id)
                if organization:
                    receipt_data['organization_name'] = organization.name
            
            # Check if receipt is marked as a company expense
            receipt_data['is_company_expense'] = False
            receipt_data['company_expense_id'] = None
            receipt_data['expense_description'] = None
            receipt_data['expense_client_id'] = None
            receipt_data['expense_client_name'] = None
            receipt_data['expense_organization_id'] = None
            receipt_data['expense_organization_name'] = None
            receipt_data['expense_reimbursable'] = None
            receipt_data['expense_status'] = None
            
            # Check for an associated company expense
            company_expense = CompanyExpense.query.filter_by(receipt_id=receipt.id).first()
            if company_expense:
                receipt_data['is_company_expense'] = True
                receipt_data['company_expense_id'] = company_expense.id
                receipt_data['expense_description'] = company_expense.description
                receipt_data['expense_client_id'] = company_expense.client_id
                receipt_data['expense_reimbursable'] = company_expense.is_reimbursable
                receipt_data['expense_status'] = company_expense.status
                
                # Get client name if available
                if company_expense.client_id:
                    client = Client.query.get(company_expense.client_id)
                    if client:
                        receipt_data['expense_client_name'] = client.name
                
                # Get organization info
                if company_expense.organization_id:
                    expense_org = Organization.query.get(company_expense.organization_id)
                    if expense_org:
                        receipt_data['expense_organization_id'] = expense_org.id
                        receipt_data['expense_organization_name'] = expense_org.name
            
            receipt_list.append(receipt_data)
        
        # Return the receipt list as JSON
        return jsonify({
            'receipts': receipt_list,
            'count': len(receipt_list),
            'organization_id': selected_org_id if selected_org_id else (default_org.id if default_org else None),
            'show_all': show_all_organizations
        })
    
    except Exception as e:
        # Handle the error with our centralized error logging
        error_response = handle_and_log_exception(
            e=e,
            error_type=ErrorTypes.RECEIPT_HISTORY,
            component="list_receipts",
            additional_info={
                "user_id": current_user.id if current_user else None,
                "traceback": traceback.format_exc(),
                "show_all_param": show_all_param if 'show_all_param' in locals() else None,
                "org_id_param": org_id_param if 'org_id_param' in locals() else None
            }
        )
        
        return jsonify({
            'error': f"Error loading receipts: {str(e)}",
            'error_id': error_response.get('error_id', 'unknown')
        }), 500

@receipts_bp.route('/receipts_list')
@login_required
def list_receipts_legacy():
    """Get a list of all saved receipts for the current user, with organization-aware filtering."""
    try:
        # Add detailed logging to troubleshoot
        user_id = current_user.id
        logging.debug(f"list_receipts: Processing for user_id={user_id}")
        
        # Check if we should show all organizations or filter by a specific one
        show_all_param = request.args.get('show_all')
        org_id_param = request.args.get('organization_id')
        
        # Convert show_all parameter properly
        show_all_organizations = False
        if show_all_param:
            if isinstance(show_all_param, str):
                show_all_organizations = show_all_param.lower() == 'true'
            else:
                show_all_organizations = bool(show_all_param)
        
        # Handle organization_id parameter
        selected_org_id = None
        if org_id_param:
            if org_id_param.lower() == 'all':
                show_all_organizations = True
            else:
                try:
                    selected_org_id = int(org_id_param)
                    # Validate that this organization belongs to the user
                    org_user = OrganizationUser.query.filter_by(
                        user_id=user_id, 
                        organization_id=selected_org_id
                    ).first()
                    
                    if not org_user:
                        return jsonify({
                            'error': 'You do not have access to this organization'
                        }), 403
                except ValueError:
                    return jsonify({
                        'error': 'Invalid organization ID'
                    }), 400
        
        # If no specific organization selected, get the default organization
        if not show_all_organizations and not selected_org_id:
            org = current_user.get_default_organization()
            selected_org_id = org.id if org else None
            
        # Get all organizations this user belongs to for the filter dropdown
        user_organizations = db.session.execute(text("""
            SELECT o.id, o.name, ou.is_default
            FROM organization o
            JOIN organization_user ou ON o.id = ou.organization_id
            WHERE ou.user_id = :user_id
            ORDER BY ou.is_default DESC, o.name
        """), {'user_id': user_id}).fetchall()
        
        organizations = [
            {'id': row[0], 'name': row[1], 'is_default': row[2]} 
            for row in user_organizations
        ]
        
        # Log what we're doing for debugging
        logging.debug(f"Receipt organization filtering: show_all={show_all_organizations}, org_id={selected_org_id}")
        
        # Get receipts based on organization filtering
        if show_all_organizations:
            # Get all receipts from organizations the user belongs to
            query = text("""
                SELECT DISTINCT r.*
                FROM receipt r
                LEFT JOIN organization_user ou ON r.organization_id = ou.organization_id
                WHERE (r.user_id = :user_id OR ou.user_id = :user_id)
                ORDER BY r.date DESC, r.created_at DESC
            """)
            receipts = db.session.execute(query, {'user_id': user_id}).mappings().all()
            logging.debug(f"list_receipts: Showing all organization receipts for user_id={user_id}")
            
        elif selected_org_id:
            # Filter by specific organization
            logging.debug(f"list_receipts: Filtering by organization_id={selected_org_id} for user_id={user_id}")
            query = text("""
                SELECT r.*
                FROM receipt r
                WHERE (r.organization_id = :org_id AND r.user_id = :user_id)
                   OR (r.organization_id = :org_id AND EXISTS (
                       SELECT 1 FROM organization_user ou 
                       WHERE ou.organization_id = :org_id AND ou.user_id = :user_id
                   ))
                ORDER BY r.date DESC, r.created_at DESC
            """)
            receipts = db.session.execute(query, {
                'user_id': user_id,
                'org_id': selected_org_id
            }).mappings().all()
            
        else:
            # No organization filtering, just get user's receipts
            # This should rarely happen as we try to always have a default org
            logging.debug(f"list_receipts: No organization filter, showing user receipts for user_id={user_id}")
            query = text("""
                SELECT r.*
                FROM receipt r
                WHERE r.user_id = :user_id
                ORDER BY r.date DESC, r.created_at DESC
            """)
            receipts = db.session.execute(query, {'user_id': user_id}).mappings().all()
        
        # Convert to list of dictionaries
        receipt_list = []
        for receipt in receipts:
            receipt_dict = dict(receipt)
            
            # Add expense information to the receipt
            receipt_dict['company_expense'] = None
            company_expense = CompanyExpense.query.filter_by(receipt_id=receipt_dict['id']).first()
            if company_expense:
                receipt_dict['company_expense'] = {
                    'id': company_expense.id,
                    'organization_id': company_expense.organization_id,
                    'client_id': company_expense.client_id,
                    'is_reimbursable': company_expense.is_reimbursable,
                    'status': company_expense.status
                }
                
                # Add organization name if available
                if company_expense.organization_id:
                    org = Organization.query.get(company_expense.organization_id)
                    if org:
                        receipt_dict['company_expense']['organization_name'] = org.name
                
                # Add client name if available
                if company_expense.client_id:
                    client = Client.query.get(company_expense.client_id)
                    if client:
                        receipt_dict['company_expense']['client_name'] = client.name
            
            # Simplify the output for the list view
            receipt_dict.pop('items', None)  # Remove items to reduce payload size
            receipt_list.append(receipt_dict)
        
        logging.debug(f"list_receipts: Found {len(receipt_list)} receipts for organization_id={selected_org_id} and user_id={user_id}")
        
        # Return organizations along with receipts for the filter UI
        response_data = {
            'success': True, 
            'receipts': receipt_list,
            'organizations': organizations,
            'selected_org_id': selected_org_id,
            'show_all': show_all_organizations
        }
        
        logging.debug(f"list_receipts: Returning {len(receipt_list)} receipts to client")
        return jsonify(response_data)
    
    except Exception as e:
        logging.error(f"Error listing receipts: {str(e)}")
        return jsonify({'error': f'Error listing receipts: {str(e)}'}), 500

@receipts_bp.route('/receipts/<int:receipt_id>', methods=['GET'])
@login_required
def get_receipt(receipt_id):
    """Get details of a specific receipt that belongs to the current user's organization."""
    try:
        # Get the user's default organization
        org = current_user.get_default_organization()
        user_id = current_user.id
        
        if org:
            org_id = org.id
            # Find the receipt by ID and either:
            # 1. matching organization_id, or
            # 2. null organization_id and user ownership (legacy data)
            logging.debug(f"Fetching receipt {receipt_id} for organization {org_id} or user {user_id} with legacy data support")
            receipt = Receipt.query.filter(
                Receipt.id == receipt_id,
                or_(
                    Receipt.organization_id == org_id,
                    and_(
                        Receipt.user_id == user_id,
                        or_(
                            Receipt.organization_id.is_(None),
                            Receipt.organization_id == 0
                        )
                    )
                )
            ).first()
        else:
            # Fallback to user ownership if no organization
            logging.debug(f"Fetching receipt {receipt_id} for user {user_id} (no organization)")
            receipt = Receipt.query.filter_by(id=receipt_id, user_id=user_id).first()
        
        if not receipt:
            return jsonify({'error': 'Receipt not found or access denied'}), 404
            
        # Check if this receipt has company expense status
        logging.debug(f"Checking allocations for receipt {receipt_id}")
        company_expense = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
        receipt_dict = receipt.to_dict()
        
        # Add additional status information
        receipt_dict['allocations'] = {
            'company_expense': company_expense is not None,
            'client_expense': False  # Will be implemented in future
        }
        
        if company_expense:
            receipt_dict['company_expense'] = {
                'id': company_expense.id,
                'organization_id': company_expense.organization_id,
                'client_id': company_expense.client_id,
                'is_reimbursable': company_expense.is_reimbursable,
                'status': company_expense.status,
                'description': company_expense.description,
                'notes': company_expense.notes
            }
            
            # Add organization name
            org = Organization.query.get(company_expense.organization_id)
            if org:
                receipt_dict['company_expense']['organization_name'] = org.name
                
            # Add client name if available
            if company_expense.client_id:
                client = Client.query.get(company_expense.client_id)
                if client:
                    receipt_dict['company_expense']['client_name'] = client.name
        
        logging.debug(f"Receipt {receipt_id} allocations: company_expense={receipt_dict['allocations']['company_expense']}, client_expense={receipt_dict['allocations']['client_expense']}")
        
        return jsonify({'success': True, 'receipt': receipt_dict})
    
    except Exception as e:
        logging.error(f"Error getting receipt: {str(e)}")
        return jsonify({'error': f'Error getting receipt: {str(e)}'}), 500

@receipts_bp.route('/receipts/clients/<int:organization_id>', methods=['GET'])
@login_required
def get_clients_by_organization(organization_id):
    """Get clients for a specific organization."""
    try:
        # Verify user belongs to the organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id, 
            organization_id=organization_id
        ).first()
        
        if not user_org:
            return jsonify({'error': 'You do not have access to the selected organization'}), 403
        
        # Get clients for this organization
        clients = Client.query.filter_by(organization_id=organization_id).all()
        client_list = [{'id': client.id, 'name': client.name} for client in clients]
        
        return jsonify({'success': True, 'clients': client_list})
    
    except Exception as e:
        logging.error(f"Error getting clients: {str(e)}")
        return jsonify({'error': f'Error getting clients: {str(e)}'}), 500

@receipts_bp.route('/receipts/delete', methods=['POST'])
@login_required
def delete_receipts():
    """Delete multiple receipts by ID, ensuring they belong to the current user's organization."""
    try:
        # Get receipt IDs from request
        data = request.get_json()
        receipt_ids = data.get('receipt_ids', [])
        
        if not receipt_ids:
            return jsonify({'error': 'No receipt IDs provided'}), 400
        
        try:
            # Convert string IDs to integers
            receipt_ids = [int(id) for id in receipt_ids]
        except ValueError:
            return jsonify({'error': 'Invalid receipt ID format'}), 400
        
        # Get the user's default organization
        org = current_user.get_default_organization()
        user_id = current_user.id
        
        try:
            if org:
                org_id = org.id
                # Find receipts to delete that belong to the user's organization
                # or receipts that belong to the user and have null organization_id (legacy data)
                receipts_to_delete = Receipt.query.filter(
                    Receipt.id.in_(receipt_ids),
                    or_(
                        Receipt.organization_id == org_id,
                        and_(
                            Receipt.user_id == user_id,
                            or_(
                                Receipt.organization_id.is_(None),
                                Receipt.organization_id == 0
                            )
                        )
                    )
                ).all()
            else:
                # Fallback to user's receipts if no organization
                receipts_to_delete = Receipt.query.filter(
                    Receipt.id.in_(receipt_ids),
                    Receipt.user_id == current_user.id
                ).all()
            
            deleted_count = len(receipts_to_delete)
            
            # Log if there's a mismatch between requested and actual deletion count
            if deleted_count != len(receipt_ids):
                logger.warning(f"Receipt ID mismatch: {len(receipt_ids)} requested, {deleted_count} found. User: {user_id}")
        except Exception as db_error:
            logger.error(f"Database error when querying receipts to delete: {str(db_error)}")
            raise
        
        try:
            # Delete each receipt (will cascade to items thanks to relationship setup)
            for receipt in receipts_to_delete:
                db.session.delete(receipt)
            
            # Commit the transaction
            db.session.commit()
            
            # Log successful deletion
            logger.info(f"User {user_id} deleted {deleted_count} receipts: {[r.id for r in receipts_to_delete]}")
            
            return jsonify({
                'success': True, 
                'deleted_count': deleted_count,
                'message': f'Successfully deleted {deleted_count} receipt(s)'
            })
        except Exception as db_error:
            # Roll back and log database error
            db.session.rollback()
            logger.error(f"Database error when deleting receipts: {str(db_error)}")
            raise
    
    except Exception as e:
        logger.error(f"Error deleting receipts: {str(e)}")
        return jsonify({
            'error': f"Error deleting receipts: {str(e)}"
        }), 500

@receipts_bp.route('/receipts/reimbursable/<int:receipt_id>', methods=['POST'])
@login_required
def update_reimbursable_status(receipt_id):
    """Update the reimbursable status of a receipt."""
    try:
        # Validate the receipt belongs to the user
        receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
        if not receipt:
            return jsonify({'error': 'Receipt not found or access denied'}), 404
        
        # Get form data
        data = request.json
        
        organization_id = data.get('organization_id')
        if not organization_id:
            return jsonify({'error': 'Organization is required'}), 400
        
        # Verify user belongs to the organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id, 
            organization_id=organization_id
        ).first()
        
        if not user_org:
            return jsonify({'error': 'You do not have access to the selected organization'}), 403
        
        # Get remaining form data
        client_id = data.get('client_id')
        description = data.get('description', '').strip()
        is_reimbursable = data.get('is_reimbursable', True)
        notes = data.get('notes', '').strip()
        status = data.get('status', ExpenseStatus.SUBMITTED.value)
        
        # Validate status
        valid_statuses = [s.value for s in ExpenseStatus]
        if status not in valid_statuses:
            status = ExpenseStatus.SUBMITTED.value
        
        # Check if client belongs to organization if client_id is provided
        if client_id:
            client = Client.query.filter_by(id=client_id, organization_id=organization_id).first()
            if not client:
                return jsonify({'error': 'Selected client does not belong to the selected organization'}), 400
        
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
            existing_expense.status = status
            
            db.session.commit()
            result_message = 'Expense updated successfully'
        else:
            # Create new company expense
            company_expense = CompanyExpense(
                receipt_id=receipt_id,
                user_id=current_user.id,
                organization_id=organization_id,
                client_id=client_id,
                description=description,
                status=status,
                is_reimbursable=is_reimbursable,
                notes=notes,
                submitted_date=datetime.utcnow()
            )
            
            db.session.add(company_expense)
            db.session.commit()
            result_message = 'Receipt classified as an expense successfully'
        
        # Get updated expense data
        updated_expense = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
        expense_data = {
            'id': updated_expense.id,
            'organization_id': updated_expense.organization_id,
            'client_id': updated_expense.client_id,
            'is_reimbursable': updated_expense.is_reimbursable,
            'status': updated_expense.status,
            'description': updated_expense.description,
            'notes': updated_expense.notes
        }
        
        # Add organization name
        org = Organization.query.get(updated_expense.organization_id)
        if org:
            expense_data['organization_name'] = org.name
            
        # Add client name if available
        if updated_expense.client_id:
            client = Client.query.get(updated_expense.client_id)
            if client:
                expense_data['client_name'] = client.name
        
        return jsonify({
            'success': True, 
            'message': result_message,
            'expense': expense_data
        })
    
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating reimbursable status: {str(e)}")
        return jsonify({'error': f'Error updating reimbursable status: {str(e)}'}), 500

