import json
from app import app
from admin_analytics import get_receipt_statistics, get_receipt_categories_breakdown

def check_analytics_data():
    with app.app_context():
        stats = get_receipt_statistics()
        print("Top Categories:")
        print(json.dumps(stats['top_categories'], indent=2))
        
        print("\nReceiptsByDate:")
        print(json.dumps(stats['receipts_by_date'], indent=2))
        
        print("\nDetailed Categories (count, percentage):")
        categories = get_receipt_categories_breakdown()
        for cat in categories:
            print(f"- {cat['category']}: {cat['count']} receipts, {cat['amount']:.2f} amount")
            print(f"  Count %: {cat['count_percentage']:.2f}, Amount %: {cat['amount_percentage']:.2f}")
        
        # Basic statistics
        receipt_count = stats['recent_receipts']
        print(f"\nRecent receipt count: {receipt_count}")
        
        # Check for rounding issues in percentages
        total_percent = sum(cat['percentage'] for cat in stats['top_categories'])
        print(f"Sum of all category percentages: {total_percent:.2f}%")
        
        # Check for zero percentages
        print("\nCategories with 0% values:")
        for cat in stats['top_categories']:
            if cat['percentage'] == 0:
                print(f"- {cat['category']}: {cat['count']} receipts but 0% percentage")
            elif cat['percentage'] < 0.1:
                print(f"- {cat['category']}: {cat['count']} receipts with very low percentage: {cat['percentage']:.4f}%")

if __name__ == "__main__":
    check_analytics_data()