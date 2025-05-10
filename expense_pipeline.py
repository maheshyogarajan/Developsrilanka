"""
Blueprint for the expense pipeline interface.
This provides a kanban-style board for managing expense reimbursements.
"""

import logging
import traceback
import os
import time
import json
from datetime import datetime, time as datetime_time
from flask import Blueprint, render_template, request, jsonify, abort, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_, and_, func, desc, text
from functools import wraps

from app import db
from models import CompanyExpense, Receipt, Organization, OrganizationUser, User, Client, ExpenseStatus
from utils import check_organization_permission, get_user_organizations, get_receipt_image_url
import thumbnail_manager
from pipeline_cache import cache_pipeline_data, invalidate_pipeline_cache, calculate_priority

# Create the blueprint
pipeline_bp = Blueprint('pipeline', __name__, url_prefix='/expenses')

@pipeline_bp.route('/api/expense-image/<int:expense_id>', methods=['GET'])
@login_required
def get_expense_image(expense_id):
    """
    API endpoint to get the image URL for a specific expense.
    Only loads the image when requested by a detail view.
    """
    try:
        # Input validation
        if not expense_id or expense_id <= 0:
            return jsonify({'error': 'Invalid expense ID'}), 400
            
        # Get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        
        # Security: Verify organization access permission
        if not check_organization_permission(current_user.id, expense.organization_id):
            logging.warning(f"User {current_user.id} attempted to access image for expense {expense_id} in unauthorized organization {expense.organization_id}")
            return jsonify({'error': 'Access denied'}), 403
        
        # Get the receipt
        if not expense.receipt_id:
            return jsonify({'error': 'No receipt associated with this expense'}), 404
            
        receipt = Receipt.query.get_or_404(expense.receipt_id)
        
        # Security: Ensure receipt belongs to the same organization if possible
        if hasattr(receipt, 'organization_id') and receipt.organization_id != expense.organization_id:
            logging.warning(f"Receipt organization mismatch: receipt {receipt.id} belongs to org {receipt.organization_id}, expense belongs to org {expense.organization_id}")
            return jsonify({'error': 'Resource not found'}), 404
        
        # Generate image URLs using project utility functions
        image_url = '/static/img/receipt-placeholder.svg'
        thumbnail_url = '/static/img/receipt-placeholder.svg'
        
        # Use appropriate image retrieval method based on schema
        try:
            if hasattr(receipt, 's3_key') and receipt.s3_key:
                # Use existing utility function for S3-stored images
                image_url = get_receipt_image_url(receipt)
                
                # Get thumbnail if available using existing thumbnail_manager
                if hasattr(thumbnail_manager, 'get_thumbnail_url'):
                    thumbnail_url = thumbnail_manager.get_thumbnail_url(receipt) or image_url
            elif hasattr(receipt, 'image_path') and receipt.image_path:
                # Legacy path-based image storage
                image_url = url_for('static', filename=f'uploads/{receipt.image_path}')
                thumbnail_url = image_url
        except Exception as e:
            logging.error(f"Error generating image URL for receipt {receipt.id}: {str(e)}")
        
        return jsonify({
            'expense_id': expense_id,
            'image_url': image_url,
            'thumbnail_url': thumbnail_url
        })
        
    except Exception as e:
        logging.error(f"Error fetching expense image: {str(e)}")
        return jsonify({'error': 'Failed to retrieve image'}), 500

@pipeline_bp.route('/pipeline')
@login_required
def expense_pipeline():
    """
    Render the main expense pipeline view.
    This shows a kanban-style board with columns for each expense status.
    """
    try:
        # Get the user's organizations
        user_organizations = get_user_organizations(current_user.id)
        
        # Get the selected organization ID (default to the first one)
        org_id = request.args.get('org_id', None)
        if org_id:
            org_id = int(org_id)
        elif user_organizations:
            # Try to find default org first
            default_org = next((org for org in user_organizations if getattr(org, 'is_default', False)), None)
            if default_org:
                org_id = default_org.id
            else:
                org_id = user_organizations[0].id
        
        # Get the current user's role in the organization
        user_role = None
        if org_id:
            org_user = OrganizationUser.query.filter_by(
                user_id=current_user.id, 
                organization_id=org_id
            ).first()
            if org_user:
                user_role = org_user.role
        
        # Determine which view to show based on role and query parameter
        view_type = request.args.get('view', 'default')
        if view_type not in ['submitter', 'approver', 'finance', 'all']:
            view_type = 'default'
        
        # Get the organization's clients for the client selector
        clients = []
        if org_id:
            clients = Client.query.filter_by(organization_id=org_id).order_by(Client.name).all()
        
        return render_template(
            'expense_pipeline.html',
            organizations=user_organizations,
            selected_org_id=org_id,
            user_role=user_role,
            view_type=view_type,
            clients=clients,
            expense_statuses=ExpenseStatus
        )
    except Exception as e:
        logging.error(f"Error rendering expense pipeline: {str(e)}")
        flash(f"Error loading expense pipeline: {str(e)}", "danger")
        return redirect(url_for('expense.expenses'))

@pipeline_bp.route('/api/pipeline-data', methods=['GET'])
@login_required
def get_pipeline_data():
    """
    API endpoint to get expense pipeline data for a specific organization.
    Returns expenses grouped by status.
    """
    # Get the organization ID from the query parameters
    org_id = request.args.get('org_id', None)
    
    # If no org_id provided, find user's default organization
    if not org_id:
        default_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            is_default=True
        ).first()
        
        # If user has a default organization, use it
        if default_org:
            org_id = default_org.organization_id
            logging.info(f"Using default organization ID: {org_id}")
        else:
            # Otherwise get the first organization user belongs to
            first_org = OrganizationUser.query.filter_by(
                user_id=current_user.id
            ).first()
            
            if first_org:
                org_id = first_org.organization_id
                logging.info(f"Using first available organization ID: {org_id}")
            else:
                logging.error("No organizations found for user")
                return jsonify({'error': 'No organizations found for user'}), 404
    
    try:
        logging.info(f"Loading pipeline data for organization ID: {org_id}")
        
        # Check if the user has permission to view this organization
        if not check_organization_permission(current_user.id, org_id, ['owner', 'admin', 'member', 'viewer']):
            return jsonify({'error': 'You do not have permission to view this organization'}), 403
        
        # Get the user's role in the organization
        org_user = OrganizationUser.query.filter_by(
            user_id=current_user.id, 
            organization_id=org_id
        ).first()
        user_role = org_user.role if org_user else None
        
        # Client filter (optional)
        client_id = request.args.get('client_id', None)
        if client_id:
            try:
                client_id = int(client_id)
            except ValueError:
                client_id = None
        
        # Time range filter (optional)
        date_from = request.args.get('date_from', None)
        date_to = request.args.get('date_to', None)
        
        # Determine which expenses to show based on view type, role, and filters
        view_type = request.args.get('view', 'default')
        
        # Base query for expenses in this organization
        query = CompanyExpense.query.filter_by(organization_id=org_id)
        
        # Apply client filter if specified
        if client_id:
            query = query.filter_by(client_id=client_id)
        
        # Apply date filters if specified
        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                query = query.filter(CompanyExpense.submitted_date >= date_from_obj)
            except ValueError:
                # Invalid date format, ignore this filter
                pass
        
        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                query = query.filter(CompanyExpense.submitted_date <= date_to_obj)
            except ValueError:
                # Invalid date format, ignore this filter
                pass
        
        # Modify query based on view type and role
        if view_type == 'submitter' or (view_type == 'default' and user_role in ['member', 'viewer']):
            # Show only the user's own expenses
            query = query.filter_by(user_id=current_user.id)
        elif view_type == 'approver' or (view_type == 'default' and user_role == 'admin'):
            # Show all expenses for admins/approvers
            pass  # No additional filtering needed
        elif view_type == 'finance' or (view_type == 'default' and user_role == 'owner'):
            # Show all expenses for finance/owners
            pass  # No additional filtering needed
        
        # Order by most recent first within each status
        if hasattr(CompanyExpense, 'updated_at'):
            query = query.order_by(desc(CompanyExpense.updated_at))
        else:
            query = query.order_by(desc(CompanyExpense.id))
        
        # Get expenses grouped by status
        pending_submission = []
        submitted = []
        approved = []
        reimbursed = []
        rejected = []
        
        # Get receipts that could be submitted (not yet associated with an expense)
        if view_type in ['submitter', 'default', 'all']:
            # Basic query for receipts belonging to the user and organization
            pending_receipts_query = Receipt.query.filter(
                Receipt.user_id == current_user.id,
                Receipt.organization_id == org_id
            )
            
            # Find receipts that aren't already associated with an expense
            expense_receipt_ids_query = db.session.query(CompanyExpense.receipt_id).filter(
                CompanyExpense.receipt_id.isnot(None)
            )
            expense_receipt_ids = [r[0] for r in expense_receipt_ids_query.all() if r[0] is not None]
            
            if expense_receipt_ids:
                pending_receipts_query = pending_receipts_query.filter(
                    ~Receipt.id.in_(expense_receipt_ids)
                )
            
            # Apply date filter if specified
            if date_from:
                try:
                    date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                    pending_receipts_query = pending_receipts_query.filter(Receipt.date >= date_from_obj)
                except ValueError:
                    pass
            
            if date_to:
                try:
                    date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                    pending_receipts_query = pending_receipts_query.filter(Receipt.date <= date_to_obj)
                except ValueError:
                    pass
            
            # Order by most recent first
            pending_receipts_query = pending_receipts_query.order_by(desc(Receipt.date))
            
            # Get the receipts
            pending_receipts = pending_receipts_query.all()
            
            # Format the receipts as expense-like objects
            for receipt in pending_receipts:
                # Get image URL for the receipt
                try:
                    image_url = get_receipt_image_url(receipt)
                    # Check if thumbnail_manager is properly initialized
                    if hasattr(thumbnail_manager, 'get_thumbnail_url'):
                        thumbnail_url = thumbnail_manager.get_thumbnail_url(receipt)
                    else:
                        logging.warning("thumbnail_manager missing get_thumbnail_url method")
                        thumbnail_url = '/static/img/receipt-placeholder.svg'
                except Exception as img_error:
                    logging.error(f"Error getting image URL for receipt {receipt.id}: {str(img_error)}")
                    image_url = '/static/img/receipt-placeholder.svg'
                    thumbnail_url = '/static/img/receipt-placeholder.svg'
                
                # Get safe user object
                user_obj = User.query.get(receipt.user_id)
                user_name = getattr(user_obj, 'username', "Unknown") if user_obj else "Unknown"
                
                pending_submission.append({
                    'id': f"receipt_{receipt.id}",
                    'description': getattr(receipt, 'vendor_name', "") or "Receipt",
                    'amount': float(getattr(receipt, 'total_amount', getattr(receipt, 'total', 0)) or 0),
                    'date': receipt.date.strftime('%Y-%m-%d') if hasattr(receipt, 'date') and receipt.date else None,
                    'receipt_id': receipt.id,
                    'category': getattr(receipt, 'expense_major_category', getattr(receipt, 'category', "Uncategorized")) or "Uncategorized",
                    'user': {
                        'id': receipt.user_id,
                        'name': user_name
                    },
                    'image_url': image_url,
                    'thumbnail_url': thumbnail_url,
                    'type': 'receipt',
                    'created_at': receipt.created_at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(receipt, 'created_at') and receipt.created_at else None,
                    'priority': 2  # Default priority for receipts
                })
        
        # Get all expenses by status
        all_expenses = query.all()
        for expense in all_expenses:
            # Get the related receipt if available
            receipt = None
            image_url = None
            thumbnail_url = None
            if expense.receipt_id:
                receipt = Receipt.query.get(expense.receipt_id)
                if receipt:
                    try:
                        image_url = get_receipt_image_url(receipt)
                        # Check if thumbnail_manager is properly initialized
                        if hasattr(thumbnail_manager, 'get_thumbnail_url'):
                            thumbnail_url = thumbnail_manager.get_thumbnail_url(receipt)
                        else:
                            logging.warning("thumbnail_manager missing get_thumbnail_url method")
                            thumbnail_url = '/static/img/receipt-placeholder.svg'
                    except Exception as img_error:
                        logging.error(f"Error getting image URL for expense receipt {receipt.id}: {str(img_error)}")
                        image_url = '/static/img/receipt-placeholder.svg'
                        thumbnail_url = '/static/img/receipt-placeholder.svg'
            
            # Get client name if available
            client_name = None
            if expense.client_id:
                client = Client.query.get(expense.client_id)
                if client:
                    client_name = client.name
            
            # Get approver name if available
            approver_name = None
            if hasattr(expense, 'approved_by_user_id') and expense.approved_by_user_id:
                approver = User.query.get(expense.approved_by_user_id)
                if approver:
                    approver_name = getattr(approver, 'name', None)
            
            # Get reimbursement processor name if available
            reimbursed_by_name = None
            if hasattr(expense, 'reimbursed_by_user_id') and expense.reimbursed_by_user_id:
                processor = User.query.get(expense.reimbursed_by_user_id)
                if processor:
                    reimbursed_by_name = getattr(processor, 'name', None)
            
            # Get rejection processor name if available
            rejected_by_name = None
            if hasattr(expense, 'rejected_by_user_id') and expense.rejected_by_user_id:
                rejector = User.query.get(expense.rejected_by_user_id)
                if rejector:
                    rejected_by_name = getattr(rejector, 'name', None)
            
            # Get expense amount from receipt relationship since CompanyExpense doesn't store amount directly
            expense_amount = 0.0
            if expense.receipt and hasattr(expense.receipt, 'total_amount'):
                expense_amount = float(expense.receipt.total_amount or 0.0)
            
            # Calculate priority (1-3) based on amount
            priority = 2  # Default medium priority
            
            if expense_amount:
                # Higher amounts get higher priority
                if expense_amount > 1000:
                    priority = 3
                elif expense_amount < 100:
                    priority = 1
            
            # Get safe user object for expense
            user_obj = User.query.get(expense.user_id)
            user_name = getattr(user_obj, 'name', "Unknown") if user_obj else "Unknown"
                
            # Build the expense data object
            expense_data = {
                'id': expense.id,
                'description': getattr(expense, 'description', "") or "Expense",
                'amount': expense_amount,
                'date': expense.submitted_date.strftime('%Y-%m-%d') if hasattr(expense, 'submitted_date') and expense.submitted_date else None,
                'receipt_id': expense.receipt_id,
                'category': getattr(expense, 'category', "Uncategorized") or "Uncategorized",
                'user': {
                    'id': expense.user_id,
                    'name': user_name
                },
                'notes': expense.notes,
                'client_id': expense.client_id,
                'client_name': client_name,
                'is_reimbursable': expense.is_reimbursable,
                'vendor_name': expense.receipt.vendor_name if expense.receipt else "",
                'image_url': image_url,
                'thumbnail_url': thumbnail_url,
                'type': 'expense',
                'status': expense.status,
                'priority': priority,
                'reimbursement_method': expense.reimbursement_method if hasattr(expense, 'reimbursement_method') else None,
                'reimbursement_reference': expense.reimbursement_reference if hasattr(expense, 'reimbursement_reference') else None,
                'approved_by': {
                    'id': expense.approved_by_user_id,
                    'name': approver_name
                } if hasattr(expense, 'approved_by_user_id') and expense.approved_by_user_id else None,
                'approved_date': expense.approved_date.strftime('%Y-%m-%d') if hasattr(expense, 'approved_date') and expense.approved_date else None,
                'reimbursed_by': {
                    'id': expense.reimbursed_by_user_id,
                    'name': reimbursed_by_name
                } if hasattr(expense, 'reimbursed_by_user_id') and expense.reimbursed_by_user_id else None,
                'reimbursed_date': expense.reimbursed_date.strftime('%Y-%m-%d') if hasattr(expense, 'reimbursed_date') and expense.reimbursed_date else None,
                'rejected_by': {
                    'id': expense.rejected_by_user_id,
                    'name': rejected_by_name
                } if hasattr(expense, 'rejected_by_user_id') and expense.rejected_by_user_id else None,
                'rejected_date': expense.rejected_date.strftime('%Y-%m-%d') if hasattr(expense, 'rejected_date') and expense.rejected_date else None,
                'rejection_reason': expense.rejection_reason if hasattr(expense, 'rejection_reason') else None,
                'created_at': expense.created_at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(expense, 'created_at') and expense.created_at else None,
                'updated_at': expense.updated_at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(expense, 'updated_at') and expense.updated_at else None,
            }
            
            # Add to the appropriate list based on status
            if expense.status == ExpenseStatus.SUBMITTED.value:
                submitted.append(expense_data)
            elif expense.status == ExpenseStatus.APPROVED.value:
                approved.append(expense_data)
            elif expense.status == ExpenseStatus.REIMBURSED.value:
                reimbursed.append(expense_data)
            elif expense.status == ExpenseStatus.REJECTED.value:
                rejected.append(expense_data)
        
        # Calculate summary statistics
        total_pending = sum(item['amount'] or 0 for item in pending_submission)
        total_submitted = sum(item['amount'] or 0 for item in submitted)
        total_approved = sum(item['amount'] or 0 for item in approved)
        total_reimbursed = sum(item['amount'] or 0 for item in reimbursed)
        total_rejected = sum(item['amount'] or 0 for item in rejected)
        
        # Prepare response data
        pipeline_data = {
            'organization_id': org_id,
            'user_role': user_role,
            'columns': {
                'pending_submission': {
                    'title': 'Pending Submission',
                    'items': pending_submission,
                    'count': len(pending_submission),
                    'total': round(total_pending, 2),
                    'color': '#495057',
                    'icon': 'far fa-file-alt'
                },
                'submitted': {
                    'title': 'Submitted',
                    'items': submitted,
                    'count': len(submitted),
                    'total': round(total_submitted, 2),
                    'color': '#1864ab',
                    'icon': 'fas fa-file-upload'
                },
                'approved': {
                    'title': 'Approved',
                    'items': approved,
                    'count': len(approved),
                    'total': round(total_approved, 2),
                    'color': '#2b8a3e',
                    'icon': 'fas fa-check-circle'
                },
                'reimbursed': {
                    'title': 'Reimbursed',
                    'items': reimbursed,
                    'count': len(reimbursed),
                    'total': round(total_reimbursed, 2),
                    'color': '#5f3dc4',
                    'icon': 'fas fa-money-bill-wave'
                },
                'rejected': {
                    'title': 'Rejected',
                    'items': rejected,
                    'count': len(rejected),
                    'total': round(total_rejected, 2),
                    'color': '#c92a2a',
                    'icon': 'fas fa-times-circle'
                }
            }
        }
        
        return jsonify(pipeline_data)
    
    except Exception as e:
        error_msg = str(e)
        stack_trace = traceback.format_exc()
        logging.error(f"Error getting pipeline data: {error_msg}")
        logging.error(f"Stack trace: {stack_trace}")
        detailed_msg = {
            'error': 'Failed to load pipeline data',
            'details': error_msg,
            'trace': stack_trace,
            'type': 'api_error'
        }
        return jsonify(detailed_msg), 500

@pipeline_bp.route('/api/update-expense-status', methods=['POST'])
@login_required
def update_expense_status():
    """
    API endpoint to update an expense's status (used for drag-and-drop).
    """
    try:
        data = request.json
        expense_id = data.get('expense_id')
        new_status = data.get('new_status')
        org_id = data.get('organization_id')
        
        # Validate input
        if not expense_id or not new_status or not org_id:
            return jsonify({'error': 'Missing required parameters'}), 400
        
        # Handle receipt-to-expense conversion
        if str(expense_id).startswith('receipt_'):
            receipt_id = int(expense_id.split('_')[1])
            return create_expense_from_receipt(receipt_id, org_id, new_status)
        
        # Get the expense
        expense = CompanyExpense.query.get_or_404(expense_id)
        if not expense:
            return jsonify({'error': 'Expense not found'}), 404
        
        # Check permissions
        if not check_organization_permission(current_user.id, org_id, ['owner', 'admin']) and expense.user_id != current_user.id:
            return jsonify({'error': 'You do not have permission to update this expense'}), 403
        
        # Validate the status transition
        valid_transition = validate_status_transition(expense.status, new_status, current_user.id, org_id)
        if not valid_transition['valid']:
            return jsonify({'error': valid_transition['message']}), 400
        
        # Update the expense status
        expense.status = new_status
        
        # Add additional information based on the new status
        current_time = datetime.utcnow()
        if new_status == ExpenseStatus.APPROVED.value:
            expense.approved_by_user_id = current_user.id
            expense.approved_date = current_time
        elif new_status == ExpenseStatus.REJECTED.value:
            expense.rejected_by_user_id = current_user.id
            expense.rejected_date = current_time
            expense.rejection_reason = data.get('rejection_reason')
        elif new_status == ExpenseStatus.REIMBURSED.value:
            expense.reimbursed_by_user_id = current_user.id
            expense.reimbursed_date = current_time
            expense.reimbursement_method = data.get('reimbursement_method')
            expense.reimbursement_reference = data.get('reimbursement_reference')
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Expense status updated successfully'})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating expense status: {str(e)}")
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

def create_expense_from_receipt(receipt_id, org_id, new_status):
    """
    Create a new expense from a receipt.
    """
    try:
        # Get the receipt
        receipt = Receipt.query.get_or_404(receipt_id)
        
        # Check that the receipt belongs to the user
        if receipt.user_id != current_user.id:
            return jsonify({'error': 'You do not have permission to use this receipt'}), 403
        
        # Check that the receipt belongs to the specified organization
        if receipt.organization_id != int(org_id):
            return jsonify({'error': 'Receipt does not belong to the specified organization'}), 400
        
        # Create a new expense
        expense = CompanyExpense()
        expense.user_id = current_user.id
        expense.organization_id = int(org_id)
        expense.receipt_id = receipt_id
        expense.description = receipt.vendor_name or "Expense from receipt"
        
        # Get total amount from receipt - for CompanyExpense, we don't set an amount directly
        # since it's derived from the receipt relationship
        total_amount = receipt.total_amount if hasattr(receipt, 'total_amount') else 0
        
        # Use submitted_date since CompanyExpense doesn't have a date field
        expense.submitted_date = datetime.utcnow()
        # Use receipt.date for reference if available
        if receipt.date:
            expense.submitted_date = datetime.combine(receipt.date, datetime_time.min)
            
        # We don't set category directly as it's derived from receipt relationship
        # We don't set vendor_name directly as it's derived from receipt relationship
        
        expense.is_reimbursable = True
        expense.status = ExpenseStatus.SUBMITTED.value
        
        db.session.add(expense)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Receipt converted to expense successfully',
            'expense_id': expense.id
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error creating expense from receipt: {str(e)}")
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

def validate_status_transition(current_status, new_status, user_id, org_id):
    """
    Validate whether a status transition is allowed based on the user's role.
    """
    # Get the user's role in the organization
    org_user = OrganizationUser.query.filter_by(
        user_id=user_id, 
        organization_id=org_id
    ).first()
    
    if not org_user:
        return {'valid': False, 'message': 'User is not a member of this organization'}
    
    user_role = org_user.role
    
    # Define allowed transitions based on role
    transitions = {
        'owner': {
            # Owners can make any transition
            ExpenseStatus.SUBMITTED.value: [ExpenseStatus.APPROVED.value, ExpenseStatus.REJECTED.value],
            ExpenseStatus.APPROVED.value: [ExpenseStatus.REIMBURSED.value, ExpenseStatus.REJECTED.value],
            ExpenseStatus.REJECTED.value: [ExpenseStatus.SUBMITTED.value, ExpenseStatus.APPROVED.value],
            ExpenseStatus.REIMBURSED.value: []  # Cannot transition from reimbursed
        },
        'admin': {
            # Admins can approve/reject but not reimburse
            ExpenseStatus.SUBMITTED.value: [ExpenseStatus.APPROVED.value, ExpenseStatus.REJECTED.value],
            ExpenseStatus.APPROVED.value: [ExpenseStatus.REJECTED.value],
            ExpenseStatus.REJECTED.value: [ExpenseStatus.SUBMITTED.value, ExpenseStatus.APPROVED.value],
            ExpenseStatus.REIMBURSED.value: []  # Cannot transition from reimbursed
        },
        'member': {
            # Members can submit/withdraw their own expenses
            ExpenseStatus.SUBMITTED.value: [],  # Cannot transition from submitted (only withdraw completely)
            ExpenseStatus.APPROVED.value: [],  # Cannot transition from approved
            ExpenseStatus.REJECTED.value: [ExpenseStatus.SUBMITTED.value],  # Can resubmit after rejection
            ExpenseStatus.REIMBURSED.value: []  # Cannot transition from reimbursed
        },
        'viewer': {
            # Viewers cannot make any transitions
        }
    }
    
    allowed_transitions = transitions.get(user_role, {}).get(current_status, [])
    
    if new_status in allowed_transitions:
        return {'valid': True}
    else:
        return {
            'valid': False, 
            'message': f"Users with role {user_role} cannot change expense status from {current_status} to {new_status}"
        }

@pipeline_bp.route('/batch-submit', methods=['GET', 'POST'])
@login_required
def batch_submit_expenses():
    """
    Handle batch submission of expenses.
    """
    if request.method == 'GET':
        # Display the batch submission form
        org_id = request.args.get('org_id')
        if not org_id:
            flash('Organization ID is required', 'danger')
            return redirect(url_for('pipeline.expense_pipeline'))
        
        # Get receipts that haven't been assigned to expenses yet
        receipt_ids = request.args.get('receipt_ids', '')
        receipt_ids = [int(id) for id in receipt_ids.split(',') if id.isdigit()]
        
        if not receipt_ids:
            flash('No receipts selected for batch submission', 'warning')
            return redirect(url_for('pipeline.expense_pipeline', org_id=org_id))
        
        # Get the selected receipts
        receipts = Receipt.query.filter(
            Receipt.id.in_(receipt_ids),
            Receipt.user_id == current_user.id,
            Receipt.organization_id == int(org_id)
        ).all()
        
        # Get clients for the organization
        clients = Client.query.filter_by(organization_id=int(org_id)).order_by(Client.name).all()
        
        return render_template(
            'batch_submit_expenses.html',
            receipts=receipts,
            clients=clients,
            org_id=org_id
        )
    else:
        # Process the batch submission
        org_id = request.form.get('org_id')
        receipt_ids = request.form.getlist('receipt_ids')
        client_id = request.form.get('client_id')
        is_reimbursable = request.form.get('is_reimbursable') == 'on'
        notes = request.form.get('notes', '')
        
        if not org_id or not receipt_ids:
            flash('Missing required parameters', 'danger')
            return redirect(url_for('pipeline.expense_pipeline'))
        
        try:
            # Convert client_id to int or None
            if client_id:
                client_id = int(client_id)
            else:
                client_id = None
            
            # Create expenses for each receipt
            created_count = 0
            for receipt_id in receipt_ids:
                receipt_id = int(receipt_id)
                receipt = Receipt.query.get(receipt_id)
                
                # Verify the receipt belongs to the user and organization
                if receipt and receipt.user_id == current_user.id and receipt.organization_id == int(org_id):
                    # Check if an expense already exists for this receipt
                    existing = CompanyExpense.query.filter_by(receipt_id=receipt_id).first()
                    if existing:
                        continue
                    
                    # Create a new expense
                    expense = CompanyExpense()
                    expense.user_id = current_user.id
                    expense.organization_id = int(org_id)
                    expense.client_id = client_id
                    expense.receipt_id = receipt_id
                    expense.description = receipt.vendor_name or "Expense from receipt"
                    
                    # Use submitted_date since CompanyExpense doesn't have a date field
                    expense.submitted_date = datetime.utcnow()
                    # Use receipt.date for reference
                    if receipt.date:
                        expense.submitted_date = datetime.combine(receipt.date, datetime_time.min)
                    
                    # Add notes from the form
                    expense.notes = notes
                    expense.is_reimbursable = is_reimbursable
                    expense.status = ExpenseStatus.SUBMITTED.value
                    
                    db.session.add(expense)
                    created_count += 1
            
            if created_count > 0:
                db.session.commit()
                flash(f'Successfully created {created_count} expenses', 'success')
            else:
                flash('No new expenses were created', 'warning')
            
            return redirect(url_for('pipeline.expense_pipeline', org_id=org_id))
        
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error during batch submission: {str(e)}")
            flash(f'Error creating expenses: {str(e)}', 'danger')
            return redirect(url_for('pipeline.expense_pipeline', org_id=org_id))