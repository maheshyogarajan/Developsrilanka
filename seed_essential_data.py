"""
Lightweight seed script that generates a minimal but representative dataset 
for testing the admin dashboard analytics features.
"""

import random
from datetime import datetime, timedelta
from app import app, db
from models import User, Receipt, ReceiptItem, UserIncome
from werkzeug.security import generate_password_hash

# Sample categories for diversity
CATEGORIES = [
    ('Office Supplies', 'Stationery'),
    ('Travel', 'Airfare'),
    ('Meals', 'Business Dinner'),
    ('Utilities', 'Internet'),
    ('Software', 'Subscriptions'),
    ('Hardware', 'Computers'),
    ('Professional Services', 'Consulting'),
    ('Marketing', 'Online Ads')
]

VENDORS = [
    'Office Depot', 'Dell', 'Hilton Hotels', 'Microsoft', 
    'Restaurant XYZ', 'Verizon', 'Apple', 'WeWork'
]

# Main function to create essential test data
def create_essential_dataset():
    """Creates a minimal but representative dataset for testing admin analytics"""
    with app.app_context():
        print("Creating essential test dataset...")
        
        # 1. Create test admin if not exists
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
            db.session.commit()
            print("Added admin user")
        
        # 2. Create a few regular users (just 3 for speed)
        existing_users = User.query.count()
        users = []
        for i in range(3):
            created_date = datetime.utcnow() - timedelta(days=random.randint(1, 50))
            user = User(
                email=f'test{existing_users+i}@example.com',
                name=f'Test User {existing_users+i}',
                password_hash=generate_password_hash('password'),
                role='user',
                created_at=created_date
            )
            db.session.add(user)
            users.append(user)
        db.session.commit()
        print(f"Added {len(users)} test users")
        
        # Add admin to users list for receipts
        users.append(admin)
        
        # 3. Create income records for users (including admin)
        for user in users:
            # Skip if user already has income
            if UserIncome.query.filter_by(user_id=user.id).first():
                continue
                
            income = UserIncome(
                user_id=user.id,
                employment_income=random.randint(500000, 5000000),
                business_income=random.randint(1000000, 10000000),
                investment_income=random.randint(100000, 2000000),
                usd_consulting_income=random.randint(5000, 50000),
            )
            db.session.add(income)
        db.session.commit()
        print(f"Added income records for {len(users)} users")
        
        # 4. Create a variety of receipts across different categories and dates
        receipt_count = 0
        for user in users:
            # Add receipts across different time periods for better analytics
            time_periods = [
                (0, 7),     # Last week
                (8, 30),    # Last month
                (31, 90),   # Last quarter
                (91, 180),  # Last 6 months
            ]
            
            # Create 3 receipts per time period per user
            for period_start, period_end in time_periods:
                for _ in range(3):
                    days_ago = random.randint(period_start, period_end)
                    receipt_date = datetime.utcnow() - timedelta(days=days_ago)
                    
                    # Pick a random category
                    major_cat, minor_cat = random.choice(CATEGORIES)
                    
                    # Generate a receipt total
                    total = round(random.uniform(500, 15000), 2)
                    
                    # Create receipt
                    receipt = Receipt(
                        user_id=user.id,
                        vendor_name=random.choice(VENDORS),
                        vendor_address="123 Business St, Colombo",
                        date=receipt_date.date(),
                        total_amount=total,
                        vat_tax=round(total * 0.08, 2) if random.random() > 0.3 else 0,
                        sscl_tax=round(total * 0.02, 2) if random.random() > 0.5 else 0,
                        created_at=receipt_date,
                        expense_major_category=major_cat,
                        expense_minor_category=minor_cat
                    )
                    db.session.add(receipt)
                    db.session.flush()
                    
                    # Add 1-3 items to the receipt
                    remaining_total = total
                    item_count = random.randint(1, 3)
                    
                    for j in range(item_count):
                        # Last item gets remaining amount
                        if j == item_count - 1:
                            price = remaining_total
                        else:
                            price = round(total * random.uniform(0.2, 0.6), 2)
                            remaining_total -= price
                        
                        item = ReceiptItem(
                            receipt_id=receipt.id,
                            name=f"Item {j+1} for {major_cat}",
                            quantity=random.randint(1, 3),
                            price=price,
                            tax_deductible=random.random() > 0.3
                        )
                        db.session.add(item)
                    
                    receipt_count += 1
                    
                    # Commit in small batches to avoid timeouts
                    if receipt_count % 5 == 0:
                        db.session.commit()
        
        # Final commit for any remaining changes
        db.session.commit()
        
        # Print summary
        print("\nEssential test data created successfully!")
        print(f"Users: {User.query.count()}")
        print(f"Receipts: {Receipt.query.count()}")
        print(f"Receipt Items: {ReceiptItem.query.count()}")
        print(f"Income Records: {UserIncome.query.count()}")

if __name__ == "__main__":
    create_essential_dataset()