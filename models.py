from app import db
from datetime import datetime
import json
from flask_login import UserMixin
from enum import Enum

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
    logo_path = db.Column(db.String(255), nullable=True)
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
    
    # Relationships
    users = db.relationship('OrganizationUser', back_populates='organization', lazy=True, cascade='all, delete-orphan')
    receipts = db.relationship('Receipt', backref='organization', lazy=True)
    clients = db.relationship('Client', backref='organization', lazy=True)
    invoices = db.relationship('Invoice', backref='organization', lazy=True)
    bank_accounts = db.relationship('BankAccount', backref='organization', lazy=True)
    
    def to_dict(self):
        """Convert the organization to a dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'logo_path': self.logo_path or '',
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
    invited_by = db.relationship('User', foreign_keys=[invited_by_user_id])
    
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
    
    # Relationships
    organizations = db.relationship('OrganizationUser', back_populates='user', lazy=True, cascade='all, delete-orphan')
    receipts = db.relationship('Receipt', backref='user', lazy=True)
    income_details = db.relationship('UserIncome', backref='user', lazy=True, uselist=False)
    clients = db.relationship('Client', backref='user', lazy=True)
    invoices = db.relationship('Invoice', backref='user', lazy=True)
    bank_accounts = db.relationship('BankAccount', backref='user', lazy=True)
    sent_invitations = db.relationship('OrganizationInvitation', foreign_keys='OrganizationInvitation.invited_by_user_id', backref='sender', lazy=True)
    
    def is_admin(self):
        """Check if user has global admin role."""
        return self.role == 'admin'
    
    def has_role(self, role):
        """Check if user has a specific global role."""
        return self.role == role
    
    def get_default_organization(self):
        """Get the user's default organization."""
        org_user = OrganizationUser.query.filter_by(user_id=self.id, is_default=True).first()
        if org_user:
            return org_user.organization
        # If no default is set but user has organizations, return the first
        org_user = OrganizationUser.query.filter_by(user_id=self.id).first()
        if org_user:
            return org_user.organization
        return None
    
    def has_organization_role(self, organization_id, role):
        """Check if user has a specific role in an organization."""
        org_user = OrganizationUser.query.filter_by(user_id=self.id, organization_id=organization_id).first()
        if not org_user:
            return False
        return org_user.role == role
    
    def to_dict(self):
        """Convert the user to a dictionary."""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'profile_pic': self.profile_pic,
            'role': self.role,
            'provider': self.social_provider,
            'created_at': self.created_at.isoformat(),
            'organizations': [org_user.to_dict() for org_user in self.organizations]
        }

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
    image_path = db.Column(db.String(255), nullable=True)
    expense_major_category = db.Column(db.String(100), nullable=True)
    expense_minor_category = db.Column(db.String(100), nullable=True)
    items = db.relationship('ReceiptItem', backref='receipt', lazy=True, cascade='all, delete-orphan')

    def get_tax_deductible_amount(self):
        """Calculate the total amount for tax deductible items.
        
        The tax deductible amount should never exceed the total_amount of the receipt.
        """
        tax_deductible_amount = 0.0
        for item in self.items:
            if item.tax_deductible:
                tax_deductible_amount += item.price * item.quantity
        
        # Ensure tax_deductible_amount doesn't exceed total_amount
        return min(tax_deductible_amount, self.total_amount)

    def to_dict(self):
        """Convert the receipt to a dictionary."""
        return {
            'id': self.id,
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
    
    def to_dict(self):
        """Convert the receipt item to a dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'quantity': self.quantity,
            'price': self.price,
            'tax_deductible': self.tax_deductible
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

class CompanyExpense(db.Model):
    """Model for storing company expense information for reimbursement."""
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)
    
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
    
    def to_dict(self):
        """Convert the company expense to a dictionary."""
        return {
            'id': self.id,
            'receipt_id': self.receipt_id,
            'user_id': self.user_id,
            'organization_id': self.organization_id,
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
            'reimburser_name': self.reimburser.name if self.reimburser else ''
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