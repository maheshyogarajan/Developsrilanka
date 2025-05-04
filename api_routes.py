"""
API endpoints for various data access.
These endpoints allow front-end AJAX calls to get data efficiently.
"""

from flask import jsonify, request
from flask_login import login_required, current_user
from sqlalchemy import text

from app import app, db
from models import OrganizationUser, Client, Organization

@app.route('/api/organizations/<int:org_id>/clients')
@login_required
def api_organization_clients(org_id):
    """API endpoint to get clients for a specific organization."""
    # Check if user has access to this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user:
        return jsonify({'error': 'Access denied'}), 403
    
    # Get all clients for this organization
    result = db.session.execute(text("""
        SELECT id, name, company_name, contact_person, email, phone, address
        FROM client 
        WHERE organization_id = :org_id
        ORDER BY name ASC
    """), {'org_id': org_id})
    
    # Convert to list of dictionaries
    clients = []
    for row in result:
        clients.append({
            'id': row[0],
            'name': row[1],
            'company_name': row[2],
            'contact_person': row[3],
            'email': row[4],
            'phone': row[5],
            'address': row[6]
        })
    
    return jsonify({'clients': clients})

@app.route('/api/user/organizations')
@login_required
def api_user_organizations():
    """API endpoint to get all organizations for the current user."""
    try:
        # Get all organizations for the current user with their role
        org_users = OrganizationUser.query.filter_by(
            user_id=current_user.id
        ).join(
            Organization, 
            Organization.id == OrganizationUser.organization_id
        ).with_entities(
            Organization.id,
            Organization.name,
            OrganizationUser.role,
            OrganizationUser.is_default
        ).all()
        
        # Convert to list of dictionaries
        organizations = []
        for org_id, name, role, is_default in org_users:
            organizations.append({
                'id': org_id,
                'name': name,
                'role': role,
                'is_default': is_default
            })
        
        return jsonify({
            'organizations': organizations,
            'current_organization_id': session.get('current_organization_id')
        })
    except Exception as e:
        app.logger.error(f"Error fetching user organizations: {str(e)}")
        return jsonify({'error': 'Failed to fetch organizations'}), 500