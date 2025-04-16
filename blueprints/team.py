"""
Team management blueprint for viewing organization members and invitations.
"""
import logging
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, abort
from flask_login import login_required, current_user
from models import db, Organization, OrganizationUser, OrganizationInvitation, UserRole, User
from decorators import role_required

# Configure logging
logger = logging.getLogger(__name__)

# Create blueprint
team_bp = Blueprint('team', __name__, url_prefix='/organizations')

@team_bp.route('/<int:org_id>/team')
@login_required
def team_dashboard(org_id):
    """Render team overview for a specific organization."""
    # Get all of the user's organizations
    user_orgs = OrganizationUser.query.filter_by(user_id=current_user.id).all()
    
    # Get the specified organization
    active_org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first_or_404()
    
    active_org = active_org_user.organization
    active_org_id = org_id
    
    # If user doesn't have a default organization and didn't specify one, redirect to create
    if not active_org:
        flash('Please create or join an organization first.', 'info')
        return redirect(url_for('organizations.create_organization'))
    
    # Get members of the active organization
    members = OrganizationUser.query.filter_by(organization_id=active_org_id).all()
    
    # Get pending invitations for the active organization
    # Only organization owners and admins can see all invitations
    current_user_role = None
    for org_user in user_orgs:
        if org_user.organization_id == active_org_id:
            current_user_role = org_user.role
    
    invitations = []
    if current_user_role in [UserRole.OWNER.value, UserRole.ADMIN.value]:
        invitations = OrganizationInvitation.query.filter_by(
            organization_id=active_org_id,
            accepted=False
        ).all()
    
    # Organize users by role for hierarchical display
    role_hierarchy = {
        UserRole.OWNER.value: [],
        UserRole.ADMIN.value: [],
        UserRole.MEMBER.value: [],
        UserRole.VIEWER.value: []
    }
    
    for member in members:
        if member.role in role_hierarchy:
            role_hierarchy[member.role].append(member)
    
    # Check if current user can invite others
    can_invite = current_user_role in [UserRole.OWNER.value, UserRole.ADMIN.value]

    return render_template('team/dashboard.html',
                          organizations=user_orgs,
                          active_org=active_org,
                          role_hierarchy=role_hierarchy,
                          invitations=invitations,
                          current_user_role=current_user_role,
                          can_invite=can_invite,
                          UserRole=UserRole,  # Add UserRole enum to template context
                          now=datetime.utcnow())  # For expiration calculations

@team_bp.route('/manage_member/<int:org_id>/<int:member_id>', methods=['POST'])
@login_required
def manage_member(org_id, member_id):
    """Manage organization member (change role or remove)."""
    # Check if user has permission to manage members (owner or admin)
    current_user_org = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first_or_404()
    
    if current_user_org.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]:
        flash('You do not have permission to manage members.', 'danger')
        return redirect(url_for('team.team_dashboard', org_id=org_id))
    
    action = request.form.get('action')
    member = OrganizationUser.query.filter_by(
        id=member_id,
        organization_id=org_id
    ).first_or_404()
    
    # Cannot modify the organization owner except by transferring ownership
    if member.role == UserRole.OWNER.value and action != 'transfer_ownership':
        flash('You cannot modify the role of the organization owner.', 'danger')
        return redirect(url_for('team.team_dashboard', org_id=org_id))
    
    # Cannot modify your own role
    if member.user_id == current_user.id and action != 'leave':
        flash('You cannot modify your own role.', 'danger')
        return redirect(url_for('team.team_dashboard', org_id=org_id))
    
    # Admins cannot modify other admins or owners
    if current_user_org.role == UserRole.ADMIN.value and member.role in [UserRole.ADMIN.value, UserRole.OWNER.value]:
        flash('Admins cannot modify other admins or the owner.', 'danger')
        return redirect(url_for('team.team_dashboard', org_id=org_id))
    
    if action == 'change_role':
        new_role = request.form.get('role')
        
        # Validate the new role
        if new_role not in [role.value for role in UserRole]:
            flash('Invalid role.', 'danger')
            return redirect(url_for('team.team_dashboard', org_id=org_id))
        
        # Cannot set someone as owner through this method
        if new_role == UserRole.OWNER.value:
            flash('Use the transfer ownership function to change the owner.', 'danger')
            return redirect(url_for('team.team_dashboard', org_id=org_id))
        
        member.role = new_role
        db.session.commit()
        
        flash(f'Member role updated successfully.', 'success')
    
    elif action == 'remove':
        # Cannot remove the owner
        if member.role == UserRole.OWNER.value:
            flash('Cannot remove the organization owner.', 'danger')
            return redirect(url_for('team.team_dashboard', org_id=org_id))
        
        db.session.delete(member)
        db.session.commit()
        
        flash(f'Member removed from organization successfully.', 'success')
    
    elif action == 'transfer_ownership':
        # Only the current owner can transfer ownership
        if current_user_org.role != UserRole.OWNER.value:
            flash('Only the organization owner can transfer ownership.', 'danger')
            return redirect(url_for('team.team_dashboard', org_id=org_id))
        
        # Change current owner to admin
        current_user_org.role = UserRole.ADMIN.value
        
        # Set new owner
        member.role = UserRole.OWNER.value
        
        db.session.commit()
        
        flash(f'Organization ownership transferred successfully.', 'success')
    
    elif action == 'leave':
        # Only allow if it's the current user's own membership
        if member.user_id != current_user.id:
            flash('You can only remove yourself with this action.', 'danger')
            return redirect(url_for('team.team_dashboard', org_id=org_id))
        
        # Cannot leave if you are the owner
        if member.role == UserRole.OWNER.value:
            flash('As the owner, you cannot leave the organization. Transfer ownership first.', 'danger')
            return redirect(url_for('team.team_dashboard', org_id=org_id))
        
        db.session.delete(member)
        db.session.commit()
        
        flash(f'You have left the organization successfully.', 'success')
        
        # Redirect to organizations page since user is no longer in this org
        return redirect(url_for('organizations.organizations'))
    
    return redirect(url_for('team.team_dashboard', org_id=org_id))

@team_bp.route('/cancel_invitation/<int:org_id>/<int:invitation_id>', methods=['POST'])
@login_required
def cancel_invitation(org_id, invitation_id):
    """Cancel a pending organization invitation."""
    # Check if user has permission to manage invitations (owner or admin)
    current_user_org = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first_or_404()
    
    if current_user_org.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]:
        flash('You do not have permission to cancel invitations.', 'danger')
        return redirect(url_for('team.team_dashboard', org_id=org_id))
    
    invitation = OrganizationInvitation.query.filter_by(
        id=invitation_id,
        organization_id=org_id
    ).first_or_404()
    
    db.session.delete(invitation)
    db.session.commit()
    
    flash(f'Invitation to {invitation.email} has been cancelled.', 'success')
    return redirect(url_for('team.team_dashboard', org_id=org_id))

@team_bp.route('/resend_invitation/<int:org_id>/<int:invitation_id>', methods=['POST'])
@login_required
def resend_invitation(org_id, invitation_id):
    """Resend a pending organization invitation."""
    # Check if user has permission to manage invitations (owner or admin)
    current_user_org = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first_or_404()
    
    if current_user_org.role not in [UserRole.OWNER.value, UserRole.ADMIN.value]:
        flash('You do not have permission to resend invitations.', 'danger')
        return redirect(url_for('team.team_dashboard', org_id=org_id))
    
    invitation = OrganizationInvitation.query.filter_by(
        id=invitation_id,
        organization_id=org_id
    ).first_or_404()
    
    # Try to send the invitation email again
    try:
        from organization_routes import send_invitation_email
        send_invitation_email(invitation)
        flash(f'Invitation resent to {invitation.email} successfully!', 'success')
    except Exception as e:
        logger.error(f"Failed to resend invitation email: {str(e)}")
        flash(f'Failed to resend invitation email: {str(e)}', 'danger')
    
    return redirect(url_for('team.team_dashboard', org_id=org_id))

def register_routes(app):
    """Register the team management routes with the app."""
    app.register_blueprint(team_bp)
    logger.info("Team management routes registered")