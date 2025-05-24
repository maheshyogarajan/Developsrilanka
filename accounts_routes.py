"""
Accounting routes for the comprehensive accounting system.
Handles chart of accounts, journal entries, P&L reports, and asset management.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import db
from accounting_models import Account, GeneralLedgerEntry, JournalEntryLine, FixedAsset, AssetCategory, DepreciationEntry
from accounting_service import AccountingService, FinancialReportService
from models import Organization, OrganizationUser
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

# Create blueprint
accounts_bp = Blueprint('accounts', __name__, url_prefix='/accounts')

def get_user_organizations():
    """Get organizations for current user."""
    org_users = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    return [org_user.organization for org_user in org_users]

def get_default_organization():
    """Get user's default organization."""
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        is_default=True
    ).first()
    return org_user.organization if org_user else None

@accounts_bp.route('/')
@login_required
def dashboard():
    """Accounting dashboard with overview."""
    try:
        organizations = get_user_organizations()
        default_org = get_default_organization()
        
        if not organizations:
            flash('Please set up an organization before accessing accounting features.', 'warning')
            return redirect(url_for('getting_started.wizard'))
        
        # Get quick stats for default organization
        stats = {}
        if default_org:
            stats = {
                'total_accounts': Account.query.filter_by(organization_id=default_org.id, is_active=True).count(),
                'total_assets': FixedAsset.query.filter_by(organization_id=default_org.id, status='active').count(),
                'recent_entries': GeneralLedgerEntry.query.filter_by(organization_id=default_org.id).order_by(GeneralLedgerEntry.created_at.desc()).limit(5).all()
            }
        
        return render_template('accounts/dashboard.html', 
                             organizations=organizations,
                             default_org=default_org,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error loading accounting dashboard: {str(e)}")
        flash('Error loading accounting dashboard.', 'danger')
        return redirect(url_for('home'))

@accounts_bp.route('/chart-of-accounts')
@login_required
def chart_of_accounts():
    """Display and manage chart of accounts."""
    try:
        org_id = request.args.get('org_id', type=int)
        organizations = get_user_organizations()
        
        if not org_id:
            default_org = get_default_organization()
            org_id = default_org.id if default_org else None
        
        if not org_id or org_id not in [org.id for org in organizations]:
            flash('Invalid organization selected.', 'danger')
            return redirect(url_for('accounts.dashboard'))
        
        # Get accounts grouped by type
        accounts = Account.query.filter_by(
            organization_id=org_id,
            is_active=True
        ).order_by(Account.account_code).all()
        
        # Group accounts by type
        grouped_accounts = {
            'asset': [a for a in accounts if a.account_type == 'asset'],
            'liability': [a for a in accounts if a.account_type == 'liability'],
            'equity': [a for a in accounts if a.account_type == 'equity'],
            'revenue': [a for a in accounts if a.account_type == 'revenue'],
            'expense': [a for a in accounts if a.account_type == 'expense']
        }
        
        selected_org = Organization.query.get(org_id)
        
        return render_template('accounts/chart_of_accounts.html',
                             grouped_accounts=grouped_accounts,
                             organizations=organizations,
                             selected_org=selected_org)
    except Exception as e:
        logger.error(f"Error loading chart of accounts: {str(e)}")
        flash('Error loading chart of accounts.', 'danger')
        return redirect(url_for('accounts.dashboard'))

@accounts_bp.route('/profit-loss')
@login_required
def profit_loss():
    """Generate and display Profit & Loss statement."""
    try:
        org_id = request.args.get('org_id', type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        organizations = get_user_organizations()
        
        if not org_id:
            default_org = get_default_organization()
            org_id = default_org.id if default_org else None
        
        # Default to current month if no dates provided
        if not start_date or not end_date:
            today = date.today()
            start_date = today.replace(day=1).isoformat()
            end_date = today.isoformat()
        
        report_data = None
        if org_id and org_id in [org.id for org in organizations]:
            try:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
                
                report_data = FinancialReportService.generate_profit_loss(
                    org_id, start_date_obj, end_date_obj
                )
            except Exception as e:
                logger.error(f"Error generating P&L report: {str(e)}")
                flash('Error generating Profit & Loss report.', 'danger')
        
        selected_org = Organization.query.get(org_id) if org_id else None
        
        return render_template('accounts/profit_loss.html',
                             report_data=report_data,
                             organizations=organizations,
                             selected_org=selected_org,
                             start_date=start_date,
                             end_date=end_date)
    except Exception as e:
        logger.error(f"Error in profit_loss route: {str(e)}")
        flash('Error loading Profit & Loss page.', 'danger')
        return redirect(url_for('accounts.dashboard'))

@accounts_bp.route('/assets')
@login_required
def assets():
    """Display and manage fixed assets."""
    try:
        org_id = request.args.get('org_id', type=int)
        organizations = get_user_organizations()
        
        if not org_id:
            default_org = get_default_organization()
            org_id = default_org.id if default_org else None
        
        if not org_id or org_id not in [org.id for org in organizations]:
            flash('Invalid organization selected.', 'danger')
            return redirect(url_for('accounts.dashboard'))
        
        # Get assets and categories
        assets = FixedAsset.query.filter_by(organization_id=org_id).order_by(FixedAsset.created_at.desc()).all()
        categories = AssetCategory.query.filter_by(organization_id=org_id, is_active=True).all()
        
        # Calculate summary stats
        total_cost = sum(asset.purchase_price for asset in assets if asset.status == 'active')
        total_depreciation = sum(asset.accumulated_depreciation for asset in assets if asset.status == 'active')
        total_book_value = total_cost - total_depreciation
        
        stats = {
            'total_assets': len([a for a in assets if a.status == 'active']),
            'total_cost': float(total_cost),
            'total_depreciation': float(total_depreciation),
            'total_book_value': float(total_book_value)
        }
        
        selected_org = Organization.query.get(org_id)
        
        return render_template('accounts/assets.html',
                             assets=assets,
                             categories=categories,
                             stats=stats,
                             organizations=organizations,
                             selected_org=selected_org)
    except Exception as e:
        logger.error(f"Error loading assets: {str(e)}")
        flash('Error loading assets.', 'danger')
        return redirect(url_for('accounts.dashboard'))

@accounts_bp.route('/journal-entries')
@login_required
def journal_entries():
    """Display journal entries."""
    try:
        org_id = request.args.get('org_id', type=int)
        page = request.args.get('page', 1, type=int)
        
        organizations = get_user_organizations()
        
        if not org_id:
            default_org = get_default_organization()
            org_id = default_org.id if default_org else None
        
        if not org_id or org_id not in [org.id for org in organizations]:
            flash('Invalid organization selected.', 'danger')
            return redirect(url_for('accounts.dashboard'))
        
        # Get paginated journal entries
        entries = GeneralLedgerEntry.query.filter_by(
            organization_id=org_id
        ).order_by(GeneralLedgerEntry.transaction_date.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        selected_org = Organization.query.get(org_id)
        
        return render_template('accounts/journal_entries.html',
                             entries=entries,
                             organizations=organizations,
                             selected_org=selected_org)
    except Exception as e:
        logger.error(f"Error loading journal entries: {str(e)}")
        flash('Error loading journal entries.', 'danger')
        return redirect(url_for('accounts.dashboard'))

@accounts_bp.route('/create-account', methods=['POST'])
@login_required
def create_account():
    """Create a new account."""
    try:
        org_id = request.form.get('organization_id', type=int)
        account_code = request.form.get('account_code', '').strip()
        account_name = request.form.get('account_name', '').strip()
        account_type = request.form.get('account_type', '').strip()
        account_subtype = request.form.get('account_subtype', '').strip()
        parent_account_id = request.form.get('parent_account_id', type=int)
        
        # Validate organization access
        organizations = get_user_organizations()
        if org_id not in [org.id for org in organizations]:
            flash('Invalid organization.', 'danger')
            return redirect(url_for('accounts.chart_of_accounts'))
        
        # Validate required fields
        if not all([account_code, account_name, account_type]):
            flash('Account code, name, and type are required.', 'danger')
            return redirect(url_for('accounts.chart_of_accounts', org_id=org_id))
        
        # Check for duplicate account code
        existing = Account.query.filter_by(
            organization_id=org_id,
            account_code=account_code
        ).first()
        
        if existing:
            flash('Account code already exists.', 'danger')
            return redirect(url_for('accounts.chart_of_accounts', org_id=org_id))
        
        # Create account
        account = Account(
            organization_id=org_id,
            account_code=account_code,
            account_name=account_name,
            account_type=account_type,
            account_subtype=account_subtype if account_subtype else None,
            parent_account_id=parent_account_id if parent_account_id else None
        )
        
        db.session.add(account)
        db.session.commit()
        
        flash('Account created successfully!', 'success')
        return redirect(url_for('accounts.chart_of_accounts', org_id=org_id))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating account: {str(e)}")
        flash('Error creating account.', 'danger')
        return redirect(url_for('accounts.chart_of_accounts'))

@accounts_bp.route('/add-asset', methods=['POST'])
@login_required
def add_asset():
    """Add a new fixed asset."""
    try:
        org_id = request.form.get('organization_id', type=int)
        description = request.form.get('description', '').strip()
        purchase_date = request.form.get('purchase_date')
        purchase_price = request.form.get('purchase_price', type=float)
        useful_life = request.form.get('useful_life', type=int)
        category_id = request.form.get('category_id', type=int)
        
        # Validate organization access
        organizations = get_user_organizations()
        if org_id not in [org.id for org in organizations]:
            flash('Invalid organization.', 'danger')
            return redirect(url_for('accounts.assets'))
        
        # Validate required fields
        if not all([description, purchase_date, purchase_price]):
            flash('Description, purchase date, and price are required.', 'danger')
            return redirect(url_for('accounts.assets', org_id=org_id))
        
        # Convert purchase date
        purchase_date_obj = datetime.strptime(purchase_date, '%Y-%m-%d').date()
        
        # Generate asset number
        last_asset = FixedAsset.query.filter_by(organization_id=org_id).order_by(FixedAsset.id.desc()).first()
        asset_number = f"AST-{(last_asset.id + 1) if last_asset else 1:06d}"
        
        # Create asset
        asset = FixedAsset(
            organization_id=org_id,
            asset_category_id=category_id if category_id else None,
            asset_number=asset_number,
            description=description,
            purchase_date=purchase_date_obj,
            purchase_price=Decimal(str(purchase_price)),
            useful_life_years=useful_life,
            current_book_value=Decimal(str(purchase_price))
        )
        
        db.session.add(asset)
        db.session.commit()
        
        flash('Asset added successfully!', 'success')
        return redirect(url_for('accounts.assets', org_id=org_id))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding asset: {str(e)}")
        flash('Error adding asset.', 'danger')
        return redirect(url_for('accounts.assets'))

@accounts_bp.route('/calculate-depreciation', methods=['POST'])
@login_required
def calculate_depreciation():
    """Calculate monthly depreciation for organization."""
    try:
        org_id = request.form.get('organization_id', type=int)
        
        # Validate organization access
        organizations = get_user_organizations()
        if org_id not in [org.id for org in organizations]:
            flash('Invalid organization.', 'danger')
            return redirect(url_for('accounts.assets'))
        
        # Calculate depreciation
        result = AccountingService.calculate_monthly_depreciation(org_id)
        
        flash(f"Calculated depreciation for {result['assets_processed']} assets. Total: ${result['total_depreciation']:.2f}", 'success')
        return redirect(url_for('accounts.assets', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error calculating depreciation: {str(e)}")
        flash('Error calculating depreciation.', 'danger')
        return redirect(url_for('accounts.assets'))

def register_routes(app):
    """Register accounting routes with the Flask application."""
    app.register_blueprint(accounts_bp)
    logger.info("Accounting routes registered successfully")