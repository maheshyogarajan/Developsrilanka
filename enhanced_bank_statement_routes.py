"""
Enhanced Bank Statement Routes for Universal Transaction System
This module provides the web interface for bank statement upload and reconciliation.
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import and_
from app import db
from models import Organization, User, OrganizationUser, Receipt, BankAccount
from enhanced_financial_models import (
    BankStatement, BankStatementPage, BankStatementProcessingLog,
    FinancialTransaction, TransactionMetaBank, SourceType, TransactionStatus
)
from enhanced_reconciliation_models import (
    SmartMatchingRules, ReconciliationAudit, ReconciliationException
)
from enhanced_financial_services import (
    UniversalGLService, EnhancedMatchingEngine, BalanceSheetService
)
from bank_statement_processor import BankStatementProcessor
from smart_reconciliation_service import SmartReconciliationService
import hashlib
import os
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
import json
import boto3
from io import BytesIO

logger = logging.getLogger(__name__)

# Create blueprint
enhanced_bank = Blueprint('enhanced_bank', __name__, url_prefix='/enhanced_bank')

@enhanced_bank.route('/statements')
@login_required
def list_statements():
    """List all bank statements for the user's organizations."""
    try:
        # Get user's organizations
        user_orgs = db.session.query(OrganizationUser).filter_by(user_id=current_user.id).all()
        org_ids = [uo.organization_id for uo in user_orgs]
        
        # Get selected organization
        selected_org_id = request.args.get('org_id', type=int)
        if not selected_org_id and org_ids:
            selected_org_id = org_ids[0]
        
        # Get bank statements for selected organization
        statements = []
        if selected_org_id:
            statements = BankStatement.query.filter_by(
                organization_id=selected_org_id
            ).order_by(BankStatement.created_at.desc()).all()
        
        # Get organizations for dropdown
        organizations = Organization.query.filter(Organization.id.in_(org_ids)).all()
        
        return render_template('enhanced_bank/statements_list.html',
                             statements=statements,
                             organizations=organizations,
                             selected_org_id=selected_org_id)
                             
    except Exception as e:
        logger.error(f"Error listing bank statements: {str(e)}")
        flash('Error loading bank statements', 'error')
        return redirect(url_for('main.index'))

@enhanced_bank.route('/upload')
@login_required
def upload_form():
    """Show bank statement upload form."""
    try:
        # Get user's organizations
        user_orgs = db.session.query(OrganizationUser).filter_by(user_id=current_user.id).all()
        org_ids = [uo.organization_id for uo in user_orgs]
        organizations = Organization.query.filter(Organization.id.in_(org_ids)).all()
        
        return render_template('enhanced_bank/upload_form.html',
                             organizations=organizations)
                             
    except Exception as e:
        logger.error(f"Error showing upload form: {str(e)}")
        flash('Error loading upload form', 'error')
        return redirect(url_for('enhanced_bank.list_statements'))

@enhanced_bank.route('/upload', methods=['POST'])
@login_required
def upload_statement():
    """Handle bank statement upload with complete processing pipeline."""
    try:
        # Validate inputs
        organization_id = request.form.get('organization_id', type=int)
        statement_file = request.files.get('statement_file')
        
        if not organization_id:
            flash('Please select an organization', 'error')
            return redirect(url_for('enhanced_bank.upload_form'))
            
        if not statement_file or not statement_file.filename:
            flash('Please select a PDF file', 'error')
            return redirect(url_for('enhanced_bank.upload_form'))
            
        # Verify user has access to organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=organization_id
        ).first()
        
        if not user_org:
            flash('Access denied to selected organization', 'error')
            return redirect(url_for('enhanced_bank.upload_form'))
        
        # Read and validate PDF
        pdf_data = statement_file.read()
        if len(pdf_data) == 0:
            flash('Uploaded file is empty', 'error')
            return redirect(url_for('enhanced_bank.upload_form'))
            
        if len(pdf_data) > 10 * 1024 * 1024:  # 10MB limit
            flash('File size must be under 10MB', 'error')
            return redirect(url_for('enhanced_bank.upload_form'))
        
        # Parse statement dates for required fields
        try:
            statement_start = request.form.get('statement_start')
            statement_end = request.form.get('statement_end')
            if statement_start and statement_end:
                period_from = datetime.strptime(statement_start, '%Y-%m-%d').date()
                period_to = datetime.strptime(statement_end, '%Y-%m-%d').date()
            else:
                # Default to current month if not provided
                today = date.today()
                period_from = today.replace(day=1)
                period_to = today
        except ValueError:
            # Fallback to current month if date parsing fails
            today = date.today()
            period_from = today.replace(day=1)
            period_to = today
        
        account_number = request.form.get('account_number', '')
        
        # Generate content hash for duplicate detection
        content_hash = BankStatement.generate_content_hash(
            pdf_data, len(pdf_data), account_number, period_from, period_to
        )
        
        # Check for duplicates
        existing = BankStatement.query.filter_by(content_sha256=content_hash).first()
        if existing:
            flash('This statement has already been uploaded', 'warning')
            return redirect(url_for('enhanced_bank.view_statement', statement_id=existing.id))
        
        # Create statement record with correct fields
        statement = BankStatement(
            organization_id=organization_id,
            content_sha256=content_hash,
            file_size_bytes=len(pdf_data),
            page_count=1,  # Will be updated by processor
            bank_name=request.form.get('bank_name', ''),
            account_number=account_number,
            statement_period_from=period_from,
            statement_period_to=period_to,
            statement_currency=request.form.get('base_currency', 'LKR'),
            processing_status='processing',
            uploaded_by=current_user.id
        )
        db.session.add(statement)
        db.session.flush()  # Get ID for S3 key
        
        # Store in S3 (using existing infrastructure)
        try:
            s3_client = boto3.client('s3')
            s3_key = f"bank-statements/{statement.id}/statement.pdf"
            s3_client.upload_fileobj(
                BytesIO(pdf_data),
                os.environ.get('AWS_S3_BUCKET_NAME'),
                s3_key
            )
            statement.original_pdf_s3_key = s3_key
        except Exception as s3_error:
            logger.warning(f"S3 upload failed: {s3_error}")
            # Continue processing without S3 - store locally if needed
        
        # Process PDF
        try:
            processor = BankStatementProcessor()
            result = processor.process_pdf(pdf_data, statement.bank_name)
            
            if result['validation']['is_valid']:
                # Update statement with extracted info
                info = result['statement_info']
                statement.statement_period_from = info.get('statement_period_start') or period_from
                statement.statement_period_to = info.get('statement_period_end') or period_to
                statement.opening_balance = info.get('opening_balance')
                statement.closing_balance = info.get('closing_balance')
                statement.total_transactions = len(result['transactions'])
                statement.processing_status = 'completed'
                
                # Get currency with fallback
                currency = statement.statement_currency or 'LKR'
                
                # Create transactions
                for txn_data in result['transactions']:
                    transaction = FinancialTransaction(
                        organization_id=organization_id,
                        transaction_date=txn_data['transaction_date'],
                        amount=Decimal(str(txn_data['amount'])),
                        description=txn_data['description'],
                        currency_code=currency,
                        fx_rate=Decimal('1.000000'),
                        source_type=SourceType.BANK_STATEMENT.value,
                        source_id=0,  # Will be updated after metadata creation
                        status=TransactionStatus.PENDING.value,
                        created_by=current_user.id
                    )
                    
                    # Auto-calculate derived fields
                    transaction.base_currency_amount = transaction.amount * transaction.fx_rate
                    transaction.net_amount = transaction.amount
                    
                    db.session.add(transaction)
                    db.session.flush()
                    
                    # Create bank metadata
                    bank_meta = TransactionMetaBank(
                        transaction_id=transaction.id,
                        bank_statement_id=statement.id,
                        bank_reference_number=txn_data.get('reference_number', ''),
                        statement_line_number=txn_data.get('line_number'),
                        running_balance=Decimal(str(txn_data.get('balance_after', 0))) if txn_data.get('balance_after') else None,
                        extraction_confidence=txn_data.get('extraction_confidence')
                    )
                    db.session.add(bank_meta)
                    db.session.flush()
                    
                    # Update source_id to reference metadata
                    transaction.source_id = bank_meta.transaction_id
                
                db.session.commit()
                
                # Trigger reconciliation if requested
                if request.form.get('auto_reconcile'):
                    try:
                        reconciler = SmartReconciliationService()
                        reconciliation_result = reconciler.reconcile_statement(
                            statement.id, organization_id
                        )
                        flash(f'Statement processed successfully! {reconciliation_result["auto_matched"]} transactions auto-matched.', 'success')
                    except Exception as recon_error:
                        logger.error(f"Reconciliation failed: {recon_error}")
                        flash('Statement processed but auto-reconciliation had issues.', 'warning')
                else:
                    flash(f'Statement processed successfully! Found {statement.total_transactions} transactions.', 'success')
                
                return redirect(url_for('enhanced_bank.view_statement', statement_id=statement.id))
                
            else:
                # Processing failed
                statement.processing_status = 'failed'
                statement.processing_error = '; '.join(result['validation']['errors'])
                db.session.commit()
                
                flash('PDF processing failed. Please check the file format.', 'error')
                return redirect(url_for('enhanced_bank.view_statement', statement_id=statement.id))
                
        except Exception as process_error:
            logger.error(f"PDF processing error: {process_error}")
            statement.processing_status = 'failed'
            statement.processing_error = str(process_error)
            db.session.commit()
            
            flash('Error processing PDF. Please ensure it\'s a valid bank statement.', 'error')
            return redirect(url_for('enhanced_bank.list_statements'))
    
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        db.session.rollback()
        flash('Upload failed. Please try again.', 'error')
        return redirect(url_for('enhanced_bank.upload_form'))

@enhanced_bank.route('/statement/<int:statement_id>')
@login_required
def view_statement(statement_id):
    """View bank statement details with transactions."""
    try:
        # Get statement with organization check
        statement = db.session.query(BankStatement).join(
            OrganizationUser,
            OrganizationUser.organization_id == BankStatement.organization_id
        ).filter(and_(
            BankStatement.id == statement_id,
            OrganizationUser.user_id == current_user.id
        )).first()
        
        if not statement:
            flash('Statement not found or access denied', 'error')
            return redirect(url_for('enhanced_bank.list_statements'))
        
        # FIXED: Use and_() to prevent string formatting errors
        transactions = db.session.query(FinancialTransaction)\
            .outerjoin(TransactionMetaBank, TransactionMetaBank.transaction_id == FinancialTransaction.id)\
            .filter(and_(
                FinancialTransaction.source_type == 'bank_statement',
                FinancialTransaction.source_id == statement_id,
                FinancialTransaction.organization_id == statement.organization_id
            ))\
            .order_by(FinancialTransaction.transaction_date.desc())\
            .all()
        
        # FIXED: Calculate reconciliation based on actual status values
        total_transactions = len(transactions)
        reconciled_count = 0
        
        for t in transactions:
            if t.reconciliation_status in ['auto_matched', 'manual_matched', 'confirmed']:
                reconciled_count += 1
        
        reconciliation_summary = {
            'total_transactions': total_transactions,
            'reconciled_count': reconciled_count,
            'reconciliation_rate': reconciled_count / total_transactions if total_transactions > 0 else 0
        }
        
        # FIXED: Include organization context for navigation
        user_orgs = db.session.query(OrganizationUser).filter_by(user_id=current_user.id).all()
        organizations = Organization.query.filter(Organization.id.in_([uo.organization_id for uo in user_orgs])).all()
        
        return render_template('enhanced_bank/view_statement.html',
                             statement=statement,
                             transactions=transactions,
                             reconciliation_summary=reconciliation_summary,
                             organizations=organizations,
                             selected_org_id=statement.organization_id)
        
    except Exception as e:
        import traceback
        logger.error(f"Error viewing statement {statement_id}: {str(e)}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        flash('Error loading statement details', 'error')
        return redirect(url_for('enhanced_bank.list_statements'))

@enhanced_bank.route('/statement/<int:statement_id>/reconcile')
@login_required
def reconcile_statement(statement_id):
    """Show reconciliation interface for manual review."""
    try:
        # Get statement with proper access control
        statement = db.session.query(BankStatement).join(
            OrganizationUser,
            OrganizationUser.organization_id == BankStatement.organization_id
        ).filter(and_(
            BankStatement.id == statement_id,
            OrganizationUser.user_id == current_user.id
        )).first()
        
        if not statement:
            flash('Statement not found or access denied', 'error')
            return redirect(url_for('enhanced_bank.list_statements'))
        
        if statement.processing_status != 'completed':
            flash('Statement must be fully processed before reconciliation', 'warning')
            return redirect(url_for('enhanced_bank.view_statement', statement_id=statement_id))
        
        # FIXED: Get all transactions for this statement using correct source pattern
        transactions = db.session.query(FinancialTransaction)\
            .outerjoin(TransactionMetaBank, TransactionMetaBank.transaction_id == FinancialTransaction.id)\
            .filter(and_(
                FinancialTransaction.source_type == 'bank_statement',
                FinancialTransaction.source_id == statement_id,
                FinancialTransaction.organization_id == statement.organization_id
            ))\
            .order_by(FinancialTransaction.transaction_date.desc())\
            .all()
        
        # Get potential receipts within date range for matching
        potential_receipts = []
        if transactions:
            date_range_start = min(t.transaction_date for t in transactions) - timedelta(days=7)
            date_range_end = max(t.transaction_date for t in transactions) + timedelta(days=7)
            
            potential_receipts = Receipt.query.filter(and_(
                Receipt.organization_id == statement.organization_id,
                Receipt.date >= date_range_start,
                Receipt.date <= date_range_end
            )).order_by(Receipt.date.desc()).all()
        
        # FIXED: Include organization context for navigation
        user_orgs = db.session.query(OrganizationUser).filter_by(user_id=current_user.id).all()
        organizations = Organization.query.filter(Organization.id.in_([uo.organization_id for uo in user_orgs])).all()
        
        return render_template('enhanced_bank/reconcile_statement.html',
                             statement=statement,
                             transactions=transactions,
                             potential_receipts=potential_receipts,
                             organizations=organizations,
                             selected_org_id=statement.organization_id)
        
    except Exception as e:
        logger.error(f"Error loading reconciliation interface: {str(e)}")
        flash('Error loading reconciliation interface', 'error')
        return redirect(url_for('enhanced_bank.view_statement', statement_id=statement_id))

@enhanced_bank.route('/api/reconcile/match', methods=['POST'])
@login_required
def save_reconciliation_match():
    """Save a manual reconciliation match between transaction and receipt."""
    try:
        data = request.get_json()
        transaction_id = data.get('transaction_id')
        receipt_id = data.get('receipt_id')
        
        if not transaction_id or not receipt_id:
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
        
        # FIXED: Get transaction with organization check using correct source pattern
        transaction = db.session.query(FinancialTransaction).join(
            BankStatement,
            and_(
                BankStatement.id == FinancialTransaction.source_id,
                FinancialTransaction.source_type == 'bank_statement'
            )
        ).join(
            OrganizationUser,
            OrganizationUser.organization_id == BankStatement.organization_id
        ).filter(and_(
            FinancialTransaction.id == transaction_id,
            OrganizationUser.user_id == current_user.id
        )).first()
        
        if not transaction:
            return jsonify({'status': 'error', 'message': 'Transaction not found'}), 404
        
        # FIXED: Get receipt with organization check using transaction's organization
        receipt = Receipt.query.filter_by(
            id=receipt_id,
            organization_id=transaction.organization_id
        ).first()
        
        if not receipt:
            return jsonify({'status': 'error', 'message': 'Receipt not found'}), 404
        
        # FIXED: Update transaction reconciliation status
        transaction.reconciliation_status = 'manual_matched'
        
        # FIXED: Create reconciliation audit record
        audit = ReconciliationAudit(
            organization_id=transaction.organization_id,
            bank_statement_id=transaction.source_id,  # Using source_id for bank statement
            reconciliation_type='manual_match',
            confidence_score=100.0,
            created_by=current_user.id,
            notes=f"Manual match: Transaction {transaction_id} with Receipt {receipt_id}"
        )
        
        db.session.add(audit)
        db.session.commit()
        
        logger.info(f"Reconciliation match saved: Transaction {transaction_id} with Receipt {receipt_id}")
        
        return jsonify({
            'status': 'success',
            'message': 'Transaction reconciled successfully',
            'audit_id': audit.id
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error saving reconciliation: {str(e)}")
        return jsonify({
            'status': 'error', 
            'message': 'Failed to save reconciliation'
        }), 500

@enhanced_bank.route('/api/reconcile', methods=['POST'])
@login_required
def api_reconcile():
    """API endpoint for manual reconciliation decisions."""
    try:
        data = request.get_json()
        
        bank_transaction_id = data.get('bank_transaction_id')
        receipt_id = data.get('receipt_id')
        action = data.get('action')  # 'match', 'no_match', 'create_new'
        confidence = data.get('confidence', 0)
        
        if not bank_transaction_id or not action:
            return jsonify({'error': 'Missing required parameters'}), 400
        
        bank_txn = FinancialTransaction.query.get_or_404(bank_transaction_id)
        
        # Check user access
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=bank_txn.organization_id
        ).first()
        
        if not user_org:
            return jsonify({'error': 'Access denied'}), 403
        
        if action == 'match' and receipt_id:
            receipt = FinancialTransaction.query.get_or_404(receipt_id)
            
            # Create manual match
            bank_txn.reconciliation_status = 'manual_matched'
            bank_txn.matched_transaction_id = receipt.id
            bank_txn.reconciliation_confidence = Decimal(str(confidence))
            
            receipt.reconciliation_status = 'manual_matched'
            receipt.matched_transaction_id = bank_txn.id
            receipt.reconciliation_confidence = Decimal(str(confidence))
            
            # Create training data for learning
            from enhanced_reconciliation_models import ReconciliationTrainingData
            
            training_data = ReconciliationTrainingData(
                organization_id=bank_txn.organization_id,
                bank_description=bank_txn.description,
                bank_amount=bank_txn.amount,
                receipt_vendor=receipt.description,
                receipt_amount=receipt.amount,
                date_difference=abs((bank_txn.transaction_date - receipt.transaction_date).days),
                user_matched=True,
                match_confidence=Decimal(str(confidence)),
                user_id=current_user.id,
                amount_difference=abs(bank_txn.amount - receipt.amount)
            )
            
            db.session.add(training_data)
            
            # Update matching rules based on this decision
            rules = SmartMatchingRules.query.filter_by(
                organization_id=bank_txn.organization_id
            ).first()
            
            if rules:
                rules.update_from_decision(True, confidence)
            
            message = f"Successfully matched bank transaction with receipt"
            
        elif action == 'no_match':
            # User decided there's no match
            if receipt_id:
                receipt = FinancialTransaction.query.get_or_404(receipt_id)
                
                # Create negative training data
                training_data = ReconciliationTrainingData(
                    organization_id=bank_txn.organization_id,
                    bank_description=bank_txn.description,
                    bank_amount=bank_txn.amount,
                    receipt_vendor=receipt.description,
                    receipt_amount=receipt.amount,
                    date_difference=abs((bank_txn.transaction_date - receipt.transaction_date).days),
                    user_matched=False,
                    match_confidence=Decimal(str(confidence)),
                    user_id=current_user.id,
                    amount_difference=abs(bank_txn.amount - receipt.amount)
                )
                
                db.session.add(training_data)
            
            message = "Marked as no match"
            
        elif action == 'create_new':
            # Create new receipt from bank transaction
            new_receipt = UniversalGLService.create_transaction_from_bank_data(
                {
                    'transaction_date': bank_txn.transaction_date,
                    'description': bank_txn.description,
                    'debit_amount': bank_txn.amount if bank_txn.amount > 0 else 0,
                    'credit_amount': abs(bank_txn.amount) if bank_txn.amount < 0 else 0,
                    'reference_number': data.get('reference_number', ''),
                    'line_number': data.get('line_number'),
                    'extraction_confidence': 100,  # Manual creation
                    'dual_pass_validated': True
                },
                bank_txn.bank_meta.bank_statement_id,
                current_user.id,
                bank_txn.organization_id
            )
            
            # Link the transactions
            bank_txn.reconciliation_status = 'manual_matched'
            bank_txn.matched_transaction_id = new_receipt.id
            
            message = "Created new transaction from bank data"
        
        else:
            return jsonify({'error': 'Invalid action'}), 400
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': message,
            'bank_transaction_id': bank_transaction_id,
            'action': action
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in manual reconciliation: {str(e)}")
        return jsonify({'error': 'Reconciliation failed. Please try again.'}), 500

@enhanced_bank.route('/api/auto-reconcile/<int:statement_id>', methods=['POST'])
@login_required
def api_auto_reconcile(statement_id):
    """API endpoint to trigger automatic reconciliation."""
    try:
        statement = BankStatement.query.get_or_404(statement_id)
        
        # Check user access
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=statement.organization_id
        ).first()
        
        if not user_org:
            return jsonify({'error': 'Access denied'}), 403
        
        # Run auto-reconciliation
        results = EnhancedMatchingEngine.reconcile_bank_statement(
            statement_id, current_user.id
        )
        
        return jsonify({
            'success': True,
            'results': {
                'auto_matched': len(results['auto_matched']),
                'manual_review': len(results['manual_review']),
                'unmatched': len(results['unmatched']),
                'split_transactions': len(results['split_transactions'])
            },
            'message': f"Auto-reconciliation complete: {len(results['auto_matched'])} matches found"
        })
        
    except Exception as e:
        logger.error(f"Error in auto-reconciliation for statement {statement_id}: {str(e)}")
        return jsonify({'error': 'Auto-reconciliation failed. Please try again.'}), 500

@enhanced_bank.route('/api/progress/<int:statement_id>')
@login_required
def api_progress(statement_id):
    """API endpoint to get processing progress."""
    try:
        statement = BankStatement.query.get_or_404(statement_id)
        
        # Check user access
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=statement.organization_id
        ).first()
        
        if not user_org:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get latest processing log
        latest_log = BankStatementProcessingLog.query.filter_by(
            bank_statement_id=statement_id
        ).order_by(BankStatementProcessingLog.created_at.desc()).first()
        
        progress_data = {
            'statement_id': statement_id,
            'processing_status': statement.processing_status,
            'current_stage': latest_log.processing_stage if latest_log else 'unknown',
            'stage_status': latest_log.stage_status if latest_log else 'unknown',
            'progress_percentage': latest_log.progress_percentage if latest_log else 0,
            'error_message': latest_log.error_message if latest_log and latest_log.stage_status == 'error' else None,
            'total_transactions': statement.total_transactions,
            'auto_matched': statement.auto_matched_count,
            'manual_review': statement.exception_count
        }
        
        return jsonify(progress_data)
        
    except Exception as e:
        logger.error(f"Error getting progress for statement {statement_id}: {str(e)}")
        return jsonify({'error': 'Failed to get progress'}), 500

@enhanced_bank.route('/dashboard')
@login_required
def dashboard():
    """Enhanced bank reconciliation dashboard."""
    try:
        # Get user's organizations
        user_orgs = db.session.query(OrganizationUser).filter_by(user_id=current_user.id).all()
        org_ids = [uo.organization_id for uo in user_orgs]
        
        # Get selected organization
        selected_org_id = request.args.get('org_id', type=int)
        if not selected_org_id and org_ids:
            selected_org_id = org_ids[0]
        
        if not selected_org_id:
            flash('No organization selected', 'error')
            return redirect(url_for('enhanced_bank.list_statements'))
        
        # Get dashboard statistics
        total_statements = BankStatement.query.filter_by(
            organization_id=selected_org_id
        ).count()
        
        pending_reconciliation = BankStatement.query.filter_by(
            organization_id=selected_org_id,
            processing_status='needs_review'
        ).count()
        
        # Get recent statements
        recent_statements = BankStatement.query.filter_by(
            organization_id=selected_org_id
        ).order_by(BankStatement.created_at.desc()).limit(5).all()
        
        # Get unmatched transactions count
        unmatched_count = FinancialTransaction.query.filter_by(
            organization_id=selected_org_id,
            reconciliation_status='unmatched'
        ).count()
        
        # Get recent reconciliation audits
        recent_audits = ReconciliationAudit.query.filter_by(
            organization_id=selected_org_id
        ).order_by(ReconciliationAudit.created_at.desc()).limit(5).all()
        
        # Get organizations for dropdown
        organizations = Organization.query.filter(Organization.id.in_(org_ids)).all()
        
        # Get balance sheet summary
        try:
            balance_sheet = BalanceSheetService.generate_balance_sheet(selected_org_id)
        except Exception as e:
            logger.warning(f"Could not generate balance sheet: {str(e)}")
            balance_sheet = None
        
        dashboard_data = {
            'total_statements': total_statements,
            'pending_reconciliation': pending_reconciliation,
            'unmatched_transactions': unmatched_count,
            'recent_statements': recent_statements,
            'recent_audits': recent_audits,
            'balance_sheet': balance_sheet,
            'organizations': organizations,
            'selected_org_id': selected_org_id
        }
        
        return render_template('enhanced_bank/dashboard.html', **dashboard_data)
        
    except Exception as e:
        logger.error(f"Error loading dashboard: {str(e)}")
        flash('Error loading dashboard', 'error')
        return redirect(url_for('index'))

@enhanced_bank.route('/reconcile')
@login_required
def reconciliation_center():
    """Central reconciliation hub showing statements needing attention."""
    try:
        # Get user's organizations
        user_orgs = db.session.query(OrganizationUser.organization_id)\
            .filter_by(user_id=current_user.id).subquery()
        
        # Get all completed statements for user's organizations
        completed_statements = db.session.query(BankStatement)\
            .filter(BankStatement.organization_id.in_(user_orgs))\
            .filter(BankStatement.processing_status == 'completed')\
            .order_by(BankStatement.created_at.desc())\
            .all()
        
        # Calculate reconciliation status for each statement
        reconciliation_summary = []
        for statement in completed_statements:
            # Count total transactions
            total_transactions = db.session.query(FinancialTransaction)\
                .filter(FinancialTransaction.bank_statement_id == statement.id)\
                .count()
            
            # Count reconciled transactions (those with audit records)
            reconciled_transactions = db.session.query(FinancialTransaction)\
                .join(ReconciliationAudit, 
                      ReconciliationAudit.bank_statement_id == statement.id)\
                .filter(FinancialTransaction.bank_statement_id == statement.id)\
                .distinct()\
                .count()
            
            unmatched_count = total_transactions - reconciled_transactions
            reconciliation_percentage = (reconciled_transactions / total_transactions * 100) if total_transactions > 0 else 100
            
            # Only include statements that need reconciliation or recently completed
            if unmatched_count > 0 or statement.created_at > datetime.utcnow() - timedelta(days=7):
                reconciliation_summary.append({
                    'statement': statement,
                    'total_transactions': total_transactions,
                    'reconciled_transactions': reconciled_transactions,
                    'unmatched_count': unmatched_count,
                    'reconciliation_percentage': reconciliation_percentage,
                    'needs_attention': unmatched_count > 0
                })
        
        # Sort by priority (unmatched first, then by date)
        reconciliation_summary.sort(key=lambda x: (not x['needs_attention'], -x['unmatched_count']))
        
        return render_template('enhanced_bank/reconciliation_center.html',
                             reconciliation_summary=reconciliation_summary)
                             
    except Exception as e:
        logger.error(f"Error loading reconciliation center: {str(e)}")
        flash('Error loading reconciliation center', 'error')
        return redirect(url_for('enhanced_bank.dashboard'))

@enhanced_bank.route('/api/bank-accounts/<int:org_id>')
@login_required
def get_bank_accounts(org_id):
    """Get bank accounts for organization dropdown."""
    try:
        # Verify user access to organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=org_id
        ).first()
        
        if not user_org:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get bank accounts for this organization
        bank_accounts = BankAccount.query.filter_by(
            organization_id=org_id
        ).order_by(BankAccount.bank_name, BankAccount.account_number).all()
        
        return jsonify({
            'success': True,
            'accounts': [
                {
                    'id': account.id,
                    'bank_name': account.bank_name,
                    'account_number': account.account_number,
                    'account_type': getattr(account, 'account_type', 'Unknown'),
                    'currency': getattr(account, 'currency', 'LKR'),
                    'display_name': f"{account.bank_name} - ****{account.account_number[-4:] if len(account.account_number) >= 4 else account.account_number}"
                }
                for account in bank_accounts
            ]
        })
        
    except Exception as e:
        logger.error(f"Error fetching bank accounts for org {org_id}: {str(e)}")
        return jsonify({'error': 'Failed to load bank accounts', 'accounts': []}), 200

# Register error handlers
@enhanced_bank.errorhandler(404)
def not_found_error(error):
    return f"<h1>Page Not Found</h1><p>The requested bank statement page could not be found.</p>", 404

@enhanced_bank.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return f"<h1>Server Error</h1><p>An error occurred while processing your bank statement request.</p>", 500