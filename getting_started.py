"""
Getting Started wizard routes for guiding new users through the setup process.
"""
from flask import Blueprint, render_template, redirect, url_for, request, jsonify, flash
from flask_login import current_user, login_required
from app import db
from datetime import datetime
from onboarding_models import OnboardingProgress
from models import OrganizationUser, Client, Organization, BankAccount, Receipt

# Create blueprint
getting_started_bp = Blueprint('getting_started', __name__)

@getting_started_bp.route('/')
@login_required
def wizard():
    """Render the Getting Started wizard page."""
    # Get or create onboarding progress record
    progress = OnboardingProgress.query.filter_by(user_id=current_user.id).first()
    if not progress:
        progress = OnboardingProgress(user_id=current_user.id)
        db.session.add(progress)
        db.session.commit()
    
    # Check for completion
    all_completed = all([
        progress.profile_completed,
        progress.organization_created,
        progress.bank_account_added,
        progress.team_member_invited,
        progress.customer_added,
        progress.document_scanned
    ])
    
    if all_completed and not progress.completed_at:
        progress.completed_at = datetime.utcnow()
        db.session.commit()
    
    # Get list of organizations the user belongs to
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    has_organizations = len(user_orgs) > 0
    
    # Find default organization for links
    default_organization = None
    if has_organizations:
        # First check for primary organization
        for org_user in user_orgs:
            if hasattr(org_user, 'is_primary') and org_user.is_primary:
                default_organization = Organization.query.get(org_user.organization_id)
                break
        
        # If no primary found, use the first one
        if not default_organization and user_orgs:
            default_organization = Organization.query.get(user_orgs[0].organization_id)
    
    # Check if user has set up profile
    has_profile = current_user.name is not None
    
    # Check if user has added bank accounts
    bank_accounts = BankAccount.query.filter_by(user_id=current_user.id).all()
    has_bank_accounts = len(bank_accounts) > 0
    
    # Check if user has clients
    clients = Client.query.filter_by(user_id=current_user.id).all()
    has_clients = len(clients) > 0
    
    # Check if user has scanned receipts
    receipts = Receipt.query.filter_by(user_id=current_user.id).all()
    has_scanned_receipts = len(receipts) > 0
    
    # Check if user has invited team members
    has_invited_team = len(current_user.sent_invitations) > 0
    
    # If any of these conditions are met but not reflected in progress, update progress
    if has_profile and not progress.profile_completed:
        progress.profile_completed = True
        db.session.commit()
    
    if has_organizations and not progress.organization_created:
        progress.organization_created = True
        db.session.commit()
    
    if has_bank_accounts and not progress.bank_account_added:
        progress.bank_account_added = True
        db.session.commit()
    
    if has_clients and not progress.customer_added:
        progress.customer_added = True
        db.session.commit()
    
    if has_scanned_receipts and not progress.document_scanned:
        progress.document_scanned = True
        db.session.commit()
    
    if has_invited_team and not progress.team_member_invited:
        progress.team_member_invited = True
        db.session.commit()
    
    return render_template('getting_started.html', 
                          progress=progress,
                          user_has_organizations=has_organizations,
                          default_organization=default_organization,
                          completion_percentage=progress.get_completion_percentage())

@getting_started_bp.route('/create-personal-finances', methods=['POST'])
@login_required
def create_personal_finances():
    """Create a 'Personal Finances' organization for the current user."""
    # Check if user already has any organizations
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    if user_orgs:
        return jsonify({
            'success': False,
            'error': 'You already have organizations. Please use the existing ones.'
        }), 400
    
    try:
        # Determine the UserRole enum value for OWNER
        from models import UserRole
        owner_role = UserRole.OWNER.value
        
        # Create the organization
        org = Organization(
            name="Personal Finances",
            type="PERSONAL",
            description="My personal finances and expenses",
            created_by_user_id=current_user.id,
            created_at=datetime.utcnow()
        )
        db.session.add(org)
        db.session.flush()  # Get ID without committing
        
        # Create owner relationship
        org_user = OrganizationUser(
            user_id=current_user.id,
            organization_id=org.id,
            role=owner_role,
            is_primary=True
        )
        db.session.add(org_user)
        
        # Update onboarding progress
        progress = OnboardingProgress.query.filter_by(user_id=current_user.id).first()
        if not progress:
            progress = OnboardingProgress(user_id=current_user.id)
            db.session.add(progress)
        
        progress.organization_created = True
        db.session.commit()
        
        flash('Personal Finances organization created successfully!', 'success')
        
        return jsonify({
            'success': True,
            'organization_id': org.id,
            'message': 'Personal Finances organization created successfully'
        })
    
    except Exception as e:
        db.session.rollback()
        print(f"Error creating Personal Finances organization: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to create organization. Please try again or contact support.'
        }), 500

@getting_started_bp.route('/api/update', methods=['POST'])
@login_required
def update_progress():
    """API endpoint to manually update onboarding progress."""
    progress = OnboardingProgress.query.filter_by(user_id=current_user.id).first()
    if not progress:
        return jsonify({'error': 'Progress record not found'}), 404
    
    step = request.json.get('step')
    completed = request.json.get('completed', True)
    
    valid_steps = {
        'profile': 'profile_completed',
        'organization': 'organization_created',
        'bank_account': 'bank_account_added',
        'team_member': 'team_member_invited',
        'customer': 'customer_added',
        'document': 'document_scanned'
    }
    
    if step in valid_steps:
        setattr(progress, valid_steps[step], completed)
    else:
        return jsonify({'error': 'Invalid step'}), 400
    
    # Check if all steps are now completed
    if all([
        progress.profile_completed,
        progress.organization_created,
        progress.bank_account_added,
        progress.team_member_invited,
        progress.customer_added,
        progress.document_scanned
    ]) and not progress.completed_at:
        progress.completed_at = datetime.utcnow()
        flash('Congratulations! You have completed all the getting started steps.', 'success')
    
    db.session.commit()
    return jsonify({'success': True, 'percentage': progress.get_completion_percentage()})

def register_routes(app):
    """Register the getting started routes with the Flask app."""
    app.register_blueprint(getting_started_bp, url_prefix='/getting-started')