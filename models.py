from app import db
from datetime import datetime
import json

class Receipt(db.Model):
    """Model for storing receipt information."""
    id = db.Column(db.Integer, primary_key=True)
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
    items = db.relationship('ReceiptItem', backref='receipt', lazy=True, cascade='all, delete-orphan')

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
            'items': [item.to_dict() for item in self.items]
        }

class ReceiptItem(db.Model):
    """Model for storing items from a receipt."""
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Float, nullable=True, default=1.0)
    price = db.Column(db.Float, nullable=False, default=0.0)
    
    def to_dict(self):
        """Convert the receipt item to a dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'quantity': self.quantity,
            'price': self.price
        }