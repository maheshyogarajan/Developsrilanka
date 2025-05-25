"""
Enhanced Bank Statement Routes for Universal Transaction System
This module provides the web interface for bank statement upload and reconciliation.
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app import db
from models import Organization, User, OrganizationUser
from enhanced_financial_models import (
    BankStatement, BankStatementPage, BankStatementProcessingLog,
    FinancialTransaction, TransactionMetaBank
)
from enhanced_reconciliation_models import (
    SmartMatchingRules, ReconciliationAudit, ReconciliationException
)
from enhanced_financial_services import (
    UniversalGLService, EnhancedMatchingEngine, BalanceSheetService
)
import hashlib
import os
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
import json

logger = logging.getLogger(__name__)

# Create blueprint
enhanced_bank = Blueprint('enhanced_bank', __name__, url_prefix='/enhanced-bank')

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
    """Handle bank statement upload with progress tracking."""
    try:
        # Validate form data
        organization_id = request.form.get('organization_id', type=int)
        if not organization_id:
            return jsonify({'error': 'Organization is required'}), 400
        
        # Check user access to organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=organization_id
        ).first()
        
        if not user_org:
            return jsonify({'error': 'Access denied to organization'}), 403
        
        # Validate file upload
        if 'statement_file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['statement_file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Only PDF files are allowed'}), 400
        
        # Read file content for hashing
        file_content = file.read()
        file_size = len(file_content)
        file.seek(0)  # Reset file pointer
        
        # Extract metadata from form
        account_number = request.form.get('account_number', '').strip()
        bank_name = request.form.get('bank_name', '').strip()
        period_from = request.form.get('period_from')
        period_to = request.form.get('period_to')
        
        if not all([account_number, period_from, period_to]):
            return jsonify({'error': 'Account number and statement period are required'}), 400
        
        # Parse dates
        try:
            period_from_date = datetime.strptime(period_from, '%Y-%m-%d').date()
            period_to_date = datetime.strptime(period_to, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
        
        # Generate content hash for duplicate detection
        content_hash = BankStatement.generate_content_hash(
            file_content, file_size, account_number, period_from_date, period_to_date
        )
        
        # Check for duplicates
        existing_statement = BankStatement.query.filter_by(
            content_sha256=content_hash
        ).first()
        
        if existing_statement:
            return jsonify({
                'error': 'This bank statement has already been uploaded',
                'existing_statement_id': existing_statement.id
            }), 409
        
        # Check for overlapping periods
        overlapping = BankStatement.query.filter(
            BankStatement.organization_id == organization_id,
            BankStatement.account_number == account_number,
            BankStatement.statement_period_from <= period_to_date,
            BankStatement.statement_period_to >= period_from_date
        ).first()
        
        if overlapping:
            return jsonify({
                'warning': f'Statement period overlaps with existing statement from {overlapping.statement_period_from} to {overlapping.statement_period_to}',
                'existing_statement_id': overlapping.id,
                'can_proceed': True
            }), 200
        
        # Create bank statement record
        statement = BankStatement(
            organization_id=organization_id,
            content_sha256=content_hash,
            file_size_bytes=file_size,
            page_count=0,  # Will be updated after PDF processing
            bank_name=bank_name,
            account_number=account_number,
            statement_period_from=period_from_date,
            statement_period_to=period_to_date,
            statement_currency=request.form.get('currency', 'USD'),
            processing_status='uploaded',
            uploaded_by=current_user.id
        )
        
        db.session.add(statement)
        db.session.flush()
        
        # Store file in S3
        filename = secure_filename(file.filename) or f"statement_{statement.id}.pdf"
        s3_key = f"bank-statements/org_{organization_id}/{period_from_date.year}/{period_from_date.month:02d}/{filename}"
        
        # TODO: Upload to S3 here
        statement.original_pdf_s3_key = s3_key
        
        # Create initial processing log
        processing_log = BankStatementProcessingLog(
            bank_statement_id=statement.id,
            processing_stage='upload',
            stage_status='completed',
            progress_percentage=10
        )
        
        db.session.add(processing_log)
        db.session.commit()
        
        logger.info(f"Bank statement uploaded successfully: {statement.id}")
        
        return jsonify({
            'success': True,
            'statement_id': statement.id,
            'message': 'Bank statement uploaded successfully',
            'next_step': 'processing'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error uploading bank statement: {str(e)}")
        return jsonify({'error': 'Upload failed. Please try again.'}), 500

@enhanced_bank.route('/statement/<int:statement_id>')
@login_required
def view_statement(statement_id):
    """View bank statement details and reconciliation status."""
    try:
        statement = BankStatement.query.get_or_404(statement_id)
        
        # Check user access
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=statement.organization_id
        ).first()
        
        if not user_org:
            flash('Access denied', 'error')
            return redirect(url_for('enhanced_bank.list_statements'))
        
        # Get processing logs
        processing_logs = BankStatementProcessingLog.query.filter_by(
            bank_statement_id=statement_id
        ).order_by(BankStatementProcessingLog.created_at.desc()).all()
        
        # Get statement pages
        pages = BankStatementPage.query.filter_by(
            bank_statement_id=statement_id
        ).order_by(BankStatementPage.page_number).all()
        
        # Get transactions
        transactions = db.session.query(FinancialTransaction).join(
            TransactionMetaBank
        ).filter(
            TransactionMetaBank.bank_statement_id == statement_id
        ).order_by(FinancialTransaction.transaction_date.desc()).all()
        
        # Get reconciliation audit
        reconciliation_audit = ReconciliationAudit.query.filter_by(
            bank_statement_id=statement_id
        ).order_by(ReconciliationAudit.created_at.desc()).first()
        
        # Get exceptions
        exceptions = ReconciliationException.query.filter_by(
            bank_statement_id=statement_id
        ).order_by(ReconciliationException.created_at.desc()).all()
        
        return render_template('enhanced_bank/statement_detail.html',
                             statement=statement,
                             processing_logs=processing_logs,
                             pages=pages,
                             transactions=transactions,
                             reconciliation_audit=reconciliation_audit,
                             exceptions=exceptions)
                             
    except Exception as e:
        logger.error(f"Error viewing statement {statement_id}: {str(e)}")
        flash('Error loading statement details', 'error')
        return redirect(url_for('enhanced_bank.list_statements'))

@enhanced_bank.route('/statement/<int:statement_id>/reconcile')
@login_required
def reconcile_statement(statement_id):
    """Show reconciliation interface for manual review."""
    try:
        statement = BankStatement.query.get_or_404(statement_id)
        
        # Check user access
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=statement.organization_id
        ).first()
        
        if not user_org:
            flash('Access denied', 'error')
            return redirect(url_for('enhanced_bank.list_statements'))
        
        # Get unmatched transactions
        unmatched_bank_txns = db.session.query(FinancialTransaction).join(
            TransactionMetaBank
        ).filter(
            TransactionMetaBank.bank_statement_id == statement_id,
            FinancialTransaction.reconciliation_status == 'unmatched'
        ).all()
        
        # Get potential receipt matches
        date_range_start = statement.statement_period_from - timedelta(days=5)
        date_range_end = statement.statement_period_to + timedelta(days=5)
        
        potential_receipts = FinancialTransaction.query.filter(
            FinancialTransaction.organization_id == statement.organization_id,
            FinancialTransaction.source_type == 'receipt_scan',
            FinancialTransaction.reconciliation_status == 'unmatched',
            FinancialTransaction.transaction_date >= date_range_start,
            FinancialTransaction.transaction_date <= date_range_end
        ).all()
        
        # Get matching rules for confidence calculations
        rules = SmartMatchingRules.query.filter_by(
            organization_id=statement.organization_id
        ).first()
        
        if not rules:
            rules = SmartMatchingRules(organization_id=statement.organization_id)
            db.session.add(rules)
            db.session.commit()
        
        # Calculate potential matches for each bank transaction
        reconciliation_items = []
        for bank_txn in unmatched_bank_txns:
            potential_matches = []
            
            for receipt in potential_receipts:
                confidence = EnhancedMatchingEngine._calculate_match_confidence(
                    bank_txn, receipt, rules
                )
                
                if confidence >= 30:  # Show matches with at least 30% confidence
                    potential_matches.append({
                        'receipt': receipt,
                        'confidence': round(confidence, 1),
                        'amount_diff': float(abs(bank_txn.amount - receipt.amount)),
                        'date_diff': abs((bank_txn.transaction_date - receipt.transaction_date).days)
                    })
            
            # Sort by confidence
            potential_matches.sort(key=lambda x: x['confidence'], reverse=True)
            
            reconciliation_items.append({
                'bank_transaction': bank_txn,
                'potential_matches': potential_matches[:5]  # Top 5 matches
            })
        
        return render_template('enhanced_bank/reconcile.html',
                             statement=statement,
                             reconciliation_items=reconciliation_items,
                             rules=rules)
                             
    except Exception as e:
        logger.error(f"Error showing reconciliation for statement {statement_id}: {str(e)}")
        flash('Error loading reconciliation interface', 'error')
        return redirect(url_for('enhanced_bank.view_statement', statement_id=statement_id))

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
        return redirect(url_for('main.index'))

# Register error handlers
@enhanced_bank.errorhandler(404)
def not_found_error(error):
    return f"<h1>Page Not Found</h1><p>The requested bank statement page could not be found.</p>", 404

@enhanced_bank.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return f"<h1>Server Error</h1><p>An error occurred while processing your bank statement request.</p>", 500