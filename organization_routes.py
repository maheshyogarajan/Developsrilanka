"""
Routes for organization management and team invitations.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort, current_app
from flask_login import login_required, current_user
from flask_mail import Message

from models import db, Organization, OrganizationUser, OrganizationInvitation, User, UserRole
from app import mail
from decorators import role_required

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create Blueprint
bp = Blueprint('organizations', __name__, url_prefix='/organizations')

@bp.route('/')
@login_required
def organizations():
    """Render the organizations dashboard with list of user's organizations."""
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    return render_template('organizations/index.html', user_orgs=user_orgs)

@bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_organization():
    """Create a new organization."""
    if request.method == 'POST':
        name = request.form.get('name')
        website = request.form.get('website')
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        tax_registration_number = request.form.get('tax_registration_number')
        primary_color = request.form.get('primary_color', '#4a6da7')
        secondary_color = request.form.get('secondary_color', '#f5f8ff')
        
        if not name:
            flash('Organization name is required', 'danger')
            return redirect(url_for('organizations.create_organization'))
        
        # Create the organization
        org = Organization(
            name=name,
            website=website,
            email=email,
            phone=phone,
            address=address,
            tax_registration_number=tax_registration_number,
            primary_color=primary_color,
            secondary_color=secondary_color
        )
        db.session.add(org)
        db.session.flush()  # Get the org ID
        
        # Link the current user as owner
        org_user = OrganizationUser(
            user_id=current_user.id,
            organization_id=org.id,
            role=UserRole.OWNER.value,
            is_default=True
        )
        db.session.add(org_user)
        
        # Make this the default organization if it's the user's first
        user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
        if not user_orgs:
            org_user.is_default = True
        
        db.session.commit()
        flash('Organization created successfully', 'success')
        return redirect(url_for('organizations.view_organization', org_id=org.id))
    
    return render_template('organizations/create.html')

@bp.route('/<int:org_id>')
@login_required
def view_organization(org_id):
    """View a specific organization."""
    # Check if user is a member of this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user and not current_user.is_admin():
        flash('You do not have access to this organization', 'danger')
        return redirect(url_for('organizations.organizations'))
    
    organization = Organization.query.get_or_404(org_id)
    team_members = OrganizationUser.query.filter_by(organization_id=org_id).all()
    pending_invitations = OrganizationInvitation.query.filter_by(
        organization_id=org_id, 
        accepted=False
    ).all()
    
    return render_template(
        'organizations/view.html',
        organization=organization,
        org_user=org_user,
        team_members=team_members,
        pending_invitations=pending_invitations
    )

@bp.route('/<int:org_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_organization(org_id):
    """Edit an existing organization."""
    # Check if user has admin or owner role in this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user or (org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]) and not current_user.is_admin():
        flash('You do not have permission to edit this organization', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = Organization.query.get_or_404(org_id)
    
    if request.method == 'POST':
        organization.name = request.form.get('name')
        organization.website = request.form.get('website')
        organization.email = request.form.get('email')
        organization.phone = request.form.get('phone')
        organization.address = request.form.get('address')
        organization.tax_registration_number = request.form.get('tax_registration_number')
        organization.primary_color = request.form.get('primary_color', '#4a6da7')
        organization.secondary_color = request.form.get('secondary_color', '#f5f8ff')
        organization.email_footer_text = request.form.get('email_footer_text')
        
        db.session.commit()
        flash('Organization updated successfully', 'success')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    return render_template('organizations/edit.html', organization=organization)

@bp.route('/<int:org_id>/delete', methods=['POST'])
@login_required
def delete_organization(org_id):
    """Delete an organization (owner only)."""
    # Check if user is owner of this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id,
        role=UserRole.OWNER.value
    ).first()
    
    if not org_user and not current_user.is_admin():
        flash('You do not have permission to delete this organization', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = Organization.query.get_or_404(org_id)
    
    # Check if this is the user's only organization
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    if len(user_orgs) == 1 and user_orgs[0].organization_id == org_id:
        flash('You cannot delete your only organization', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # If this was the default organization, set another one as default
    if org_user and org_user.is_default:
        other_org = OrganizationUser.query.filter(
            OrganizationUser.user_id == current_user.id,
            OrganizationUser.organization_id != org_id
        ).first()
        if other_org:
            other_org.is_default = True
    
    # Delete the organization and all its team memberships
    db.session.delete(organization)
    db.session.commit()
    
    flash('Organization deleted successfully', 'success')
    return redirect(url_for('organizations.organizations'))

@bp.route('/<int:org_id>/set-default', methods=['POST'])
@login_required
def set_default_organization(org_id):
    """Set an organization as the user's default."""
    # Check if user is a member of this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user:
        flash('You are not a member of this organization', 'danger')
        return redirect(url_for('organizations.organizations'))
    
    # Remove default from all other organizations
    OrganizationUser.query.filter_by(user_id=current_user.id).update({'is_default': False})
    
    # Set this one as default
    org_user.is_default = True
    db.session.commit()
    
    flash('Default organization updated successfully', 'success')
    return redirect(url_for('organizations.organizations'))

@bp.route('/<int:org_id>/invite', methods=['GET', 'POST'])
@login_required
def invite_team_member(org_id):
    """Invite a new team member to the organization."""
    # Check if user has permission to invite members
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user or (org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]) and not current_user.is_admin():
        flash('You do not have permission to invite team members', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = Organization.query.get_or_404(org_id)
    
    if request.method == 'POST':
        email = request.form.get('email')
        role = request.form.get('role', UserRole.MEMBER.value)
        
        if not email:
            flash('Email address is required', 'danger')
            return redirect(url_for('organizations.invite_team_member', org_id=org_id))
        
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            # Check if already a member
            existing_membership = OrganizationUser.query.filter_by(
                user_id=existing_user.id,
                organization_id=org_id
            ).first()
            
            if existing_membership:
                flash('This user is already a member of your organization', 'warning')
                return redirect(url_for('organizations.view_organization', org_id=org_id))
        
        # Check for existing invitation
        existing_invitation = OrganizationInvitation.query.filter_by(
            organization_id=org_id,
            email=email,
            accepted=False
        ).first()
        
        if existing_invitation and not existing_invitation.is_expired():
            flash('An invitation has already been sent to this email address', 'warning')
            return redirect(url_for('organizations.view_organization', org_id=org_id))
        
        # Generate invitation token
        token = str(uuid.uuid4())
        expires_at = datetime.utcnow() + timedelta(days=7)
        
        # Create invitation
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
        try:
            send_invitation_email(invitation)
            flash('Invitation sent successfully', 'success')
        except Exception as e:
            logger.error(f"Failed to send invitation email: {str(e)}")
            flash('Invitation created but email could not be sent', 'warning')
        
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    return render_template(
        'organizations/invite.html',
        organization=organization,
        roles=[role.value for role in UserRole if role != UserRole.OWNER]  # Don't allow inviting owners
    )

@bp.route('/invitations/<token>')
def accept_invitation(token):
    """Accept an organization invitation."""
    invitation = OrganizationInvitation.query.filter_by(token=token).first_or_404()
    
    if invitation.is_expired():
        flash('This invitation has expired', 'danger')
        return redirect(url_for('home'))
    
    if invitation.accepted:
        flash('This invitation has already been accepted', 'warning')
        return redirect(url_for('home'))
    
    organization = Organization.query.get_or_404(invitation.organization_id)
    
    # Check if user is logged in
    if current_user.is_authenticated:
        # If logged in user doesn't match invitation email
        if current_user.email != invitation.email:
            flash('This invitation was sent to a different email address', 'danger')
            return redirect(url_for('home'))
        
        # Add user to organization
        existing_membership = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=invitation.organization_id
        ).first()
        
        if existing_membership:
            flash('You are already a member of this organization', 'info')
        else:
            org_user = OrganizationUser(
                user_id=current_user.id,
                organization_id=invitation.organization_id,
                role=invitation.role
            )
            
            # Check if this is the user's first organization
            user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
            if not user_orgs:
                org_user.is_default = True
            
            db.session.add(org_user)
            
            # Mark invitation as accepted
            invitation.accepted = True
            db.session.commit()
            
            flash(f'You have joined {organization.name}!', 'success')
        
        return redirect(url_for('organizations.view_organization', org_id=organization.id))
    else:
        # Store invitation token in session and redirect to login
        # Use Flask sessions to store the token temporarily
        session = {}
        session['pending_invitation_token'] = token
        return redirect(url_for('login', next=url_for('organizations.accept_invitation', token=token)))

@bp.route('/<int:org_id>/members/<int:user_id>/role', methods=['POST'])
@login_required
def update_member_role(org_id, user_id):
    """Update a team member's role."""
    # Check if current user is owner or admin
    current_org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not current_org_user or (current_org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]) and not current_user.is_admin():
        flash('You do not have permission to update member roles', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Cannot change role of organization owner
    target_org_user = OrganizationUser.query.filter_by(
        user_id=user_id, 
        organization_id=org_id
    ).first_or_404()
    
    if target_org_user.role == UserRole.OWNER.value and current_org_user.role != UserRole.OWNER.value:
        flash('Only the organization owner can change an owner\'s role', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Update role
    new_role = request.form.get('role')
    if new_role not in [role.value for role in UserRole]:
        flash('Invalid role', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Prevent creating multiple owners
    if new_role == UserRole.OWNER.value:
        existing_owner = OrganizationUser.query.filter_by(
            organization_id=org_id,
            role=UserRole.OWNER.value
        ).first()
        
        if existing_owner and existing_owner.user_id != user_id:
            flash('An organization can only have one owner. Please remove the existing owner first.', 'danger')
            return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    target_org_user.role = new_role
    db.session.commit()
    
    flash('Member role updated successfully', 'success')
    return redirect(url_for('organizations.view_organization', org_id=org_id))

@bp.route('/<int:org_id>/members/<int:user_id>/remove', methods=['POST'])
@login_required
def remove_team_member(org_id, user_id):
    """Remove a team member from the organization."""
    # Check if current user is owner or admin
    current_org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not current_org_user or (current_org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]) and not current_user.is_admin():
        flash('You do not have permission to remove team members', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Cannot remove organization owner
    target_org_user = OrganizationUser.query.filter_by(
        user_id=user_id, 
        organization_id=org_id
    ).first_or_404()
    
    if target_org_user.role == UserRole.OWNER.value:
        flash('The organization owner cannot be removed', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Cannot remove yourself
    if target_org_user.user_id == current_user.id:
        flash('You cannot remove yourself from the organization', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    # Remove member
    db.session.delete(target_org_user)
    db.session.commit()
    
    flash('Team member removed successfully', 'success')
    return redirect(url_for('organizations.view_organization', org_id=org_id))

@bp.route('/<int:org_id>/invitations/<int:invitation_id>/cancel', methods=['POST'])
@login_required
def cancel_invitation(org_id, invitation_id):
    """Cancel a pending invitation."""
    # Check if current user is owner or admin
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user or (org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]) and not current_user.is_admin():
        flash('You do not have permission to cancel invitations', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    invitation = OrganizationInvitation.query.filter_by(
        id=invitation_id, 
        organization_id=org_id
    ).first_or_404()
    
    if invitation.accepted:
        flash('This invitation has already been accepted and cannot be cancelled', 'warning')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    db.session.delete(invitation)
    db.session.commit()
    
    flash('Invitation cancelled successfully', 'success')
    return redirect(url_for('organizations.view_organization', org_id=org_id))

@bp.route('/<int:org_id>/branding', methods=['GET', 'POST'])
@login_required
def edit_branding(org_id):
    """Edit the organization's branding settings."""
    # Check if user has admin or owner role in this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id, 
        organization_id=org_id
    ).first()
    
    if not org_user or (org_user.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]) and not current_user.is_admin():
        flash('You do not have permission to edit branding settings', 'danger')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    organization = Organization.query.get_or_404(org_id)
    
    if request.method == 'POST':
        organization.primary_color = request.form.get('primary_color', '#4a6da7')
        organization.secondary_color = request.form.get('secondary_color', '#f5f8ff')
        organization.email_footer_text = request.form.get('email_footer_text')
        
        # Handle logo upload if provided
        if 'logo' in request.files and request.files['logo']:
            logo_file = request.files['logo']
            if logo_file.filename != '':
                try:
                    # Create uploads directory if it doesn't exist
                    upload_dir = os.path.join(current_app.static_folder, 'uploads', 'logos')
                    os.makedirs(upload_dir, exist_ok=True)
                    
                    # Generate unique filename
                    filename = f"{org_id}_{uuid.uuid4()}.{logo_file.filename.split('.')[-1]}"
                    file_path = os.path.join(upload_dir, filename)
                    
                    # Save file
                    logo_file.save(file_path)
                    
                    # Update logo path in database (store relative path)
                    organization.logo_path = f"/static/uploads/logos/{filename}"
                    
                except Exception as e:
                    logger.error(f"Failed to upload logo: {str(e)}")
                    flash('Failed to upload logo', 'danger')
        
        db.session.commit()
        flash('Branding settings updated successfully', 'success')
        return redirect(url_for('organizations.view_organization', org_id=org_id))
    
    return render_template('organizations/branding.html', organization=organization)

def send_invitation_email(invitation):
    """
    Send an organization invitation email.
    
    Args:
        invitation: OrganizationInvitation object
    """
    organization = Organization.query.get(invitation.organization_id)
    invited_by = User.query.get(invitation.invited_by_user_id)
    
    subject = f"{invited_by.name or 'Someone'} invited you to join {organization.name}"
    
    accept_url = url_for('organizations.accept_invitation', token=invitation.token, _external=True)
    
    # HTML email body
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ text-align: center; margin-bottom: 20px; }}
            .button {{ display: inline-block; background-color: {organization.primary_color}; color: white; text-decoration: none; padding: 10px 20px; border-radius: 4px; }}
            .footer {{ margin-top: 30px; font-size: 12px; color: #777; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>You've been invited!</h1>
            </div>
            
            <p>Hello,</p>
            
            <p>{invited_by.name or 'Someone'} has invited you to join <strong>{organization.name}</strong> on the financial platform.</p>
            
            <p>You've been invited to join as: <strong>{invitation.role}</strong></p>
            
            <p style="text-align: center; margin: 30px 0;">
                <a href="{accept_url}" class="button">Accept Invitation</a>
            </p>
            
            <p>This invitation will expire in 7 days.</p>
            
            <p>If you don't have an account yet, you'll be able to create one when you accept the invitation.</p>
            
            <div class="footer">
                <p>If you received this email by mistake, you can simply ignore it.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Create message
    msg = Message(
        subject=subject,
        recipients=[invitation.email],
        html=html_content,
        sender=(organization.name, organization.email if organization.email else os.environ.get('GMAIL_USERNAME'))
    )
    
    # Send email
    mail.send(msg)

def register_routes(app):
    """Register the organization routes with the app."""
    app.register_blueprint(bp)
    
    # Add additional helpers to Jinja environment
    app.jinja_env.globals.update(
        UserRole=UserRole
    )