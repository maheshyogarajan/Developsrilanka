"""
Enhanced Financial Services for Universal Transaction System
This module implements the business logic for smart reconciliation and GL integration.
"""

from app import db
from models import User, Organization, Receipt, AuditLog
from accounting_models import GeneralLedgerEntry, JournalEntryLine, Account
from enhanced_financial_models import (
    FinancialTransaction, TransactionMetaReceipt, TransactionMetaBank, 
    TransactionMetaManual, TransactionMetaJournal, BankStatement, 
    BankStatementPage, BankStatementProcessingLog, FundingSource, 
    FundingSourceTransaction, SourceType, TransactionStatus, 
    ReconciliationStatus
)
from enhanced_reconciliation_models import (
    ReconciliationException, ReconciliationTrainingData, SmartMatchingRules,
    SplitTransactionGroup, SplitTransactionMember, ReconciliationAudit,
    AccountBalanceCache
)
from account_mapping_service import AccountMappingService
from datetime import datetime, date, timedelta
from decimal import Decimal
import hashlib
import logging
import re
from flask_login import current_user
from sqlalchemy import func, and_, or_, case

logger = logging.getLogger(__name__)

class UniversalGLService:
    """Service for creating transactions with automatic GL integration."""
    
    @staticmethod
    def create_transaction_from_receipt(receipt_data, receipt_meta_data, user_id, organization_id):
        """Create a financial transaction from a receipt scan."""
        try:
            # Get organization base currency
            org = Organization.query.get(organization_id)
            base_currency = getattr(org, 'base_currency', 'USD')
            
            # Create core transaction
            transaction = FinancialTransaction(
                organization_id=organization_id,
                transaction_date=receipt_data.get('date', date.today()),
                amount=Decimal(str(receipt_data.get('total_amount', 0))),
                description=receipt_meta_data.get('vendor_name', 'Receipt'),
                currency_code=base_currency,
                fx_rate=Decimal('1.000000'),  # Assume same currency for now
                source_type=SourceType.RECEIPT_SCAN.value,
                source_id=0,  # Will be updated after meta creation
                service_charge=Decimal(str(receipt_meta_data.get('service_charge', 0))),
                status=TransactionStatus.PENDING.value,
                created_by=user_id
            )
            
            # Calculate base currency amount and net amount
            transaction.base_currency_amount = transaction.amount * transaction.fx_rate
            transaction.net_amount = transaction.amount - transaction.service_charge
            
            db.session.add(transaction)
            db.session.flush()
            
            # Create receipt metadata
            receipt_meta = TransactionMetaReceipt(
                transaction_id=transaction.id,
                vendor_name=receipt_meta_data.get('vendor_name', ''),
                vendor_address=receipt_meta_data.get('vendor_address', ''),
                vendor_contact=receipt_meta_data.get('vendor_contact', ''),
                vat_registration_number=receipt_meta_data.get('vat_registration_number', ''),
                service_charge=Decimal(str(receipt_meta_data.get('service_charge', 0))),
                sscl_tax=Decimal(str(receipt_meta_data.get('sscl_tax', 0))),
                vat_tax=Decimal(str(receipt_meta_data.get('vat_tax', 0))),
                s3_key=receipt_meta_data.get('s3_key', ''),
                thumbnail_s3_key=receipt_meta_data.get('thumbnail_s3_key', ''),
                extraction_confidence=receipt_meta_data.get('extraction_confidence'),
                extraction_model=receipt_meta_data.get('extraction_model', ''),
                original_extracted_text=receipt_meta_data.get('original_extracted_text', ''),
                expense_major_category=receipt_meta_data.get('expense_major_category', ''),
                expense_minor_category=receipt_meta_data.get('expense_minor_category', '')
            )
            
            db.session.add(receipt_meta)
            
            # Update source_id to point to receipt meta
            transaction.source_id = transaction.id
            
            # Determine GL accounts
            expense_account = AccountMappingService.get_account_for_category(
                receipt_meta_data.get('expense_major_category', 'Operating Expenses'),
                receipt_meta_data.get('expense_minor_category')
            )
            
            # Get or create funding source (defaults to directors loan)
            funding_source = FundingSourceService.get_or_create_directors_loan(
                organization_id, user_id
            )
            
            # Create GL entry
            gl_entry = UniversalGLService._create_gl_entry(
                transaction, expense_account, funding_source.account_code, user_id
            )
            
            # Link transaction to GL entry
            transaction.ledger_entry_id = gl_entry.id
            transaction.debit_account_code = expense_account
            transaction.credit_account_code = funding_source.account_code
            
            # Update funding source balance
            FundingSourceService.update_balance(
                funding_source, transaction.amount, transaction.id
            )
            
            # Create audit trail
            AuditLog(
                entity_type='financial_transaction',
                entity_id=transaction.id,
                action='CREATE',
                changed_fields={
                    'source_type': SourceType.RECEIPT_SCAN.value,
                    'amount': float(transaction.amount),
                    'vendor': receipt_meta_data.get('vendor_name', ''),
                    'funding_source': funding_source.source_name
                },
                user_id=user_id
            )
            
            db.session.commit()
            logger.info(f"Created receipt transaction {transaction.id} for organization {organization_id}")
            
            return transaction
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating receipt transaction: {str(e)}")
            raise

    @staticmethod
    def create_transaction_from_bank_data(bank_data, bank_statement_id, user_id, organization_id):
        """Create a financial transaction from bank statement data."""
        try:
            # Get bank statement for currency info
            bank_statement = BankStatement.query.get(bank_statement_id)
            currency = bank_statement.statement_currency if bank_statement else 'USD'
            
            # Determine transaction amount (debit or credit)
            amount = bank_data.get('debit_amount', 0) or bank_data.get('credit_amount', 0)
            
            # Create core transaction
            transaction = FinancialTransaction(
                organization_id=organization_id,
                transaction_date=bank_data.get('transaction_date', date.today()),
                amount=Decimal(str(amount)),
                description=bank_data.get('description', 'Bank Transaction'),
                currency_code=currency,
                fx_rate=Decimal('1.000000'),  # Will be updated with actual rates
                source_type=SourceType.BANK_STATEMENT.value,
                source_id=0,  # Will be updated after meta creation
                status=TransactionStatus.PENDING.value,
                created_by=user_id
            )
            
            transaction.base_currency_amount = transaction.amount * transaction.fx_rate
            transaction.net_amount = transaction.amount
            
            db.session.add(transaction)
            db.session.flush()
            
            # Create bank metadata
            bank_meta = TransactionMetaBank(
                transaction_id=transaction.id,
                bank_statement_id=bank_statement_id,
                bank_reference_number=bank_data.get('reference_number', ''),
                statement_line_number=bank_data.get('line_number'),
                running_balance=Decimal(str(bank_data.get('balance', 0))),
                extraction_confidence=bank_data.get('extraction_confidence'),
                dual_pass_validated=bank_data.get('dual_pass_validated', False),
                discrepancies_found=bank_data.get('discrepancies_found', False)
            )
            
            db.session.add(bank_meta)
            
            # Update source_id
            transaction.source_id = transaction.id
            
            # Determine GL accounts from description
            expense_account = AccountMappingService.get_account_code_from_description(
                bank_data.get('description', '')
            )
            
            # Bank transactions hit the bank account
            bank_account_code = BankAccountService.get_account_code_for_statement(bank_statement_id)
            
            # Create GL entry
            gl_entry = UniversalGLService._create_gl_entry(
                transaction, expense_account, bank_account_code, user_id
            )
            
            transaction.ledger_entry_id = gl_entry.id
            transaction.debit_account_code = expense_account
            transaction.credit_account_code = bank_account_code
            
            db.session.commit()
            logger.info(f"Created bank transaction {transaction.id} for statement {bank_statement_id}")
            
            return transaction
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating bank transaction: {str(e)}")
            raise

    @staticmethod
    def _create_gl_entry(transaction, debit_account_code, credit_account_code, user_id):
        """Create a general ledger entry for the transaction."""
        # Get next entry number
        entry_number = UniversalGLService._get_next_entry_number(transaction.organization_id)
        
        # Create journal entry
        gl_entry = GeneralLedgerEntry(
            organization_id=transaction.organization_id,
            entry_number=entry_number,
            transaction_date=transaction.transaction_date,
            reference_type=transaction.source_type,
            reference_id=transaction.id,
            description=transaction.description,
            total_amount=transaction.amount,
            created_by=user_id
        )
        
        db.session.add(gl_entry)
        db.session.flush()
        
        # Get account objects
        debit_account = Account.query.filter_by(
            organization_id=transaction.organization_id,
            account_code=debit_account_code
        ).first()
        
        credit_account = Account.query.filter_by(
            organization_id=transaction.organization_id,
            account_code=credit_account_code
        ).first()
        
        if not debit_account or not credit_account:
            raise ValueError(f"Could not find accounts: {debit_account_code}, {credit_account_code}")
        
        # Create debit line
        debit_line = JournalEntryLine(
            ledger_entry_id=gl_entry.id,
            account_id=debit_account.id,
            debit_amount=transaction.amount,
            credit_amount=Decimal('0'),
            description=transaction.description,
            line_number=1
        )
        
        # Create credit line
        credit_line = JournalEntryLine(
            ledger_entry_id=gl_entry.id,
            account_id=credit_account.id,
            debit_amount=Decimal('0'),
            credit_amount=transaction.amount,
            description=transaction.description,
            line_number=2
        )
        
        db.session.add_all([debit_line, credit_line])
        
        return gl_entry

    @staticmethod
    def _get_next_entry_number(organization_id):
        """Get next sequential journal entry number."""
        latest_entry = GeneralLedgerEntry.query.filter_by(
            organization_id=organization_id
        ).order_by(GeneralLedgerEntry.id.desc()).first()
        
        if latest_entry:
            # Extract number from entry_number (format: "JE-2024-001")
            try:
                current_year = datetime.now().year
                parts = latest_entry.entry_number.split('-')
                if len(parts) >= 3 and parts[1] == str(current_year):
                    next_num = int(parts[2]) + 1
                else:
                    next_num = 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1
        
        return f"JE-{datetime.now().year}-{next_num:03d}"

class FundingSourceService:
    """Service for managing funding sources and balances."""
    
    @staticmethod
    def get_or_create_directors_loan(organization_id, director_user_id):
        """Get or create directors loan funding source."""
        funding_source = FundingSource.query.filter_by(
            organization_id=organization_id,
            source_type='directors_loan'
        ).first()
        
        if not funding_source:
            # Get directors loan account
            directors_loan_account = Account.query.filter_by(
                organization_id=organization_id,
                account_code='2400'  # Director's Loan Account
            ).first()
            
            if not directors_loan_account:
                raise ValueError("Directors loan account (2400) not found in chart of accounts")
            
            # Get director name
            director = User.query.get(director_user_id)
            director_name = director.name if director else "Director"
            
            funding_source = FundingSource(
                organization_id=organization_id,
                source_type='directors_loan',
                source_name=f"{director_name}'s Loan",
                account_code='2400',
                current_balance=Decimal('0'),
                currency_code='USD'
            )
            
            db.session.add(funding_source)
            db.session.flush()
        
        return funding_source
    
    @staticmethod
    def update_balance(funding_source, amount, transaction_id):
        """Update funding source balance and create transaction record."""
        # Determine transaction type
        if funding_source.source_type == 'directors_loan':
            # Directors loan increases when expenses are charged to it
            transaction_type = 'credit'
            funding_source.current_balance += amount
        else:
            # Bank accounts decrease when money is spent
            transaction_type = 'debit'
            funding_source.current_balance -= amount
        
        # Create funding source transaction
        funding_txn = FundingSourceTransaction(
            funding_source_id=funding_source.id,
            financial_transaction_id=transaction_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_after=funding_source.current_balance
        )
        
        db.session.add(funding_txn)
        funding_source.updated_at = datetime.utcnow()
        
        return funding_txn

class EnhancedMatchingEngine:
    """Smart reconciliation engine with learning capabilities."""
    
    @staticmethod
    def reconcile_bank_statement(bank_statement_id, user_id):
        """Complete bank statement reconciliation with learning."""
        try:
            statement = BankStatement.query.get(bank_statement_id)
            if not statement:
                raise ValueError(f"Bank statement {bank_statement_id} not found")
            
            # Get organization matching rules
            rules = SmartMatchingRules.query.filter_by(
                organization_id=statement.organization_id
            ).first()
            
            if not rules:
                rules = SmartMatchingRules(organization_id=statement.organization_id)
                db.session.add(rules)
                db.session.flush()
            
            # Get bank transactions to reconcile
            bank_transactions = FinancialTransaction.query.filter_by(
                organization_id=statement.organization_id,
                source_type=SourceType.BANK_STATEMENT.value,
                reconciliation_status=ReconciliationStatus.UNMATCHED.value
            ).join(TransactionMetaBank).filter(
                TransactionMetaBank.bank_statement_id == bank_statement_id
            ).all()
            
            # Get potential receipt matches
            date_range_start = statement.statement_period_from - timedelta(days=rules.date_tolerance_days)
            date_range_end = statement.statement_period_to + timedelta(days=rules.date_tolerance_days)
            
            potential_receipts = FinancialTransaction.query.filter(
                FinancialTransaction.organization_id == statement.organization_id,
                FinancialTransaction.source_type == SourceType.RECEIPT_SCAN.value,
                FinancialTransaction.reconciliation_status == ReconciliationStatus.UNMATCHED.value,
                FinancialTransaction.transaction_date >= date_range_start,
                FinancialTransaction.transaction_date <= date_range_end
            ).all()
            
            reconciliation_results = {
                'auto_matched': [],
                'manual_review': [],
                'unmatched': [],
                'split_transactions': []
            }
            
            # Process each bank transaction
            for bank_txn in bank_transactions:
                best_match, best_confidence = EnhancedMatchingEngine._find_best_match(
                    bank_txn, potential_receipts, rules
                )
                
                if best_confidence >= rules.auto_approve_threshold:
                    # Auto-approve high confidence matches
                    EnhancedMatchingEngine._create_automatic_match(
                        bank_txn, best_match, best_confidence, user_id
                    )
                    reconciliation_results['auto_matched'].append({
                        'bank_transaction': bank_txn,
                        'receipt': best_match,
                        'confidence': best_confidence
                    })
                    
                elif best_confidence >= rules.manual_review_threshold:
                    # Flag for manual review
                    reconciliation_results['manual_review'].append({
                        'bank_transaction': bank_txn,
                        'potential_match': best_match,
                        'confidence': best_confidence
                    })
                    
                else:
                    # No good matches found - remains unmatched
                    reconciliation_results['unmatched'].append({
                        'bank_transaction': bank_txn
                    })
            
            # Check for split transactions
            split_groups = EnhancedMatchingEngine._detect_split_transactions(
                reconciliation_results['unmatched'], potential_receipts, rules
            )
            
            reconciliation_results['split_transactions'] = split_groups
            
            # Update statement statistics
            statement.auto_matched_count = len(reconciliation_results['auto_matched'])
            statement.manual_matched_count = 0  # Will be updated during manual review
            statement.exception_count = len(reconciliation_results['manual_review'])
            statement.processing_status = 'reconciled' if len(reconciliation_results['manual_review']) == 0 else 'needs_review'
            statement.reconciled_at = datetime.utcnow()
            
            # Create reconciliation audit
            audit = ReconciliationAudit(
                organization_id=statement.organization_id,
                bank_statement_id=bank_statement_id,
                reconciliation_date=date.today(),
                reconciled_by=user_id,
                total_transactions=len(bank_transactions),
                auto_matched=len(reconciliation_results['auto_matched']),
                manual_matched=0,
                unmatched=len(reconciliation_results['unmatched']),
                bank_opening_balance=statement.opening_balance,
                bank_closing_balance=statement.closing_balance,
                session_start_time=datetime.utcnow(),
                session_end_time=datetime.utcnow()
            )
            
            db.session.add(audit)
            db.session.commit()
            
            logger.info(f"Reconciled bank statement {bank_statement_id}: {len(reconciliation_results['auto_matched'])} auto-matched, {len(reconciliation_results['manual_review'])} need review")
            
            return reconciliation_results
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error reconciling bank statement {bank_statement_id}: {str(e)}")
            raise

    @staticmethod
    def _find_best_match(bank_txn, potential_receipts, rules):
        """Find the best matching receipt for a bank transaction."""
        best_match = None
        best_confidence = 0
        
        for receipt in potential_receipts:
            confidence = EnhancedMatchingEngine._calculate_match_confidence(
                bank_txn, receipt, rules
            )
            
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = receipt
        
        return best_match, best_confidence

    @staticmethod
    def _calculate_match_confidence(bank_txn, receipt, rules):
        """Calculate confidence score for potential match with enhanced logic."""
        confidence_score = 0
        
        # Amount matching with service charge tolerance (40% weight)
        amount_diff = abs(bank_txn.amount - receipt.amount)
        
        if amount_diff == 0:
            amount_score = 100
        elif amount_diff <= rules.service_charge_tolerance:
            # Likely service charge - still high confidence
            amount_score = 90 - (float(amount_diff) / float(rules.service_charge_tolerance)) * 20
        else:
            # Check percentage tolerance
            percent_diff = (float(amount_diff) / float(receipt.amount)) * 100
            if percent_diff <= float(rules.amount_tolerance_percent):
                amount_score = 80 - percent_diff * 5
            else:
                return 0  # Amount difference too large
        
        # Date matching (30% weight)
        date_diff = abs((bank_txn.transaction_date - receipt.transaction_date).days)
        if date_diff <= rules.date_tolerance_days:
            date_score = 100 - (date_diff * 10)
        else:
            date_score = 0
        
        # Description similarity (30% weight)
        desc_similarity = EnhancedMatchingEngine._calculate_description_similarity(
            bank_txn.description, receipt.description
        )
        
        # Apply vendor patterns if available
        vendor_pattern = rules.get_vendor_pattern(receipt.description)
        if vendor_pattern:
            # Adjust confidence based on learned patterns
            if 'typical_fee' in vendor_pattern:
                expected_fee = vendor_pattern['typical_fee']
                if abs(amount_diff - expected_fee) <= 1.0:
                    amount_score = min(amount_score + 10, 100)
        
        # Weighted confidence score
        confidence_score = (amount_score * 0.4) + (date_score * 0.3) + (desc_similarity * 0.3)
        
        return min(confidence_score, 100)

    @staticmethod
    def _calculate_description_similarity(bank_desc, receipt_desc):
        """Calculate description similarity using improved algorithm."""
        if not bank_desc or not receipt_desc:
            return 0
        
        bank_desc = bank_desc.lower().strip()
        receipt_desc = receipt_desc.lower().strip()
        
        # Exact match
        if bank_desc == receipt_desc:
            return 100
        
        # Substring match
        if bank_desc in receipt_desc or receipt_desc in bank_desc:
            return 85
        
        # Word overlap
        bank_words = set(re.findall(r'\w+', bank_desc))
        receipt_words = set(re.findall(r'\w+', receipt_desc))
        
        if not bank_words or not receipt_words:
            return 0
        
        overlap = len(bank_words.intersection(receipt_words))
        total_words = len(bank_words.union(receipt_words))
        
        similarity = (overlap / total_words) * 100
        
        # Boost for common vendor patterns
        vendor_keywords = ['shell', 'petrol', 'fuel', 'gas', 'uber', 'taxi', 'restaurant', 'hotel']
        for keyword in vendor_keywords:
            if keyword in bank_desc and keyword in receipt_desc:
                similarity = min(similarity + 15, 100)
                break
        
        return similarity

    @staticmethod
    def _create_automatic_match(bank_txn, receipt, confidence, user_id):
        """Create an automatic match between bank transaction and receipt."""
        # Update reconciliation status
        bank_txn.reconciliation_status = ReconciliationStatus.AUTO_MATCHED.value
        bank_txn.matched_transaction_id = receipt.id
        bank_txn.reconciliation_confidence = Decimal(str(confidence))
        
        receipt.reconciliation_status = ReconciliationStatus.AUTO_MATCHED.value
        receipt.matched_transaction_id = bank_txn.id
        receipt.reconciliation_confidence = Decimal(str(confidence))
        
        # Create training data for learning
        training_data = ReconciliationTrainingData(
            organization_id=bank_txn.organization_id,
            bank_description=bank_txn.description,
            bank_amount=bank_txn.amount,
            receipt_vendor=receipt.description,
            receipt_amount=receipt.amount,
            date_difference=abs((bank_txn.transaction_date - receipt.transaction_date).days),
            user_matched=True,
            match_confidence=Decimal(str(confidence)),
            user_id=user_id,
            amount_difference=abs(bank_txn.amount - receipt.amount),
            description_similarity=Decimal(str(EnhancedMatchingEngine._calculate_description_similarity(
                bank_txn.description, receipt.description
            )))
        )
        
        db.session.add(training_data)
        
        # Update funding source (receipt should now point to bank account instead of directors loan)
        FundingSourceService._transfer_funding_source(receipt, bank_txn)

    @staticmethod
    def _detect_split_transactions(unmatched_bank_txns, potential_receipts, rules):
        """Detect and handle split transactions."""
        split_groups = []
        
        for bank_txn_data in unmatched_bank_txns:
            bank_txn = bank_txn_data['bank_transaction']
            
            # Find multiple receipts that sum to bank transaction amount
            matching_receipts = []
            for receipt in potential_receipts:
                if abs((bank_txn.transaction_date - receipt.transaction_date).days) <= rules.date_tolerance_days:
                    matching_receipts.append(receipt)
            
            # Check combinations of receipts
            split_combination = EnhancedMatchingEngine._find_split_combination(
                bank_txn.amount, matching_receipts, rules.service_charge_tolerance
            )
            
            if split_combination:
                # Create split group
                split_group = SplitTransactionGroup(
                    organization_id=bank_txn.organization_id,
                    group_type='card_settlement',
                    total_amount=bank_txn.amount,
                    source_bank_transaction_id=bank_txn.id
                )
                
                db.session.add(split_group)
                db.session.flush()
                
                # Add members
                for receipt in split_combination:
                    member = SplitTransactionMember(
                        split_group_id=split_group.id,
                        financial_transaction_id=receipt.id,
                        amount=receipt.amount,
                        percentage=(float(receipt.amount) / float(bank_txn.amount)) * 100
                    )
                    db.session.add(member)
                    
                    # Mark as part of split
                    receipt.reconciliation_status = ReconciliationStatus.AUTO_MATCHED.value
                    receipt.matched_transaction_id = bank_txn.id
                
                bank_txn.reconciliation_status = ReconciliationStatus.AUTO_MATCHED.value
                
                split_groups.append(split_group)
        
        return split_groups

    @staticmethod
    def _find_split_combination(target_amount, receipts, tolerance):
        """Find combination of receipts that sum to target amount."""
        # Simple greedy approach for now - could be enhanced with dynamic programming
        receipts.sort(key=lambda r: r.amount, reverse=True)
        
        def find_combination(remaining_amount, available_receipts, current_combination):
            if abs(remaining_amount) <= tolerance:
                return current_combination
            
            if remaining_amount <= 0 or not available_receipts:
                return None
            
            for i, receipt in enumerate(available_receipts):
                new_combination = current_combination + [receipt]
                new_remaining = remaining_amount - receipt.amount
                new_available = available_receipts[i+1:]
                
                result = find_combination(new_remaining, new_available, new_combination)
                if result is not None:
                    return result
            
            return None
        
        return find_combination(float(target_amount), receipts, [])

class BankAccountService:
    """Service for managing bank account integration."""
    
    @staticmethod
    def get_account_code_for_statement(bank_statement_id):
        """Get the GL account code for a bank statement."""
        # For now, return default bank account - this would be enhanced to link to specific accounts
        return '1000'  # Cash - Primary Bank Account

class BalanceSheetService:
    """Service for generating real-time balance sheets."""
    
    @staticmethod
    def generate_balance_sheet(organization_id, as_of_date=None):
        """Generate real-time balance sheet from cached balances."""
        if not as_of_date:
            as_of_date = date.today()
        
        # Try to get from cache first
        cached_balances = AccountBalanceCache.query.filter_by(
            organization_id=organization_id,
            balance_as_of_date=as_of_date
        ).all()
        
        if not cached_balances:
            # Generate fresh balances
            BalanceSheetService._refresh_balance_cache(organization_id, as_of_date)
            cached_balances = AccountBalanceCache.query.filter_by(
                organization_id=organization_id,
                balance_as_of_date=as_of_date
            ).all()
        
        # Group by account type
        balance_sheet = {
            'assets': {'accounts': [], 'total': Decimal('0')},
            'liabilities': {'accounts': [], 'total': Decimal('0')},
            'equity': {'accounts': [], 'total': Decimal('0')},
            'as_of_date': as_of_date.isoformat()
        }
        
        for cache_entry in cached_balances:
            account = cache_entry.account
            account_data = {
                'account_code': account.account_code,
                'account_name': account.account_name,
                'balance': float(cache_entry.net_balance),
                'base_currency_balance': float(cache_entry.base_currency_balance)
            }
            
            if account.account_type == 'asset':
                balance_sheet['assets']['accounts'].append(account_data)
                balance_sheet['assets']['total'] += cache_entry.net_balance
            elif account.account_type == 'liability':
                balance_sheet['liabilities']['accounts'].append(account_data)
                balance_sheet['liabilities']['total'] += cache_entry.net_balance
            elif account.account_type == 'equity':
                balance_sheet['equity']['accounts'].append(account_data)
                balance_sheet['equity']['total'] += cache_entry.net_balance
        
        # Verify balance sheet equation
        total_assets = balance_sheet['assets']['total']
        total_liab_equity = balance_sheet['liabilities']['total'] + balance_sheet['equity']['total']
        variance = total_assets - total_liab_equity
        
        balance_sheet['balanced'] = abs(variance) < Decimal('0.01')
        balance_sheet['variance'] = float(variance)
        
        # Convert totals to float for JSON serialization
        balance_sheet['assets']['total'] = float(balance_sheet['assets']['total'])
        balance_sheet['liabilities']['total'] = float(balance_sheet['liabilities']['total'])
        balance_sheet['equity']['total'] = float(balance_sheet['equity']['total'])
        
        return balance_sheet

    @staticmethod
    def _refresh_balance_cache(organization_id, as_of_date):
        """Refresh account balance cache for given date."""
        # This would typically be done by a background job
        # For now, we'll compute balances directly
        
        accounts = Account.query.filter_by(organization_id=organization_id).all()
        
        for account in accounts:
            # Calculate balance from journal entries
            balance_query = db.session.query(
                func.sum(JournalEntryLine.debit_amount).label('total_debits'),
                func.sum(JournalEntryLine.credit_amount).label('total_credits')
            ).join(GeneralLedgerEntry).filter(
                JournalEntryLine.account_id == account.id,
                GeneralLedgerEntry.transaction_date <= as_of_date
            ).first()
            
            total_debits = balance_query.total_debits or Decimal('0')
            total_credits = balance_query.total_credits or Decimal('0')
            
            # Calculate net balance based on account type
            if account.account_type in ['asset', 'expense']:
                net_balance = total_debits - total_credits
            else:
                net_balance = total_credits - total_debits
            
            # Create or update cache entry
            cache_entry = AccountBalanceCache.query.filter_by(
                organization_id=organization_id,
                account_id=account.id,
                balance_as_of_date=as_of_date
            ).first()
            
            if cache_entry:
                cache_entry.debit_balance = total_debits
                cache_entry.credit_balance = total_credits
                cache_entry.net_balance = net_balance
                cache_entry.base_currency_balance = net_balance  # Assume same currency for now
                cache_entry.last_updated = datetime.utcnow()
            else:
                cache_entry = AccountBalanceCache(
                    organization_id=organization_id,
                    account_id=account.id,
                    balance_as_of_date=as_of_date,
                    debit_balance=total_debits,
                    credit_balance=total_credits,
                    net_balance=net_balance,
                    base_currency_balance=net_balance
                )
                db.session.add(cache_entry)
        
        db.session.commit()