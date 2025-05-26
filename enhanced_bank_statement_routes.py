"""
Enhanced Bank Statement Routes for Universal Transaction System
This module provides the web interface for bank statement upload and reconciliation.
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import and_
from datetime import datetime
from io import BytesIO
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
from bank_statement_processor import BankStatementProcessor, EnhancedBankStatementProcessor
from simple_audit_trail import SimpleAuditLogger, SimpleRulesEngine, store_audit_trail_in_statement, load_audit_trail_from_statement
from enhanced_rules_engine import OrganizationRulesEngine
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
        
        # Auto-extract statement period dates from PDF content
        try:
            # First try to extract dates from the PDF content itself
            processor = BankStatementProcessor()
            result = processor.process_pdf(pdf_data, bank_hint=request.form.get('bank_name', ''))
            
            # Get dates from PDF processing
            info = result.get('statement_info', {})
            period_from = info.get('start_date')
            period_to = info.get('end_date')
            
            # If PDF extraction didn't provide dates, try manual form input as fallback
            if not period_from or not period_to:
                statement_start = request.form.get('statement_start')
                statement_end = request.form.get('statement_end')
                if statement_start and statement_end:
                    period_from = datetime.strptime(statement_start, '%Y-%m-%d').date()
                    period_to = datetime.strptime(statement_end, '%Y-%m-%d').date()
                else:
                    # Extract from filename if possible (e.g., Current-1000120800-20250508.pdf)
                    filename = statement_file.filename.lower()
                    import re
                    date_match = re.search(r'(\d{8})', filename)
                    if date_match:
                        date_str = date_match.group(1)
                        try:
                            period_to = datetime.strptime(date_str, '%Y%m%d').date()
                            period_from = period_to.replace(day=1)  # Start of month
                        except ValueError:
                            period_to = date.today()
                            period_from = period_to.replace(day=1)
                    else:
                        # Use current month as last resort
                        today = date.today()
                        period_from = today.replace(day=1)
                        period_to = today
                        
            logger.info(f"Auto-extracted statement period: {period_from} to {period_to}")
            
        except Exception as date_error:
            logger.warning(f"Date extraction failed: {date_error}, using defaults")
            # Fallback to current month if all extraction methods fail
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
        
        # Create statement record with detailed logging
        logger.info(f"Creating bank statement record - Org: {organization_id}, Size: {len(pdf_data)} bytes")
        try:
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
            logger.info(f"Statement record created successfully with ID: {statement.id}")
        except Exception as e:
            logger.error(f"Failed to create statement record: {e}")
            db.session.rollback()
            flash(f'Failed to create statement record: {e}', 'error')
            return redirect(url_for('enhanced_bank.upload_form'))
        
        # Store in S3 with detailed logging
        logger.info(f"Uploading PDF to S3 for statement ID: {statement.id}")
        try:
            s3_client = boto3.client('s3')
            s3_key = f"bank-statements/{statement.id}/statement.pdf"
            s3_client.upload_fileobj(
                BytesIO(pdf_data),
                os.environ.get('AWS_S3_BUCKET_NAME'),
                s3_key
            )
            statement.original_pdf_s3_key = s3_key
            logger.info(f"S3 upload successful: {s3_key}")
        except Exception as s3_error:
            logger.error(f"S3 upload failed for statement {statement.id}: {s3_error}")
            # Continue processing without S3 - store locally if needed
        
        # Process PDF with enhanced validation and complete audit trail
        logger.info(f"Starting enhanced PDF processing with audit trail for statement ID: {statement.id}")
        try:
            # Initialize audit logging system
            audit_logger = SimpleAuditLogger()
            rules_engine = SimpleRulesEngine()
            
            # Start audit trail
            audit_trail = audit_logger.start_processing(statement.id, "enhanced_validation_with_audit")
            
            # First extract full text for account number detection
            from PyPDF2 import PdfReader
            from io import BytesIO
            
            pdf_reader = PdfReader(BytesIO(pdf_data))
            full_text = ""
            for page in pdf_reader.pages:
                full_text += page.extract_text() + "\n"
            
            # Log extraction method
            audit_logger.log_event(
                data=f"pdf_size:{len(pdf_data)}",
                stage='extraction',
                rule_id='pdf_text_extraction',
                decision='accept',
                message=f'Extracted text from {len(pdf_reader.pages)} pages using PyPDF2'
            )
            
            # Initialize enhanced processor and extract bank metadata
            enhanced_processor = EnhancedBankStatementProcessor()
            
            # Extract bank metadata from PDF text
            bank_metadata = enhanced_processor.extract_bank_metadata(full_text)
            logger.info(f"Bank metadata extracted: {bank_metadata}")
            
            # Update statement with extracted metadata
            if bank_metadata['bank_name'] != 'Unknown Bank':
                statement.bank_name = bank_metadata['bank_name']
                audit_logger.log_event(
                    data=f"bank_name:{bank_metadata['bank_name']}",
                    stage='extraction',
                    rule_id='bank_name_extraction',
                    decision='accept',
                    message=f'Extracted bank name: {bank_metadata["bank_name"]}'
                )
            
            if bank_metadata['account_number']:
                statement.account_number = bank_metadata['account_number']
                audit_logger.log_event(
                    data=f"account_number:{bank_metadata['account_number']}",
                    stage='extraction',
                    rule_id='account_number_extraction',
                    decision='accept',
                    message=f'Extracted account number: {bank_metadata["account_number"]}'
                )
            
            # Update statement period if extracted
            if bank_metadata['statement_period_from'] and bank_metadata['statement_period_to']:
                try:
                    period_from_extracted = datetime.strptime(bank_metadata['statement_period_from'], '%d/%m/%Y').date()
                    period_to_extracted = datetime.strptime(bank_metadata['statement_period_to'], '%d/%m/%Y').date()
                    statement.statement_period_from = period_from_extracted
                    statement.statement_period_to = period_to_extracted
                    audit_logger.log_event(
                        data=f"period:{bank_metadata['statement_period_from']} to {bank_metadata['statement_period_to']}",
                        stage='extraction',
                        rule_id='period_extraction',
                        decision='accept',
                        message=f'Extracted statement period: {bank_metadata["statement_period_from"]} to {bank_metadata["statement_period_to"]}'
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse extracted period dates: {e}")
                    pass  # Keep original dates if parsing fails
            
            # Detect account numbers for validation filtering
            enhanced_processor.extract_account_numbers_from_statement(full_text)
            
            # Commit the metadata updates to database immediately
            try:
                db.session.commit()
                logger.info(f"Statement metadata committed to database - Bank: {statement.bank_name}, Account: {statement.account_number}")
            except Exception as e:
                logger.error(f"Failed to commit statement metadata: {e}")
                db.session.rollback()
            
            # Log detected account numbers
            if enhanced_processor.detected_account_numbers:
                audit_logger.log_event(
                    data=f"account_numbers:{list(enhanced_processor.detected_account_numbers)}",
                    stage='extraction',
                    rule_id='account_number_detection',
                    decision='flag',
                    message=f'Detected {len(enhanced_processor.detected_account_numbers)} account numbers for validation filtering'
                )
            
            # Use legacy processor for basic extraction, then enhance validation
            legacy_processor = BankStatementProcessor()
            result = legacy_processor.process_pdf(pdf_data, statement.bank_name)
            logger.info(f"Legacy PDF processing - Valid: {result['validation']['is_valid']}, Raw transactions found: {len(result.get('transactions', []))}")
            
            # Log extraction results
            audit_logger.log_event(
                data=f"raw_transactions:{len(result.get('transactions', []))}",
                stage='extraction',
                rule_id='legacy_processor_extraction',
                decision='accept' if result['validation']['is_valid'] else 'reject',
                message=f'Legacy processor extracted {len(result.get("transactions", []))} potential transactions'
            )
            
            if result['validation']['is_valid']:
                # Apply enhanced validation with complete audit trail
                logger.info("Applying enhanced validation with audit trail to extracted transactions")
                
                # Process transactions through our new rules engine with audit logging
                validated_transactions = []
                
                for i, txn_data in enumerate(result['transactions']):
                    try:
                        raw_amount = Decimal(str(txn_data['amount']))
                        description = txn_data.get('description', '')
                        
                        # Log each transaction being processed
                        audit_logger.log_event(
                            data=f"txn_{i+1}:amount={raw_amount},desc={description[:50]}",
                            stage='validation',
                            rule_id='transaction_processing',
                            decision='flag',
                            message=f'Processing transaction {i+1}: {raw_amount} - {description[:50]}...'
                        )
                        
                        # Apply rules engine validation with audit logging
                        is_valid = rules_engine.validate_transaction(
                            float(raw_amount), description, audit_logger
                        )
                        
                        if is_valid:
                            # Determine transaction type based on amount sign
                            transaction_type = 'receipt' if raw_amount > 0 else 'payment'
                            
                            # Add enhanced metadata to transaction
                            enhanced_txn = txn_data.copy()
                            enhanced_txn['transaction_type'] = transaction_type
                            enhanced_txn['validation_passed'] = True
                            validated_transactions.append(enhanced_txn)
                            
                        # Note: Rejections are already logged by the rules engine
                            
                    except Exception as validation_error:
                        logger.error(f"Enhanced validation failed for transaction {i+1}: {validation_error}")
                        audit_logger.log_event(
                            data=f"txn_{i+1}:error",
                            stage='validation',
                            rule_id='validation_error',
                            decision='reject',
                            message=f"Validation error for transaction {i+1}: {validation_error}"
                        )
                
                # Complete the audit trail
                completed_audit_trail = audit_logger.finish_processing()
                
                # Log enhanced validation summary with audit trail data
                logger.info(f"Enhanced validation complete - Accepted: {audit_logger.get_acceptance_count()}, "
                           f"Rejected: {audit_logger.get_rejection_count()}")
                
                # Log detailed rejection reasons from audit trail
                rejection_reasons = audit_logger.get_rejection_summary()
                if rejection_reasons:
                    logger.warning(f"AUDIT TRAIL REJECTED {len(rejection_reasons)} TRANSACTIONS:")
                    for reason in rejection_reasons:
                        logger.warning(f"  → {reason}")
                else:
                    logger.info("Audit trail: All extracted transactions passed validation rules")
                
                # Log detected account numbers for verification
                if enhanced_processor.detected_account_numbers:
                    logger.info(f"Detected account numbers (excluded from amounts): {list(enhanced_processor.detected_account_numbers)}")
                
                # Log processing summary with audit trail data
                logger.info(f"PROCESSING SUMMARY: Extracted {len(result['transactions'])} raw transactions, "
                           f"Audit trail approved {len(validated_transactions)}, "
                           f"rejected {len(result['transactions']) - len(validated_transactions)}")
                
                # Store complete audit trail in statement for user review
                store_audit_trail_in_statement(statement, completed_audit_trail)
                
                # Update statement with extracted info
                info = result['statement_info']
                logger.info(f"Updating statement info: periods, balances, transaction count")
                statement.statement_period_from = info.get('statement_period_start') or period_from
                statement.statement_period_to = info.get('statement_period_end') or period_to
                statement.opening_balance = info.get('opening_balance')
                statement.closing_balance = info.get('closing_balance')
                statement.total_transactions = len(validated_transactions)
                statement.processing_status = 'completed'
                
                # Get currency with fallback
                currency = statement.statement_currency or 'LKR'
                logger.info(f"Processing {len(validated_transactions)} validated transactions with currency: {currency}")
                
                # Create transactions with enhanced data and audit trail
                transactions_created = 0
                transactions_failed = 0
                try:
                    for i, txn_data in enumerate(validated_transactions):
                        try:
                            logger.debug(f"Creating transaction {i+1}/{len(validated_transactions)}: {txn_data.get('amount', 'N/A')} {currency}")
                            raw_amount = Decimal(str(txn_data['amount']))
                            
                            logger.debug(f"Creating financial transaction record for transaction {i+1}")
                            transaction = FinancialTransaction(
                                organization_id=organization_id,
                                transaction_date=txn_data['transaction_date'],
                                amount=raw_amount,
                                description=txn_data['description'],
                                currency_code=currency,
                                fx_rate=Decimal('1.000000'),
                                source_type=SourceType.BANK_STATEMENT.value,
                                source_id=statement.id,  # Reference the bank statement directly
                                status=TransactionStatus.PENDING.value,
                                created_by=current_user.id
                            )
                            
                            # Auto-calculate derived fields
                            transaction.base_currency_amount = transaction.amount * transaction.fx_rate
                            transaction.net_amount = transaction.amount
                            
                            db.session.add(transaction)
                            db.session.flush()
                            logger.debug(f"Financial transaction created with ID: {transaction.id}")
                            
                            # Create bank metadata with detailed logging
                            logger.debug(f"Creating metadata record for transaction {transaction.id}")
                            bank_meta = TransactionMetaBank(
                                transaction_id=transaction.id,
                                bank_statement_id=statement.id,
                                bank_reference_number=txn_data.get('reference_number', ''),
                                statement_line_number=i + 1,  # Use reliable line numbering
                                running_balance=Decimal(str(txn_data.get('balance_after', 0))) if txn_data.get('balance_after') else None,
                                extraction_confidence=txn_data.get('extraction_confidence', 0.8)
                            )
                            db.session.add(bank_meta)
                            db.session.flush()
                            logger.debug(f"Metadata record created for transaction {transaction.id}")
                            
                            transactions_created += 1
                            
                        except Exception as txn_error:
                            logger.error(f"Failed to create transaction {i+1}: {txn_error}", exc_info=True)
                            transactions_failed += 1
                            # Continue processing other transactions rather than failing completely
                            continue
                    
                    # Update statement with actual count of created transactions
                    statement.total_transactions = transactions_created
                    db.session.commit()
                    
                    logger.info(f"Processing complete - Created: {transactions_created}, Failed: {transactions_failed}, Total extracted: {len(result['transactions'])}")
                    
                    if transactions_failed > 0:
                        logger.warning(f"{transactions_failed} transactions failed validation and were skipped")
                    
                except Exception as e:
                    logger.error(f"Critical transaction creation error: {e}", exc_info=True)
                    db.session.rollback()
                    statement.processing_status = 'failed'
                    statement.total_transactions = 0
                    db.session.commit()
                    flash('Failed to process bank statement transactions', 'error')
                    return redirect(url_for('enhanced_bank.list_statements'))
                
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

@enhanced_bank.route('/statement/<int:statement_id>/validation_results')
@login_required
def view_validation_results(statement_id):
    """Display detailed validation results with audit trail."""
    statement = BankStatement.query.get_or_404(statement_id)
    
    # Load audit trail from processing_notes
    audit_data = load_audit_trail_from_statement(statement)
    
    if not audit_data:
        flash('No validation data available for this statement.', 'warning')
        return redirect(url_for('enhanced_bank.view_statement', statement_id=statement_id))
    
    # Parse rejection reasons for user-friendly display
    rejection_reasons = {}
    for reason in audit_data.get('rejection_reasons', []):
        # Group similar reasons for better user experience
        if 'account number' in reason.lower() or 'account pattern' in reason.lower():
            key = 'Account numbers detected in amounts'
        elif 'summary' in reason.lower() or 'total' in reason.lower():
            key = 'Summary/total rows excluded'
        elif 'threshold' in reason.lower() or 'below minimum' in reason.lower():
            key = 'Amount below minimum threshold'
        elif 'zero amount' in reason.lower():
            key = 'Zero amount transactions'
        elif 'date pattern' in reason.lower():
            key = 'Date-like patterns in descriptions'
        else:
            key = reason
        
        rejection_reasons[key] = rejection_reasons.get(key, 0) + 1
    
    # Calculate processing duration if available
    processing_duration = None
    if audit_data.get('start_time') and audit_data.get('end_time'):
        try:
            from datetime import datetime
            start = datetime.fromisoformat(audit_data['start_time'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(audit_data['end_time'].replace('Z', '+00:00'))
            processing_duration = round((end - start).total_seconds(), 2)
        except:
            processing_duration = None
    
    return render_template('enhanced_bank/validation_results.html',
                         statement=statement,
                         audit_summary=audit_data.get('summary', {}),
                         rejection_reasons=rejection_reasons,
                         audit_events=audit_data.get('events', []),
                         processing_method=audit_data.get('processing_method'),
                         processing_duration=processing_duration)

@enhanced_bank.route('/reconcile/<int:statement_id>')
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

@enhanced_bank.route('/api/reconcile/approve', methods=['POST'])
@login_required
def approve_transaction():
    """Approve a bank transaction without requiring a match."""
    try:
        data = request.get_json()
        transaction_id = data.get('transaction_id')
        
        if not transaction_id:
            return jsonify({'status': 'error', 'message': 'Transaction ID required'}), 400
        
        # Get the transaction
        transaction = FinancialTransaction.query.get(transaction_id)
        if not transaction:
            return jsonify({'status': 'error', 'message': 'Transaction not found'}), 404
        
        # Verify user has access to this organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=transaction.organization_id
        ).first()
        
        if not user_org:
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
        # Update transaction status
        transaction.status = TransactionStatus.APPROVED.value
        transaction.reconciliation_status = ReconciliationStatus.MANUAL_MATCHED.value
        transaction.approved_by = current_user.id
        transaction.approved_at = datetime.utcnow()
        
        # Create audit record
        audit_record = ReconciliationAudit(
            bank_statement_id=transaction.bank_meta.bank_statement_id if transaction.bank_meta else None,
            transaction_id=transaction.id,
            action_type='approve',
            performed_by=current_user.id,
            confidence_score=Decimal('100.00'),  # Manual approval = 100% confidence
            notes=f'Manually approved by {current_user.username}'
        )
        
        db.session.add(audit_record)
        db.session.commit()
        
        logger.info(f"Transaction {transaction_id} approved by user {current_user.id}")
        
        return jsonify({
            'status': 'success',
            'message': 'Transaction approved successfully',
            'transaction_id': transaction_id
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving transaction: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Failed to approve transaction'}), 500


@enhanced_bank.route('/api/reconcile/reject', methods=['POST'])
@login_required
def reject_transaction():
    """Reject a bank transaction for later review."""
    try:
        data = request.get_json()
        transaction_id = data.get('transaction_id')
        
        if not transaction_id:
            return jsonify({'status': 'error', 'message': 'Transaction ID required'}), 400
        
        # Get the transaction
        transaction = FinancialTransaction.query.get(transaction_id)
        if not transaction:
            return jsonify({'status': 'error', 'message': 'Transaction not found'}), 404
        
        # Verify user has access to this organization
        user_org = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=transaction.organization_id
        ).first()
        
        if not user_org:
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
        # Update transaction status
        transaction.status = TransactionStatus.DISPUTED.value
        transaction.reconciliation_status = ReconciliationStatus.DISPUTED.value
        
        # Create audit record
        audit_record = ReconciliationAudit(
            bank_statement_id=transaction.bank_meta.bank_statement_id if transaction.bank_meta else None,
            transaction_id=transaction.id,
            action_type='reject',
            performed_by=current_user.id,
            confidence_score=Decimal('0.00'),  # Rejection = 0% confidence
            notes=f'Manually rejected by {current_user.username} for further review'
        )
        
        db.session.add(audit_record)
        db.session.commit()
        
        logger.info(f"Transaction {transaction_id} rejected by user {current_user.id}")
        
        return jsonify({
            'status': 'success',
            'message': 'Transaction rejected successfully',
            'transaction_id': transaction_id
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error rejecting transaction: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Failed to reject transaction'}), 500


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

# Phase 2B: Rule Customization Routes
@enhanced_bank.route('/validation_rules')
@login_required
def manage_validation_rules():
    """Display and manage organization-specific validation rules."""
    try:
        # Get current organization from session
        org_id = session.get('current_organization_id')
        if not org_id:
            flash('Please select an organization first', 'warning')
            return redirect(url_for('enhanced_bank.list_statements'))
        
        # Initialize rules engine for this organization
        rules_engine = OrganizationRulesEngine(org_id)
        
        # Get current rules configuration
        current_rules = rules_engine.get_rules_summary()
        
        # Get validation statistics
        stats = rules_engine.get_validation_statistics(org_id)
        
        return render_template('enhanced_bank/rule_customization.html',
                             current_rules=current_rules,
                             stats=stats)
                             
    except Exception as e:
        logger.error(f"Error loading validation rules: {str(e)}")
        flash('Error loading validation rules configuration', 'error')
        return redirect(url_for('enhanced_bank.list_statements'))

@enhanced_bank.route('/validation_rules', methods=['POST'])
@login_required
def update_validation_rules():
    """Update organization-specific validation rules."""
    try:
        # Get current organization from session
        org_id = session.get('current_organization_id')
        if not org_id:
            flash('Please select an organization first', 'warning')
            return redirect(url_for('enhanced_bank.list_statements'))
        
        # Initialize rules engine for this organization
        rules_engine = OrganizationRulesEngine(org_id)
        
        # Parse form data into rules structure
        new_rules = {
            'account_number_detection': {
                'enabled': 'enable_account_detection' in request.form,
                'patterns': [p.strip() for p in request.form.get('account_patterns', '').split('\n') if p.strip()]
            },
            'amount_thresholds': {
                'enabled': True,  # Always enabled for safety
                'min_amount': float(request.form.get('min_amount', 0.01)),
                'max_amount': float(request.form.get('max_amount', 1000000000))
            },
            'summary_row_detection': {
                'enabled': True,  # Always enabled for accuracy
                'patterns': [p.strip() for p in request.form.get('summary_patterns', '').split('\n') if p.strip()]
            },
            'date_pattern_exclusion': {
                'enabled': True,  # Always enabled for accuracy
                'patterns': [p.strip() for p in request.form.get('date_patterns', '').split('\n') if p.strip()]
            }
        }
        
        # Save the new rules
        if rules_engine.save_organization_rules(new_rules):
            flash('Validation rules updated successfully! Changes will apply to new statement processing.', 'success')
        else:
            flash('Failed to save validation rules. Please try again.', 'error')
            
        return redirect(url_for('enhanced_bank.manage_validation_rules'))
        
    except Exception as e:
        logger.error(f"Error updating validation rules: {str(e)}")
        flash('Error updating validation rules. Please check your input and try again.', 'error')
        return redirect(url_for('enhanced_bank.manage_validation_rules'))