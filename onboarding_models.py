from app import db
from datetime import datetime

class OnboardingProgress(db.Model):
    """Model for tracking user's onboarding progress through the Getting Started wizard."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    profile_completed = db.Column(db.Boolean, default=False)
    organization_created = db.Column(db.Boolean, default=False)
    bank_account_added = db.Column(db.Boolean, default=False)
    team_member_invited = db.Column(db.Boolean, default=False)
    customer_added = db.Column(db.Boolean, default=False)
    document_scanned = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    user = db.relationship('User', backref=db.backref('onboarding_progress', uselist=False))
    
    def get_completion_percentage(self):
        """Calculate percentage of completed steps."""
        completed_steps = sum([
            self.profile_completed,
            self.organization_created, 
            self.bank_account_added,
            self.team_member_invited,
            self.customer_added,
            self.document_scanned
        ])
        return int((completed_steps / 6) * 100)
    
    def is_complete(self):
        """Check if all onboarding steps are completed."""
        return all([
            self.profile_completed,
            self.organization_created,
            self.bank_account_added,
            self.team_member_invited,
            self.customer_added,
            self.document_scanned
        ])