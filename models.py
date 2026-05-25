from app import db
from datetime import datetime, date, timedelta
import json
import logging
from flask_login import UserMixin
from enum import Enum
from onboarding_models import OnboardingProgress


class AuditLog(db.Model):
    """Model for tracking data changes."""
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)  # 'receipt', 'receipt_item', etc.
    entity_id = db.Column(db.Integer, nullable=False)       # ID of the entity being modified
    action = db.Column(db.String(20), nullable=False)       # 'INSERT', 'UPDATE', 'DELETE'
    changed_fields = db.Column(db.JSON, nullable=False)     # JSON object of field_name: {old_value, new_value}
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('audit_logs', lazy=True))
    
    def to_dict(self):
        """Convert the audit log to a dictionary."""
        result = {
            'id': self.id,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'action': self.action,
            'changed_fields': self.changed_fields,
            'timestamp': self.timestamp.isoformat(),
            'ip_address': self.ip_address or '',
            'user_agent': self.user_agent or ''
        }
        
        if self.user_id and self.user:
            result['user_id'] = self.user_id
            result['user_name'] = self.user.name
            result['user_email'] = self.user.email
            
        return result

class InvoiceStatus(Enum):
    DRAFT = "draft"
    SENT = "sent"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"

class PaymentMethod(Enum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CHECK = "check"
    CREDIT_CARD = "credit_card"
    PAYPAL = "paypal"
    OTHER = "other"

class UserRole(Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

class Organization(db.Model):
    """Organization model for multi-tenant functionality."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    logo_path = db.Column(db.String(255), nullable=True)  # Legacy field, kept for backward compatibility
    logo_s3_key = db.Column(db.String(255), nullable=True)  # S3 key for stored logo
    website = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.Text, nullable=True)
    tax_registration_number = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Branding and customization
    primary_color = db.Column(db.String(20), nullable=True, default="#4a6da7")
    secondary_color = db.Column(db.String(20), nullable=True, default="#f5f8ff")
    email_footer_text = db.Column(db.Text, nullable=True)
    
    # Bank statement validation rules (JSON stored as text)
    validation_rules = db.Column(db.Text, nullable=True)

    # Per-organization OCR provider override. NULL = use the global OCR_PROVIDER
    # env var. Allowed values: "gemini", "glm".
    ocr_provider = db.Column(db.String(20), nullable=True)

    # Relationships
    users = db.relationship('OrganizationUser', back_populates='organization', lazy=True, cascade='all, delete-orphan')
    receipts = db.relationship('Receipt', backref='organization', lazy=True)
    clients = db.relationship('Client', backref='organization', lazy=True)
    invoices = db.relationship('Invoice', backref='organization', lazy=True)
    bank_accounts = db.relationship('BankAccount', backref='organization', lazy=True)
    
    @property
    def logo_url(self):
        """Generate the URL for the logo image."""
        if self.logo_s3_key:
            from s3_storage import get_s3_url
            try:
                return get_s3_url(self.logo_s3_key)
            except Exception:
                pass
        return None
    
    def to_dict(self):
        """Convert the organization to a dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'logo_path': self.logo_path or '',
            'logo_s3_key': self.logo_s3_key or '',
            'logo_url': self.logo_url or '',
            'website': self.website or '',
            'email': self.email or '',
            'phone': self.phone or '',
            'address': self.address or '',
            'tax_registration_number': self.tax_registration_number or '',
            'primary_color': self.primary_color,
            'secondary_color': self.secondary_color,
            'email_footer_text': self.email_footer_text or '',
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

class OrganizationUser(db.Model):
    """Join table for users and organizations with role information."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=UserRole.MEMBER.value)
    is_default = db.Column(db.Boolean, default=False)  # Is this the user's default organization
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', back_populates='users')
    user = db.relationship('User', back_populates='organizations')
    
    def to_dict(self):
        """Convert the organization user to a dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'organization_id': self.organization_id,
            'role': self.role,
            'is_default': self.is_default,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'organization': self.organization.to_dict(),
            'user': {
                'id': self.user.id,
                'name': self.user.name,
                'email': self.user.email
            }
        }

class FriendInvitation(db.Model):
    """Model for storing friend invitations."""
    id = db.Column(db.Integer, primary_key=True)
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    token = db.Column(db.String(255), nullable=False, unique=True)
    accepted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    personal_message = db.Column(db.Text, nullable=True)
    # Track who accepted the invitation and when
    accepted_at = db.Column(db.DateTime, nullable=True)
    accepted_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # New fields for tracking conversions and rewards
    converted_to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reward_days_granted = db.Column(db.Integer, default=0)
    reward_granted_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    invited_by = db.relationship('User', foreign_keys=[invited_by_user_id], backref='sent_friend_invitations')
    accepted_by = db.relationship('User', foreign_keys=[accepted_by_user_id], backref='accepted_friend_invitations')
    converted_to_user = db.relationship('User', foreign_keys=[converted_to_user_id], backref='converted_from_invitation')
    
    def is_expired(self):
        """Check if the invitation is expired."""
        return datetime.utcnow() > self.expires_at
    
    def to_dict(self):
        """Convert the invitation to a dictionary."""
        result = {
            'id': self.id,
            'invited_by_user_id': self.invited_by_user_id,
            'invited_by_name': self.invited_by.name,
            'email': self.email,
            'token': self.token,
            'accepted': self.accepted,
            'created_at': self.created_at.isoformat(),
            'expires_at': self.expires_at.isoformat(),
            'is_expired': self.is_expired(),
            'personal_message': self.personal_message
        }
        
        if self.accepted_at:
            result['accepted_at'] = self.accepted_at.isoformat()
        
        if self.accepted_by_user_id and self.accepted_by:
            result['accepted_by_user_id'] = self.accepted_by_user_id
            result['accepted_by_name'] = self.accepted_by.name
            
        # Include new fields for tracking conversions and rewards
        if self.converted_to_user_id and self.converted_to_user:
            result['converted_to_user_id'] = self.converted_to_user_id
            result['converted_to_user_name'] = self.converted_to_user.name
            
        if self.reward_days_granted:
            result['reward_days_granted'] = self.reward_days_granted
            
        if self.reward_granted_at:
            result['reward_granted_at'] = self.reward_granted_at.isoformat()
            
        return result


class TrustActivity(db.Model):
    """Model for tracking activities that contribute to trust level."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    activity_type = db.Column(db.String(50), nullable=False)
    points = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationship
    user = db.relationship('User', backref=db.backref('trust_activities', lazy='dynamic'))
    
    def to_dict(self):
        """Convert the trust activity to a dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else '',
            'activity_type': self.activity_type,
            'points': self.points,
            'description': self.description or '',
            'created_at': self.created_at.isoformat()
        }


class TrustRanking(db.Model):
    """Model for tracking monthly trust rankings for users who opted in."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ranking_month = db.Column(db.Date, nullable=False)
    trust_points = db.Column(db.Integer, nullable=False, default=0)
    rank_position = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationship
    user = db.relationship('User', backref=db.backref('trust_rankings', lazy='dynamic'))
    
    # Composite unique constraint
    __table_args__ = (db.UniqueConstraint('user_id', 'ranking_month', name='unique_user_monthly_ranking'),)
    
    def to_dict(self):
        """Convert the trust ranking to a dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else '',
            'ranking_month': self.ranking_month.isoformat(),
            'trust_points': self.trust_points,
            'rank_position': self.rank_position,
            'created_at': self.created_at.isoformat()
        }


class OrganizationInvitation(db.Model):
    """Model for storing organization invitations."""
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=UserRole.MEMBER.value)
    token = db.Column(db.String(255), nullable=False, unique=True)
    accepted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    
    # Relationships
    organization = db.relationship('Organization')
    # Define relationship with User model without backreference (will use sent_invitations in User model)
    invited_by = db.relationship('User', foreign_keys=[invited_by_user_id], overlaps="sender")
    
    def is_expired(self):
        """Check if the invitation is expired."""
        return datetime.utcnow() > self.expires_at
    
    def to_dict(self):
        """Convert the invitation to a dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'organization_name': self.organization.name,
            'invited_by_user_id': self.invited_by_user_id,
            'invited_by_name': self.invited_by.name,
            'email': self.email,
            'role': self.role,
            'token': self.token,
            'accepted': self.accepted,
            'created_at': self.created_at.isoformat(),
            'expires_at': self.expires_at.isoformat(),
            'is_expired': self.is_expired()
        }

class User(UserMixin, db.Model):
    """User model for authentication."""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=True)
    profile_pic = db.Column(db.String(255), nullable=True)
    # Password authentication
    password_hash = db.Column(db.String(256), nullable=True)
    # Global role (superadmin access)
    role = db.Column(db.String(20), nullable=False, default='user')  # 'user', 'admin', etc.
    # Social login information
    social_id = db.Column(db.String(255), unique=True, nullable=True)
    social_provider = db.Column(db.String(50), nullable=True)  # 'google', 'facebook', etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Email verification fields
    is_email_verified = db.Column(db.Boolean, default=False)
    email_verification_token = db.Column(db.String(100), nullable=True)
    email_verification_salt = db.Column(db.String(100), nullable=True)
    email_verification_sent_at = db.Column(db.DateTime, nullable=True)
    
    # Onboarding status - must complete before using main features
    onboarding_completed = db.Column(db.Boolean, default=False)

    # Persona: 'sl_foreign_income' routes to the Remittance Ledger; NULL = legacy/default flows.
    # Added Wave A 2026-05-16 per FIESTA_USEFULNESS_REVIEW.md council synthesis.
    persona = db.Column(db.String(50), nullable=True)

    # S2 signup (Wave 1, 2026-05-20): the version of ToS / Privacy the user
    # accepted at signup. Persisted so a) we can prove acceptance later, b) we
    # can re-prompt for re-acceptance when a future version materially changes
    # rights. NULL = legacy users (pre-S2 `/register` route) or skipped checkbox.
    # Format: free-text "vMAJOR.MINOR" or "vMAJOR.MINOR-draft" until counsel review.
    tos_accepted_version = db.Column(db.String(32), nullable=True)
    tos_accepted_at = db.Column(db.DateTime, nullable=True)
    privacy_accepted_version = db.Column(db.String(32), nullable=True)
    privacy_accepted_at = db.Column(db.DateTime, nullable=True)

    # S1 triage (Wave 1, 2026-05-20): persists the 3 post-signup fact-find
    # answers. Shape: {earning_source, earning_vehicle[], filing_history,
    # completed_at}. Nullable so legacy users + mid-flow signups don't break.
    # Column added by add_triage_answers_to_user.py (idempotent).
    triage_answers = db.Column(db.JSON, nullable=True)

    # C8 F8.7 — Real last-login timestamp.
    # Added by migrations/add_last_login_at.py (idempotent ALTER TABLE).
    # Set on every successful login_user() call in app.py (email_login,
    # verify_email, Google OAuth, Facebook OAuth handlers).
    # Backfill from events table (auth_login event) is part of the migration.
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # F5 GDPR/PDPA soft-delete (Tier D5, 2026-05-24).
    # Set by POST /api/me/delete (data_rights_routes.py). Non-NULL means the
    # account is soft-deleted: PII anonymised (name/email rewritten to
    # `deleted_user_<id>@deleted.fiesta`), subscriptions cancelled, but
    # financial rows retained per SL IRA s.120 (privacy_policy.html §4).
    # Added by migrations/add_user_deleted_at.py (idempotent ALTER TABLE).
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    # Tier D6 / A2 — paid-acquisition first-touch attribution.
    # Lifted from the Flask session by utm_capture.persist_to_user() at signup
    # time. Idempotent — set once at signup, never overwritten thereafter.
    # Added by migrations/add_utm_columns_to_user.py (idempotent ALTER TABLE).
    # Partial index on utm_source supports channel-breakdown analytics.
    utm_source = db.Column(db.String(128), nullable=True, index=True)
    utm_medium = db.Column(db.String(128), nullable=True)
    utm_campaign = db.Column(db.String(128), nullable=True)
    utm_term = db.Column(db.String(128), nullable=True)
    utm_content = db.Column(db.String(128), nullable=True)

    # Trust level and rewards
    subscription_status = db.Column(db.String(30), nullable=False, default='free_trial')
    access_expiration_date = db.Column(db.DateTime, nullable=True)
    successful_invites_count = db.Column(db.Integer, nullable=False, default=0)
    earned_access_days = db.Column(db.Integer, nullable=False, default=0)
    invitations_sent_today = db.Column(db.Integer, nullable=False, default=0)
    last_invitation_date = db.Column(db.Date, nullable=True)
    trust_level = db.Column(db.Integer, nullable=False, default=0)
    show_in_trust_ranking = db.Column(db.Boolean, nullable=False, default=False)
    trust_points = db.Column(db.Integer, nullable=False, default=0)
    trust_ranking_consent_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    organizations = db.relationship('OrganizationUser', back_populates='user', lazy=True, cascade='all, delete-orphan')
    receipts = db.relationship('Receipt', backref='user', lazy=True)
    income_details = db.relationship('UserIncome', backref='user', lazy=True, uselist=False)
    clients = db.relationship('Client', backref='user', lazy=True)
    invoices = db.relationship('Invoice', backref='user', lazy=True)
    bank_accounts = db.relationship('BankAccount', backref='user', lazy=True)
    sent_invitations = db.relationship('OrganizationInvitation', 
                                 foreign_keys='OrganizationInvitation.invited_by_user_id', 
                                 backref='sender', 
                                 lazy=True,
                                 overlaps="invited_by")
    # This relationship is defined in onboarding_models.py with backref
    
    def is_admin(self):
        """Check if user has global admin role."""
        return self.role == 'admin'

    def has_role(self, role):
        """Check if user has a specific global role."""
        return self.role == role

    def promote_to_admin(self, *, granted_by=None, reason=None):
        """Promote this user to global admin.

        X9 F8.1: the walkthrough seed script did `u.is_admin = spec["admin"]`,
        which silently failed because `is_admin` is a METHOD on this model, not
        a column. Setting it sets a transient attribute, the role column stays
        at 'user', and `is_admin()` returns False for the "admin" seed user.
        Use this helper instead — it writes to `role` and logs the change.

        `granted_by` is the User performing the action (None for system seeds).

        Stage C4: also writes a row to the AuditLog table so admin grants are
        durable in the database, not just stdout logs. AuditLog write is
        best-effort — if it fails (table missing, FK violation), the role
        change still commits via the caller's session.
        """
        previous = self.role
        self.role = 'admin'
        try:
            import logging
            logging.info(
                "admin_role_granted user_id=%s email=%s previous_role=%s granted_by=%s reason=%s",
                self.id, self.email, previous,
                getattr(granted_by, 'id', None), reason,
            )
        except Exception:
            pass

        # Stage C4 F8.1: durable AuditLog entry for admin grants.
        try:
            granted_by_id = getattr(granted_by, "id", None)
            audit = AuditLog(
                entity_type="user",
                entity_id=self.id,
                action="UPDATE",
                changed_fields={
                    "role": {"old_value": previous, "new_value": "admin"},
                    "reason": reason,
                    "operation": "promote_to_admin",
                },
                user_id=granted_by_id,
            )
            db.session.add(audit)
        except Exception:
            # Best-effort — don't break the promotion if AuditLog write fails.
            pass

        # Stage C4: also keep the forward-looking ``user.is_admin`` BOOLEAN
        # column (added by ``add_admin_and_stripe_columns_to_user.py``) in
        # sync with ``role`` so the two readers don't drift. Column isn't
        # declared on the model — we touch it via raw SQL on the session.
        try:
            from sqlalchemy import text as _text
            db.session.execute(
                _text('UPDATE "user" SET is_admin = TRUE WHERE id = :uid'),
                {"uid": self.id},
            )
        except Exception:
            pass

        return self

    def demote_from_admin(self, *, revoked_by=None, reason=None):
        """Revoke global admin role and drop the user back to 'user'."""
        previous = self.role
        self.role = 'user'
        try:
            import logging
            logging.info(
                "admin_role_revoked user_id=%s email=%s previous_role=%s revoked_by=%s reason=%s",
                self.id, self.email, previous,
                getattr(revoked_by, 'id', None), reason,
            )
        except Exception:
            pass
        # Stage C4: keep the BOOLEAN column in sync on demotion too.
        try:
            from sqlalchemy import text as _text
            db.session.execute(
                _text('UPDATE "user" SET is_admin = FALSE WHERE id = :uid'),
                {"uid": self.id},
            )
        except Exception:
            pass
        return self
    
    def get_default_organization(self):
        """Get the user's default organization."""
        org_user = OrganizationUser.query.filter_by(user_id=self.id, is_default=True).first()
        if org_user:
            return org_user.organization
            
        # If no default is set but user has organizations, return the first
        org_user = OrganizationUser.query.filter_by(user_id=self.id).first()
        if org_user:
            return org_user.organization
            
    def get_daily_invitation_limit(self):
        """Calculate the daily invitation limit based on trust level."""
        # Trust level 0: 3 invitations per day
        # Trust level 1: 5 invitations per day
        # Trust level 2: 10 invitations per day
        # Trust level 3: 20 invitations per day
        limits = {
            0: 3,
            1: 5,
            2: 10,
            3: 20
        }
        return limits.get(self.trust_level, 3)  # Default to 3 if trust level is invalid
    
    def reset_invitation_counter(self):
        """Reset the daily invitation counter if last invitation was from a previous day."""
        today = date.today()
        if self.last_invitation_date is None or self.last_invitation_date < today:
            self.invitations_sent_today = 0
            self.last_invitation_date = today
            db.session.commit()
            
    def recalculate_trust_level(self):
        """Recalculate user's trust level based on trust points.
        
        Trust level thresholds:
        - Level 0: 0-99 points (default for new users)
        - Level 1: 100-499 points
        - Level 2: 500-999 points
        - Level 3: 1000+ points
        """
        if self.trust_points >= 1000:
            new_level = 3
        elif self.trust_points >= 500:
            new_level = 2
        elif self.trust_points >= 100:
            new_level = 1
        else:
            new_level = 0
            
        # Only update if level changed
        if new_level != self.trust_level:
            self.trust_level = new_level
            db.session.commit()
            
        return self.trust_level
    
    def get_available_invitations(self):
        """Get the number of invitations still available today."""
        self.reset_invitation_counter()
        daily_limit = self.get_daily_invitation_limit()
        return max(0, daily_limit - self.invitations_sent_today)
        
    def get_trust_activity_summary(self):
        """Get a summary of the user's trust activities."""
        from sqlalchemy import func
        
        # Get count and points by activity type
        activity_stats = db.session.query(
            TrustActivity.activity_type,
            func.count(TrustActivity.id).label('count'),
            func.sum(TrustActivity.points).label('total_points')
        ).filter(
            TrustActivity.user_id == self.id
        ).group_by(
            TrustActivity.activity_type
        ).all()
        
        # Format into a dictionary
        summary = {
            'total_points': self.trust_points,
            'trust_level': self.trust_level,
            'activities': {}
        }
        
        for activity in activity_stats:
            summary['activities'][activity.activity_type] = {
                'count': activity.count,
                'points': activity.total_points
            }
            
        # Calculate points needed for next level
        if self.trust_level < 3:
            next_level_thresholds = {
                0: 100,    # Need 100 to reach level 1
                1: 500,    # Need 500 to reach level 2
                2: 1000,   # Need 1000 to reach level 3
            }
            
            points_needed = next_level_thresholds[self.trust_level] - self.trust_points
            if points_needed < 0:
                points_needed = 0
                
            summary['points_needed_for_next_level'] = points_needed
            summary['next_level'] = self.trust_level + 1
        else:
            summary['points_needed_for_next_level'] = 0
            summary['next_level'] = 3  # Already at max
            
        return summary
    
    def track_sent_invitation(self):
        """Track that an invitation has been sent and update counters."""
        self.reset_invitation_counter()
        self.invitations_sent_today += 1
        self.last_invitation_date = date.today()
        db.session.commit()
        
    def get_trust_level_name(self):
        """Get the name of the user's trust level."""
        names = {
            0: "New Member",
            1: "Basic Member",
            2: "Trusted Member",
            3: "Elite Member"
        }
        return names.get(self.trust_level, "Member")
    
    def has_organization_role(self, organization_id, role):
        """Check if user has a specific role in an organization."""
        org_user = OrganizationUser.query.filter_by(user_id=self.id, organization_id=organization_id).first()
        if not org_user:
            return False
        return org_user.role == role
        
    def get_daily_invitation_limit(self):
        """Get the number of invitations a user can send per day based on trust level."""
        # Trust level 0: 3 invites per day
        # Trust level 1: 5 invites per day
        # Trust level 2: 10 invites per day
        # Trust level 3: 20 invites per day
        limits = {
            0: 3,
            1: 5,
            2: 10,
            3: 20
        }
        return limits.get(self.trust_level, 3)  # Default to 3 if trust level not in limits
        
    def can_send_invitation(self):
        """Check if user can send an invitation based on their limits."""
        # Reset counter if it's a new day
        today = date.today()
        if self.last_invitation_date is None or self.last_invitation_date < today:
            self.invitations_sent_today = 0
            self.last_invitation_date = today
            
        # Check if under daily limit
        return self.invitations_sent_today < self.get_daily_invitation_limit()
        
    def increment_invitation_counter(self):
        """Increment the daily invitation counter."""
        # Ensure we're tracking for today
        today = date.today()
        if self.last_invitation_date is None or self.last_invitation_date < today:
            self.invitations_sent_today = 1
            self.last_invitation_date = today
        else:
            self.invitations_sent_today += 1
    
    def to_dict(self):
        """Convert the user to a dictionary."""
        data = {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'profile_pic': self.profile_pic,
            'role': self.role,
            'provider': self.social_provider,
            'created_at': self.created_at.isoformat(),
            'organizations': [org_user.to_dict() for org_user in self.organizations],
            'subscription_status': self.subscription_status,
            'trust_level': self.trust_level,
            'trust_points': self.trust_points,
            'show_in_trust_ranking': self.show_in_trust_ranking,
            'successful_invites_count': self.successful_invites_count,
            'earned_access_days': self.earned_access_days
        }
        
        if self.access_expiration_date:
            data['access_expiration_date'] = self.access_expiration_date.isoformat()
            
        if self.last_invitation_date:
            data['last_invitation_date'] = self.last_invitation_date.isoformat()
            
        if self.trust_ranking_consent_at:
            data['trust_ranking_consent_at'] = self.trust_ranking_consent_at.isoformat()
            
        return data
        
    def get_days_remaining(self):
        """
        Calculate the number of days remaining until the user's access expires.
        
        Returns:
            int: Number of days remaining, or 0 if expired or no expiration date set
        """
        if not self.access_expiration_date:
            return 0
            
        # Calculate the days difference between now and expiration date
        now = datetime.utcnow()
        if now > self.access_expiration_date:
            return 0
            
        # Get the days difference
        delta = self.access_expiration_date - now
        return delta.days
        
    def is_access_expired(self):
        """
        Check if the user's access has expired.
        
        Returns:
            bool: True if expired or no expiration date, False otherwise
        """
        if not self.access_expiration_date:
            return True
            
        return datetime.utcnow() > self.access_expiration_date
        
    def get_subscription_status_display(self):
        """
        Get a user-friendly display of the subscription status.
        
        Returns:
            str: Formatted subscription status message
        """
        status_display = {
            'free_trial': 'Free Trial',
            'basic': 'Basic Plan',
            'pro': 'Pro Plan',
            'premium': 'Premium Plan',
            'extended': 'Extended Access',
            'invited': 'Invitation Access'
        }
        
        return status_display.get(self.subscription_status, 'Free Trial')

class Receipt(db.Model):
    """Model for storing receipt information."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Link to user
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)  # Link to organization
    vendor_name = db.Column(db.String(255), nullable=False)
    vendor_address = db.Column(db.Text, nullable=True)
    vendor_contact = db.Column(db.String(255), nullable=True)
    date = db.Column(db.Date, nullable=True)
    total_amount = db.Column(db.Float, nullable=False, default=0.0)
    service_charge = db.Column(db.Float, nullable=True, default=0.0)
    vat_registration_number = db.Column(db.String(100), nullable=True)
    sscl_tax = db.Column(db.Float, nullable=True, default=0.0)
    vat_tax = db.Column(db.Float, nullable=True, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    image_path = db.Column(db.String(255), nullable=True)  # Local file path (legacy)
    s3_key = db.Column(db.String(255), nullable=True)  # S3 object key for receipt image
    thumbnail_s3_key = db.Column(db.String(255), nullable=True)  # S3 object key for receipt thumbnail
    expense_major_category = db.Column(db.String(100), nullable=True)
    expense_minor_category = db.Column(db.String(100), nullable=True)
    
    # New fields for tracking original extraction data
    original_extracted_text = db.Column(db.Text, nullable=True)  # Raw extracted text from the AI model
    extraction_confidence = db.Column(db.Float, nullable=True)  # Confidence score of the extraction
    extraction_model = db.Column(db.String(100), nullable=True)  # Model used for extraction
    correction_count = db.Column(db.Integer, default=0)  # Number of corrections made by users
    
    items = db.relationship('ReceiptItem', backref='receipt', lazy=True, cascade='all, delete-orphan')
    
    def record_edit(self, user_id, changed_fields, ip_address=None, user_agent=None):
        """
        Record edits to a receipt in the audit log.
        
        Args:
            user_id: ID of the user making the edit
            changed_fields: Dictionary of {field_name: {'old': old_value, 'new': new_value}}
            ip_address: IP address of the user (optional)
            user_agent: User agent string (optional)
            
        Returns:
            The created AuditLog instance
        """
        try:
            audit = AuditLog(
                entity_type='receipt',
                entity_id=self.id,
                action='UPDATE',
                changed_fields=changed_fields,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent
            )
            db.session.add(audit)
            
            # Increment correction count
            self.correction_count += 1
            
            # Don't commit here - let the caller handle the transaction
            
            return audit
        except Exception as e:
            logging.error(f"Error recording receipt edit: {str(e)}")
            return None
    
    @property
    def s3_url(self):
        """Generate a presigned URL for the receipt image in S3 if available."""
        if not self.s3_key:
            return None
            
        # Import here to avoid circular imports
        from s3_storage import generate_presigned_url
        import logging
        
        try:
            # Generate and return the presigned URL
            url = generate_presigned_url(self.s3_key)
            if not url:
                logging.warning(f"Failed to generate S3 URL for key: {self.s3_key} (object may not exist)")
            return url
        except Exception as e:
            logging.error(f"Error generating S3 URL for key {self.s3_key}: {str(e)}")
            return None
    
    @property
    def s3_direct_url(self):
        """Generate a direct URL to the S3 image suitable for printed reports."""
        if not self.s3_key:
            return None
            
        # Import here to avoid circular imports
        from s3_storage import generate_presigned_url
        import logging
        
        try:
            # Generate a longer-lived URL for print reports (2 hours)
            url = generate_presigned_url(self.s3_key, expiration=7200)
            if not url:
                logging.warning(f"Failed to generate direct S3 URL for key: {self.s3_key} (object may not exist)")
                return None
            logging.info(f"Successfully generated S3 URL for receipt {self.id}: {url}")
            return url
        except Exception as e:
            logging.error(f"Error generating direct S3 URL for key {self.s3_key}: {str(e)}")
            return None
    
    @property
    def thumbnail_url(self):
        """Generate a presigned URL for the thumbnail image in S3 if available."""
        import logging
        
        try:
            # Import here to avoid circular imports
            from thumbnail_manager import get_best_thumbnail_url
            
            # Use the dedicated thumbnail_s3_key if available, otherwise generate from main image
            url = get_best_thumbnail_url(self.s3_key, self.thumbnail_s3_key)
            if not url:
                logging.warning(f"Failed to generate thumbnail URL for receipt {self.id}")
                # Fallback to the main image URL
                return self.s3_url
            return url
        except Exception as e:
            logging.error(f"Error generating thumbnail URL for receipt {self.id}: {str(e)}")
            # Fallback to the main image URL
            return self.s3_url
    
    def get_secure_image_url(self, thumbnail=True):
        """
        Get a secure URL for this receipt's image through server-side proxy.
        
        This method returns a URL that goes through the application's authentication 
        system, ensuring only authorized users can access the image.
        
        Args:
            thumbnail (bool): Whether to request the thumbnail version (default: True)
            
        Returns:
            str: The secure URL for the receipt image
        """
        try:
            # Import here to avoid circular imports
            from flask import url_for
            
            # Generate the URL through the secure route
            return url_for('expense.get_receipt_image', 
                           receipt_id=self.id, 
                           thumbnail='true' if thumbnail else 'false',
                           _external=False)  # Relative URL to avoid domain issues
        except Exception as e:
            import logging
            logging.error(f"Error generating secure image URL for receipt {self.id}: {str(e)}")
            return None

    def get_tax_deductible_amount(self):
        """Calculate the total amount for tax deductible items using deductibility percentage.
        
        The tax deductible amount should never exceed the total_amount of the receipt.
        Uses the deductibility_percentage field for partial deductions (e.g., 50% for meals).
        """
        tax_deductible_amount = 0.0
        for item in self.items:
            if item.tax_deductible:
                item_total = item.price * item.quantity
                
                # Use deductibility_percentage if available (0-100)
                if item.deductibility_percentage is not None:
                    deductible_portion = item_total * (item.deductibility_percentage / 100.0)
                    tax_deductible_amount += deductible_portion
                else:
                    # Fall back to 100% if percentage not set
                    tax_deductible_amount += item_total
        
        # Ensure tax_deductible_amount doesn't exceed total_amount
        return min(tax_deductible_amount, self.total_amount)

    def to_dict(self):
        """Convert the receipt to a dictionary."""
        # Import here to avoid circular imports
        from utils import get_receipt_image_url
        
        # Use the standardized helper function to get the image URL
        image_url = get_receipt_image_url(self, prefer_s3=True)
        
        # For backward compatibility, still include s3_url separately
        s3_url = self.s3_url if self.s3_key else None
        
        # Generate secure URLs that go through server-side proxying
        secure_url = self.get_secure_image_url(thumbnail=False)
        secure_thumbnail_url = self.get_secure_image_url(thumbnail=True)
            
        return {
            'id': self.id,
            'user_id': self.user_id,
            'organization_id': self.organization_id,
            'vendor_name': self.vendor_name,
            'vendor_address': self.vendor_address or '',
            'vendor_contact': self.vendor_contact or '',
            'date': self.date.strftime('%Y-%m-%d') if self.date else '',
            'total_amount': self.total_amount,
            'service_charge': self.service_charge,
            'vat_registration_number': self.vat_registration_number or '',
            'sscl_tax': self.sscl_tax,
            'vat_tax': self.vat_tax,
            'created_at': self.created_at.isoformat(),
            'image_path': self.image_path or '',
            's3_key': self.s3_key or '',
            'thumbnail_s3_key': self.thumbnail_s3_key or '',
            'image_url': secure_url or image_url,  # Prioritize secure URL
            's3_url': s3_url,  # Keep for backward compatibility
            'thumbnail_url': secure_thumbnail_url or self.thumbnail_url,  # Prioritize secure thumbnail
            'secure_image_url': secure_url,  # Add dedicated secure URL property
            'secure_thumbnail_url': secure_thumbnail_url,  # Add dedicated secure thumbnail URL property
            'expense_major_category': self.expense_major_category or '',
            'expense_minor_category': self.expense_minor_category or '',
            'tax_deductible_amount': self.get_tax_deductible_amount(),
            'items': [item.to_dict() for item in self.items]
        }

class ReceiptItem(db.Model):
    """Model for storing items from a receipt."""
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Float, nullable=True, default=1.0)
    price = db.Column(db.Float, nullable=False, default=0.0)
    tax_deductible = db.Column(db.Boolean, nullable=False, default=False)
    
    deductibility_percentage = db.Column(db.Integer, nullable=True, default=None)
    tax_law_reference = db.Column(db.String(500), nullable=True, default=None)
    classification_confidence = db.Column(db.Float, nullable=True, default=None)
    deduction_notes = db.Column(db.Text, nullable=True, default=None)
    
    def to_dict(self):
        """Convert the receipt item to a dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'quantity': self.quantity,
            'price': self.price,
            'tax_deductible': self.tax_deductible,
            'deductibility_percentage': self.deductibility_percentage,
            'tax_law_reference': self.tax_law_reference,
            'classification_confidence': self.classification_confidence,
            'deduction_notes': self.deduction_notes
        }

class Client(db.Model):
    """Model for storing client information for invoicing."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)
    name = db.Column(db.String(255), nullable=False)  # Required primary name for client (could be person or company)
    company_name = db.Column(db.String(255), nullable=True)  # Optional company name if client is a company
    contact_person = db.Column(db.String(255), nullable=True)  # Optional contact person if client is a company
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.Text, nullable=True)
    tax_registration_number = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    invoices = db.relationship('Invoice', backref='client', lazy=True)
    
    def to_dict(self):
        """Convert the client to a dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'company_name': self.company_name or '',
            'contact_person': self.contact_person or '',
            'email': self.email or '',
            'phone': self.phone or '',
            'address': self.address or '',
            'tax_registration_number': self.tax_registration_number or '',
            'notes': self.notes or '',
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

class Invoice(db.Model):
    """Model for storing invoice information."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    bank_account_id = db.Column(db.Integer, db.ForeignKey('bank_account.id'), nullable=True)
    invoice_number = db.Column(db.String(50), nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=InvoiceStatus.DRAFT.value)
    notes = db.Column(db.Text, nullable=True)
    currency = db.Column(db.String(3), nullable=False, default="LKR")
    subtotal = db.Column(db.Float, nullable=False, default=0.0)
    tax_percent = db.Column(db.Float, nullable=False, default=0.0)
    tax_amount = db.Column(db.Float, nullable=False, default=0.0)
    discount_percent = db.Column(db.Float, nullable=False, default=0.0)
    discount_amount = db.Column(db.Float, nullable=False, default=0.0)
    total = db.Column(db.Float, nullable=False, default=0.0)
    # Sender's information fields
    sender_name = db.Column(db.String(255), nullable=True)
    sender_company = db.Column(db.String(255), nullable=True)
    sender_address = db.Column(db.Text, nullable=True)
    sender_phone = db.Column(db.String(50), nullable=True)
    sender_email = db.Column(db.String(255), nullable=True)
    sender_tax_registration = db.Column(db.String(100), nullable=True)
    # Email tracking
    last_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    items = db.relationship('InvoiceItem', backref='invoice', lazy=True, cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='invoice', lazy=True, cascade='all, delete-orphan')
    
    def get_amount_paid(self):
        """Calculate the total amount paid on this invoice."""
        return sum(payment.amount for payment in self.payments)
    
    def get_amount_due(self):
        """Calculate the remaining amount due on this invoice."""
        return self.total - self.get_amount_paid()
    
    def update_status(self):
        """Update the invoice status based on payments and due date."""
        amount_paid = self.get_amount_paid()
        
        # Don't change status for cancelled invoices
        if self.status == InvoiceStatus.CANCELLED.value:
            return
            
        if amount_paid >= self.total:
            self.status = InvoiceStatus.PAID.value
        elif amount_paid > 0:
            self.status = InvoiceStatus.PARTIALLY_PAID.value
        elif self.due_date < datetime.utcnow().date() and self.status not in [InvoiceStatus.PAID.value, InvoiceStatus.CANCELLED.value]:
            self.status = InvoiceStatus.OVERDUE.value
        elif self.status == InvoiceStatus.DRAFT.value:
            # Keep draft status
            pass
        else:
            self.status = InvoiceStatus.SENT.value
    
    def to_dict(self):
        """Convert the invoice to a dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'client_id': self.client_id,
            'client_name': self.client.name if self.client else '',
            'client_company_name': self.client.company_name if self.client and self.client.company_name else '',
            'client_contact_person': self.client.contact_person if self.client and self.client.contact_person else '',
            'bank_account_id': self.bank_account_id,
            'bank_account': self.bank_account.to_dict() if self.bank_account else None,
            'invoice_number': self.invoice_number,
            'issue_date': self.issue_date.strftime('%Y-%m-%d'),
            'due_date': self.due_date.strftime('%Y-%m-%d'),
            'status': self.status,
            'notes': self.notes or '',
            'currency': self.currency,
            'subtotal': self.subtotal,
            'tax_percent': self.tax_percent,
            'tax_amount': self.tax_amount,
            'discount_percent': self.discount_percent,
            'discount_amount': self.discount_amount,
            'total': self.total,
            'amount_paid': self.get_amount_paid(),
            'amount_due': self.get_amount_due(),
            # Sender information
            'sender_name': self.sender_name or '',
            'sender_company': self.sender_company or '',
            'sender_address': self.sender_address or '',
            'sender_phone': self.sender_phone or '',
            'sender_email': self.sender_email or '',
            'sender_tax_registration': self.sender_tax_registration or '',
            'last_sent_at': self.last_sent_at.isoformat() if self.last_sent_at else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'items': [item.to_dict() for item in self.items],
            'payments': [payment.to_dict() for payment in self.payments]
        }

class InvoiceItem(db.Model):
    """Model for storing items included in an invoice."""
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1.0)
    unit_price = db.Column(db.Float, nullable=False, default=0.0)
    tax_percent = db.Column(db.Float, nullable=False, default=0.0)
    tax_amount = db.Column(db.Float, nullable=False, default=0.0)
    total = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        """Convert the invoice item to a dictionary."""
        return {
            'id': self.id,
            'invoice_id': self.invoice_id,
            'description': self.description,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'tax_percent': self.tax_percent,
            'tax_amount': self.tax_amount,
            'total': self.total,
            'created_at': self.created_at.isoformat()
        }

class Payment(db.Model):
    """Model for storing payment information for invoices."""
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    payment_method = db.Column(db.String(20), nullable=False, default=PaymentMethod.BANK_TRANSFER.value)
    reference = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        """Convert the payment to a dictionary."""
        return {
            'id': self.id,
            'invoice_id': self.invoice_id,
            'date': self.date.strftime('%Y-%m-%d'),
            'amount': self.amount,
            'payment_method': self.payment_method,
            'reference': self.reference or '',
            'notes': self.notes or '',
            'created_at': self.created_at.isoformat()
        }

class BankAccount(db.Model):
    """Model for storing user bank account information."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)
    
    account_name = db.Column(db.String(255), nullable=False)  # Name identifier for the account (e.g., "Primary Business Account")
    bank_name = db.Column(db.String(255), nullable=False)  # Name of the bank
    account_number = db.Column(db.String(100), nullable=False)  # Account number
    branch_name = db.Column(db.String(255), nullable=True)  # Branch name (optional)
    swift_code = db.Column(db.String(50), nullable=True)  # SWIFT/BIC code (optional)
    iban = db.Column(db.String(100), nullable=True)  # IBAN for international transfers (optional)
    is_default = db.Column(db.Boolean, default=False)  # Whether this is the default account
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship with invoices is defined in the Invoice model
    
    def to_dict(self):
        """Convert the bank account to a dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'organization_id': self.organization_id,
            'account_name': self.account_name,
            'bank_name': self.bank_name,
            'account_number': self.account_number,
            'branch_name': self.branch_name or '',
            'swift_code': self.swift_code or '',
            'iban': self.iban or '',
            'is_default': self.is_default,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class ExpenseStatus(Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REIMBURSED = "reimbursed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

class CompanyExpense(db.Model):
    """Model for storing company expense information for reimbursement."""
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=ExpenseStatus.SUBMITTED.value)
    is_reimbursable = db.Column(db.Boolean, nullable=False, default=True)  # Flag to indicate if expense is reimbursable
    
    submitted_date = db.Column(db.DateTime, default=datetime.utcnow)
    approval_date = db.Column(db.DateTime, nullable=True)
    reimbursed_date = db.Column(db.DateTime, nullable=True)
    
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reimbursed_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    receipt = db.relationship('Receipt', backref='company_expense', lazy=True, uselist=False)
    submitter = db.relationship('User', foreign_keys=[user_id], backref='submitted_expenses', lazy=True)
    approver = db.relationship('User', foreign_keys=[approved_by_user_id], backref='approved_expenses', lazy=True)
    reimburser = db.relationship('User', foreign_keys=[reimbursed_by_user_id], backref='reimbursed_expenses', lazy=True)
    client = db.relationship('Client', backref='company_expenses', lazy=True)
    organization = db.relationship('Organization', backref='company_expenses', lazy=True)
    
    def to_dict(self):
        """Convert the company expense to a dictionary."""
        return {
            'id': self.id,
            'receipt_id': self.receipt_id,
            'user_id': self.user_id,
            'organization_id': self.organization_id,
            'client_id': self.client_id,
            'description': self.description or '',
            'status': self.status,
            'is_reimbursable': self.is_reimbursable,
            'submitted_date': self.submitted_date.isoformat() if self.submitted_date else None,
            'approval_date': self.approval_date.isoformat() if self.approval_date else None,
            'reimbursed_date': self.reimbursed_date.isoformat() if self.reimbursed_date else None,
            'approved_by_user_id': self.approved_by_user_id,
            'reimbursed_by_user_id': self.reimbursed_by_user_id,
            'notes': self.notes or '',
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'receipt': self.receipt.to_dict() if self.receipt else None,
            'submitter_name': self.submitter.name if self.submitter else '',
            'approver_name': self.approver.name if self.approver else '',
            'reimburser_name': self.reimburser.name if self.reimburser else '',
            'client_name': self.client.name if self.client else '',
            'organization_name': self.organization.name if self.organization else ''
        }

class ClientExpense(db.Model):
    """Model for storing expenses allocated to clients for project billing."""
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)
    
    description = db.Column(db.Text, nullable=True)
    project_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Define relationships
    receipt = db.relationship('Receipt', backref='client_expenses', lazy=True)
    client = db.relationship('Client', backref='expenses', lazy=True)
    user = db.relationship('User', foreign_keys=[user_id], backref='client_expenses', lazy=True)
    
    def to_dict(self):
        """Convert the client expense to a dictionary."""
        return {
            'id': self.id,
            'receipt_id': self.receipt_id,
            'client_id': self.client_id,
            'user_id': self.user_id,
            'organization_id': self.organization_id,
            'description': self.description,
            'project_name': self.project_name,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'receipt': self.receipt.to_dict() if self.receipt else None,
            'client_name': self.client.name if self.client else None
        }


class UserIncome(db.Model):
    """Model for storing user income details for tax calculations."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)
    # LKR Income
    employment_income = db.Column(db.Float, nullable=True, default=0.0)
    business_income = db.Column(db.Float, nullable=True, default=0.0)
    investment_income = db.Column(db.Float, nullable=True, default=0.0)
    # USD Income
    usd_consulting_income = db.Column(db.Float, nullable=True, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def get_total_lkr_income(self):
        """Calculate the total income from all LKR sources."""
        return (self.employment_income or 0) + (self.business_income or 0) + (self.investment_income or 0)
    
    def get_total_usd_income(self):
        """Calculate the total income from all USD sources."""
        return (self.usd_consulting_income or 0)
    
    def to_dict(self):
        """Convert the income details to a dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'employment_income': self.employment_income or 0,
            'business_income': self.business_income or 0,
            'investment_income': self.investment_income or 0,
            'usd_consulting_income': self.usd_consulting_income or 0,
            'total_lkr_income': self.get_total_lkr_income(),
            'total_usd_income': self.get_total_usd_income(),
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class RegistrationRateLimit(db.Model):
    """Model for tracking registration attempts by IP address to prevent bot signups."""
    __tablename__ = 'registration_rate_limit'
    
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(50), nullable=False)
    window_start = db.Column(db.DateTime, nullable=False)
    attempt_count = db.Column(db.Integer, default=1)
    first_attempt_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_attempt_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('ip_address', 'window_start', name='unique_ip_window'),
    )
    
    @staticmethod
    def get_current_window():
        """Get the current hour window start timestamp."""
        now = datetime.utcnow()
        return now.replace(minute=0, second=0, microsecond=0)
    
    @staticmethod
    def check_rate_limit(ip_address, max_attempts=3):
        """
        Check if the IP is rate limited for registration.
        Returns (allowed, attempts_remaining, retry_after_seconds)
        """
        window_start = RegistrationRateLimit.get_current_window()
        
        record = RegistrationRateLimit.query.filter_by(
            ip_address=ip_address,
            window_start=window_start
        ).first()
        
        if not record:
            return True, max_attempts, 0
        
        if record.attempt_count >= max_attempts:
            next_window = window_start + timedelta(hours=1)
            retry_after = (next_window - datetime.utcnow()).total_seconds()
            return False, 0, int(max(0, retry_after))
        
        return True, max_attempts - record.attempt_count, 0
    
    @staticmethod
    def record_attempt(ip_address):
        """
        Record a registration attempt from an IP address using atomic upsert.
        Uses INSERT ... ON CONFLICT ... DO UPDATE for thread-safe operation.
        """
        from sqlalchemy import text
        window_start = RegistrationRateLimit.get_current_window()
        now = datetime.utcnow()
        
        try:
            stmt = text("""
                INSERT INTO registration_rate_limit 
                    (ip_address, window_start, attempt_count, first_attempt_at, last_attempt_at)
                VALUES 
                    (:ip, :window, 1, :now, :now)
                ON CONFLICT (ip_address, window_start) 
                DO UPDATE SET 
                    attempt_count = registration_rate_limit.attempt_count + 1,
                    last_attempt_at = :now
                RETURNING attempt_count
            """)
            
            result = db.session.execute(stmt, {
                'ip': ip_address,
                'window': window_start,
                'now': now
            })
            db.session.commit()
            
            attempt_count = result.fetchone()[0]
            return attempt_count
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error recording rate limit attempt: {str(e)}")
            return 1
    
    @staticmethod
    def cleanup_old_records(hours=48):
        """Remove rate limit records older than specified hours."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        deleted = RegistrationRateLimit.query.filter(
            RegistrationRateLimit.window_start < cutoff
        ).delete()
        db.session.commit()
        return deleted


class WorkerHeartbeat(db.Model):
    """Liveness signal written by each Celery worker.

    The Postgres-backed (SQLAlchemy) Celery broker does not support the
    fanout / broadcast control commands that `celery.control.inspect()`
    relies on, so we cannot ask "are any workers alive?" through the
    broker itself. Instead each worker upserts its name + a `last_seen`
    timestamp into this table on startup and on every Celery heartbeat
    tick. The web process reads `last_seen` to decide whether the worker
    pool is healthy enough to accept new receipt-scan tasks.
    """
    __tablename__ = 'worker_heartbeat'

    worker_name = db.Column(db.String(255), primary_key=True)
    last_seen = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @classmethod
    def upsert(cls, worker_name: str) -> None:
        """Record that `worker_name` is alive right now."""
        from sqlalchemy import text
        try:
            db.session.execute(
                text(
                    """
                    INSERT INTO worker_heartbeat (worker_name, last_seen)
                    VALUES (:name, :now)
                    ON CONFLICT (worker_name)
                    DO UPDATE SET last_seen = EXCLUDED.last_seen
                    """
                ),
                {'name': worker_name, 'now': datetime.utcnow()},
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def alive_workers(cls, max_age_seconds: int = 60):
        """Return rows for workers seen within the last `max_age_seconds`."""
        cutoff = datetime.utcnow() - timedelta(seconds=max_age_seconds)
        return cls.query.filter(cls.last_seen >= cutoff).all()

    @classmethod
    def has_live_worker(cls, max_age_seconds: int = 60) -> bool:
        return bool(cls.alive_workers(max_age_seconds=max_age_seconds))


# ---------------------------------------------------------------------------
# C9 F8.8 — SystemSetting: DB-persisted operator settings.
#
# Replaces the module-global dicts in admin_routes.py (tax_settings,
# general_settings) with rows that survive pod restarts.
#
# Key design choices:
#   * `key` is the primary key — simple key/value store, not relational.
#   * `value` is TEXT and JSON-encoded by convention (store strings,
#     numbers, booleans, and lists all as JSON so the type is self-
#     describing and round-trips cleanly).
#   * `updated_by_user_id` is a nullable integer FK — nullable so that
#     seed rows written by the migration script have no user attached.
#   * `updated_at` defaults to utcnow and is updated on every set().
#
# Consumer migration note: module-global dicts in admin_routes.py
# (tax_settings, general_settings) still exist for backward compatibility.
# New code should read via SystemSetting.get(...); existing consumers of
# those dicts should migrate to SystemSetting.get(...) over time.
#
# Table created by migrations/add_system_setting.py (idempotent CREATE
# TABLE IF NOT EXISTS). Seed defaults are written by the same migration.
# ---------------------------------------------------------------------------
class SystemSetting(db.Model):
    """Operator-editable key/value settings persisted to the database.

    Values are stored as JSON text so complex types (lists, dicts) round-trip
    cleanly. Simple scalars (str, int, float, bool) are also stored as JSON.

    Usage::

        # Read (returns None if key missing):
        val = SystemSetting.get('lkr_business_tax_rate', default=36.0)

        # Write:
        SystemSetting.set('lkr_business_tax_rate', 36.0, user_id=current_user.id)
    """
    __tablename__ = 'system_setting'

    key = db.Column(db.String(128), primary_key=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    # NULL for seed rows written by migration (no authenticated user).
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    # Relationship back to the user who last updated this setting (optional).
    updated_by = db.relationship('User', foreign_keys=[updated_by_user_id])

    @classmethod
    def get(cls, key: str, default=None):
        """Fetch and JSON-decode a setting by key.

        Returns `default` when the key does not exist or the value is NULL.
        """
        import json as _json
        row = cls.query.get(key)
        if row is None or row.value is None:
            return default
        try:
            return _json.loads(row.value)
        except Exception:
            # Corrupt value — return it as a raw string rather than raising.
            return row.value

    @classmethod
    def set(cls, key: str, value, user_id=None):
        """JSON-encode and upsert a setting.

        Creates the row if it doesn't exist; updates it otherwise.
        Commits the session.
        """
        import json as _json
        try:
            encoded = _json.dumps(value)
        except (TypeError, ValueError):
            encoded = str(value)

        row = cls.query.get(key)
        if row is None:
            row = cls(key=key, value=encoded, updated_at=datetime.utcnow(),
                      updated_by_user_id=user_id)
            db.session.add(row)
        else:
            row.value = encoded
            row.updated_at = datetime.utcnow()
            row.updated_by_user_id = user_id
        db.session.commit()
        return row

    @classmethod
    def all_settings(cls) -> dict:
        """Return all settings as a plain dict {key: decoded_value}."""
        import json as _json
        result = {}
        for row in cls.query.order_by(cls.key).all():
            try:
                result[row.key] = _json.loads(row.value) if row.value is not None else None
            except Exception:
                result[row.key] = row.value
        return result

    def __repr__(self):
        return f"<SystemSetting key={self.key!r} updated_at={self.updated_at}>"