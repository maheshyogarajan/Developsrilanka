"""
Auto-Reconciliation Matching Engine
Implements Tier-1 deterministic rules and Tier-2 ML matching
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from decimal import Decimal
import json
import hashlib
from app import db
from sqlalchemy import text
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class MatchCandidate:
    """Represents a potential transaction match"""
    bank_txn_id: int
    expense_txn_id: int
    match_score: float
    match_type: str
    confidence_tier: str
    rules_applied: List[str]
    features: Dict

class DeterministicMatcher:
    """Tier-1 deterministic matching rules using exact criteria"""
    
    def __init__(self, organization_id: int):
        self.organization_id = organization_id
        self.match_threshold_exact = 100.0
        self.match_threshold_high = 95.0
        
    def find_matches(self, date_range_days: int = 30) -> List[MatchCandidate]:
        """
        Find deterministic matches using exact amount and date proximity rules
        
        Args:
            date_range_days: Look for matches within this many days
            
        Returns:
            List of high-confidence match candidates
        """
        matches = []
        
        # Get unmatched bank transactions
        bank_txns = self._get_unmatched_bank_transactions(date_range_days)
        expense_txns = self._get_unmatched_expense_transactions(date_range_days)
        
        logger.info(f"Processing {len(bank_txns)} bank transactions against {len(expense_txns)} expenses")
        
        for bank_txn in bank_txns:
            best_matches = self._find_deterministic_matches(bank_txn, expense_txns)
            matches.extend(best_matches)
            
        return matches
    
    def _get_unmatched_bank_transactions(self, date_range_days: int) -> List[Dict]:
        """Get bank transactions that haven't been matched yet"""
        
        cutoff_date = datetime.now() - timedelta(days=date_range_days)
        
        result = db.session.execute(text("""
            SELECT 
                ft.id,
                ft.amount,
                ft.transaction_date,
                ft.description,
                ft.match_status,
                bs.bank_name
            FROM financial_transaction ft
            JOIN bank_statement bs ON ft.bank_statement_id = bs.id
            WHERE bs.organization_id = :org_id
            AND ft.match_status IN ('pending', 'unmatched')
            AND ft.transaction_date >= :cutoff_date
            AND ft.amount IS NOT NULL
            ORDER BY ft.transaction_date DESC
        """), {
            'org_id': self.organization_id,
            'cutoff_date': cutoff_date
        })
        
        return [dict(row._mapping) for row in result.fetchall()]
    
    def _get_unmatched_expense_transactions(self, date_range_days: int) -> List[Dict]:
        """Get expense transactions that haven't been matched yet"""
        
        cutoff_date = datetime.now() - timedelta(days=date_range_days)
        
        result = db.session.execute(text("""
            SELECT 
                ce.id,
                ce.amount,
                ce.expense_date,
                ce.description,
                ce.category,
                ce.minor_category
            FROM company_expense ce
            WHERE ce.organization_id = :org_id
            AND ce.expense_date >= :cutoff_date
            AND ce.amount IS NOT NULL
            AND ce.id NOT IN (
                SELECT DISTINCT expense_transaction_id 
                FROM transaction_match 
                WHERE match_status = 'accepted'
                AND expense_transaction_id IS NOT NULL
            )
            ORDER BY ce.expense_date DESC
        """), {
            'org_id': self.organization_id,
            'cutoff_date': cutoff_date
        })
        
        return [dict(row._mapping) for row in result.fetchall()]
    
    def _find_deterministic_matches(self, bank_txn: Dict, expense_txns: List[Dict]) -> List[MatchCandidate]:
        """Apply deterministic matching rules to find exact matches"""
        
        matches = []
        bank_amount = float(bank_txn['amount'])
        bank_date = bank_txn['transaction_date']
        
        for expense_txn in expense_txns:
            expense_amount = float(expense_txn['amount'])
            expense_date = expense_txn['expense_date']
            
            # Rule 1: Exact amount match within ±3 days
            if self._amounts_match_exactly(bank_amount, expense_amount):
                date_diff = abs((bank_date - expense_date).days)
                
                if date_diff <= 3:
                    score = 100.0 - (date_diff * 2)  # Reduce score by 2% per day
                    rules_applied = ['exact_amount', f'date_within_{date_diff}_days']
                    
                    # Rule 2: Description similarity bonus
                    desc_similarity = self._calculate_description_similarity(
                        bank_txn['description'], expense_txn['description']
                    )
                    
                    if desc_similarity > 0.7:
                        score = min(100.0, score + 5.0)
                        rules_applied.append('description_similarity')
                    
                    matches.append(MatchCandidate(
                        bank_txn_id=bank_txn['id'],
                        expense_txn_id=expense_txn['id'],
                        match_score=score,
                        match_type='deterministic',
                        confidence_tier='tier1',
                        rules_applied=rules_applied,
                        features={
                            'amount_diff': 0.0,
                            'date_diff_days': date_diff,
                            'description_similarity': desc_similarity,
                            'bank_amount': bank_amount,
                            'expense_amount': expense_amount
                        }
                    ))
            
            # Rule 3: Near-exact amount match (within 1%) for larger transactions
            elif (bank_amount > 100 and 
                  self._amounts_match_approximately(bank_amount, expense_amount, tolerance=0.01)):
                
                date_diff = abs((bank_date - expense_date).days)
                if date_diff <= 2:  # Stricter date range for approximate matches
                    amount_diff_pct = abs(bank_amount - expense_amount) / max(bank_amount, expense_amount)
                    score = 95.0 - (amount_diff_pct * 100) - (date_diff * 3)
                    
                    matches.append(MatchCandidate(
                        bank_txn_id=bank_txn['id'],
                        expense_txn_id=expense_txn['id'],
                        match_score=score,
                        match_type='deterministic',
                        confidence_tier='tier1',
                        rules_applied=['approximate_amount', f'date_within_{date_diff}_days'],
                        features={
                            'amount_diff': abs(bank_amount - expense_amount),
                            'amount_diff_pct': amount_diff_pct,
                            'date_diff_days': date_diff,
                            'bank_amount': bank_amount,
                            'expense_amount': expense_amount
                        }
                    ))
        
        # Sort by score and return top matches
        matches.sort(key=lambda x: x.match_score, reverse=True)
        return matches[:3]  # Return top 3 matches per transaction
    
    def _amounts_match_exactly(self, amount1: float, amount2: float) -> bool:
        """Check if two amounts match exactly (accounting for floating point precision)"""
        return abs(amount1 - amount2) < 0.01
    
    def _amounts_match_approximately(self, amount1: float, amount2: float, tolerance: float) -> bool:
        """Check if two amounts match within a percentage tolerance"""
        if max(amount1, amount2) == 0:
            return False
        
        diff_pct = abs(amount1 - amount2) / max(amount1, amount2)
        return diff_pct <= tolerance
    
    def _calculate_description_similarity(self, desc1: str, desc2: str) -> float:
        """Calculate basic description similarity using word overlap"""
        if not desc1 or not desc2:
            return 0.0
        
        # Normalize descriptions
        words1 = set(desc1.lower().split())
        words2 = set(desc2.lower().split())
        
        # Calculate Jaccard similarity
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        
        return intersection / union if union > 0 else 0.0

class MatchingEngine:
    """Main matching engine that orchestrates all matching tiers"""
    
    def __init__(self, organization_id: int):
        self.organization_id = organization_id
        self.deterministic_matcher = DeterministicMatcher(organization_id)
    
    def run_auto_reconciliation(self) -> Dict:
        """
        Run the complete auto-reconciliation process
        
        Returns:
            Statistics about matches found and processed
        """
        logger.info(f"Starting auto-reconciliation for organization {self.organization_id}")
        
        # Step 1: Find deterministic matches
        tier1_matches = self.deterministic_matcher.find_matches()
        
        # Step 2: Process high-confidence matches automatically
        auto_accepted = self._auto_accept_high_confidence_matches(tier1_matches)
        
        # Step 3: Store pending matches for review
        pending_review = self._store_pending_matches(tier1_matches)
        
        stats = {
            'tier1_matches_found': len(tier1_matches),
            'auto_accepted': len(auto_accepted),
            'pending_review': len(pending_review),
            'organization_id': self.organization_id
        }
        
        logger.info(f"Auto-reconciliation completed: {stats}")
        return stats
    
    def _auto_accept_high_confidence_matches(self, matches: List[MatchCandidate]) -> List[int]:
        """Auto-accept matches with score ≥95%"""
        
        auto_accepted = []
        
        for match in matches:
            if match.match_score >= 95.0:
                try:
                    # Create the match record
                    match_id = self._create_match_record(match, 'accepted')
                    
                    # Update transaction statuses
                    self._update_transaction_status(
                        match.bank_txn_id, 'matched', match.match_score
                    )
                    
                    auto_accepted.append(match_id)
                    logger.info(f"Auto-accepted match {match_id} with score {match.match_score:.1f}")
                    
                except Exception as e:
                    logger.error(f"Error auto-accepting match: {e}")
        
        return auto_accepted
    
    def _store_pending_matches(self, matches: List[MatchCandidate]) -> List[int]:
        """Store matches with 70-95% confidence for manual review"""
        
        pending_matches = []
        
        for match in matches:
            if 70.0 <= match.match_score < 95.0:
                try:
                    match_id = self._create_match_record(match, 'proposed')
                    pending_matches.append(match_id)
                    
                except Exception as e:
                    logger.error(f"Error storing pending match: {e}")
        
        return pending_matches
    
    def _create_match_record(self, match: MatchCandidate, status: str) -> int:
        """Create a transaction_match record"""
        
        result = db.session.execute(text("""
            INSERT INTO transaction_match (
                bank_transaction_id,
                expense_transaction_id,
                match_type,
                match_score,
                confidence_tier,
                match_status,
                match_rules_applied,
                ml_features,
                organization_id
            ) VALUES (
                :bank_txn_id,
                :expense_txn_id,
                :match_type,
                :match_score,
                :confidence_tier,
                :match_status,
                :rules_applied,
                :features,
                :org_id
            ) RETURNING id
        """), {
            'bank_txn_id': match.bank_txn_id,
            'expense_txn_id': match.expense_txn_id,
            'match_type': match.match_type,
            'match_score': match.match_score,
            'confidence_tier': match.confidence_tier,
            'match_status': status,
            'rules_applied': json.dumps(match.rules_applied),
            'features': json.dumps(match.features),
            'org_id': self.organization_id
        })
        
        match_id = result.fetchone()[0]
        db.session.commit()
        return match_id
    
    def _update_transaction_status(self, txn_id: int, status: str, score: float):
        """Update transaction match status and score"""
        
        db.session.execute(text("""
            UPDATE financial_transaction 
            SET match_status = :status,
                match_score = :score,
                auto_matched = :auto_matched,
                last_matched_at = NOW()
            WHERE id = :txn_id
        """), {
            'status': status,
            'score': score,
            'auto_matched': status == 'matched',
            'txn_id': txn_id
        })
        
        db.session.commit()