"""
Enhanced Rules Engine with Organization-Specific Customization
Provides configurable validation rules for bank statement processing.
"""

import json
from typing import Dict, List, Any, Optional
from models import db, Organization
from simple_audit_trail import SimpleAuditLogger
import logging

logger = logging.getLogger(__name__)

class OrganizationRulesEngine:
    """Rules engine with organization-specific customization support."""
    
    def __init__(self, organization_id: Optional[int] = None):
        self.organization_id = organization_id
        self.rules = self._load_rules()
        
    def _load_rules(self) -> Dict[str, Any]:
        """Load rules with organization-specific overrides."""
        # Default rules baseline
        default_rules = {
            'account_number_detection': {
                'enabled': True,
                'patterns': ['1000120800', '4567891234'],
                'priority': 1
            },
            'amount_thresholds': {
                'enabled': True,
                'min_amount': 0.01,
                'max_amount': 1000000000,
                'priority': 2
            },
            'summary_row_detection': {
                'enabled': True,
                'patterns': [
                    'TOTAL DEPOSITS', 'CLOSING BALANCE', 'OPENING BALANCE', 
                    'BALANCE BROUGHT FORWARD', 'BALANCE B/F', 'TOTAL WITHDRAWALS'
                ],
                'priority': 3
            },
            'date_pattern_exclusion': {
                'enabled': True,
                'patterns': ['25/26', '26/27', '24/25', '23/24'],
                'priority': 4
            }
        }
        
        # Apply organization-specific overrides
        if self.organization_id:
            org_rules = self._get_organization_rules(self.organization_id)
            if org_rules:
                # Merge organization rules with defaults
                for rule_name, rule_config in org_rules.items():
                    if rule_name in default_rules:
                        default_rules[rule_name].update(rule_config)
                    else:
                        # Add completely new rule types
                        default_rules[rule_name] = rule_config
        
        return default_rules
    
    def _get_organization_rules(self, org_id: int) -> Optional[Dict]:
        """Get organization-specific rule overrides."""
        try:
            org = Organization.query.get(org_id)
            if org and org.validation_rules:
                return json.loads(org.validation_rules)
            return None
        except Exception as e:
            logger.error(f"Failed to load organization rules: {e}")
            return None
    
    def save_organization_rules(self, rules: Dict[str, Any]) -> bool:
        """Save organization-specific rule overrides."""
        try:
            if not self.organization_id:
                return False
                
            org = Organization.query.get(self.organization_id)
            if org:
                # Store rules as JSON in organization field
                org.validation_rules = json.dumps(rules)
                db.session.commit()
                # Reload rules
                self.rules = self._load_rules()
                logger.info(f"Saved validation rules for organization {self.organization_id}")
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save organization rules: {e}")
            return False
    
    def validate_transaction(self, 
                           amount: float, 
                           description: str, 
                           audit_logger: SimpleAuditLogger) -> bool:
        """Enhanced validation with organization-specific rules."""
        
        # Rule 1: Account number detection (if enabled)
        if self.rules['account_number_detection']['enabled']:
            amount_str = str(amount).replace('.', '').replace(',', '')
            for pattern in self.rules['account_number_detection']['patterns']:
                if pattern in amount_str:
                    audit_logger.log_event(
                        data=f"amount:{amount}",
                        stage='validation',
                        rule_id='account_number_detection',
                        decision='reject',
                        message=f'Amount {amount} matches organization account pattern {pattern}',
                        confidence=0.95
                    )
                    return False
        
        # Rule 2: Amount thresholds (if enabled)
        if self.rules['amount_thresholds']['enabled']:
            min_amt = self.rules['amount_thresholds']['min_amount']
            max_amt = self.rules['amount_thresholds']['max_amount']
            
            if abs(amount) < min_amt:
                audit_logger.log_event(
                    data=f"amount:{amount}",
                    stage='validation',
                    rule_id='amount_threshold_min',
                    decision='reject',
                    message=f'Amount {amount} below organization minimum {min_amt}',
                    confidence=0.90
                )
                return False
                
            if abs(amount) > max_amt:
                audit_logger.log_event(
                    data=f"amount:{amount}",
                    stage='validation',
                    rule_id='amount_threshold_max',
                    decision='reject',
                    message=f'Amount {amount} exceeds organization maximum {max_amt}',
                    confidence=0.85
                )
                return False
        
        # Rule 3: Summary row detection (if enabled)
        if self.rules['summary_row_detection']['enabled']:
            for pattern in self.rules['summary_row_detection']['patterns']:
                if pattern in description.upper():
                    audit_logger.log_event(
                        data=f"description:{description}",
                        stage='validation',
                        rule_id='summary_row_detection',
                        decision='reject',
                        message=f'Description contains organization summary pattern: {pattern}',
                        confidence=0.92
                    )
                    return False
        
        # Rule 4: Date pattern exclusion (if enabled)
        if self.rules['date_pattern_exclusion']['enabled']:
            for pattern in self.rules['date_pattern_exclusion']['patterns']:
                if pattern in description:
                    audit_logger.log_event(
                        data=f"description:{description}",
                        stage='validation',
                        rule_id='date_pattern_exclusion',
                        decision='reject',
                        message=f'Description contains organization date pattern: {pattern}',
                        confidence=0.88
                    )
                    return False
        
        # All rules passed
        audit_logger.log_event(
            data=f"amount:{amount}, desc:{description[:50]}",
            stage='validation',
            rule_id='validation_passed',
            decision='accept',
            message='Transaction passed all organization validation rules',
            confidence=0.98
        )
        return True
    
    def get_rules_summary(self) -> Dict[str, Any]:
        """Get current rules configuration for display."""
        return {
            'account_detection_enabled': self.rules['account_number_detection']['enabled'],
            'account_patterns': self.rules['account_number_detection']['patterns'],
            'min_amount': self.rules['amount_thresholds']['min_amount'],
            'max_amount': self.rules['amount_thresholds']['max_amount'],
            'summary_patterns': self.rules['summary_row_detection']['patterns'],
            'date_patterns': self.rules['date_pattern_exclusion']['patterns']
        }
    
    def get_validation_statistics(self, organization_id: int) -> Dict[str, Any]:
        """Get validation performance statistics for this organization."""
        try:
            # Query recent statements for this organization
            from enhanced_financial_models import BankStatement
            from datetime import datetime, timedelta
            
            recent_statements = BankStatement.query.filter_by(
                organization_id=organization_id
            ).filter(
                BankStatement.created_at >= datetime.utcnow() - timedelta(days=30)
            ).all()
            
            total_processed = len(recent_statements)
            total_transactions = 0
            successful_statements = 0
            
            for statement in recent_statements:
                if statement.processing_status == 'completed':
                    successful_statements += 1
                    # Count transactions if available
                    if hasattr(statement, 'transaction_count') and statement.transaction_count:
                        total_transactions += statement.transaction_count
            
            success_rate = (successful_statements / total_processed * 100) if total_processed > 0 else 0
            avg_transactions = (total_transactions / total_processed) if total_processed > 0 else 0
            
            return {
                'success_rate': round(success_rate, 1),
                'total_processed': total_processed,
                'avg_transactions': round(avg_transactions, 1),
                'period_days': 30
            }
            
        except Exception as e:
            logger.error(f"Failed to get validation statistics: {e}")
            return {
                'success_rate': 0,
                'total_processed': 0,
                'avg_transactions': 0,
                'period_days': 30
            }


def create_default_rules_for_organization(organization_id: int) -> bool:
    """Initialize default validation rules for a new organization."""
    try:
        engine = OrganizationRulesEngine(organization_id)
        default_rules = {
            'account_number_detection': {
                'enabled': True,
                'patterns': ['1000120800', '4567891234']
            },
            'amount_thresholds': {
                'enabled': True,
                'min_amount': 0.01,
                'max_amount': 1000000000
            },
            'summary_row_detection': {
                'enabled': True,
                'patterns': [
                    'TOTAL DEPOSITS', 'CLOSING BALANCE', 'OPENING BALANCE', 
                    'BALANCE BROUGHT FORWARD', 'BALANCE B/F', 'TOTAL WITHDRAWALS'
                ]
            },
            'date_pattern_exclusion': {
                'enabled': True,
                'patterns': ['25/26', '26/27', '24/25', '23/24']
            }
        }
        
        return engine.save_organization_rules(default_rules)
        
    except Exception as e:
        logger.error(f"Failed to create default rules for organization {organization_id}: {e}")
        return False