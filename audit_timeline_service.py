"""
Audit Trail Timeline Service
Integrates extraction events and validation rule changes into comprehensive audit timeline
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from app import db
from sqlalchemy import text
import json
import hashlib
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class AuditEvent:
    """Standardized audit event structure"""
    event_id: str
    timestamp: datetime
    event_type: str  # 'extraction', 'validation', 'reconciliation', 'security'
    entity_type: str  # 'transaction', 'rule', 'match', 'user'
    entity_id: int
    organization_id: int
    user_id: Optional[int]
    action: str  # 'create', 'update', 'delete', 'approve', 'reject'
    changes: Dict[str, Any]
    metadata: Dict[str, Any]
    confidence_score: Optional[float]
    risk_level: str  # 'low', 'medium', 'high', 'critical'

class AuditTimelineService:
    """Service for managing comprehensive audit trails and timeline visualization"""
    
    def __init__(self, organization_id: int):
        self.organization_id = organization_id
        
    def create_extraction_audit_event(self, transaction_id: int, extraction_data: Dict, user_id: int) -> str:
        """Create audit event for PDF extraction activities"""
        
        event_id = self._generate_event_id('extraction', transaction_id)
        
        # Calculate extraction confidence and risk
        confidence = extraction_data.get('extraction_confidence', 0.0)
        risk_level = self._calculate_risk_level(confidence, 'extraction')
        
        audit_event = AuditEvent(
            event_id=event_id,
            timestamp=datetime.now(),
            event_type='extraction',
            entity_type='transaction',
            entity_id=transaction_id,
            organization_id=self.organization_id,
            user_id=user_id,
            action='create',
            changes={
                'amount_extracted': extraction_data.get('amount'),
                'description_extracted': extraction_data.get('description'),
                'page_idx': extraction_data.get('page_idx'),
                'bbox_coordinates': extraction_data.get('bbox'),
                'parser_method': extraction_data.get('parser_method', 'unknown')
            },
            metadata={
                'pdf_hash': extraction_data.get('pdf_hash'),
                'extraction_rules_applied': extraction_data.get('rules_applied', []),
                'processing_time_ms': extraction_data.get('processing_time_ms'),
                'pdf_page_count': extraction_data.get('page_count', 1)
            },
            confidence_score=confidence,
            risk_level=risk_level
        )
        
        return self._store_audit_event(audit_event)
    
    def create_validation_rule_audit_event(self, rule_id: int, rule_changes: Dict, user_id: int, action: str) -> str:
        """Create audit event for validation rule modifications"""
        
        event_id = self._generate_event_id('validation', rule_id)
        
        # Assess risk level for rule changes
        risk_level = self._assess_rule_change_risk(rule_changes, action)
        
        audit_event = AuditEvent(
            event_id=event_id,
            timestamp=datetime.now(),
            event_type='validation',
            entity_type='rule',
            entity_id=rule_id,
            organization_id=self.organization_id,
            user_id=user_id,
            action=action,
            changes=rule_changes,
            metadata={
                'rule_category': rule_changes.get('category', 'unknown'),
                'affected_transactions': self._count_affected_transactions(rule_id),
                'rule_complexity_score': self._calculate_rule_complexity(rule_changes)
            },
            confidence_score=None,
            risk_level=risk_level
        )
        
        return self._store_audit_event(audit_event)
    
    def create_reconciliation_audit_event(self, match_id: int, match_data: Dict, user_id: int, action: str) -> str:
        """Create audit event for reconciliation activities"""
        
        event_id = self._generate_event_id('reconciliation', match_id)
        
        # Calculate reconciliation confidence and risk
        match_score = match_data.get('match_score', 0.0)
        risk_level = self._calculate_risk_level(match_score / 100.0, 'reconciliation')
        
        audit_event = AuditEvent(
            event_id=event_id,
            timestamp=datetime.now(),
            event_type='reconciliation',
            entity_type='match',
            entity_id=match_id,
            organization_id=self.organization_id,
            user_id=user_id,
            action=action,
            changes={
                'match_score': match_score,
                'match_type': match_data.get('match_type'),
                'confidence_tier': match_data.get('confidence_tier'),
                'previous_status': match_data.get('previous_status'),
                'new_status': match_data.get('new_status')
            },
            metadata={
                'bank_transaction_id': match_data.get('bank_transaction_id'),
                'expense_receipt_id': match_data.get('expense_receipt_id'),
                'ml_features_used': match_data.get('ml_features', []),
                'rules_applied': match_data.get('rules_applied', [])
            },
            confidence_score=match_score / 100.0,
            risk_level=risk_level
        )
        
        return self._store_audit_event(audit_event)
    
    def create_security_audit_event(self, entity_id: int, security_event: Dict, user_id: Optional[int] = None) -> str:
        """Create audit event for security-related activities"""
        
        event_id = self._generate_event_id('security', entity_id)
        
        audit_event = AuditEvent(
            event_id=event_id,
            timestamp=datetime.now(),
            event_type='security',
            entity_type=security_event.get('entity_type', 'system'),
            entity_id=entity_id,
            organization_id=self.organization_id,
            user_id=user_id,
            action=security_event.get('action', 'access'),
            changes=security_event.get('changes', {}),
            metadata={
                'ip_address': security_event.get('ip_address'),
                'user_agent': security_event.get('user_agent'),
                'endpoint': security_event.get('endpoint'),
                'method': security_event.get('method'),
                'status_code': security_event.get('status_code')
            },
            confidence_score=None,
            risk_level=security_event.get('risk_level', 'medium')
        )
        
        return self._store_audit_event(audit_event)
    
    def get_audit_timeline(self, days_back: int = 30, event_types: Optional[List[str]] = None) -> List[Dict]:
        """Get comprehensive audit timeline for organization"""
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        # Build event type filter
        event_type_filter = ""
        if event_types:
            event_type_filter = f"AND event_type = ANY(ARRAY{event_types})"
        
        result = db.session.execute(text(f"""
            SELECT 
                event_id,
                timestamp,
                event_type,
                entity_type,
                entity_id,
                user_id,
                action,
                changes,
                metadata,
                confidence_score,
                risk_level,
                u.username as performed_by
            FROM audit_timeline at
            LEFT JOIN "user" u ON at.user_id = u.id
            WHERE at.organization_id = :org_id
            AND at.timestamp >= :cutoff_date
            {event_type_filter}
            ORDER BY at.timestamp DESC
            LIMIT 1000
        """), {
            'org_id': self.organization_id,
            'cutoff_date': cutoff_date
        })
        
        timeline_events = []
        for row in result.fetchall():
            row_dict = dict(row._mapping)
            timeline_events.append(row_dict)
        
        return timeline_events
    
    def get_entity_audit_history(self, entity_type: str, entity_id: int) -> List[Dict]:
        """Get complete audit history for a specific entity"""
        
        result = db.session.execute(text("""
            SELECT 
                event_id,
                timestamp,
                event_type,
                action,
                changes,
                metadata,
                confidence_score,
                risk_level,
                u.username as performed_by
            FROM audit_timeline at
            LEFT JOIN "user" u ON at.user_id = u.id
            WHERE at.organization_id = :org_id
            AND at.entity_type = :entity_type
            AND at.entity_id = :entity_id
            ORDER BY at.timestamp DESC
        """), {
            'org_id': self.organization_id,
            'entity_type': entity_type,
            'entity_id': entity_id
        })
        
        history = []
        for row in result.fetchall():
            row_dict = dict(row._mapping)
            history.append(row_dict)
        
        return history
    
    def get_audit_analytics(self, days_back: int = 7) -> Dict:
        """Get audit analytics and insights"""
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        # Event counts by type
        result = db.session.execute(text("""
            SELECT 
                event_type,
                COUNT(*) as event_count,
                AVG(confidence_score) as avg_confidence,
                COUNT(CASE WHEN risk_level = 'high' THEN 1 END) as high_risk_count,
                COUNT(CASE WHEN risk_level = 'critical' THEN 1 END) as critical_risk_count
            FROM audit_timeline
            WHERE organization_id = :org_id
            AND timestamp >= :cutoff_date
            GROUP BY event_type
        """), {
            'org_id': self.organization_id,
            'cutoff_date': cutoff_date
        })
        
        analytics = {
            'period_days': days_back,
            'event_summary': {},
            'risk_distribution': {
                'low': 0, 'medium': 0, 'high': 0, 'critical': 0
            },
            'confidence_trends': {}
        }
        
        for row in result.fetchall():
            row_dict = dict(row._mapping)
            analytics['event_summary'][row_dict['event_type']] = {
                'count': row_dict['event_count'],
                'avg_confidence': row_dict['avg_confidence'] or 0.0,
                'high_risk_count': row_dict['high_risk_count'],
                'critical_risk_count': row_dict['critical_risk_count']
            }
        
        # Risk distribution
        risk_result = db.session.execute(text("""
            SELECT risk_level, COUNT(*) as count
            FROM audit_timeline
            WHERE organization_id = :org_id
            AND timestamp >= :cutoff_date
            GROUP BY risk_level
        """), {
            'org_id': self.organization_id,
            'cutoff_date': cutoff_date
        })
        
        for row in risk_result.fetchall():
            analytics['risk_distribution'][row.risk_level] = row.count
        
        return analytics
    
    def _store_audit_event(self, audit_event: AuditEvent) -> str:
        """Store audit event in timeline table"""
        
        try:
            # Store in audit_timeline table
            db.session.execute(text("""
                INSERT INTO audit_timeline (
                    event_id, timestamp, event_type, entity_type, entity_id,
                    organization_id, user_id, action, changes, metadata,
                    confidence_score, risk_level
                ) VALUES (
                    :event_id, :timestamp, :event_type, :entity_type, :entity_id,
                    :organization_id, :user_id, :action, :changes, :metadata,
                    :confidence_score, :risk_level
                )
            """), {
                'event_id': audit_event.event_id,
                'timestamp': audit_event.timestamp,
                'event_type': audit_event.event_type,
                'entity_type': audit_event.entity_type,
                'entity_id': audit_event.entity_id,
                'organization_id': audit_event.organization_id,
                'user_id': audit_event.user_id,
                'action': audit_event.action,
                'changes': json.dumps(audit_event.changes),
                'metadata': json.dumps(audit_event.metadata),
                'confidence_score': audit_event.confidence_score,
                'risk_level': audit_event.risk_level
            })
            
            # Also store in existing audit_log table for compatibility
            db.session.execute(text("""
                INSERT INTO audit_log (
                    user_id, action, table_name, record_id, 
                    old_values, new_values, timestamp, organization_id
                ) VALUES (
                    :user_id, :action, :table_name, :record_id,
                    :old_values, :new_values, :timestamp, :organization_id
                )
            """), {
                'user_id': audit_event.user_id,
                'action': f"{audit_event.event_type}_{audit_event.action}",
                'table_name': audit_event.entity_type,
                'record_id': audit_event.entity_id,
                'old_values': json.dumps({}),
                'new_values': json.dumps(audit_event.changes),
                'timestamp': audit_event.timestamp,
                'organization_id': audit_event.organization_id
            })
            
            db.session.commit()
            logger.info(f"Stored audit event: {audit_event.event_id}")
            return audit_event.event_id
            
        except Exception as e:
            logger.error(f"Error storing audit event: {e}")
            db.session.rollback()
            raise
    
    def _generate_event_id(self, event_type: str, entity_id: int) -> str:
        """Generate unique event ID"""
        timestamp = datetime.now().isoformat()
        data = f"{event_type}_{entity_id}_{timestamp}_{self.organization_id}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def _calculate_risk_level(self, confidence: float, event_type: str) -> str:
        """Calculate risk level based on confidence and event type"""
        
        if event_type == 'extraction':
            if confidence >= 0.95:
                return 'low'
            elif confidence >= 0.80:
                return 'medium'
            elif confidence >= 0.60:
                return 'high'
            else:
                return 'critical'
        
        elif event_type == 'reconciliation':
            if confidence >= 0.95:
                return 'low'
            elif confidence >= 0.70:
                return 'medium'
            else:
                return 'high'
        
        return 'medium'  # Default
    
    def _assess_rule_change_risk(self, rule_changes: Dict, action: str) -> str:
        """Assess risk level for validation rule changes"""
        
        if action == 'delete':
            return 'high'
        elif action == 'create':
            return 'medium'
        else:  # update
            # Check if critical thresholds are being modified
            critical_fields = ['amount_threshold', 'confidence_threshold', 'auto_approve']
            if any(field in rule_changes for field in critical_fields):
                return 'high'
            return 'medium'
    
    def _count_affected_transactions(self, rule_id: int) -> int:
        """Count transactions potentially affected by rule change"""
        
        try:
            result = db.session.execute(text("""
                SELECT COUNT(*) 
                FROM financial_transaction ft
                JOIN bank_statement bs ON ft.bank_statement_id = bs.id
                WHERE bs.organization_id = :org_id
                AND ft.created_at >= NOW() - INTERVAL '30 days'
            """), {'org_id': self.organization_id})
            
            return result.fetchone()[0]
        except:
            return 0
    
    def _calculate_rule_complexity(self, rule_changes: Dict) -> float:
        """Calculate complexity score for rule changes"""
        
        complexity = 0.0
        
        # Basic complexity factors
        complexity += len(rule_changes) * 0.1  # Number of changed fields
        
        # Specific field complexity weights
        if 'regex_pattern' in rule_changes:
            complexity += 0.3
        if 'amount_threshold' in rule_changes:
            complexity += 0.2
        if 'auto_approve' in rule_changes:
            complexity += 0.4
        
        return min(complexity, 1.0)  # Cap at 1.0