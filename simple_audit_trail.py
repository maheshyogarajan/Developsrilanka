"""
Simple Audit Trail System for Bank Statement Processing
Works with existing PyPDF2 + Enhanced validation system
Provides complete decision tracking without external dependencies
"""

import json
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)

@dataclass
class ValidationEvent:
    """Single validation decision event."""
    row_hash: str
    stage: str  # 'extraction', 'validation', 'transformation'
    rule_id: str
    decision: str  # 'accept', 'reject', 'flag'
    message: str
    confidence: float = 0.8
    page_index: Optional[int] = None
    raw_data: Optional[Dict] = None
    timestamp: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()

@dataclass
class ProcessingAuditTrail:
    """Complete audit trail for a bank statement processing session."""
    statement_id: int
    processing_method: str
    start_time: str
    end_time: Optional[str] = None
    events: List[ValidationEvent] = None
    summary: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.events is None:
            self.events = []
        if self.summary is None:
            self.summary = {
                'total_events': 0,
                'accepted_count': 0,
                'rejected_count': 0,
                'flagged_count': 0
            }

class SimpleAuditLogger:
    """Lightweight audit logging system for bank statement processing."""
    
    def __init__(self):
        self.current_trail: Optional[ProcessingAuditTrail] = None
        
    def start_processing(self, statement_id: int, method: str = "enhanced_validation") -> ProcessingAuditTrail:
        """Start a new processing audit trail."""
        self.current_trail = ProcessingAuditTrail(
            statement_id=statement_id,
            processing_method=method,
            start_time=datetime.utcnow().isoformat()
        )
        logger.info(f"Started audit trail for statement {statement_id}")
        return self.current_trail
    
    def log_event(self, 
                  data: str, 
                  stage: str, 
                  rule_id: str, 
                  decision: str, 
                  message: str,
                  confidence: float = 0.8,
                  raw_data: Dict = None) -> ValidationEvent:
        """Log a single validation event."""
        
        if not self.current_trail:
            raise ValueError("No active audit trail. Call start_processing() first.")
        
        # Create row hash for tracking
        row_hash = hashlib.md5(str(data).encode()).hexdigest()
        
        event = ValidationEvent(
            row_hash=row_hash,
            stage=stage,
            rule_id=rule_id,
            decision=decision,
            message=message,
            confidence=confidence,
            raw_data=raw_data or {}
        )
        
        self.current_trail.events.append(event)
        
        # Update summary
        self.current_trail.summary['total_events'] += 1
        if decision == 'accept':
            self.current_trail.summary['accepted_count'] += 1
        elif decision == 'reject':
            self.current_trail.summary['rejected_count'] += 1
        elif decision == 'flag':
            self.current_trail.summary['flagged_count'] += 1
        
        logger.debug(f"Logged {decision} event: {rule_id} - {message}")
        return event
    
    def finish_processing(self) -> ProcessingAuditTrail:
        """Complete the audit trail and return results."""
        if not self.current_trail:
            raise ValueError("No active audit trail to finish.")
        
        self.current_trail.end_time = datetime.utcnow().isoformat()
        trail = self.current_trail
        self.current_trail = None
        
        logger.info(f"Completed audit trail: {trail.summary}")
        return trail
    
    def get_rejection_summary(self) -> List[str]:
        """Get list of rejection reasons from current trail."""
        if not self.current_trail:
            return []
        
        return [
            event.message for event in self.current_trail.events 
            if event.decision == 'reject'
        ]
    
    def get_acceptance_count(self) -> int:
        """Get count of accepted items."""
        if not self.current_trail:
            return 0
        return self.current_trail.summary['accepted_count']
    
    def get_rejection_count(self) -> int:
        """Get count of rejected items."""
        if not self.current_trail:
            return 0
        return self.current_trail.summary['rejected_count']

class SimpleRulesEngine:
    """Simple rules engine using basic Python logic instead of external libraries."""
    
    def __init__(self):
        self.rules = self._load_built_in_rules()
        
    def _load_built_in_rules(self) -> Dict[str, Any]:
        """Load built-in validation rules."""
        return {
            'account_number_detection': {
                'priority': 1,
                'patterns': ['1000120800', '4567891234'],  # Common account number patterns
                'action': 'reject',
                'message': 'Amount appears to be an account number'
            },
            'amount_threshold': {
                'priority': 2,
                'max_amount': 1000000000,
                'action': 'reject',
                'message': 'Amount exceeds reasonable threshold'
            },
            'summary_row_detection': {
                'priority': 3,
                'patterns': ['TOTAL DEPOSITS', 'CLOSING BALANCE', 'OPENING BALANCE', 'BALANCE BROUGHT FORWARD'],
                'action': 'reject',
                'message': 'Row identified as summary/total line'
            },
            'date_pattern_rejection': {
                'priority': 4,
                'patterns': ['25/26', '26/27'],
                'action': 'reject',
                'message': 'Description contains date-like pattern, likely header text'
            },
            'zero_amount': {
                'priority': 5,
                'action': 'reject',
                'message': 'Zero amount transactions are not valid'
            },
            'tiny_amount': {
                'priority': 6,
                'threshold': 0.01,
                'action': 'reject',
                'message': 'Amount below minimum threshold'
            }
        }
    
    def validate_transaction(self, 
                           amount: float, 
                           description: str, 
                           audit_logger: SimpleAuditLogger) -> bool:
        """
        Validate a transaction against all rules.
        Returns True if transaction should be accepted.
        """
        
        # Rule 1: Account number detection
        amount_str = str(amount).replace('.', '').replace(',', '')
        for pattern in self.rules['account_number_detection']['patterns']:
            if pattern in amount_str:
                audit_logger.log_event(
                    data=f"amount:{amount}",
                    stage='validation',
                    rule_id='account_number_detection',
                    decision='reject',
                    message=f'Amount {amount} matches account pattern {pattern}'
                )
                return False
        
        # Rule 2: Amount threshold
        if abs(amount) > self.rules['amount_threshold']['max_amount']:
            audit_logger.log_event(
                data=f"amount:{amount}",
                stage='validation',
                rule_id='amount_threshold',
                decision='reject',
                message=f'Amount {amount} exceeds threshold'
            )
            return False
        
        # Rule 3: Summary row detection
        for pattern in self.rules['summary_row_detection']['patterns']:
            if pattern in description.upper():
                audit_logger.log_event(
                    data=f"description:{description}",
                    stage='validation',
                    rule_id='summary_row_detection',
                    decision='reject',
                    message=f'Description contains summary pattern: {pattern}'
                )
                return False
        
        # Rule 4: Date pattern rejection
        for pattern in self.rules['date_pattern_rejection']['patterns']:
            if pattern in description:
                audit_logger.log_event(
                    data=f"description:{description}",
                    stage='validation',
                    rule_id='date_pattern_rejection',
                    decision='reject',
                    message=f'Description contains date pattern: {pattern}'
                )
                return False
        
        # Rule 5: Zero amount
        if amount == 0:
            audit_logger.log_event(
                data=f"amount:{amount}",
                stage='validation',
                rule_id='zero_amount',
                decision='reject',
                message='Zero amount transaction'
            )
            return False
        
        # Rule 6: Tiny amount
        if abs(amount) < self.rules['tiny_amount']['threshold']:
            audit_logger.log_event(
                data=f"amount:{amount}",
                stage='validation',
                rule_id='tiny_amount',
                decision='reject',
                message=f'Amount {amount} below threshold'
            )
            return False
        
        # If we get here, transaction passes all rules
        audit_logger.log_event(
            data=f"amount:{amount}, desc:{description[:50]}",
            stage='validation',
            rule_id='validation_passed',
            decision='accept',
            message='Transaction passed all validation rules'
        )
        return True

def store_audit_trail_in_statement(statement, audit_trail: ProcessingAuditTrail):
    """Store audit trail data in the existing BankStatement processing_notes field."""
    try:
        audit_data = {
            'processing_method': audit_trail.processing_method,
            'start_time': audit_trail.start_time,
            'end_time': audit_trail.end_time,
            'summary': audit_trail.summary,
            'rejection_reasons': [
                event.message for event in audit_trail.events 
                if event.decision == 'reject'
            ],
            'total_events': len(audit_trail.events),
            'audit_version': '1.0'
        }
        
        statement.processing_notes = json.dumps(audit_data)
        logger.info(f"Stored audit trail for statement {statement.id}")
        
    except Exception as e:
        logger.error(f"Failed to store audit trail: {e}")

def load_audit_trail_from_statement(statement) -> Optional[Dict[str, Any]]:
    """Load audit trail data from BankStatement processing_notes field."""
    try:
        if statement.processing_notes:
            return json.loads(statement.processing_notes)
        return None
    except Exception as e:
        logger.error(f"Failed to load audit trail: {e}")
        return None