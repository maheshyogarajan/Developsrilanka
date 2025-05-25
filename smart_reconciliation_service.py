"""
Smart Reconciliation Service
Automatically matches bank transactions with existing receipts and expenses using AI-powered algorithms.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
import re
from difflib import SequenceMatcher

from models import Receipt, CompanyExpense, db
from enhanced_financial_models import FinancialTransaction, BankStatement
from enhanced_reconciliation_models import (
    ReconciliationTrainingData, SmartMatchingRules, 
    ReconciliationException, ReconciliationAudit
)

logger = logging.getLogger(__name__)

@dataclass
class MatchCandidate:
    """Represents a potential match between bank transaction and receipt/expense."""
    receipt_id: Optional[int]
    expense_id: Optional[int]
    confidence_score: float
    match_reasons: List[str]
    amount_difference: float
    date_difference: int  # days
    description_similarity: float
    merchant_match: bool

class SmartReconciliationService:
    """Service for intelligent transaction reconciliation."""
    
    def __init__(self):
        self.min_confidence_threshold = 0.7
        self.auto_match_threshold = 0.9
        self.date_tolerance_days = 3
        
    def reconcile_statement(self, statement_id: int, organization_id: int) -> Dict[str, Any]:
        """
        Perform smart reconciliation for a bank statement.
        
        Args:
            statement_id: ID of the bank statement to reconcile
            organization_id: Organization ID for context
            
        Returns:
            Dictionary containing reconciliation results
        """
        try:
            # Get bank statement and transactions
            statement = BankStatement.query.get(statement_id)
            if not statement:
                raise ValueError(f"Bank statement {statement_id} not found")
            
            # Get all bank transactions for this statement
            bank_transactions = FinancialTransaction.query.filter_by(
                source_type='BANK_STATEMENT',
                source_id=statement_id
            ).all()
            
            # Get existing receipts and expenses in date range
            start_date = statement.statement_start_date - timedelta(days=self.date_tolerance_days)
            end_date = statement.statement_end_date + timedelta(days=self.date_tolerance_days)
            
            receipts = Receipt.query.filter(
                Receipt.organization_id == organization_id,
                Receipt.date >= start_date,
                Receipt.date <= end_date,
                Receipt.reconciliation_status != 'reconciled'
            ).all()
            
            expenses = CompanyExpense.query.filter(
                CompanyExpense.organization_id == organization_id,
                CompanyExpense.date >= start_date,
                CompanyExpense.date <= end_date,
                CompanyExpense.reconciliation_status != 'reconciled'
            ).all()
            
            # Perform matching
            reconciliation_results = {
                'statement_id': statement_id,
                'total_transactions': len(bank_transactions),
                'auto_matched': 0,
                'potential_matches': 0,
                'unmatched': 0,
                'matches': [],
                'exceptions': [],
                'processing_timestamp': datetime.now().isoformat()
            }
            
            for transaction in bank_transactions:
                match_result = self._find_best_match(transaction, receipts, expenses)
                
                if match_result:
                    if match_result.confidence_score >= self.auto_match_threshold:
                        # Auto-match with high confidence
                        self._create_automatic_match(transaction, match_result, organization_id)
                        reconciliation_results['auto_matched'] += 1
                    else:
                        # Potential match requiring manual review
                        reconciliation_results['potential_matches'] += 1
                        reconciliation_results['matches'].append({
                            'transaction_id': transaction.id,
                            'candidate': match_result,
                            'requires_review': True
                        })
                else:
                    # No match found
                    reconciliation_results['unmatched'] += 1
                    self._create_reconciliation_exception(transaction, organization_id)
            
            return reconciliation_results
            
        except Exception as e:
            logger.error(f"Error in smart reconciliation: {str(e)}")
            raise
    
    def _find_best_match(self, transaction: FinancialTransaction, 
                        receipts: List[Receipt], 
                        expenses: List[CompanyExpense]) -> Optional[MatchCandidate]:
        """Find the best matching receipt or expense for a transaction."""
        candidates = []
        
        # Check receipts
        for receipt in receipts:
            candidate = self._evaluate_receipt_match(transaction, receipt)
            if candidate and candidate.confidence_score >= self.min_confidence_threshold:
                candidates.append(candidate)
        
        # Check expenses
        for expense in expenses:
            candidate = self._evaluate_expense_match(transaction, expense)
            if candidate and candidate.confidence_score >= self.min_confidence_threshold:
                candidates.append(candidate)
        
        # Return best candidate
        if candidates:
            return max(candidates, key=lambda c: c.confidence_score)
        
        return None
    
    def _evaluate_receipt_match(self, transaction: FinancialTransaction, 
                               receipt: Receipt) -> Optional[MatchCandidate]:
        """Evaluate how well a receipt matches a bank transaction."""
        try:
            # Amount matching
            amount_diff = abs(float(transaction.amount) - float(receipt.total))
            amount_score = max(0, 1.0 - (amount_diff / float(receipt.total)))
            
            # Date matching
            date_diff = abs((transaction.transaction_date - receipt.date).days)
            date_score = max(0, 1.0 - (date_diff / self.date_tolerance_days))
            
            # Description matching
            desc_similarity = self._calculate_description_similarity(
                transaction.description, receipt.merchant_name or ""
            )
            
            # Merchant matching
            merchant_match = self._check_merchant_match(
                transaction.description, receipt.merchant_name
            )
            
            # Calculate overall confidence score
            confidence = (
                amount_score * 0.4 +
                date_score * 0.3 +
                desc_similarity * 0.2 +
                (1.0 if merchant_match else 0.0) * 0.1
            )
            
            if confidence >= self.min_confidence_threshold:
                return MatchCandidate(
                    receipt_id=receipt.id,
                    expense_id=None,
                    confidence_score=confidence,
                    match_reasons=self._generate_match_reasons(
                        amount_score, date_score, desc_similarity, merchant_match
                    ),
                    amount_difference=amount_diff,
                    date_difference=date_diff,
                    description_similarity=desc_similarity,
                    merchant_match=merchant_match
                )
            
        except Exception as e:
            logger.debug(f"Error evaluating receipt match: {str(e)}")
        
        return None
    
    def _evaluate_expense_match(self, transaction: FinancialTransaction, 
                               expense: CompanyExpense) -> Optional[MatchCandidate]:
        """Evaluate how well an expense matches a bank transaction."""
        try:
            # Amount matching
            amount_diff = abs(float(transaction.amount) - float(expense.amount))
            amount_score = max(0, 1.0 - (amount_diff / float(expense.amount)))
            
            # Date matching
            date_diff = abs((transaction.transaction_date - expense.date).days)
            date_score = max(0, 1.0 - (date_diff / self.date_tolerance_days))
            
            # Description matching
            desc_similarity = self._calculate_description_similarity(
                transaction.description, expense.description or ""
            )
            
            # Merchant matching (using expense description)
            merchant_match = self._check_merchant_match(
                transaction.description, expense.description or ""
            )
            
            # Calculate overall confidence score
            confidence = (
                amount_score * 0.4 +
                date_score * 0.3 +
                desc_similarity * 0.2 +
                (1.0 if merchant_match else 0.0) * 0.1
            )
            
            if confidence >= self.min_confidence_threshold:
                return MatchCandidate(
                    receipt_id=None,
                    expense_id=expense.id,
                    confidence_score=confidence,
                    match_reasons=self._generate_match_reasons(
                        amount_score, date_score, desc_similarity, merchant_match
                    ),
                    amount_difference=amount_diff,
                    date_difference=date_diff,
                    description_similarity=desc_similarity,
                    merchant_match=merchant_match
                )
            
        except Exception as e:
            logger.debug(f"Error evaluating expense match: {str(e)}")
        
        return None
    
    def _calculate_description_similarity(self, desc1: str, desc2: str) -> float:
        """Calculate similarity between two descriptions."""
        if not desc1 or not desc2:
            return 0.0
        
        # Clean and normalize descriptions
        clean_desc1 = re.sub(r'[^\w\s]', '', desc1.lower().strip())
        clean_desc2 = re.sub(r'[^\w\s]', '', desc2.lower().strip())
        
        # Use sequence matcher for similarity
        return SequenceMatcher(None, clean_desc1, clean_desc2).ratio()
    
    def _check_merchant_match(self, transaction_desc: str, merchant_name: str) -> bool:
        """Check if merchant name appears in transaction description."""
        if not transaction_desc or not merchant_name:
            return False
        
        # Clean merchant name
        clean_merchant = re.sub(r'[^\w\s]', '', merchant_name.lower().strip())
        clean_desc = transaction_desc.lower()
        
        # Check for exact match or partial match
        if clean_merchant in clean_desc:
            return True
        
        # Check for individual words
        merchant_words = clean_merchant.split()
        if len(merchant_words) > 1:
            # At least half the words should match
            matches = sum(1 for word in merchant_words if word in clean_desc)
            return matches >= len(merchant_words) / 2
        
        return False
    
    def _generate_match_reasons(self, amount_score: float, date_score: float, 
                               desc_similarity: float, merchant_match: bool) -> List[str]:
        """Generate human-readable reasons for the match."""
        reasons = []
        
        if amount_score > 0.9:
            reasons.append("Exact amount match")
        elif amount_score > 0.7:
            reasons.append("Close amount match")
        
        if date_score > 0.9:
            reasons.append("Same date")
        elif date_score > 0.7:
            reasons.append("Date within tolerance")
        
        if merchant_match:
            reasons.append("Merchant name matches")
        
        if desc_similarity > 0.7:
            reasons.append("Description similarity")
        
        return reasons
    
    def _create_automatic_match(self, transaction: FinancialTransaction, 
                               match: MatchCandidate, organization_id: int):
        """Create an automatic match with high confidence."""
        try:
            # Update transaction with match information
            if match.receipt_id:
                transaction.receipt_id = match.receipt_id
                # Update receipt reconciliation status
                receipt = Receipt.query.get(match.receipt_id)
                if receipt:
                    receipt.reconciliation_status = 'reconciled'
                    receipt.bank_transaction_id = transaction.id
            
            elif match.expense_id:
                transaction.expense_id = match.expense_id
                # Update expense reconciliation status
                expense = CompanyExpense.query.get(match.expense_id)
                if expense:
                    expense.reconciliation_status = 'reconciled'
                    expense.bank_transaction_id = transaction.id
            
            # Create audit record
            audit = ReconciliationAudit(
                transaction_id=transaction.id,
                organization_id=organization_id,
                match_type='automatic',
                confidence_score=match.confidence_score,
                match_reasons=', '.join(match.match_reasons),
                created_at=datetime.now()
            )
            
            db.session.add(audit)
            db.session.commit()
            
            # Store training data for future improvements
            self._store_training_data(transaction, match, 'accepted')
            
        except Exception as e:
            logger.error(f"Error creating automatic match: {str(e)}")
            db.session.rollback()
            raise
    
    def _create_reconciliation_exception(self, transaction: FinancialTransaction, 
                                       organization_id: int):
        """Create an exception for unmatched transactions."""
        try:
            exception = ReconciliationException(
                transaction_id=transaction.id,
                organization_id=organization_id,
                exception_type='no_match_found',
                description=f"No matching receipt or expense found for transaction: {transaction.description}",
                amount=transaction.amount,
                transaction_date=transaction.transaction_date,
                status='pending',
                created_at=datetime.now()
            )
            
            db.session.add(exception)
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error creating reconciliation exception: {str(e)}")
            db.session.rollback()
            raise
    
    def _store_training_data(self, transaction: FinancialTransaction, 
                           match: MatchCandidate, decision: str):
        """Store training data to improve future matching."""
        try:
            training_data = ReconciliationTrainingData(
                transaction_description=transaction.description,
                transaction_amount=transaction.amount,
                transaction_date=transaction.transaction_date,
                matched_receipt_id=match.receipt_id,
                matched_expense_id=match.expense_id,
                confidence_score=match.confidence_score,
                user_decision=decision,
                amount_difference=match.amount_difference,
                date_difference=match.date_difference,
                description_similarity=match.description_similarity,
                merchant_match=match.merchant_match,
                created_at=datetime.now()
            )
            
            db.session.add(training_data)
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error storing training data: {str(e)}")
    
    def manual_reconcile(self, transaction_id: int, target_id: int, 
                        target_type: str, organization_id: int) -> bool:
        """
        Manually reconcile a transaction with a receipt or expense.
        
        Args:
            transaction_id: ID of the bank transaction
            target_id: ID of the receipt or expense to match
            target_type: 'receipt' or 'expense'
            organization_id: Organization ID for context
            
        Returns:
            True if successful, False otherwise
        """
        try:
            transaction = FinancialTransaction.query.get(transaction_id)
            if not transaction:
                raise ValueError(f"Transaction {transaction_id} not found")
            
            if target_type == 'receipt':
                receipt = Receipt.query.get(target_id)
                if not receipt:
                    raise ValueError(f"Receipt {target_id} not found")
                
                transaction.receipt_id = target_id
                receipt.reconciliation_status = 'reconciled'
                receipt.bank_transaction_id = transaction_id
                
            elif target_type == 'expense':
                expense = CompanyExpense.query.get(target_id)
                if not expense:
                    raise ValueError(f"Expense {target_id} not found")
                
                transaction.expense_id = target_id
                expense.reconciliation_status = 'reconciled'
                expense.bank_transaction_id = transaction_id
            
            else:
                raise ValueError(f"Invalid target type: {target_type}")
            
            # Create audit record
            audit = ReconciliationAudit(
                transaction_id=transaction_id,
                organization_id=organization_id,
                match_type='manual',
                confidence_score=1.0,  # Manual matches are 100% confident
                match_reasons='Manual reconciliation by user',
                created_at=datetime.now()
            )
            
            db.session.add(audit)
            db.session.commit()
            
            return True
            
        except Exception as e:
            logger.error(f"Error in manual reconciliation: {str(e)}")
            db.session.rollback()
            return False
    
    def get_reconciliation_summary(self, statement_id: int) -> Dict[str, Any]:
        """Get reconciliation summary for a statement."""
        try:
            statement = BankStatement.query.get(statement_id)
            if not statement:
                return {}
            
            transactions = FinancialTransaction.query.filter_by(
                bank_statement_id=statement_id
            ).all()
            
            total_transactions = len(transactions)
            reconciled_count = sum(1 for t in transactions if t.receipt_id or t.expense_id)
            exceptions_count = ReconciliationException.query.filter_by(
                transaction_id__in=[t.id for t in transactions]
            ).count()
            
            return {
                'total_transactions': total_transactions,
                'reconciled_count': reconciled_count,
                'unreconciled_count': total_transactions - reconciled_count,
                'exceptions_count': exceptions_count,
                'reconciliation_rate': reconciled_count / total_transactions if total_transactions > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Error getting reconciliation summary: {str(e)}")
            return {}

# Example usage
if __name__ == "__main__":
    service = SmartReconciliationService()
    print("Smart Reconciliation Service initialized successfully!")