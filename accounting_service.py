"""
Accounting service layer for handling business logic and integrations.
This service manages journal entries, depreciation calculations, and financial reporting.
"""

from app import db
from accounting_models import (
    Account, GeneralLedgerEntry, JournalEntryLine, FixedAsset, 
    DepreciationEntry, AssetCategory, AccountingPeriod
)
from models import Receipt, Invoice, CompanyExpense, Organization
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import func, and_, or_
import logging

logger = logging.getLogger(__name__)

class AccountingService:
    """Service class for accounting operations."""
    
    @staticmethod
    def get_next_entry_number(organization_id):
        """Generate next journal entry number for organization."""
        last_entry = GeneralLedgerEntry.query.filter_by(
            organization_id=organization_id
        ).order_by(GeneralLedgerEntry.id.desc()).first()
        
        if last_entry and last_entry.entry_number:
            try:
                last_number = int(last_entry.entry_number.split('-')[-1])
                return f"JE-{last_number + 1:06d}"
            except:
                pass
        
        return "JE-000001"
    
    @staticmethod
    def create_receipt_journal_entry(receipt_id, user_id):
        """Create journal entry when a receipt is processed."""
        try:
            receipt = Receipt.query.get(receipt_id)
            if not receipt:
                raise ValueError(f"Receipt {receipt_id} not found")
            
            # Find appropriate expense account
            expense_account = Account.query.filter_by(
                organization_id=receipt.organization_id,
                account_type='expense',
                is_active=True
            ).first()
            
            if not expense_account:
                # Create default expense account if none exists
                expense_account = Account(
                    organization_id=receipt.organization_id,
                    account_code='5100',
                    account_name='Office Expenses',
                    account_type='expense',
                    account_subtype='operating_expense'
                )
                db.session.add(expense_account)
                db.session.flush()
            
            # Find accounts payable account
            ap_account = Account.query.filter_by(
                organization_id=receipt.organization_id,
                account_code='2100'
            ).first()
            
            if not ap_account:
                ap_account = Account(
                    organization_id=receipt.organization_id,
                    account_code='2100',
                    account_name='Accounts Payable',
                    account_type='liability',
                    account_subtype='current_liability'
                )
                db.session.add(ap_account)
                db.session.flush()
            
            # Create ledger entry
            entry_number = AccountingService.get_next_entry_number(receipt.organization_id)
            ledger_entry = GeneralLedgerEntry(
                organization_id=receipt.organization_id,
                entry_number=entry_number,
                transaction_date=receipt.date or date.today(),
                reference_type='receipt',
                reference_id=receipt.id,
                description=f"Expense: {receipt.vendor_name}",
                total_amount=receipt.total_amount,
                created_by=user_id
            )
            db.session.add(ledger_entry)
            db.session.flush()
            
            # Create journal lines
            # Debit expense account
            debit_line = JournalEntryLine(
                ledger_entry_id=ledger_entry.id,
                account_id=expense_account.id,
                debit_amount=receipt.total_amount,
                credit_amount=Decimal('0'),
                description=f"Expense - {receipt.vendor_name}",
                line_number=1
            )
            
            # Credit accounts payable
            credit_line = JournalEntryLine(
                ledger_entry_id=ledger_entry.id,
                account_id=ap_account.id,
                debit_amount=Decimal('0'),
                credit_amount=receipt.total_amount,
                description=f"Payable - {receipt.vendor_name}",
                line_number=2
            )
            
            db.session.add(debit_line)
            db.session.add(credit_line)
            db.session.commit()
            
            logger.info(f"Created journal entry {entry_number} for receipt {receipt_id}")
            return ledger_entry
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating receipt journal entry: {str(e)}")
            raise
    
    @staticmethod
    def create_invoice_journal_entry(invoice_id, user_id):
        """Create journal entry when an invoice is created."""
        try:
            invoice = Invoice.query.get(invoice_id)
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")
            
            # Find revenue account
            revenue_account = Account.query.filter_by(
                organization_id=invoice.organization_id,
                account_type='revenue',
                is_active=True
            ).first()
            
            if not revenue_account:
                revenue_account = Account(
                    organization_id=invoice.organization_id,
                    account_code='4100',
                    account_name='Service Revenue',
                    account_type='revenue',
                    account_subtype='operating_revenue'
                )
                db.session.add(revenue_account)
                db.session.flush()
            
            # Find accounts receivable account
            ar_account = Account.query.filter_by(
                organization_id=invoice.organization_id,
                account_code='1200'
            ).first()
            
            if not ar_account:
                ar_account = Account(
                    organization_id=invoice.organization_id,
                    account_code='1200',
                    account_name='Accounts Receivable',
                    account_type='asset',
                    account_subtype='current_asset'
                )
                db.session.add(ar_account)
                db.session.flush()
            
            # Create ledger entry
            entry_number = AccountingService.get_next_entry_number(invoice.organization_id)
            ledger_entry = GeneralLedgerEntry(
                organization_id=invoice.organization_id,
                entry_number=entry_number,
                transaction_date=invoice.issue_date,
                reference_type='invoice',
                reference_id=invoice.id,
                description=f"Invoice {invoice.invoice_number}",
                total_amount=invoice.total,
                created_by=user_id
            )
            db.session.add(ledger_entry)
            db.session.flush()
            
            # Create journal lines
            # Debit accounts receivable
            debit_line = JournalEntryLine(
                ledger_entry_id=ledger_entry.id,
                account_id=ar_account.id,
                debit_amount=invoice.total,
                credit_amount=Decimal('0'),
                description=f"Invoice {invoice.invoice_number}",
                line_number=1
            )
            
            # Credit revenue
            credit_line = JournalEntryLine(
                ledger_entry_id=ledger_entry.id,
                account_id=revenue_account.id,
                debit_amount=Decimal('0'),
                credit_amount=invoice.total,
                description=f"Revenue - Invoice {invoice.invoice_number}",
                line_number=2
            )
            
            db.session.add(debit_line)
            db.session.add(credit_line)
            db.session.commit()
            
            logger.info(f"Created journal entry {entry_number} for invoice {invoice_id}")
            return ledger_entry
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating invoice journal entry: {str(e)}")
            raise
    
    @staticmethod
    def calculate_monthly_depreciation(organization_id, period_date=None):
        """Calculate and record depreciation for all active assets."""
        if not period_date:
            period_date = date.today().replace(day=1)  # First day of current month
        
        try:
            active_assets = FixedAsset.query.filter_by(
                organization_id=organization_id,
                status='active'
            ).all()
            
            total_depreciation = Decimal('0')
            entries_created = 0
            
            for asset in active_assets:
                # Check if depreciation already calculated for this period
                existing_entry = DepreciationEntry.query.filter_by(
                    fixed_asset_id=asset.id,
                    period_date=period_date
                ).first()
                
                if existing_entry:
                    continue
                
                depreciation_amount = asset.calculate_depreciation(period_date)
                if depreciation_amount <= 0:
                    continue
                
                # Update asset accumulated depreciation
                asset.accumulated_depreciation += depreciation_amount
                new_book_value = asset.get_current_book_value()
                
                # Create depreciation entry
                dep_entry = DepreciationEntry(
                    fixed_asset_id=asset.id,
                    period_date=period_date,
                    depreciation_amount=depreciation_amount,
                    accumulated_depreciation=asset.accumulated_depreciation,
                    book_value=new_book_value,
                    is_calculated=True
                )
                db.session.add(dep_entry)
                
                total_depreciation += depreciation_amount
                entries_created += 1
            
            # Create journal entry for total depreciation
            if total_depreciation > 0:
                AccountingService.create_depreciation_journal_entry(
                    organization_id, period_date, total_depreciation
                )
            
            db.session.commit()
            logger.info(f"Calculated depreciation for {entries_created} assets, total: {total_depreciation}")
            
            return {
                'assets_processed': entries_created,
                'total_depreciation': float(total_depreciation),
                'period_date': period_date.isoformat()
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error calculating depreciation: {str(e)}")
            raise
    
    @staticmethod
    def create_depreciation_journal_entry(organization_id, period_date, total_amount):
        """Create journal entry for depreciation expense."""
        try:
            # Find or create depreciation expense account
            dep_expense_account = Account.query.filter_by(
                organization_id=organization_id,
                account_code='5800'
            ).first()
            
            if not dep_expense_account:
                dep_expense_account = Account(
                    organization_id=organization_id,
                    account_code='5800',
                    account_name='Depreciation Expense',
                    account_type='expense',
                    account_subtype='depreciation_expense'
                )
                db.session.add(dep_expense_account)
                db.session.flush()
            
            # Find or create accumulated depreciation account
            acc_dep_account = Account.query.filter_by(
                organization_id=organization_id,
                account_code='1550'
            ).first()
            
            if not acc_dep_account:
                acc_dep_account = Account(
                    organization_id=organization_id,
                    account_code='1550',
                    account_name='Accumulated Depreciation',
                    account_type='asset',
                    account_subtype='accumulated_depreciation'
                )
                db.session.add(acc_dep_account)
                db.session.flush()
            
            # Create ledger entry
            entry_number = AccountingService.get_next_entry_number(organization_id)
            ledger_entry = GeneralLedgerEntry(
                organization_id=organization_id,
                entry_number=entry_number,
                transaction_date=period_date,
                reference_type='depreciation',
                reference_id=None,
                description=f"Monthly depreciation - {period_date.strftime('%B %Y')}",
                total_amount=total_amount,
                created_by=None
            )
            db.session.add(ledger_entry)
            db.session.flush()
            
            # Create journal lines
            # Debit depreciation expense
            debit_line = JournalEntryLine(
                ledger_entry_id=ledger_entry.id,
                account_id=dep_expense_account.id,
                debit_amount=total_amount,
                credit_amount=Decimal('0'),
                description="Monthly depreciation expense",
                line_number=1
            )
            
            # Credit accumulated depreciation
            credit_line = JournalEntryLine(
                ledger_entry_id=ledger_entry.id,
                account_id=acc_dep_account.id,
                debit_amount=Decimal('0'),
                credit_amount=total_amount,
                description="Accumulated depreciation",
                line_number=2
            )
            
            db.session.add(debit_line)
            db.session.add(credit_line)
            
            return ledger_entry
            
        except Exception as e:
            logger.error(f"Error creating depreciation journal entry: {str(e)}")
            raise

class FinancialReportService:
    """Service class for generating financial reports."""
    
    @staticmethod
    def generate_profit_loss(organization_id, start_date, end_date):
        """Generate Profit & Loss statement for organization and period."""
        try:
            from models import Receipt
            
            # Initialize report structure with expense categories
            report = {
                'organization_id': organization_id,
                'period': f"{start_date} to {end_date}",
                'revenue_accounts': [],
                'expense_categories': [],
                'total_revenue': Decimal('0'),
                'total_expenses': Decimal('0'),
                'net_income': Decimal('0')
            }
            
            # Get revenue accounts
            revenue_accounts = Account.query.filter_by(
                organization_id=organization_id,
                account_type='revenue',
                is_active=True
            ).all()
            
            for account in revenue_accounts:
                balance = FinancialReportService.get_account_balance_for_period(
                    account.id, start_date, end_date
                )
                if balance != 0:  # Only show accounts with activity
                    account_data = {
                        'account_code': account.account_code,
                        'account_name': account.account_name,
                        'balance': float(abs(balance))  # Revenue shows as positive
                    }
                    report['revenue_accounts'].append(account_data)
                    report['total_revenue'] += abs(balance)
            
            # Get expenses by major category from receipts
            expense_query = db.session.query(
                Receipt.expense_major_category,
                func.sum(Receipt.total_amount).label('total_amount')
            ).filter(
                Receipt.organization_id == organization_id,
                Receipt.date >= start_date,
                Receipt.date <= end_date,
                Receipt.expense_major_category.isnot(None)
            ).group_by(Receipt.expense_major_category).all()
            
            # Add expense categories to report
            for category, amount in expense_query:
                if amount and amount > 0:
                    category_data = {
                        'category_name': category,
                        'balance': float(amount)
                    }
                    report['expense_categories'].append(category_data)
                    report['total_expenses'] += Decimal(str(amount))
            
            # Calculate net income
            report['net_income'] = report['total_revenue'] - report['total_expenses']
            
            # Convert Decimal to float for JSON serialization
            report['total_revenue'] = float(report['total_revenue'])
            report['total_expenses'] = float(report['total_expenses'])
            report['net_income'] = float(report['net_income'])
            
            return report
            
        except Exception as e:
            logger.error(f"Error generating P&L report: {str(e)}")
            raise
    
    @staticmethod
    def get_account_balance_for_period(account_id, start_date, end_date):
        """Get account balance for a specific period."""
        try:
            query = db.session.query(
                func.sum(JournalEntryLine.debit_amount).label('total_debits'),
                func.sum(JournalEntryLine.credit_amount).label('total_credits')
            ).join(GeneralLedgerEntry).filter(
                JournalEntryLine.account_id == account_id,
                GeneralLedgerEntry.transaction_date >= start_date,
                GeneralLedgerEntry.transaction_date <= end_date
            )
            
            result = query.first()
            total_debits = result.total_debits or Decimal('0')
            total_credits = result.total_credits or Decimal('0')
            
            # Get account to determine normal balance
            account = Account.query.get(account_id)
            if account.account_type in ['asset', 'expense']:
                return total_debits - total_credits
            else:
                return total_credits - total_debits
                
        except Exception as e:
            logger.error(f"Error calculating account balance: {str(e)}")
            return Decimal('0')