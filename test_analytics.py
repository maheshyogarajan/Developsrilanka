"""
Test script to check the admin analytics data.
"""

import json
from datetime import datetime
from app import app
import admin_analytics

def test_user_stats():
    with app.app_context():
        print("\n===== USER STATISTICS =====")
        stats = admin_analytics.get_user_statistics()
        print(f"Total Users: {stats['total_users']}")
        print(f"Active Users: {stats['active_users']}")
        print(f"Inactive Users: {stats['inactive_users']}")
        print(f"Growth Rate: {stats['growth_rate']}%")
        print("User Registrations by Date:")
        for date, count in stats['registrations_by_date'].items():
            print(f"  {date}: {count} users")
            
def test_receipt_stats():
    with app.app_context():
        print("\n===== RECEIPT STATISTICS =====")
        stats = admin_analytics.get_receipt_statistics()
        print(f"Total Receipts: {stats['total_receipts']}")
        print(f"Recent Receipts (30d): {stats['recent_receipts']}")
        print(f"Total Amount: {stats['total_amount']}")
        print(f"Avg Amount: {stats['avg_amount']}")
        print(f"Median Amount: {stats['median_amount']}")
        print(f"Tax Deductible %: {stats['tax_deductible_percentage']}%")
        
        print("\nTop Categories:")
        for cat in stats['top_categories']:
            print(f"  {cat['category']}: {cat['count']} receipts ({cat['percentage']:.1f}%)")
            
        print("\nReceipts by Date:")
        for date, count in stats['receipts_by_date'].items():
            print(f"  {date}: {count} receipts")

def test_income_stats():
    with app.app_context():
        print("\n===== INCOME STATISTICS =====")
        stats = admin_analytics.get_income_statistics()
        print(f"Employment Income: {stats['employment_income']}")
        print(f"Business Income: {stats['business_income']}")
        print(f"Investment Income: {stats['investment_income']}")
        print(f"USD Consulting Income: {stats['usd_consulting_income']}")
        print(f"Total LKR Income: {stats['total_lkr_income']}")
        print(f"Total Income: {stats['total_income']}")
        print(f"Users with Income Data: {stats['users_with_income']}")
        
        print("\nIncome Distribution:")
        for type, pct in stats['income_distribution'].items():
            print(f"  {type}: {pct:.1f}%")

def test_tax_savings_stats():
    with app.app_context():
        print("\n===== TAX SAVINGS STATISTICS =====")
        stats = admin_analytics.get_tax_savings_statistics()
        print(f"Total Deductible Amount: {stats['total_deductible_amount']}")
        print(f"LKR Business Deductible: {stats['lkr_business_deductible']}")
        print(f"USD Consulting Deductible: {stats['usd_consulting_deductible']}")
        print(f"LKR Business Savings: {stats['lkr_business_savings']}")
        print(f"USD Consulting Savings: {stats['usd_consulting_savings']}")
        print(f"Total Savings: {stats['total_savings']}")
        print(f"Savings %: {stats['savings_percentage']}%")

if __name__ == "__main__":
    print(f"Running analytics tests at {datetime.utcnow().isoformat()}")
    test_user_stats()
    test_receipt_stats()
    test_income_stats()
    test_tax_savings_stats()
    print("\nTests completed.")