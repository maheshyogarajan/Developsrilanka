"""
Service for mapping receipt categories to chart of accounts.
This enables enhanced P&L reporting with professional accounting structure.
"""
from typing import Dict, List, Optional
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class AccountMappingService:
    """Maps receipt expense categories to chart of accounts for enhanced P&L reporting."""
    
    # Comprehensive mapping from receipt categories to account codes
    CATEGORY_TO_ACCOUNT_MAP = {
        # Common expense categories mapped to specific accounts
        'Meals': '5002',  # Meals & Entertainment
        'Food': '5002',   # Meals & Entertainment
        'Entertainment': '5002',  # Meals & Entertainment
        'Meals and Entertainment': '5002',  # Meals & Entertainment
        
        'Travel': '5003',      # Transport & Travel
        'Transport': '5003',   # Transport & Travel
        'Travel and Transportation': '5003',  # Transport & Travel
        'Accommodation': '5003',  # Transport & Travel
        'Fuel': '5003',        # Transport & Travel
        
        'Office Supplies': '5004',  # Office Supplies
        'Stationery': '5004',       # Office Supplies
        'Supplies': '5004',         # Office Supplies
        
        'Professional Services': '5005',  # Professional Services
        'Consulting': '5005',             # Professional Services
        'Legal': '5016',                  # Legal & Accounting
        'Accounting': '5016',             # Legal & Accounting
        
        'Marketing': '5006',        # Marketing & Advertising
        'Advertising': '5006',      # Marketing & Advertising
        'Marketing and Advertising': '5006',  # Marketing & Advertising
        'Promotion': '5006',        # Marketing & Advertising
        
        'Utilities': '5007',        # Utilities
        'Electricity': '5007',      # Utilities
        'Water': '5007',           # Utilities
        'Internet': '5040',        # Telephone & Internet
        'Phone': '5040',           # Telephone & Internet
        'Telephone': '5040',       # Telephone & Internet
        
        'Rent': '5008',            # Rent & Facilities
        'Facilities': '5008',      # Rent & Facilities
        'Office Rent': '5008',     # Rent & Facilities
        
        'Insurance': '5009',       # Insurance
        
        'Software': '5010',        # Software & SaaS
        'SaaS': '5010',           # Software & SaaS
        'Technology': '5010',      # Software & SaaS
        'Subscription': '5020',    # Subscriptions & Memberships
        
        'Bank Fees': '5011',       # Bank & Merchant Fees
        'Banking': '5011',         # Bank & Merchant Fees
        'Transaction Fees': '5011', # Bank & Merchant Fees
        
        'Training': '5015',        # Training & Development
        'Education': '5015',       # Training & Development
        'Development': '5015',     # Training & Development
        
        'Maintenance': '5030',     # Repairs & Maintenance
        'Repairs': '5030',         # Repairs & Maintenance
        
        'Licenses': '5080',        # Licences, Permits & Government Fees
        'Permits': '5080',         # Licences, Permits & Government Fees
        'Government Fees': '5080', # Licences, Permits & Government Fees
        
        # Payroll related
        'Payroll': '5013',         # Payroll – Wages & Salaries
        'Wages': '5013',           # Payroll – Wages & Salaries
        'Salaries': '5013',        # Payroll – Wages & Salaries
        'Benefits': '5014',        # Employee Benefits
        
        # Additional common categories
        'Administrative Expenses': '5001',  # Operating Expenses – General
        'Administration': '5001',           # Operating Expenses – General
        'Office Expenses': '5004',          # Office Supplies
        'Equipment': '5030',                # Repairs & Maintenance
        'Computers': '5010',                # Software & SaaS
        'Medical': '5014',                  # Employee Benefits
        'Health': '5014',                   # Employee Benefits
        'Telecommunications': '5040',       # Telephone & Internet
        'Communication': '5040',            # Telephone & Internet
        'Vehicle': '5003',                  # Transport & Travel
        'Auto': '5003',                     # Transport & Travel
        'Automobile': '5003',               # Transport & Travel
        'Postage': '5001',                  # Operating Expenses – General
        'Shipping': '5003',                 # Transport & Travel
        'Cleaning': '5008',                 # Rent & Facilities
        'Security': '5008',                 # Rent & Facilities
        'Office Equipment': '5004',         # Office Supplies
        'Computer Equipment': '5010',       # Software & SaaS
        'Office Furniture': '5008',         # Rent & Facilities
        'Research': '5015',                 # Training & Development
        'Books': '5015',                    # Training & Development
        'Publications': '5015',             # Training & Development
        
        # Default fallbacks
        'Operating Expenses': '5001',  # Operating Expenses – General
        'General': '5001',             # Operating Expenses – General
        'Other': '5900',               # Other Expenses / Miscellaneous
        'Miscellaneous': '5900',       # Other Expenses / Miscellaneous
    }
    
    # Account hierarchy for P&L grouping
    ACCOUNT_HIERARCHY = {
        'cost_of_goods_sold': {
            'name': 'Cost of Goods Sold',
            'accounts': ['3100', '3200', '3300'],
            'order': 1
        },
        'operating_expenses': {
            'name': 'Operating Expenses',
            'accounts': [
                '5001', '5002', '5003', '5004', '5005', '5006', '5007', '5008', 
                '5009', '5010', '5011', '5012', '5013', '5014', '5015', '5016',
                '5020', '5030', '5040', '5050', '5080'
            ],
            'order': 2
        },
        'other_expenses': {
            'name': 'Other Expenses',
            'accounts': ['5060', '5900'],
            'order': 3
        }
    }
    
    @classmethod
    def get_account_code_for_category(cls, category: str, minor_category: Optional[str] = None) -> str:
        """
        Map a receipt category to an account code.
        
        Args:
            category: The major expense category from receipt scanning
            minor_category: The minor expense category (more specific)
            
        Returns:
            Account code (e.g., '5002') or default '5001' for unmapped categories
        """
        # Prioritize minor category if available
        if minor_category:
            # Try exact match on minor category first
            account_code = cls.CATEGORY_TO_ACCOUNT_MAP.get(minor_category)
            if account_code:
                return account_code
                
            # Try case-insensitive match on minor category
            minor_lower = minor_category.lower()
            for cat, code in cls.CATEGORY_TO_ACCOUNT_MAP.items():
                if cat.lower() == minor_lower:
                    return code
                    
            # Try partial match on minor category
            for cat, code in cls.CATEGORY_TO_ACCOUNT_MAP.items():
                if cat.lower() in minor_lower or minor_lower in cat.lower():
                    return code
        
        # Fall back to major category if minor category didn't match
        if not category:
            return '5001'  # Default to Operating Expenses - General
            
        # Try exact match on major category
        account_code = cls.CATEGORY_TO_ACCOUNT_MAP.get(category)
        if account_code:
            return account_code
            
        # Try case-insensitive match on major category
        category_lower = category.lower()
        for cat, code in cls.CATEGORY_TO_ACCOUNT_MAP.items():
            if cat.lower() == category_lower:
                return code
                
        # Try partial match on major category
        for cat, code in cls.CATEGORY_TO_ACCOUNT_MAP.items():
            if cat.lower() in category_lower or category_lower in cat.lower():
                return code
                
        # Default fallback
        logger.warning(f"No account mapping found for category: {category}, minor: {minor_category}")
        return '5001'  # Operating Expenses - General
    
    @classmethod
    def group_expenses_by_accounts(cls, expense_data: List[Dict]) -> Dict:
        """
        Group expense data by chart of accounts structure.
        
        Args:
            expense_data: List of expense dictionaries with 'category' and 'amount'
            
        Returns:
            Dictionary grouped by account hierarchy for P&L display
        """
        # Initialize account totals
        account_totals = {}
        
        # Process each expense
        for expense in expense_data:
            category = expense.get('category', '')
            minor_category = expense.get('minor_category', '')
            amount = Decimal(str(expense.get('amount', 0)))
            
            account_code = cls.get_account_code_for_category(category, minor_category)
            
            if account_code not in account_totals:
                account_totals[account_code] = Decimal('0')
            account_totals[account_code] += amount
        
        # Group by hierarchy
        grouped_expenses = {}
        
        for group_key, group_info in cls.ACCOUNT_HIERARCHY.items():
            group_accounts = []
            group_total = Decimal('0')
            
            for account_code in group_info['accounts']:
                if account_code in account_totals:
                    amount = account_totals[account_code]
                    group_accounts.append({
                        'account_code': account_code,
                        'amount': float(amount)
                    })
                    group_total += amount
            
            if group_accounts:  # Only include groups with expenses
                grouped_expenses[group_key] = {
                    'name': group_info['name'],
                    'accounts': group_accounts,
                    'total': float(group_total),
                    'order': group_info['order']
                }
        
        return grouped_expenses
    
    @classmethod
    def get_account_name_for_code(cls, account_code: str) -> str:
        """
        Get the account name for a given account code.
        
        Args:
            account_code: The account code (e.g., '5002')
            
        Returns:
            Account name (e.g., 'Meals & Entertainment')
        """
        # This would ideally query the Account model, but for now we'll use static mapping
        account_names = {
            '3100': 'COGS – Materials / Merchandise',
            '3200': 'COGS – Direct Labour', 
            '3300': 'COGS – Freight In & Import Duty',
            '5001': 'Operating Expenses – General',
            '5002': 'Meals & Entertainment',
            '5003': 'Transport & Travel',
            '5004': 'Office Supplies',
            '5005': 'Professional Services',
            '5006': 'Marketing & Advertising',
            '5007': 'Utilities',
            '5008': 'Rent & Facilities',
            '5009': 'Insurance',
            '5010': 'Software & SaaS',
            '5011': 'Bank & Merchant Fees',
            '5012': 'Depreciation Expense',
            '5013': 'Payroll – Wages & Salaries',
            '5014': 'Employee Benefits (EPF/ETF employer share, health)',
            '5015': 'Training & Development',
            '5016': 'Legal & Accounting',
            '5020': 'Subscriptions & Memberships',
            '5030': 'Repairs & Maintenance',
            '5040': 'Telephone & Internet',
            '5050': 'Bad Debt Expense',
            '5060': 'Interest Expense',
            '5080': 'Licences, Permits & Government Fees',
            '5900': 'Other Expenses / Miscellaneous',
        }
        
        return account_names.get(account_code, f'Account {account_code}')
    
    @classmethod
    def enhance_expense_breakdown(cls, category_breakdown: List[Dict]) -> Dict:
        """
        Transform existing category breakdown into account-based structure.
        
        Args:
            category_breakdown: List of {'category': str, 'amount': float, 'count': int}
            
        Returns:
            Enhanced breakdown grouped by chart of accounts
        """
        # Convert to format expected by group_expenses_by_accounts
        expense_data = [
            {'category': item['category'], 'amount': item['amount']}
            for item in category_breakdown
        ]
        
        # Group by accounts
        grouped = cls.group_expenses_by_accounts(expense_data)
        
        # Add account names and enhance structure
        for group_key, group_data in grouped.items():
            for account in group_data['accounts']:
                account['name'] = cls.get_account_name_for_code(account['account_code'])
                account['display_name'] = f"{account['account_code']} - {account['name']}"
        
        return grouped