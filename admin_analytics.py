"""
Administrative analytics module for optimized data processing and statistics.
This module provides efficient database query functions and caching mechanisms
for the admin dashboard to improve performance.
"""

import time
import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from sqlalchemy import func, desc, case, extract, cast, Float, text
from sqlalchemy.sql import label

from app import app, db
from models import User, Receipt, ReceiptItem, UserIncome

# Configure logging
logger = logging.getLogger(__name__)

# Cache durations (in seconds)
CACHE_DURATION_SHORT = 60 * 5       # 5 minutes
CACHE_DURATION_MEDIUM = 60 * 30     # 30 minutes
CACHE_DURATION_LONG = 60 * 60 * 3   # 3 hours

# Cache for storing pre-computed results
_cache = {}

def timed_lru_cache(seconds=600, maxsize=128):
    """
    Decorator for LRU cache with timeout.
    
    Args:
        seconds: Cache lifetime in seconds
        maxsize: Maximum cache size
        
    Returns:
        Decorated function with timed cache
    """
    def decorator(func):
        # Apply standard LRU cache
        cached_func = lru_cache(maxsize=maxsize)(func)
        
        # Track last clear time
        cached_func.last_clear_time = time.time()
        
        def wrapper(*args, **kwargs):
            # Check if cache needs resetting
            now = time.time()
            if now - cached_func.last_clear_time > seconds:
                cached_func.cache_clear()
                cached_func.last_clear_time = now
                
            return cached_func(*args, **kwargs)
            
        # Copy func attributes
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
        
    return decorator

def memoize_with_timeout(timeout):
    """
    Decorator for memoizing function results with a timeout.
    
    Args:
        timeout: Cache timeout in seconds
        
    Returns:
        Decorated function with timeout-based memoization
    """
    def decorator(func):
        func_key = func.__name__
        
        def wrapper(*args, **kwargs):
            # Create a hash key from the function args/kwargs
            key_parts = [func_key]
            key_parts.extend([str(arg) for arg in args])
            key_parts.extend([f"{k}={v}" for k, v in sorted(kwargs.items())])
            cache_key = ":".join(key_parts)
            
            # Check cache
            if cache_key in _cache:
                result, timestamp = _cache[cache_key]
                if time.time() - timestamp <= timeout:
                    logger.debug(f"Cache hit for {func_key}")
                    return result
                    
            # Execute function and store result
            logger.debug(f"Cache miss for {func_key}")
            result = func(*args, **kwargs)
            _cache[cache_key] = (result, time.time())
            return result
            
        return wrapper
    return decorator

def clear_analytics_cache():
    """Clear all cached analytics data."""
    global _cache
    _cache = {}
    logger.info("Analytics cache cleared")

#
# User Statistics
#

@memoize_with_timeout(CACHE_DURATION_MEDIUM)
def get_user_statistics(days=30):
    """
    Get aggregated user statistics with optimized queries.
    
    Args:
        days: Number of days to look back
        
    Returns:
        Dictionary with user statistics
    """
    logger.info(f"Calculating user statistics for past {days} days")
    start_time = time.time()
    
    # Calculate reference date
    reference_date = datetime.utcnow() - timedelta(days=days)
    
    # Stats to calculate
    total_users = User.query.count()
    new_users = User.query.filter(User.created_at >= reference_date).count()
    
    # Active users with optimized query (users who have receipts in the period)
    active_users_subquery = db.session.query(Receipt.user_id)\
        .filter(Receipt.created_at >= reference_date)\
        .group_by(Receipt.user_id)\
        .subquery()
        
    active_users = db.session.query(func.count())\
        .select_from(User)\
        .join(active_users_subquery, User.id == active_users_subquery.c.user_id)\
        .scalar() or 0
    
    # Calculate growth rate
    previous_period_start = reference_date - timedelta(days=days)
    previous_users = User.query.filter(
        User.created_at >= previous_period_start,
        User.created_at < reference_date
    ).count()
    
    growth_rate = ((new_users - previous_users) / max(previous_users, 1)) * 100 if previous_users else 0
    
    # User registrations over time (optimized with date bucketing)
    user_registrations = db.session.query(
        func.date_trunc('day', User.created_at).label('day'),
        func.count(User.id).label('count')
    ).filter(User.created_at >= reference_date)\
     .group_by('day')\
     .order_by('day')\
     .all()
    
    # Convert to serializable format
    registrations_by_date = {
        day.strftime('%Y-%m-%d'): count 
        for day, count in user_registrations
    }
    
    # Elapsed time for performance monitoring
    elapsed = time.time() - start_time
    logger.info(f"User statistics calculated in {elapsed:.2f} seconds")
    
    return {
        'total_users': total_users,
        'new_users': new_users,
        'active_users': active_users,
        'inactive_users': total_users - active_users,
        'growth_rate': round(growth_rate, 2),
        'registrations_by_date': registrations_by_date
    }

@memoize_with_timeout(CACHE_DURATION_MEDIUM)
def get_user_role_distribution():
    """
    Get distribution of users by role with optimized query.
    
    Returns:
        Dictionary with role counts
    """
    role_counts = db.session.query(
        User.role, 
        func.count(User.id).label('count')
    ).group_by(User.role).all()
    
    return {role: count for role, count in role_counts}

#
# Receipt Statistics
#

@memoize_with_timeout(CACHE_DURATION_MEDIUM)
def get_receipt_statistics(days=30):
    """
    Get aggregated receipt statistics with optimized queries.
    
    Args:
        days: Number of days to look back
        
    Returns:
        Dictionary with receipt statistics
    """
    logger.info(f"Calculating receipt statistics for past {days} days")
    start_time = time.time()
    
    # Calculate reference date
    reference_date = datetime.utcnow() - timedelta(days=days)
    
    # Basic counts
    total_receipts = Receipt.query.count()
    recent_receipts = Receipt.query.filter(Receipt.created_at >= reference_date).count()
    
    # Aggregated financial data
    financials = db.session.query(
        func.sum(Receipt.total_amount).label('total_amount'),
        func.avg(Receipt.total_amount).label('avg_amount'),
        func.min(Receipt.total_amount).label('min_amount'),
        func.max(Receipt.total_amount).label('max_amount'),
        # Approximate median using percentile_cont (PostgreSQL specific)
        func.percentile_cont(0.5).within_group(
            Receipt.total_amount.asc()
        ).label('median_amount')
    ).filter(Receipt.created_at >= reference_date).first()
    
    # Safe extraction accounting for None results
    total_amount = financials.total_amount or 0
    avg_amount = financials.avg_amount or 0
    min_amount = financials.min_amount or 0
    max_amount = financials.max_amount or 0
    median_amount = financials.median_amount or 0
    
    # Top expense categories - optimized with limit
    category_data = db.session.query(
        Receipt.expense_major_category,
        func.count(Receipt.id).label('count'),
        func.sum(Receipt.total_amount).label('amount')
    ).filter(
        Receipt.expense_major_category.isnot(None),
        Receipt.created_at >= reference_date
    ).group_by(Receipt.expense_major_category)\
     .order_by(desc('count'))\
     .limit(10)\
     .all()
    
    # Format category data
    top_categories = []
    for category, count, amount in category_data:
        if category:  # Skip None categories
            top_categories.append({
                'category': category,
                'count': count,
                'amount': float(amount) if amount else 0,
                'percentage': (count / max(recent_receipts, 1) * 100) if recent_receipts > 0 else 0
            })
    
    # Tax deductible statistics
    tax_deductible_stats = db.session.query(
        func.sum(case((ReceiptItem.tax_deductible == True, ReceiptItem.price), else_=0)).label('tax_deductible_amount'),
        func.sum(ReceiptItem.price).label('total_amount')
    ).join(Receipt, ReceiptItem.receipt_id == Receipt.id)\
     .filter(Receipt.created_at >= reference_date)\
     .first()
     
    tax_deductible_amount = tax_deductible_stats.tax_deductible_amount or 0
    item_total_amount = tax_deductible_stats.total_amount or 0
    tax_deductible_percentage = (tax_deductible_amount / max(item_total_amount, 1) * 100) if item_total_amount > 0 else 0
    
    # Daily receipt counts (optimized with date bucketing)
    daily_counts = db.session.query(
        func.date_trunc('day', Receipt.created_at).label('day'),
        func.count(Receipt.id).label('count')
    ).filter(Receipt.created_at >= reference_date)\
     .group_by('day')\
     .order_by('day')\
     .all()
    
    # Convert to serializable format
    receipts_by_date = {
        day.strftime('%Y-%m-%d'): count 
        for day, count in daily_counts
    }
    
    # Elapsed time for performance monitoring
    elapsed = time.time() - start_time
    logger.info(f"Receipt statistics calculated in {elapsed:.2f} seconds")
    
    return {
        'total_receipts': total_receipts,
        'recent_receipts': recent_receipts,
        'total_amount': float(total_amount),
        'avg_amount': float(avg_amount),
        'min_amount': float(min_amount),
        'max_amount': float(max_amount),
        'median_amount': float(median_amount),
        'top_categories': top_categories,
        'tax_deductible_amount': float(tax_deductible_amount),
        'tax_deductible_percentage': float(tax_deductible_percentage),
        'receipts_by_date': receipts_by_date
    }

@memoize_with_timeout(CACHE_DURATION_LONG)
def get_receipt_categories_breakdown():
    """
    Get detailed breakdown of receipt categories with optimized query.
    
    Returns:
        List of category statistics
    """
    # Optimize with single query for all category metrics
    category_data = db.session.query(
        Receipt.expense_major_category,
        func.count(Receipt.id).label('count'),
        func.sum(Receipt.total_amount).label('total_amount'),
        func.avg(Receipt.total_amount).label('avg_amount'),
        func.min(Receipt.created_at).label('first_seen'),
        func.max(Receipt.created_at).label('last_seen')
    ).filter(Receipt.expense_major_category.isnot(None))\
     .group_by(Receipt.expense_major_category)\
     .order_by(desc('count'))\
     .all()
     
    # Calculate totals for percentages
    total_count = sum(count for _, count, _, _, _, _ in category_data)
    total_amount = sum(amount for _, _, amount, _, _, _ in category_data if amount)
    
    # Format results
    result = []
    for category, count, amount, avg, first_seen, last_seen in category_data:
        if category:  # Skip None categories
            result.append({
                'category': category,
                'count': count,
                'amount': float(amount) if amount else 0,
                'avg_amount': float(avg) if avg else 0,
                'count_percentage': (count / max(total_count, 1) * 100) if total_count else 0,
                'amount_percentage': (amount / max(total_amount, 1) * 100) if total_amount and amount else 0,
                'first_seen': first_seen,
                'last_seen': last_seen
            })
            
    return result

#
# Income Statistics
#

@memoize_with_timeout(CACHE_DURATION_MEDIUM)
def get_income_statistics():
    """
    Get aggregated income statistics with optimized queries.
    
    Returns:
        Dictionary with income statistics
    """
    # Aggregate all income sources
    income_data = db.session.query(
        func.sum(UserIncome.employment_income).label('employment_income'),
        func.sum(UserIncome.business_income).label('business_income'),
        func.sum(UserIncome.investment_income).label('investment_income'),
        func.sum(UserIncome.usd_consulting_income).label('usd_consulting_income')
    ).first()
    
    # Extract and safely handle None values
    employment_income = income_data.employment_income or 0
    business_income = income_data.business_income or 0
    investment_income = income_data.investment_income or 0
    usd_consulting_income = income_data.usd_consulting_income or 0
    
    # Calculate totals
    total_lkr_income = employment_income + business_income + investment_income
    total_income = total_lkr_income + usd_consulting_income  # Simple sum for overview
    
    # Calculate percentage distributions
    income_distribution = {
        'employment': (employment_income / max(total_income, 1) * 100) if total_income else 0,
        'business': (business_income / max(total_income, 1) * 100) if total_income else 0,
        'investment': (investment_income / max(total_income, 1) * 100) if total_income else 0,
        'usd_consulting': (usd_consulting_income / max(total_income, 1) * 100) if total_income else 0
    }
    
    # Count users with income data
    users_with_income = UserIncome.query.count()
    
    return {
        'employment_income': float(employment_income),
        'business_income': float(business_income),
        'investment_income': float(investment_income),
        'usd_consulting_income': float(usd_consulting_income),
        'total_lkr_income': float(total_lkr_income),
        'total_income': float(total_income),
        'income_distribution': income_distribution,
        'users_with_income': users_with_income
    }

#
# Tax Savings Statistics
#

@memoize_with_timeout(CACHE_DURATION_MEDIUM)
def get_tax_savings_statistics():
    """
    Calculate tax savings statistics based on income and deductible expenses.
    
    Returns:
        Dictionary with tax savings statistics
    """
    # Get income statistics first
    income_stats = get_income_statistics()
    
    # Get deductible amounts
    deductible_query = db.session.query(
        Receipt.user_id,
        func.sum(case((ReceiptItem.tax_deductible == True, ReceiptItem.price), else_=0)).label('tax_deductible_amount')
    ).join(ReceiptItem, Receipt.id == ReceiptItem.receipt_id)\
     .group_by(Receipt.user_id)\
     .subquery()
    
    # Join with income data
    tax_data = db.session.query(
        func.sum(UserIncome.business_income).label('business_income'),
        func.sum(UserIncome.usd_consulting_income).label('usd_consulting_income'),
        func.sum(deductible_query.c.tax_deductible_amount).label('total_deductible')
    ).outerjoin(deductible_query, UserIncome.user_id == deductible_query.c.user_id)\
     .first()
    
    # Get tax rates from settings
    from admin_routes import tax_settings
    lkr_business_tax_rate = tax_settings.get('lkr_business_tax_rate', 36.0) / 100
    usd_consulting_tax_rate = tax_settings.get('usd_consulting_tax_rate', 15.0) / 100
    
    # Extract values safely
    business_income = tax_data.business_income or 0
    usd_consulting_income = tax_data.usd_consulting_income or 0
    total_deductible = tax_data.total_deductible or 0
    
    # Calculate potential tax savings
    # First deduct from LKR business income (higher rate)
    lkr_deductible = min(business_income, total_deductible)
    lkr_savings = lkr_deductible * lkr_business_tax_rate
    
    # Then deduct any remaining from USD consulting income
    remaining_deductible = max(0, total_deductible - lkr_deductible)
    usd_deductible = min(usd_consulting_income, remaining_deductible)
    usd_savings = usd_deductible * usd_consulting_tax_rate
    
    # Total savings
    total_savings = lkr_savings + usd_savings
    
    # Effective tax rate reduction
    total_taxable_income = business_income + usd_consulting_income
    savings_percentage = (total_savings / max(total_taxable_income, 1) * 100) if total_taxable_income else 0
    
    return {
        'total_deductible_amount': float(total_deductible),
        'lkr_business_deductible': float(lkr_deductible),
        'usd_consulting_deductible': float(usd_deductible),
        'lkr_business_savings': float(lkr_savings),
        'usd_consulting_savings': float(usd_savings),
        'total_savings': float(total_savings),
        'savings_percentage': float(savings_percentage)
    }

#
# System Statistics
#

@memoize_with_timeout(CACHE_DURATION_SHORT)
def get_system_statistics():
    """
    Get system statistics including database metrics.
    
    Returns:
        Dictionary with system statistics
    """
    # Database table sizes and counts
    table_stats = {
        'users': User.query.count(),
        'receipts': Receipt.query.count(),
        'receipt_items': ReceiptItem.query.count(),
        'income_records': UserIncome.query.count()
    }
    
    # Database size approximation (very rough estimation)
    # A proper implementation would use database-specific queries
    estimated_size_kb = (
        table_stats['users'] * 2 +             # ~2KB per user
        table_stats['receipts'] * 5 +          # ~5KB per receipt
        table_stats['receipt_items'] * 0.5 +   # ~0.5KB per item
        table_stats['income_records'] * 1      # ~1KB per income record
    )
    
    return {
        'table_counts': table_stats,
        'estimated_db_size_kb': int(estimated_size_kb),
        'cache_entries': len(_cache),
        'server_time': datetime.utcnow()
    }

#
# Real-time Dashboard Data
#

@memoize_with_timeout(CACHE_DURATION_SHORT)
def get_dashboard_data():
    """
    Get comprehensive real-time dashboard data with optimized queries.
    
    Returns:
        Dictionary with all dashboard data
    """
    start_time = time.time()
    
    # Combine all statistics into a single dashboard data object
    dashboard_data = {
        'users': get_user_statistics(),
        'receipts': get_receipt_statistics(),
        'income': get_income_statistics(),
        'tax_savings': get_tax_savings_statistics(),
        'system': get_system_statistics(),
        'timestamp': datetime.utcnow().isoformat(),
    }
    
    # Add some derived metrics for the dashboard
    dashboard_data['overview'] = {
        'user_count': dashboard_data['users']['total_users'],
        'receipt_count': dashboard_data['receipts']['total_receipts'],
        'total_amount': dashboard_data['receipts']['total_amount'],
        'avg_receipts_per_user': (dashboard_data['receipts']['total_receipts'] / 
                                max(dashboard_data['users']['total_users'], 1)) 
                                if dashboard_data['users']['total_users'] > 0 else 0,
        'tax_savings': dashboard_data['tax_savings']['total_savings'],
        'most_common_category': dashboard_data['receipts']['top_categories'][0]['category'] 
                               if dashboard_data['receipts']['top_categories'] else 'Unknown',
    }
    
    # Calculate performance metrics
    elapsed = time.time() - start_time
    dashboard_data['_meta'] = {
        'execution_time': elapsed,
        'cache_status': 'hit' if start_time in _cache else 'miss',
    }
    
    logger.info(f"Dashboard data compiled in {elapsed:.2f} seconds")
    return dashboard_data