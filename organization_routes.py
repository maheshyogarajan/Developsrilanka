"""
Routes for organization management and team invitations.
"""
import os
import uuid
import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, abort, jsonify
from markupsafe import Markup
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from flask_wtf.csrf import validate_csrf
from models import db, Organization, OrganizationUser, OrganizationInvitation, UserRole, Client
from accounting_models import Account
from decorators import role_required
from flask_mail import Message

# Configure logging
logger = logging.getLogger(__name__)

# Create blueprint
organizations_bp = Blueprint('organizations', __name__, url_prefix='/organizations')

def create_default_chart_of_accounts(organization_id):
    """Create comprehensive 74-account chart of accounts for new organization."""
    
    comprehensive_accounts = [
        # ASSETS (23 accounts)
        {'code': '1001', 'name': 'Cash – Primary Bank', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1002', 'name': 'Cash – Business Savings', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1005', 'name': 'Petty Cash', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1050', 'name': 'Short-Term Investments', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1100', 'name': 'Accounts Receivable', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1110', 'name': 'Allowance for Doubtful Accounts', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1200', 'name': 'Inventory', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1300', 'name': 'Prepaid Expenses', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1350', 'name': 'Input VAT / GST Receivable', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1400', 'name': 'Security Deposits & Advances', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1500', 'name': 'Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1550', 'name': 'Accum. Depreciation – Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1600', 'name': 'Computer Equipment & Software', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1650', 'name': 'Accum. Depreciation – Computer Equipment & Software', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1700', 'name': 'Vehicles', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1750', 'name': 'Accum. Depreciation – Vehicles', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1800', 'name': 'Buildings / Leasehold Improvements', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1850', 'name': 'Accum. Depreciation – Buildings & Leasehold Improvements', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1900', 'name': 'Furniture & Fixtures', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1950', 'name': 'Accum. Depreciation – Furniture & Fixtures', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1970', 'name': 'Intangible Assets (Licences, IP)', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1980', 'name': 'Accum. Amortisation – Intangibles', 'type': 'asset', 'subtype': 'fixed_asset'},
        
        # LIABILITIES (12 accounts)
        {'code': '2001', 'name': 'Accounts Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2050', 'name': 'Credit Cards Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2100', 'name': 'Accrued Expenses', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2150', 'name': 'Sales Tax / VAT Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2200', 'name': 'Payroll Liabilities (PAYE, EPF/ETF, etc.)', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2250', 'name': 'Income Tax Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2300', 'name': 'Other Taxes & Levies Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2350', 'name': 'Customer Deposits / Deferred Revenue', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2400', 'name': 'Loan – Current Portion', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2450', 'name': 'Loan – Long-Term', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2500', 'name': 'Lease Liabilities', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2550', 'name': 'Shareholder / Director Loans', 'type': 'liability', 'subtype': 'long_term_liability'},
        
        # EQUITY (5 accounts)
        {'code': '3001', 'name': 'Share / Owner Capital', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3002', 'name': 'Retained Earnings', 'type': 'equity', 'subtype': 'retained_earnings'},
        {'code': '3005', 'name': 'Additional Paid-In Capital', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3010', 'name': 'Dividends / Owner Draws', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3020', 'name': 'Treasury Shares (contra-equity)', 'type': 'equity', 'subtype': 'owner_equity'},
        
        # COST OF GOODS SOLD (3 accounts)
        {'code': '3100', 'name': 'COGS – Materials / Merchandise', 'type': 'expense', 'subtype': 'cost_of_goods_sold'},
        {'code': '3200', 'name': 'COGS – Direct Labour', 'type': 'expense', 'subtype': 'cost_of_goods_sold'},
        {'code': '3300', 'name': 'COGS – Freight In & Import Duty', 'type': 'expense', 'subtype': 'cost_of_goods_sold'},
        
        # REVENUE (7 accounts)
        {'code': '4001', 'name': 'Service Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4002', 'name': 'Consulting Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4003', 'name': 'Product Sales', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4100', 'name': 'Subscription / Recurring Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4200', 'name': 'Interest Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        {'code': '4300', 'name': 'Sales Returns & Allowances (contra-revenue)', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4900', 'name': 'Other Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        
        # EXPENSES (24 accounts)
        {'code': '5001', 'name': 'Operating Expenses – General', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5002', 'name': 'Meals & Entertainment', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5003', 'name': 'Transport & Travel', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5004', 'name': 'Office Supplies', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5005', 'name': 'Professional Services', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5006', 'name': 'Marketing & Advertising', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5007', 'name': 'Utilities', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5008', 'name': 'Rent & Facilities', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5009', 'name': 'Insurance', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5010', 'name': 'Software & SaaS', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5011', 'name': 'Bank & Merchant Fees', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5012', 'name': 'Depreciation Expense', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5013', 'name': 'Payroll – Wages & Salaries', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5014', 'name': 'Employee Benefits (EPF/ETF employer share, health)', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5015', 'name': 'Training & Development', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5016', 'name': 'Legal & Accounting', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5020', 'name': 'Subscriptions & Memberships', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5030', 'name': 'Repairs & Maintenance', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5040', 'name': 'Telephone & Internet', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5050', 'name': 'Bad Debt Expense', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5060', 'name': 'Interest Expense', 'type': 'expense', 'subtype': 'other_expense'},
        {'code': '5080', 'name': 'Licences, Permits & Government Fees', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5900', 'name': 'Other Expenses / Miscellaneous', 'type': 'expense', 'subtype': 'other_expense'},
    ]
    
    # Create all accounts for the organization
    for account_data in comprehensive_accounts:
        account = Account(
            organization_id=organization_id,
            account_code=account_data['code'],
            account_name=account_data['name'],
            account_type=account_data['type'],
            account_subtype=account_data['subtype'],
            is_active=True,
            description=f"Default {account_data['type']} account",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.session.add(account)
    
    logger.info(f"Created {len(comprehensive_accounts)} default accounts for organization {organization_id}")

@organizations_bp.route('/')
@login_required
def organizations():
    """Render the organizations dashboard with list of user's organizations."""
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    
    # Get the default organization
    default_org = None
    for org_user in user_orgs:
        if org_user.is_default:
            default_org = org_user.organization
            break
    
    return render_template('organizations/index.html', 
                          organizations=user_orgs,
                          default_org=default_org)

@organizations_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_organization():
    """Create a new organization."""
    # Check if this is being loaded in a modal
    is_modal = request.args.get('modal') == 'true'
    
    if request.method == 'POST':
        name = request.form.get('name')
        if not name:
            flash('Organization name is required.', 'danger')
            return render_template('organizations/create.html', is_modal=is_modal)
        
        # Create new organization
        new_org = Organization(
            name=name,
            website=request.form.get('website'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            address=request.form.get('address'),
            tax_registration_number=request.form.get('tax_registration_number'),
            primary_color=request.form.get('primary_color', '#4a6da7'),
            secondary_color=request.form.get('secondary_color', '#f5f8ff'),
            email_footer_text=request.form.get('email_footer_text')
        )
        
        db.session.add(new_org)
        db.session.flush()  # Get the ID without committing
        
        # Create organization-user relationship
        is_first_org = OrganizationUser.query.filter_by(user_id=current_user.id).count() == 0
        org_user = OrganizationUser(
            user_id=current_user.id,
            organization_id=new_org.id,
            role=UserRole.OWNER.value,
            is_default=is_first_org  # First organization is default
        )
        
        db.session.add(org_user)
        
        # Create comprehensive chart of accounts for the new organization
        create_default_chart_of_accounts(new_org.id)
        
        db.session.commit()
        
        # If this was called from a modal, return a page with JavaScript to communicate with parent
        if is_modal:
            return render_template('organizations/create_success_modal.html', 
                                  organization=new_org)
        
        flash(f'Organization "{name}" created successfully!', 'success')
        return redirect(url_for('organizations.view_organization', org_id=new_org.id))
    
    return render_template('organizations/create.html', is_modal=is_modal)

@organizations_bp.route('/<int:org_id>')
@login_required
def view_organization(org_id):
    """View a specific organization."""
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    organization = org_user.organization
    
    # Get all members of this organization
    team_members = OrganizationUser.query.filter_by(
        organization_id=org_id
    ).all()
    
    # Get pending invitations for this organization
    pending_invitations = OrganizationInvitation.query.filter_by(
        organization_id=org_id,
        accepted=False
    ).all()
    
    return render_template('organizations/view.html',
                          organization=organization,
                          org_user=org_user,
                          team_members=team_members,
                          pending_invitations=pending_invitations)

@organizations_bp.route('/<int:org_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_organization(org_id):
    """Edit an existing organization."""
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Check permissions
    if org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value] and not current_user.is_admin():
        flash('You do not have permission to edit this organization.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = org_user.organization
    
    if request.method == 'POST':
        try:
            validate_csrf(request.form.get('csrf_token'))
        except Exception as e:
            flash('CSRF token validation failed. Please try again.', 'danger')
            return redirect(url_for('organizations.edit_organization', org_id=org_id))
        
        organization.name = request.form.get('name')
        organization.website = request.form.get('website')
        organization.email = request.form.get('email')
        organization.phone = request.form.get('phone')
        organization.address = request.form.get('address')
        organization.tax_registration_number = request.form.get('tax_registration_number')
        organization.primary_color = request.form.get('primary_color')
        organization.secondary_color = request.form.get('secondary_color')
        organization.email_footer_text = request.form.get('email_footer_text')
        
        # Handle logo upload
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename:
            try:
                from PIL import Image
                from s3_storage import S3Storage
                import io
                import imghdr
                
                # Read the uploaded file
                img_data = logo_file.read()
                
                # Validate that it's a valid image file
                image_type = imghdr.what(None, h=img_data)
                if not image_type:
                    raise ValueError("Invalid image file format")
                
                # Define allowed image formats
                ALLOWED_FORMATS = ['jpeg', 'jpg', 'png', 'gif']
                
                # Open the image with PIL
                img = Image.open(io.BytesIO(img_data))
                
                # Format validation
                img_format = getattr(img, 'format', '').lower()
                logger.info(f"Uploaded image format: {img_format}, mode: {img.mode}")
                
                if img_format and img_format.lower() not in ALLOWED_FORMATS:
                    raise ValueError(f"Unsupported image format: {img_format}. Please use JPEG, PNG or GIF.")
                
                # Resize the image if necessary (max dimensions 500x500)
                max_size = (500, 500)
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                
                # Initialize S3 storage and upload
                s3 = S3Storage()
                s3_key = s3.upload_image(img, organization_id=organization.id)
                
                # Save S3 key to organization
                organization.logo_s3_key = s3_key
                logger.info(f"Logo uploaded for organization {organization.id}, S3 key: {s3_key}")
            except ValueError as ve:
                logger.warning(f"Invalid logo format: {str(ve)}")
                flash(f'Invalid logo: {str(ve)}', 'warning')
            except Exception as e:
                logger.error(f"Error uploading organization logo: {str(e)}")
                flash(f'Error uploading logo: {str(e)}', 'danger')
        
        db.session.commit()
        
        flash('Organization updated successfully!', 'success')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    return render_template('organizations/edit.html', 
                          organization=organization,
                          org_user=org_user)

@organizations_bp.route('/<int:org_id>/delete', methods=['POST'])
@login_required
def delete_organization(org_id):
    """Delete an organization (owner only)."""
    try:
        validate_csrf(request.form.get('csrf_token'))
    except Exception as e:
        flash('CSRF token validation failed. Please try again.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Only owner or global admin can delete an organization
    if org_user.role != UserRole.OWNER.value and not current_user.is_admin():
        flash('You do not have permission to delete this organization.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = org_user.organization
    org_name = organization.name
    
    # Check if this is the user's last organization
    if OrganizationUser.query.filter_by(user_id=current_user.id).count() == 1:
        flash('You cannot delete your last organization. Create a new one first.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Set a new default organization if this was the default
    if org_user.is_default:
        # Find any other organization for this user
        another_org_user = OrganizationUser.query.filter(
            OrganizationUser.user_id == current_user.id,
            OrganizationUser.organization_id != org_id
        ).first()
        
        if another_org_user:
            another_org_user.is_default = True
            db.session.add(another_org_user)
    
    # Delete the organization
    db.session.delete(organization)
    db.session.commit()
    
    flash(f'Organization "{org_name}" has been deleted.', 'success')
    return redirect(url_for('organizations.organizations'))

@organizations_bp.route('/<int:org_id>/set-default', methods=['POST'])
@login_required
def set_default_organization(org_id):
    """Set an organization as the user's default."""
    try:
        validate_csrf(request.form.get('csrf_token'))
    except Exception as e:
        flash('CSRF token validation failed. Please try again.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Find the organization and check if user is a member
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Already default
    if org_user.is_default:
        return redirect(url_for('organizations.organizations'))
    
    # Clear current default
    current_default = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        is_default=True
    ).first()
    
    if current_default:
        current_default.is_default = False
        db.session.add(current_default)
    
    # Set new default
    org_user.is_default = True
    db.session.add(org_user)
    db.session.commit()
    
    flash(f'"{org_user.organization.name}" is now your default organization.', 'success')
    return redirect(url_for('organizations.organizations'))

@organizations_bp.route('/<int:org_id>/invite', methods=['GET', 'POST'])
@login_required
def invite_team_member(org_id):
    """Invite a new team member to the organization."""
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Check permissions
    if org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value] and not current_user.is_admin():
        flash('You do not have permission to invite members to this organization.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = org_user.organization
    
    if request.method == 'POST':
        try:
            validate_csrf(request.form.get('csrf_token'))
        except Exception as e:
            flash('CSRF token validation failed. Please try again.', 'danger')
            return redirect(url_for('organizations.invite_team_member', org_id=org_id))
        
        email = request.form.get('email')
        role = request.form.get('role')
        
        # Validate email
        if not email:
            flash('Email address is required.', 'danger')
            return redirect(url_for('organizations.invite_team_member', org_id=org_id))
        
        # Check if user is already a member
        existing_member = OrganizationUser.query.join(OrganizationUser.user).filter(
            OrganizationUser.organization_id == org_id,
            OrganizationUser.user.has(email=email)
        ).first()
        
        if existing_member:
            flash(f'User with email {email} is already a member of this organization.', 'warning')
            return redirect(url_for('organizations.view_organization', org_id=org_id))
        
        # Check for existing invitation
        existing_invitation = OrganizationInvitation.query.filter_by(
            organization_id=org_id,
            email=email,
            accepted=False
        ).first()
        
        if existing_invitation and not existing_invitation.is_expired():
            flash(f'An invitation has already been sent to {email} and is still pending.', 'warning')
            return redirect(url_for('organizations.view_organization', org_id=org_id))
        
        # Validate email format before creating invitation
        import re
        
        # First normalize the email (trim and lowercase)
        email = email.strip().lower()
        
        # Comprehensive email validation function
        def is_valid_email(email):
            # Check for consecutive dots in local part
            if '@' in email and '..' in email.split('@')[0]:
                return False
                
            # Use regex for the rest of the validation
            pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*[a-zA-Z0-9]@([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
            return bool(re.match(pattern, email))
        
        if not is_valid_email(email):
            flash(f'Invalid email format: {email}. Please enter a valid email address.', 'danger')
            return redirect(url_for('organizations.invite_team_member', org_id=org_id))
            
        # Create new invitation with token
        token = str(uuid.uuid4())
        expires_at = datetime.utcnow() + timedelta(days=7)  # 7 days expiration
        
        # Normalize email (lowercase)
        email = email.strip().lower()
        
        invitation = OrganizationInvitation(
            organization_id=org_id,
            invited_by_user_id=current_user.id,
            email=email,
            role=role,
            token=token,
            expires_at=expires_at
        )
        
        db.session.add(invitation)
        db.session.commit()
        
        # Send invitation email
        if send_invitation_email(invitation):
            flash(Markup(f'Invitation sent to {email} successfully! <span style="color: red; font-weight: bold;">Please ask the user to also check their SPAM emailbox.</span>'), 'success')
        else:
            logger.error(f"Failed to send invitation email")
            flash(f'Invitation created but email could not be sent. Please check email settings.', 'warning')
        
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Get available roles (owner can only be assigned by transferring ownership)
    roles = [UserRole.ADMIN.value, UserRole.MEMBER.value, UserRole.VIEWER.value]
    
    return render_template('organizations/invite.html', 
                          organization=organization,
                          roles=roles)

@organizations_bp.route('/invitation/<token>', methods=['GET', 'POST'])
def accept_invitation(token):
    """Accept an organization invitation."""
    # Import the OrganizationInvitation model
    from models import OrganizationInvitation, OrganizationUser
    
    # Log the invitation acceptance attempt
    logger.info(f"Organization invitation acceptance attempt for token: {token}")
    
    invitation = OrganizationInvitation.query.filter_by(token=token).first_or_404()
    
    # Check if invitation is expired
    if invitation.is_expired():
        logger.warning(f"Expired organization invitation: {token}")
        flash('This invitation has expired.', 'danger')
        return redirect(url_for('home'))
    
    # Check if invitation is already accepted
    if invitation.accepted:
        logger.info(f"Already accepted organization invitation: {token}")
        flash('This invitation has already been accepted.', 'warning')
        return redirect(url_for('home'))
    
    # If it's a GET request, show the confirmation page
    if request.method == 'GET':
        organization = Organization.query.get(invitation.organization_id)
        if not organization:
            flash('The organization no longer exists.', 'danger')
            return redirect(url_for('home'))
        
        return render_template('organizations/confirm_invitation.html', 
                              invitation=invitation,
                              organization=organization,
                              token=token)
    
    # Handle POST request for accepting the invitation
    if request.method == 'POST':
        # Check if user is not logged in, redirect to login
        if not current_user.is_authenticated:
            logger.info(f"User not authenticated for organization invitation: {token}, storing in session")
            flash('Please log in or create an account to accept this invitation.', 'info')
            # Store invitation token in session to redirect back after login
            from flask import session
            session['invitation_token'] = token
            return redirect(url_for('login'))
        
        # Check if user is already a member
        existing_member = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=invitation.organization_id
        ).first()
        
        if existing_member:
            flash('You are already a member of this organization.', 'warning')
            invitation.accepted = True
            db.session.commit()
            return redirect(url_for('organizations.view_organization', org_id=invitation.organization_id))
        
        # Create organization membership
        is_first_org = OrganizationUser.query.filter_by(user_id=current_user.id).count() == 0
        org_user = OrganizationUser(
            user_id=current_user.id,
            organization_id=invitation.organization_id,
            role=invitation.role,
            is_default=is_first_org  # First organization is default
        )
        
        # Mark invitation as accepted
        invitation.accepted = True
        
        db.session.add(org_user)
        db.session.commit()
        
        flash(f'You have joined {invitation.organization.name} successfully!', 'success')
        return redirect(url_for('organizations.view_organization', org_id=invitation.organization_id))
    
    # This shouldn't happen - redirect back to the confirmation page
    return redirect(url_for('organizations.accept_invitation', token=token))

@organizations_bp.route('/<int:org_id>/member/<int:user_id>/role', methods=['POST'])
@login_required
def update_member_role(org_id, user_id):
    """Update a team member's role."""
    try:
        validate_csrf(request.form.get('csrf_token'))
    except Exception as e:
        flash('CSRF token validation failed. Please try again.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Check permissions - only owner or admin can update roles
    if org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value] and not current_user.is_admin():
        flash('You do not have permission to update member roles.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Only owner can set another user as owner (transfer ownership)
    new_role = request.form.get('role')
    if new_role == UserRole.OWNER.value and org_user.role != UserRole.OWNER.value and not current_user.is_admin():
        flash('Only the organization owner can transfer ownership.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Get the member to update
    member = OrganizationUser.query.filter_by(
        user_id=user_id,
        organization_id=org_id
    ).first_or_404()
    
    # Prevent users from changing their own role
    if member.user_id == current_user.id and not current_user.is_admin():
        flash('You cannot change your own role.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Handle ownership transfer
    if new_role == UserRole.OWNER.value:
        # Find current owner and demote to admin
        current_owner = OrganizationUser.query.filter_by(
            organization_id=org_id,
            role=UserRole.OWNER.value
        ).first()
        
        if current_owner:
            current_owner.role = UserRole.ADMIN.value
            db.session.add(current_owner)
    
    # Update the member's role
    member.role = new_role
    db.session.add(member)
    db.session.commit()
    
    flash(f'Role updated successfully to {new_role}.', 'success')
    return redirect(url_for('organizations.view_organization', org_id=org_id))

@organizations_bp.route('/<int:org_id>/member/<int:user_id>/remove', methods=['POST'])
@login_required
def remove_team_member(org_id, user_id):
    """Remove a team member from the organization."""
    try:
        validate_csrf(request.form.get('csrf_token'))
    except Exception as e:
        flash('CSRF token validation failed. Please try again.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Check permissions - only owner or admin can remove members
    if org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value] and not current_user.is_admin():
        flash('You do not have permission to remove members.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Get the member to remove
    member = OrganizationUser.query.filter_by(
        user_id=user_id,
        organization_id=org_id
    ).first_or_404()
    
    # Prevent removing owner
    if member.role == UserRole.OWNER.value and not current_user.is_admin():
        flash('The organization owner cannot be removed.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Prevent users from removing themselves
    if member.user_id == current_user.id and not current_user.is_admin():
        flash('You cannot remove yourself from the organization.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Delete the membership
    db.session.delete(member)
    db.session.commit()
    
    flash('Member removed successfully.', 'success')
    return redirect(url_for('organizations.view_organization', org_id=org_id))

@organizations_bp.route('/<int:org_id>/invitation/<int:invitation_id>/cancel', methods=['POST'])
@login_required
def cancel_invitation(org_id, invitation_id):
    """Cancel a pending invitation."""
    try:
        validate_csrf(request.form.get('csrf_token'))
    except Exception as e:
        flash('CSRF token validation failed. Please try again.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Check permissions - only owner or admin can cancel invitations
    if org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value] and not current_user.is_admin():
        flash('You do not have permission to cancel invitations.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Get the invitation to cancel
    invitation = OrganizationInvitation.query.filter_by(
        id=invitation_id,
        organization_id=org_id,
        accepted=False
    ).first_or_404()
    
    # Delete the invitation
    db.session.delete(invitation)
    db.session.commit()
    
    flash('Invitation cancelled successfully.', 'success')
    return redirect(url_for('organizations.view_organization', org_id=org_id))

@organizations_bp.route('/<int:org_id>/branding', methods=['GET', 'POST'])
@login_required
def edit_branding(org_id):
    """Edit the organization's branding settings."""
    from PIL import Image
    import io
    
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first_or_404()
    
    # Check permissions
    if org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value] and not current_user.is_admin():
        flash('You do not have permission to edit branding.', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = org_user.organization
    
    if request.method == 'POST':
        # Handle logo upload if provided
        if 'logo' in request.files and request.files['logo'].filename:
            logo_file = request.files['logo']
            if logo_file:
                try:
                    # Check file extension
                    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'}
                    filename = secure_filename(logo_file.filename)
                    file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else None
                    
                    if file_ext not in allowed_extensions:
                        flash(f'Logo must be one of the following formats: {", ".join(allowed_extensions)}', 'danger')
                        return redirect(url_for('organizations.edit_branding', org_id=org_id))
                    
                    # Create unique filename to avoid overwriting
                    unique_filename = f"org_{org_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.{file_ext}"
                    
                    # Create directory if it doesn't exist
                    logo_dir = os.path.join(current_app.static_folder, 'uploads', 'logos')
                    os.makedirs(logo_dir, exist_ok=True)
                    
                    logo_path = os.path.join(logo_dir, unique_filename)
                    
                    # Process the image (resize if not SVG)
                    if file_ext != 'svg':
                        # Open the image using Pillow
                        img = Image.open(logo_file)
                        
                        # Calculate new dimensions while maintaining aspect ratio
                        max_width = 300
                        max_height = 150
                        width, height = img.size
                        
                        # Only resize if the image is larger than our max dimensions
                        if width > max_width or height > max_height:
                            # Calculate new dimensions while maintaining aspect ratio
                            if width / height > max_width / max_height:
                                new_width = max_width
                                new_height = int(height * (max_width / width))
                            else:
                                new_height = max_height
                                new_width = int(width * (max_height / height))
                                
                            img = img.resize((new_width, new_height), Image.LANCZOS)
                        
                        # Save the processed image
                        img.save(logo_path)
                    else:
                        # Save SVG as is
                        logo_file.save(logo_path)
                    
                    # Delete previous logo file if it exists
                    if organization.logo_path:
                        try:
                            old_logo_path = os.path.join(current_app.root_path, 'static', organization.logo_path.lstrip('/static/'))
                            if os.path.exists(old_logo_path):
                                os.remove(old_logo_path)
                        except Exception as e:
                            logger.warning(f"Could not delete old logo: {str(e)}")
                    
                    # Update the logo path in database (store relative path for URL generation)
                    organization.logo_path = f"/static/uploads/logos/{unique_filename}"
                    
                    # Add cache-busting parameter to prevent browser caching
                    organization.logo_path += f"?v={int(datetime.utcnow().timestamp())}"
                    
                except Exception as e:
                    logger.error(f"Error processing logo: {str(e)}")
                    flash(f'Error processing logo: {str(e)}', 'danger')
                    return redirect(url_for('organizations.edit_branding', org_id=org_id))
        
        # Update branding settings
        organization.primary_color = request.form.get('primary_color')
        organization.secondary_color = request.form.get('secondary_color')
        organization.email_footer_text = request.form.get('email_footer_text')
        
        db.session.commit()
        
        flash('Branding updated successfully!', 'success')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    return render_template('organizations/branding.html', 
                          organization=organization)

def send_invitation_email(invitation):
    """
    Send an organization invitation email using SendGrid.
    
    Args:
        invitation: OrganizationInvitation object
        
    Returns:
        Boolean indicating success or failure
    """
    import traceback  # For detailed error logging
    import os
    import json
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, From, To, Subject, HtmlContent
    
    # Import the SendGrid logger for detailed API logging
    from sendgrid_logger import (
        log_api_request, 
        log_api_response, 
        log_api_error, 
        log_email_content
    )
    
    try:
        organization = invitation.organization
        inviter = invitation.invited_by
        
        # Create the accept invitation URL
        accept_url = url_for('organizations.accept_invitation', 
                            token=invitation.token, 
                            _external=True)
        
        # Get app URL for links
        app_url = request.host_url.rstrip('/')
        
        # Create the subject for email
        subject = f"Invitation to join {organization.name} on DevelopSriLanka"
        
        # Log the email attempt
        logger.info(f"Sending organization invitation email to: {invitation.email}")
        logger.info(f"From: {inviter.name if inviter else 'Organization owner'} <{inviter.email if inviter else 'unknown'}>")
        logger.info(f"Subject: {subject}")
        
        # Get the SendGrid API key
        sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
        
        # Check if SendGrid API key is configured
        if not sendgrid_api_key:
            logger.warning("SendGrid API key is not configured. Email will not be sent.")
            logger.info(f"Would have sent organization invitation email to: {invitation.email}")
            logger.info(f"Subject: {subject}")
            flash("Email feature requires SendGrid API key configuration", "warning")
            return False
        
        # Create HTML email with organization branding
        html_content = render_template('email/organization_invitation.html',
                                organization=organization,
                                inviter=inviter,
                                accept_url=accept_url,
                                invitation=invitation,
                                current_year=datetime.utcnow().year)
        
        # Get sender email from config or use a default
        # Use noreply@developsrilanka.com as the default sender for better deliverability
        # This address is more likely to be verified in the SendGrid account
        from_email = current_app.config.get('MAIL_DEFAULT_SENDER', ('DevelopSriLanka', 'noreply@developsrilanka.com'))
        if isinstance(from_email, tuple):
            sender_name, sender_email = from_email
        else:
            sender_name = 'DevelopSriLanka'
            sender_email = from_email
            
        # Use the verified sender email address from SendGrid account
        # This is the only verified sender that can be used with this SendGrid account
        sender_email = 'info@developsrilanka.com'  
        
        # Log which sender email we're using
        logger.info(f"Using verified SendGrid sender email address: {sender_email}")
            
        # Validate the recipient email address 
        import re
        recipient_email = invitation.email.strip().lower()
        # More comprehensive email validation pattern
        # This pattern prevents:
        # - Consecutive dots in any part of the email
        # - Leading or trailing dots in local part
        # - Leading dot in domain part
        # - Spaces or special characters in domain
        email_pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*[a-zA-Z0-9]@[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$'
        
        if not re.match(email_pattern, recipient_email):
            logger.error(f"Invalid recipient email format: {recipient_email}")
            return False
            
        # Create SendGrid mail message with proper sender formatting
        # The recommended way to set sender info based on SendGrid documentation
        message = Mail(
            from_email=sender_email,  # Just use plain email address
            to_emails=recipient_email,  # Use the validated and normalized email
            subject=subject,
            html_content=html_content
        )
        
        # Set the friendly display name separately for better compatibility
        # This is the recommended pattern in SendGrid's Python SDK
        message.from_email.name = f"{organization.name} via {sender_name}"
        
        # Send the email using SendGrid
        try:
            # Log email sending details using both regular logger and SendGrid logger
            logger.info(f"Preparing to send organization invitation email with SendGrid:")
            logger.info(f"From: {organization.name} via {sender_name} <{sender_email}>")
            logger.info(f"To: {invitation.email}")
            logger.info(f"Subject: {subject}")
            
            # Use detailed SendGrid logger to log the API request
            log_api_request(
                recipient_email=recipient_email, 
                sender_email=sender_email,
                sender_name=f"{organization.name} via {sender_name}",
                subject=subject
            )
            
            # Log the email content for debugging
            log_email_content(html_content=html_content)
            
            # Create SendGrid client and send message
            sg = SendGridAPIClient(sendgrid_api_key)
            
            # Capture raw API request data for logging
            api_request = message.get()
            print(f"SENDGRID API REQUEST: {json.dumps(api_request, indent=2)}")
            
            # Send the message
            response = sg.send(message)
            
            # Log the response with both loggers
            logger.info(f"SendGrid response status code: {response.status_code}")
            logger.info(f"SendGrid response body: {response.body}")
            logger.info(f"SendGrid response headers: {response.headers}")
            
            # Use detailed SendGrid logger for response
            log_api_response(
                status_code=response.status_code,
                response_body=response.body,
                response_headers=response.headers
            )
            
            # Log successful sending
            logger.info(f"Successfully sent organization invitation via SendGrid to {invitation.email}")
            print(f"SENDGRID EMAIL SENT: Successfully sent organization invitation to {invitation.email}")
            
            return True
            
        except Exception as sendgrid_error:
            error_details = traceback.format_exc()
            
            # Standard logger
            logger.error(f"==== SENDGRID ERROR DETAILS ====")
            logger.error(f"Failed to send organization invitation via SendGrid to {invitation.email}")
            logger.error(f"Error type: {type(sendgrid_error).__name__}")
            logger.error(f"Error message: {str(sendgrid_error)}")
            logger.error(f"From email: {sender_email}")
            logger.error(f"From name: {organization.name} via {sender_name}")
            logger.error(f"Full error traceback: {error_details}")
            logger.error(f"==== END SENDGRID ERROR DETAILS ====")
            
            # Use detailed SendGrid logger for error
            log_api_error(
                error=sendgrid_error,
                recipient_email=recipient_email,
                error_type=type(sendgrid_error).__name__,
                detailed_traceback=True
            )
            
            # Also print to console for immediate debugging
            print(f"SENDGRID EMAIL ERROR: Failed to send invitation to {invitation.email}")
            print(f"Error type: {type(sendgrid_error).__name__}")
            print(f"Error message: {str(sendgrid_error)}")
            
            flash(f"Failed to send organization invitation: {str(sendgrid_error)}", "danger")
            return False
            
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"Failed to prepare organization invitation email: {str(e)}")
        logger.error(f"Error details: {error_details}")
        flash(f"Organization invitation system error: {str(e)}", "danger")
        return False

@organizations_bp.route('/list')
@login_required
def list_organizations():
    """API endpoint to get all organizations for the current user."""
    logger.debug(f"Fetching organizations list for user_id={current_user.id}")
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    
    logger.debug(f"Found {len(user_orgs)} organizations for user")
    
    organizations = []
    for org_user in user_orgs:
        organizations.append({
            'id': org_user.organization_id,
            'name': org_user.organization.name,
            'is_default': org_user.is_default
        })
        logger.debug(f"Adding organization to list: id={org_user.organization_id}, name={org_user.organization.name}")
    
    response = {
        'success': True,
        'organizations': organizations
    }
    logger.debug(f"Returning organization list response: {response}")
    return jsonify(response)

@organizations_bp.route('/api/organizations')
@login_required
def api_organizations():
    """
    API endpoint to get all organizations for the current user.
    Used for organization dropdown in forms.
    """
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    
    # Get default organization ID
    default_organization_id = None
    for org_user in user_orgs:
        if org_user.is_default:
            default_organization_id = org_user.organization_id
            break
    
    # Convert to simple dict format
    organizations = []
    for org_user in user_orgs:
        org = org_user.organization
        organizations.append({
            'id': org.id,
            'name': org.name
        })
    
    return jsonify({
        'organizations': organizations,
        'default_organization_id': default_organization_id
    })

@organizations_bp.route('/api/organizations/<int:org_id>/clients')
@login_required
def api_organization_clients(org_id):
    """
    API endpoint to get clients for a specific organization.
    Used for client dropdown in forms.
    """
    # Check if user has access to this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first_or_404()
    
    # Get clients for this organization
    clients = Client.query.filter_by(organization_id=org_id).all()
    
    # Convert to simple dict format
    client_list = [{'id': client.id, 'name': client.name} for client in clients]
    
    return jsonify({
        'organization_id': org_id,
        'organization_name': org_user.organization.name,
        'clients': client_list
    })

def register_routes(app):
    """Register the organization routes with the app."""
    app.register_blueprint(organizations_bp)
    logger.info("Organization routes registered")
    
    # Add jinja function for current time
    @app.template_filter('now')
    def _jinja2_filter_now():
        return datetime.utcnow()