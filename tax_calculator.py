"""
Tax calculator module for personal and business tax calculations.
This module provides functions to calculate tax savings based on different
organization types (personal or business) and their specific tax rates.
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import func
from app import db
from models import Receipt, UserIncome, Organization, User

def calculate_personal_tax_savings(user_id, tax_deductible_amount, time_period=None):
    """
    Calculate personal tax savings based on user's income sources and their personal organization's tax rates.
    
    Args:
        user_id: User ID to calculate tax savings for
        tax_deductible_amount: Total tax deductible expenses amount
        time_period: Optional time period filter (e.g., 'month', 'year', 'quarter')
    
    Returns:
        Dictionary with tax savings information
    """
    from models import User, Organization, UserIncome
    
    user = User.query.get(user_id)
    if not user:
        return None
    
    # Get personal organization and tax rates
    personal_org = user.personal_organization
    if not personal_org:
        logging.warning(f"User {user_id} has no personal organization, using default tax rates")
        # Use default tax rates if no personal organization
        employment_tax_rate = 24.0
        business_income_tax_rate = 36.0
        investment_tax_rate = 14.0
        consulting_tax_rate = 15.0
    else:
        # Use organization-specific tax rates
        employment_tax_rate = personal_org.employment_tax_rate or 24.0
        business_income_tax_rate = personal_org.business_income_tax_rate or 36.0
        investment_tax_rate = personal_org.investment_tax_rate or 14.0
        consulting_tax_rate = personal_org.consulting_tax_rate or 15.0
    
    # Get income details
    income_details = UserIncome.query.filter_by(user_id=user_id).first()
    if not income_details:
        logging.warning(f"No income details found for user {user_id}")
        return {
            'organization_type': 'personal',
            'tax_deductible_amount': tax_deductible_amount,
            'tax_savings': 0.0,
            'total_income': 0.0,
            'details': {
                'business_income': {
                    'income': 0.0,
                    'tax_rate': business_income_tax_rate,
                    'deduction': 0.0,
                    'savings': 0.0
                },
                'consulting_income': {
                    'income': 0.0,
                    'tax_rate': consulting_tax_rate,
                    'deduction': 0.0,
                    'savings': 0.0
                },
                'employment_income': {
                    'income': 0.0,
                    'tax_rate': employment_tax_rate,
                    'deduction': 0.0,
                    'savings': 0.0
                },
                'investment_income': {
                    'income': 0.0,
                    'tax_rate': investment_tax_rate,
                    'deduction': 0.0,
                    'savings': 0.0
                }
            }
        }
    
    # Extract income values with fallback to 0
    business_income = float(income_details.business_income or 0)
    consulting_income = float(income_details.usd_consulting_income or 0)
    employment_income = float(income_details.employment_income or 0)
    investment_income = float(income_details.investment_income or 0)
    
    # Convert rates to percentages for calculation
    employment_tax_pct = employment_tax_rate / 100.0
    business_income_tax_pct = business_income_tax_rate / 100.0
    investment_tax_pct = investment_tax_rate / 100.0
    consulting_tax_pct = consulting_tax_rate / 100.0
    
    # Calculate total income
    total_income = business_income + employment_income + investment_income
    
    # Initialize deductions for each income type
    deduction_business = 0.0
    deduction_consulting = 0.0
    deduction_employment = 0.0
    deduction_investment = 0.0
    
    # Allocate tax deductible expenses by priority based on tax rates
    # (higher rates first for maximum savings)
    remaining_deduction = tax_deductible_amount
    
    # Priority 1: Business Income (highest rate)
    if business_income > 0 and remaining_deduction > 0:
        deduction_business = min(remaining_deduction, business_income)
        remaining_deduction -= deduction_business
    
    # Priority 2: Employment Income
    if employment_income > 0 and remaining_deduction > 0:
        deduction_employment = min(remaining_deduction, employment_income)
        remaining_deduction -= deduction_employment
    
    # Priority 3: USD Consulting Income
    if consulting_income > 0 and remaining_deduction > 0:
        deduction_consulting = min(remaining_deduction, consulting_income)
        remaining_deduction -= deduction_consulting
    
    # Priority 4: Investment Income (lowest rate)
    if investment_income > 0 and remaining_deduction > 0:
        deduction_investment = min(remaining_deduction, investment_income)
        remaining_deduction -= deduction_investment
    
    # Calculate tax savings for each income type
    savings_business = deduction_business * business_income_tax_pct
    savings_consulting = deduction_consulting * consulting_tax_pct
    savings_employment = deduction_employment * employment_tax_pct
    savings_investment = deduction_investment * investment_tax_pct
    
    # Calculate total tax savings
    total_tax_savings = (
        savings_business +
        savings_consulting +
        savings_employment +
        savings_investment
    )
    
    # Return results
    return {
        'organization_type': 'personal',
        'tax_deductible_amount': tax_deductible_amount,
        'tax_savings': total_tax_savings,
        'total_income': total_income,
        'details': {
            'business_income': {
                'income': business_income,
                'tax_rate': business_income_tax_rate,
                'deduction': deduction_business,
                'savings': savings_business
            },
            'consulting_income': {
                'income': consulting_income,
                'tax_rate': consulting_tax_rate,
                'deduction': deduction_consulting,
                'savings': savings_consulting
            },
            'employment_income': {
                'income': employment_income,
                'tax_rate': employment_tax_rate,
                'deduction': deduction_employment,
                'savings': savings_employment
            },
            'investment_income': {
                'income': investment_income,
                'tax_rate': investment_tax_rate,
                'deduction': deduction_investment,
                'savings': savings_investment
            }
        }
    }

def calculate_business_tax_savings(org_id, tax_deductible_amount, time_period=None):
    """
    Calculate business tax savings based on organization's corporate and dividend tax rates.
    
    Args:
        org_id: Organization ID to calculate tax savings for
        tax_deductible_amount: Total tax deductible expenses amount
        time_period: Optional time period filter (e.g., 'month', 'year', 'quarter')
    
    Returns:
        Dictionary with tax savings information
    """
    from models import Organization
    
    organization = Organization.query.get(org_id)
    if not organization:
        return None
    
    # Get organization tax rates with fallbacks to defaults
    corporate_tax_rate = organization.corporate_tax_rate or 24.0
    dividend_tax_rate = organization.dividend_tax_rate or 14.0
    
    # Convert rates to percentages for calculation
    corporate_tax_pct = corporate_tax_rate / 100.0
    dividend_tax_pct = dividend_tax_rate / 100.0
    
    # Calculate effective tax rate
    # Formula: total_tax = corporate_tax + (1 - corporate_tax) * dividend_tax
    effective_tax_pct = corporate_tax_pct + ((1 - corporate_tax_pct) * dividend_tax_pct)
    effective_tax_rate = effective_tax_pct * 100
    
    # Calculate corporate tax savings (direct savings from deduction)
    corporate_tax_savings = tax_deductible_amount * corporate_tax_pct
    
    # Calculate after-tax amount (what would have been distributed as dividends)
    after_corporate_tax_amount = tax_deductible_amount * (1 - corporate_tax_pct)
    
    # Calculate dividend tax savings (savings on the dividend taxation)
    dividend_tax_savings = after_corporate_tax_amount * dividend_tax_pct
    
    # Calculate total tax savings
    total_tax_savings = corporate_tax_savings + dividend_tax_savings
    
    # Return results
    return {
        'organization_type': 'business',
        'tax_deductible_amount': tax_deductible_amount,
        'corporate_tax_rate': corporate_tax_rate,
        'dividend_tax_rate': dividend_tax_rate,
        'effective_tax_rate': effective_tax_rate,
        'tax_savings': total_tax_savings,
        'details': {
            'corporate_tax': {
                'tax_rate': corporate_tax_rate,
                'savings': corporate_tax_savings
            },
            'dividend_tax': {
                'tax_rate': dividend_tax_rate,
                'after_corporate_tax_amount': after_corporate_tax_amount,
                'savings': dividend_tax_savings
            }
        }
    }

def get_tax_deductible_amount(user_id, organization_id=None, time_period=None):
    """
    Calculate total tax deductible amount from receipts for a user or organization.
    
    Args:
        user_id: User ID to get tax deductible amount for
        organization_id: Optional organization ID filter
        time_period: Optional time period filter (e.g., 'month', 'year', 'quarter')
    
    Returns:
        Total tax deductible amount
    """
    from models import Receipt
    
    # Start with base query for user's receipts
    query = Receipt.query.filter_by(user_id=user_id)
    
    # Add organization filter if specified
    if organization_id:
        query = query.filter_by(organization_id=organization_id)
    
    # Add time period filter if specified
    if time_period:
        if time_period == 'month':
            start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(Receipt.date >= start_date)
        elif time_period == 'quarter':
            current_month = datetime.utcnow().month
            quarter_start_month = ((current_month - 1) // 3) * 3 + 1
            start_date = datetime.utcnow().replace(month=quarter_start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(Receipt.date >= start_date)
        elif time_period == 'year':
            start_date = datetime.utcnow().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(Receipt.date >= start_date)
    
    # Get all matching receipts
    receipts = query.all()
    
    # Calculate total tax deductible amount
    total_deductible = 0.0
    for receipt in receipts:
        deductible = receipt.get_tax_deductible_amount()
        total_deductible += min(deductible, receipt.total_amount)
    
    return total_deductible

def calculate_tax_savings_by_organization_type(user_id, organization_id=None, time_period=None):
    """
    Calculate tax savings based on the organization type.
    
    Args:
        user_id: User ID to calculate tax savings for
        organization_id: Optional organization ID; if None, uses personal organization
        time_period: Optional time period filter (e.g., 'month', 'year', 'quarter')
    
    Returns:
        Dictionary with tax savings information
    """
    from models import User, Organization
    
    # Get tax deductible amount
    tax_deductible_amount = get_tax_deductible_amount(
        user_id=user_id,
        organization_id=organization_id,
        time_period=time_period
    )
    
    # If no organization specified, use user's personal organization
    if not organization_id:
        user = User.query.get(user_id)
        if user and user.personal_organization_id:
            organization_id = user.personal_organization_id
    
    # Get organization to determine type
    organization = Organization.query.get(organization_id) if organization_id else None
    
    if not organization:
        # Fallback to personal tax calculation with default rates
        logging.warning(f"No organization found for ID {organization_id}, using personal calculation")
        return calculate_personal_tax_savings(user_id, tax_deductible_amount, time_period)
    
    # Choose calculation based on organization type
    if organization.is_personal:
        # Personal organization calculation
        return calculate_personal_tax_savings(user_id, tax_deductible_amount, time_period)
    else:
        # Business organization calculation
        return calculate_business_tax_savings(organization_id, tax_deductible_amount, time_period)