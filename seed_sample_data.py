"""
Seed script to generate more interesting sample data for the admin dashboard.
"""

import json
import random
from datetime import datetime, timedelta
from app import app, db
from models import User, Receipt, ReceiptItem, UserIncome
from werkzeug.security import generate_password_hash

# Sample data
EXPENSE_CATEGORIES = [
    'Office Supplies', 'Travel', 'Meals', 'Utilities', 
    'Software', 'Hardware', 'Professional Services', 'Marketing',
    'Transport', 'Rent', 'Insurance', 'Telecommunications'
]

SUBCATEGORIES = {
    'Office Supplies': ['Stationery', 'Furniture', 'Printers', 'Paper', 'Ink'],
    'Travel': ['Airfare', 'Hotels', 'Taxi', 'Meals', 'Car Rental'],
    'Meals': ['Client Meetings', 'Staff Lunch', 'Business Dinner', 'Catering'],
    'Utilities': ['Electricity', 'Water', 'Gas', 'Internet', 'Waste Management'],
    'Software': ['Subscriptions', 'Licenses', 'Cloud Services', 'Development Tools'],
    'Hardware': ['Computers', 'Servers', 'Peripherals', 'Mobile Devices'],
    'Professional Services': ['Accounting', 'Legal', 'Consulting', 'Training'],
    'Marketing': ['Online Ads', 'Print', 'Events', 'Design Services'],
    'Transport': ['Fuel', 'Maintenance', 'Public Transport', 'Delivery Services'],
    'Rent': ['Office Space', 'Storage', 'Event Venues', 'Equipment Rentals'],
    'Insurance': ['Property', 'Liability', 'Health', 'Vehicle'],
    'Telecommunications': ['Phone Plans', 'Internet Services', 'Conference Systems']
}

VENDORS = [
    'Office Depot', 'Amazon', 'Dell', 'Hilton Hotels', 'Uber', 'Airbnb',
    'Microsoft', 'Zoom', 'Adobe', 'Google', 'Restaurant XYZ', 'Cafe ABC',
    'Verizon', 'AT&T', 'Apple', 'Best Buy', 'Staples', 'Home Depot',
    'WeWork', 'Enterprise Car Rental', 'FedEx', 'DHL', 'Lyft'
]

RANDOM_ITEMS = [
    'Laptop', 'Monitor', 'Keyboard', 'Mouse', 'Office Chair', 'Desk',
    'Software Subscription', 'Cloud Storage', 'Phone Service', 'Internet',
    'Printer Paper', 'Ink Cartridges', 'Business Cards', 'Marketing Materials',
    'Hotel Stay', 'Flight Tickets', 'Taxi Ride', 'Conference Registration',
    'Meal with Client', 'Office Lunch', 'Coffee Meeting', 'Office Snacks',
    'Legal Services', 'Accounting Services', 'Consulting Services',
    'Web Hosting', 'Domain Name', 'SSL Certificate', 'Online Advertising',
    'Training Course', 'Reference Books', 'Professional Membership',
    'Shipping Costs', 'Office Maintenance', 'Cleaning Services'
]

# Create sample users
def create_sample_users(count=5):
    """Create sample users for testing."""
    print(f"Creating {count} sample users...")
    
    # Create an admin user if not exists
    admin = User.query.filter_by(email='admin@example.com').first()
    if not admin:
        admin = User(
            email='admin@example.com',
            name='Admin User',
            password_hash=generate_password_hash('adminpass'),
            role='admin',
            created_at=datetime.utcnow() - timedelta(days=60)
        )
        db.session.add(admin)
        print("Added admin user")
    
    # Create regular users
    existing_count = User.query.count()
    for i in range(count):
        created_date = datetime.utcnow() - timedelta(days=random.randint(1, 50))
        user = User(
            email=f'user{existing_count+i}@example.com',
            name=f'Test User {existing_count+i}',
            password_hash=generate_password_hash(f'password{i}'),
            role='user',
            created_at=created_date
        )
        db.session.add(user)
    
    db.session.commit()
    print(f"Created {count} sample users")

# Create sample receipts with more variety
def create_sample_receipts(count=50):
    """Create sample receipts for testing."""
    print(f"Creating {count} sample receipts...")
    
    users = User.query.all()
    if not users:
        print("No users found! Create users first.")
        return

    # Create receipts
    for i in range(count):
        # Random date within last 60 days
        days_ago = random.randint(0, 60)
        receipt_date = datetime.utcnow() - timedelta(days=days_ago)
        
        # Pick a random user
        user = random.choice(users)
        
        # Pick a random category and subcategory
        category = random.choice(EXPENSE_CATEGORIES)
        subcategory = random.choice(SUBCATEGORIES[category])
        
        # Generate a random total (between 500 and 20000)
        total_amount = round(random.uniform(500, 20000), 2)
        
        # Maybe add some tax
        vat_tax = round(total_amount * 0.08, 2) if random.random() > 0.3 else 0
        sscl_tax = round(total_amount * 0.02, 2) if random.random() > 0.5 else 0
        
        # Create the receipt
        receipt = Receipt(
            user_id=user.id,
            vendor_name=random.choice(VENDORS),
            vendor_address=f"123 Business St, Colombo {random.randint(1, 15)}",
            vendor_contact=f"+94 {random.randint(700000000, 799999999)}",
            date=receipt_date.date(),
            total_amount=total_amount,
            vat_tax=vat_tax,
            sscl_tax=sscl_tax,
            created_at=receipt_date,
            expense_major_category=category,
            expense_minor_category=subcategory
        )
        db.session.add(receipt)
        db.session.flush()  # To get the receipt ID
        
        # Generate 1-5 items for this receipt
        item_count = random.randint(1, 5)
        item_sum = 0
        
        for j in range(item_count):
            # Last item will be adjusted to match the total
            if j == item_count - 1:
                price = total_amount - item_sum
            else:
                # Random proportion of the total
                price = round(total_amount * random.uniform(0.1, 0.5), 2)
                item_sum += price
            
            # Some items are tax deductible
            tax_deductible = random.random() > 0.2
            
            item = ReceiptItem(
                receipt_id=receipt.id,
                name=random.choice(RANDOM_ITEMS),
                quantity=random.randint(1, 3),
                price=price,
                tax_deductible=tax_deductible
            )
            db.session.add(item)
    
    db.session.commit()
    print(f"Created {count} sample receipts")

# Create sample income records
def create_sample_income(count=5):
    """Create sample income records for testing."""
    print(f"Creating {count} sample income records...")
    
    users = User.query.all()
    if not users:
        print("No users found! Create users first.")
        return

    # Clear existing income records
    UserIncome.query.delete()
    
    # Create income records
    for i in range(count):
        user = users[i] if i < len(users) else random.choice(users)
        
        # Check if user already has income
        existing = UserIncome.query.filter_by(user_id=user.id).first()
        if existing:
            continue
            
        # Generate random income values
        employment_income = random.randint(500000, 5000000) if random.random() > 0.3 else 0
        business_income = random.randint(1000000, 10000000) if random.random() > 0.3 else 0
        investment_income = random.randint(100000, 2000000) if random.random() > 0.5 else 0
        usd_consulting_income = random.randint(3000, 50000) if random.random() > 0.4 else 0
        
        income = UserIncome(
            user_id=user.id,
            employment_income=employment_income,
            business_income=business_income,
            investment_income=investment_income,
            usd_consulting_income=usd_consulting_income,
            created_at=datetime.utcnow() - timedelta(days=random.randint(1, 30))
        )
        db.session.add(income)
    
    db.session.commit()
    print(f"Created income records for {count} users")

if __name__ == "__main__":
    with app.app_context():
        # Create sample data (smaller dataset for faster execution)
        create_sample_users(8)
        create_sample_receipts(50)
        create_sample_income(6)
        
        # Print summary
        print("\nSample data created successfully!")
        print(f"Users: {User.query.count()}")
        print(f"Receipts: {Receipt.query.count()}")
        print(f"Receipt Items: {ReceiptItem.query.count()}")
        print(f"Income Records: {UserIncome.query.count()}")