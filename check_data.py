"""
Check the current database status for admin panel data.
"""
from app import app, db
from models import User, Receipt, UserIncome
from datetime import datetime, timedelta
import json

def check_db_data():
    """Print database statistics for debugging admin panel data issues."""
    print("\n=== Database Statistics ===")
    
    # User statistics
    users = User.query.all()
    print(f"Total Users: {len(users)}")
    print(f"Users with Admin Role: {User.query.filter_by(role='admin').count()}")
    
    # User creation dates for growth chart
    user_dates = {}
    for user in users:
        date_str = user.created_at.strftime("%Y-%m-%d")
        user_dates[date_str] = user_dates.get(date_str, 0) + 1
    
    print("\nUser Growth (daily sign-ups):")
    for date, count in sorted(user_dates.items()):
        print(f"  {date}: {count} users")
    
    # Receipt statistics
    receipts = Receipt.query.all()
    print(f"\nTotal Receipts: {len(receipts)}")
    
    # Receipt creation dates for activity chart
    receipt_dates = {}
    for receipt in receipts:
        date_str = receipt.created_at.strftime("%Y-%m-%d")
        receipt_dates[date_str] = receipt_dates.get(date_str, 0) + 1
    
    print("\nDaily Receipt Activity:")
    for date, count in sorted(receipt_dates.items()):
        print(f"  {date}: {count} receipts")
    
    # Category breakdown
    categories = {}
    for receipt in receipts:
        cat = receipt.expense_major_category or "Uncategorized"
        categories[cat] = categories.get(cat, 0) + 1
    
    print("\nReceipt Categories:")
    for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat}: {count} receipts")
    
    # Income statistics
    incomes = UserIncome.query.all()
    print(f"\nUsers with Income Records: {len(incomes)}")
    
    if incomes:
        total_lkr_business = sum(i.business_income or 0 for i in incomes)
        total_usd_consulting = sum(i.usd_consulting_income or 0 for i in incomes)
        print(f"  Total LKR Business Income: {total_lkr_business:,.2f}")
        print(f"  Total USD Consulting Income: {total_usd_consulting:,.2f}")

if __name__ == "__main__":
    with app.app_context():
        check_db_data()