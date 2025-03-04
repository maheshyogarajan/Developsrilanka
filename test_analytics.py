"""
Test script to check the admin analytics data.
"""

from app import app
from admin_analytics import (
    get_user_statistics, 
    get_receipt_statistics,
    get_user_role_distribution,
    get_receipt_categories_breakdown,
    get_dashboard_data,
    get_income_statistics,
    get_tax_savings_statistics
)

def test_user_stats():
    user_stats = get_user_statistics()
    print("\n=== User Statistics ===")
    print(f"Total Users: {user_stats.get('total_users', 'N/A')}")
    print(f"Active Users: {user_stats.get('active_users', 'N/A')}")
    print(f"New Users (30 days): {user_stats.get('new_users', 'N/A')}")
    
    # Check user growth data
    user_growth = user_stats.get('user_growth', [])
    print("\nUser Growth Data:")
    for entry in user_growth:
        print(f"  {entry.get('date')}: {entry.get('count')} users")

def test_receipt_stats():
    receipt_stats = get_receipt_statistics()
    print("\n=== Receipt Statistics ===")
    print(f"Total Receipts: {receipt_stats.get('total_receipts', 'N/A')}")
    print(f"New Receipts (30 days): {receipt_stats.get('new_receipts', 'N/A')}")
    print(f"Average Receipt Amount: {receipt_stats.get('avg_amount', 'N/A')}")
    
    # Check daily activity data
    daily_activity = receipt_stats.get('daily_activity', [])
    print("\nDaily Receipt Activity:")
    for entry in daily_activity:
        print(f"  {entry.get('date')}: {entry.get('count')} receipts")
    
    # Check category breakdown
    categories = get_receipt_categories_breakdown()
    print("\nReceipt Categories Breakdown:")
    for cat in categories:
        print(f"  {cat.get('category', 'Unknown')}: {cat.get('count', 0)} receipts ({cat.get('percentage', 0):.1f}%)")

def test_income_stats():
    income_stats = get_income_statistics()
    print("\n=== Income Statistics ===")
    print(f"Total Income Records: {income_stats.get('total_income_records', 'N/A')}")
    
    lkr_business = income_stats.get('total_lkr_business_income', 'N/A')
    if isinstance(lkr_business, (int, float)):
        print(f"Total LKR Business Income: {lkr_business:,.2f}")
    else:
        print(f"Total LKR Business Income: {lkr_business}")
        
    usd_consulting = income_stats.get('total_usd_consulting_income', 'N/A')
    if isinstance(usd_consulting, (int, float)):
        print(f"Total USD Consulting Income: {usd_consulting:,.2f}")
    else:
        print(f"Total USD Consulting Income: {usd_consulting}")

def test_tax_savings_stats():
    tax_stats = get_tax_savings_statistics()
    print("\n=== Tax Savings Statistics ===")
    
    for key, label in [
        ('total_tax_deductible_amount', 'Total Tax Deductible Amount'), 
        ('estimated_tax_savings', 'Estimated Tax Savings'),
        ('tax_savings_lkr_business', 'Tax Savings from LKR Business Income'),
        ('tax_savings_usd_consulting', 'Tax Savings from USD Consulting Income')
    ]:
        value = tax_stats.get(key, 'N/A')
        if isinstance(value, (int, float)):
            print(f"{label}: {value:,.2f}")
        else:
            print(f"{label}: {value}")

if __name__ == "__main__":
    with app.app_context():
        test_user_stats()
        test_receipt_stats()
        test_income_stats()
        test_tax_savings_stats()