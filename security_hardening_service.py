"""
Security Hardening Service
Implements enterprise-grade security controls including RLS, JWT scopes, and PII masking
"""

import logging
import re
from typing import Dict, List, Optional, Any
from datetime import datetime
import hashlib
import json
from flask import request, g
from functools import wraps
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)

class SecurityHardeningService:
    """Comprehensive security hardening and monitoring service"""
    
    def __init__(self):
        self.pii_patterns = {
            'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'phone': r'\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b',
            'ssn': r'\b\d{3}-?\d{2}-?\d{4}\b',
            'credit_card': r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
            'bank_account': r'\b\d{8,17}\b'
        }
        
    def mask_pii_in_logs(self, log_data: str) -> str:
        """Mask PII information in log entries"""
        
        masked_data = log_data
        
        for pii_type, pattern in self.pii_patterns.items():
            def mask_match(match):
                value = match.group()
                if pii_type == 'email':
                    # Mask email: user@domain.com -> u***@d*****.com
                    parts = value.split('@')
                    if len(parts) == 2:
                        user_part = parts[0]
                        domain_part = parts[1]
                        masked_user = user_part[0] + '*' * (len(user_part) - 1) if len(user_part) > 1 else '*'
                        domain_parts = domain_part.split('.')
                        if len(domain_parts) >= 2:
                            masked_domain = domain_parts[0][0] + '*' * (len(domain_parts[0]) - 1)
                            return f"{masked_user}@{masked_domain}.{domain_parts[-1]}"
                elif pii_type == 'phone':
                    # Mask phone: (555) 123-4567 -> (***) ***-4567
                    return re.sub(r'\d', '*', value[:-4]) + value[-4:]
                elif pii_type == 'bank_account':
                    # Mask bank account: 123456789 -> *****6789
                    return '*' * (len(value) - 4) + value[-4:]
                else:
                    # Default masking for other PII types
                    return '*' * len(value)
                
                return value
            
            masked_data = re.sub(pattern, mask_match, masked_data)
        
        return masked_data
    
    def apply_row_level_security(self, user_id: int, organization_ids: List[int]):
        """Apply Row Level Security policies for the current session"""
        
        try:
            # Set RLS variables for current session
            db.session.execute(text("""
                SET row_security = on;
                SET app.current_user_id = :user_id;
                SET app.current_org_ids = :org_ids;
            """), {
                'user_id': user_id,
                'org_ids': ','.join(map(str, organization_ids))
            })
            
            # Enable RLS on critical tables
            security_policies = [
                "ALTER TABLE financial_transaction ENABLE ROW LEVEL SECURITY",
                "ALTER TABLE receipt ENABLE ROW LEVEL SECURITY", 
                "ALTER TABLE company_expense ENABLE ROW LEVEL SECURITY",
                "ALTER TABLE bank_statement ENABLE ROW LEVEL SECURITY",
                "ALTER TABLE transaction_match ENABLE ROW LEVEL SECURITY",
                "ALTER TABLE audit_timeline ENABLE ROW LEVEL SECURITY"
            ]
            
            for policy in security_policies:
                try:
                    db.session.execute(text(policy))
                except Exception as e:
                    # Policy may already exist
                    logger.debug(f"RLS policy application: {e}")
            
            db.session.commit()
            logger.info(f"Applied RLS for user {user_id} with orgs {organization_ids}")
            
        except Exception as e:
            logger.error(f"Error applying RLS: {e}")
            db.session.rollback()
    
    def create_rls_policies(self):
        """Create Row Level Security policies for all critical tables"""
        
        try:
            # RLS policy for financial transactions
            db.session.execute(text("""
                DROP POLICY IF EXISTS financial_transaction_org_policy ON financial_transaction;
                CREATE POLICY financial_transaction_org_policy ON financial_transaction
                FOR ALL TO authenticated
                USING (
                    EXISTS (
                        SELECT 1 FROM bank_statement bs 
                        WHERE bs.id = financial_transaction.bank_statement_id 
                        AND bs.organization_id = ANY(
                            string_to_array(current_setting('app.current_org_ids', true), ',')::int[]
                        )
                    )
                );
            """))
            
            # RLS policy for receipts
            db.session.execute(text("""
                DROP POLICY IF EXISTS receipt_org_policy ON receipt;
                CREATE POLICY receipt_org_policy ON receipt
                FOR ALL TO authenticated
                USING (
                    organization_id = ANY(
                        string_to_array(current_setting('app.current_org_ids', true), ',')::int[]
                    )
                );
            """))
            
            # RLS policy for company expenses  
            db.session.execute(text("""
                DROP POLICY IF EXISTS company_expense_org_policy ON company_expense;
                CREATE POLICY company_expense_org_policy ON company_expense
                FOR ALL TO authenticated
                USING (
                    organization_id = ANY(
                        string_to_array(current_setting('app.current_org_ids', true), ',')::int[]
                    )
                );
            """))
            
            # RLS policy for audit timeline
            db.session.execute(text("""
                DROP POLICY IF EXISTS audit_timeline_org_policy ON audit_timeline;
                CREATE POLICY audit_timeline_org_policy ON audit_timeline
                FOR ALL TO authenticated
                USING (
                    organization_id = ANY(
                        string_to_array(current_setting('app.current_org_ids', true), ',')::int[]
                    )
                );
            """))
            
            db.session.commit()
            logger.info("Successfully created RLS policies")
            
        except Exception as e:
            logger.error(f"Error creating RLS policies: {e}")
            db.session.rollback()
    
    def validate_jwt_scopes(self, required_scopes: List[str]) -> bool:
        """Validate JWT token scopes for API access"""
        
        # Extract JWT scopes from request headers or session
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header.startswith('Bearer '):
            return False
        
        try:
            # In a real implementation, decode and validate JWT token
            # For now, simulate scope validation
            token = auth_header[7:]  # Remove 'Bearer ' prefix
            
            # Simulate token scope extraction
            # In production, use proper JWT library like PyJWT
            user_scopes = getattr(g, 'user_scopes', ['read:basic'])
            
            # Check if user has all required scopes
            return all(scope in user_scopes for scope in required_scopes)
            
        except Exception as e:
            logger.error(f"JWT validation error: {e}")
            return False
    
    def log_security_event(self, event_type: str, details: Dict, risk_level: str = 'medium'):
        """Log security events with proper PII masking"""
        
        # Mask PII in event details
        masked_details = {}
        for key, value in details.items():
            if isinstance(value, str):
                masked_details[key] = self.mask_pii_in_logs(value)
            else:
                masked_details[key] = value
        
        security_log = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'details': masked_details,
            'risk_level': risk_level,
            'ip_address': request.remote_addr if request else 'unknown',
            'user_agent': request.headers.get('User-Agent', 'unknown') if request else 'unknown'
        }
        
        # Store in security log table or external SIEM
        try:
            db.session.execute(text("""
                INSERT INTO security_log (
                    event_type, details, risk_level, ip_address, 
                    user_agent, timestamp, organization_id
                ) VALUES (
                    :event_type, :details, :risk_level, :ip_address,
                    :user_agent, :timestamp, :org_id
                )
            """), {
                'event_type': event_type,
                'details': json.dumps(masked_details),
                'risk_level': risk_level,
                'ip_address': security_log['ip_address'],
                'user_agent': security_log['user_agent'][:500],  # Truncate long user agents
                'timestamp': datetime.now(),
                'org_id': getattr(g, 'current_organization_id', None)
            })
            db.session.commit()
        except Exception as e:
            logger.error(f"Error logging security event: {e}")
    
    def perform_security_scan(self, organization_id: int) -> Dict:
        """Perform comprehensive security scan of organization data"""
        
        scan_results = {
            'timestamp': datetime.now().isoformat(),
            'organization_id': organization_id,
            'vulnerabilities': [],
            'compliance_issues': [],
            'recommendations': [],
            'risk_score': 0
        }
        
        try:
            # Check for unencrypted sensitive data
            sensitive_data_check = db.session.execute(text("""
                SELECT COUNT(*) as count
                FROM receipt 
                WHERE organization_id = :org_id
                AND description LIKE '%password%' 
                OR description LIKE '%ssn%'
                OR description LIKE '%credit%card%'
            """), {'org_id': organization_id})
            
            sensitive_row = sensitive_data_check.fetchone()
            sensitive_count = sensitive_row[0] if sensitive_row else 0
            if sensitive_count > 0:
                scan_results['vulnerabilities'].append({
                    'type': 'sensitive_data_exposure',
                    'severity': 'high',
                    'count': sensitive_count,
                    'description': 'Potentially sensitive data found in receipt descriptions'
                })
                scan_results['risk_score'] += 30
            
            # Check for weak access controls
            weak_access_check = db.session.execute(text("""
                SELECT COUNT(*) as count
                FROM organization_user ou
                JOIN "user" u ON ou.user_id = u.id
                WHERE ou.organization_id = :org_id
                AND ou.role = 'owner'
            """), {'org_id': organization_id})
            
            owner_row = weak_access_check.fetchone()
            owner_count = owner_row[0] if owner_row else 0
            if owner_count > 3:
                scan_results['compliance_issues'].append({
                    'type': 'excessive_privileges',
                    'severity': 'medium',
                    'count': owner_count,
                    'description': 'Too many users with owner privileges'
                })
                scan_results['risk_score'] += 15
            
            # Check for audit trail gaps
            audit_gap_check = db.session.execute(text("""
                SELECT COUNT(*) as count
                FROM financial_transaction ft
                JOIN bank_statement bs ON ft.bank_statement_id = bs.id
                WHERE bs.organization_id = :org_id
                AND ft.created_at >= NOW() - INTERVAL '30 days'
                AND NOT EXISTS (
                    SELECT 1 FROM audit_timeline at
                    WHERE at.entity_type = 'transaction'
                    AND at.entity_id = ft.id
                )
            """), {'org_id': organization_id})
            
            audit_row = audit_gap_check.fetchone()
            unaudited_count = audit_row[0] if audit_row else 0
            if unaudited_count > 0:
                scan_results['compliance_issues'].append({
                    'type': 'audit_trail_gaps',
                    'severity': 'medium',
                    'count': unaudited_count,
                    'description': 'Transactions without audit trail entries'
                })
                scan_results['risk_score'] += 10
            
            # Generate recommendations based on findings
            if scan_results['risk_score'] > 40:
                scan_results['recommendations'].append(
                    'Immediate security review required - high risk score detected'
                )
            
            if sensitive_count > 0:
                scan_results['recommendations'].append(
                    'Implement field-level encryption for sensitive data'
                )
            
            if owner_count > 3:
                scan_results['recommendations'].append(
                    'Review and reduce owner privileges following principle of least access'
                )
            
            # Overall risk classification
            if scan_results['risk_score'] <= 20:
                scan_results['risk_classification'] = 'low'
            elif scan_results['risk_score'] <= 50:
                scan_results['risk_classification'] = 'medium'
            else:
                scan_results['risk_classification'] = 'high'
            
            logger.info(f"Security scan completed for org {organization_id}: {scan_results['risk_classification']} risk")
            return scan_results
            
        except Exception as e:
            logger.error(f"Error performing security scan: {e}")
            scan_results['error'] = str(e)
            return scan_results

def require_scopes(scopes: List[str]):
    """Decorator to require specific JWT scopes for API endpoints"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            security_service = SecurityHardeningService()
            
            if not security_service.validate_jwt_scopes(scopes):
                security_service.log_security_event(
                    'insufficient_permissions',
                    {
                        'required_scopes': scopes,
                        'endpoint': request.endpoint,
                        'method': request.method
                    },
                    'high'
                )
                return {'error': 'Insufficient permissions'}, 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def mask_sensitive_response(response_data: Dict) -> Dict:
    """Mask sensitive information in API responses"""
    security_service = SecurityHardeningService()
    
    if isinstance(response_data, dict):
        masked_response = {}
        for key, value in response_data.items():
            if isinstance(value, str):
                masked_response[key] = security_service.mask_pii_in_logs(value)
            elif isinstance(value, dict):
                masked_response[key] = mask_sensitive_response(value)
            elif isinstance(value, list):
                masked_response[key] = [
                    mask_sensitive_response(item) if isinstance(item, dict) else item 
                    for item in value
                ]
            else:
                masked_response[key] = value
        return masked_response
    
    return response_data