"""
API endpoints for various data access.
These endpoints allow front-end AJAX calls to get data efficiently.
"""

from flask import jsonify, request, session
from flask_login import login_required, current_user
from sqlalchemy import text
from flask_wtf.csrf import CSRFProtect

from app import app, db, csrf
from models import OrganizationUser, Client, Organization

# Exempt API routes from CSRF protection
@csrf.exempt
@app.route('/api/organization/<int:org_id>/clients')
@app.route('/api/organizations/<int:org_id>/clients')  # Keep this for backward compatibility
@login_required
def api_organization_clients(org_id):
    """API endpoint to get clients for a specific organization."""
    try:
        app.logger.debug(f"Fetching clients for organization {org_id}")
        
        # Check if user has access to this organization
        org_user = OrganizationUser.query.filter_by(
            user_id=current_user.id,
            organization_id=org_id
        ).first()
        
        if not org_user:
            app.logger.warning(f"User {current_user.id} attempted to access clients for unauthorized org {org_id}")
            return jsonify({'error': 'Access denied', 'clients': []}), 403
        
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
        
        app.logger.debug(f"Found {len(clients)} clients for organization {org_id}")
        return jsonify({'clients': clients})
        
    except Exception as e:
        app.logger.error(f"Error fetching clients for org {org_id}: {str(e)}")
        return jsonify({'error': str(e), 'clients': []})

@csrf.exempt
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
        app.logger.debug(f"Found {len(org_users)} organizations for user_id={current_user.id}")
        for org_id, name, role, is_default in org_users:
            organizations.append({
                'id': org_id,
                'name': name,
                'role': role,
                'is_default': is_default
            })
            app.logger.debug(f"Adding organization: id={org_id}, name={name}, role={role}, is_default={is_default}")
        
        return jsonify({
            'organizations': organizations,
            'current_organization_id': session.get('current_organization_id')
        })
    except Exception as e:
        app.logger.error(f"Error fetching user organizations: {str(e)}")
        return jsonify({'error': 'Failed to fetch organizations'}), 500
        
@csrf.exempt
@app.route('/api/receipt/save-context', methods=['POST'])
@login_required
def api_save_receipt_context():
    """API endpoint to save receipt context (organization and expense type)."""
    try:
        organization_id = request.json.get('organization_id')
        expense_type = request.json.get('expense_type', 'personal')  # 'personal' or 'company'
        client_id = request.json.get('client_id')  # Add client_id parameter
        
        # Validate organization access
        if organization_id:
            org_user = OrganizationUser.query.filter_by(
                user_id=current_user.id,
                organization_id=organization_id
            ).first()
            
            if not org_user:
                return jsonify({'error': 'Access denied to this organization'}), 403
            
            # Validate client belongs to organization if client_id is provided
            if client_id:
                client = Client.query.filter_by(
                    id=client_id,
                    organization_id=organization_id
                ).first()
                
                if not client:
                    app.logger.warning(f"Invalid client {client_id} for organization {organization_id}")
                    return jsonify({'error': 'Invalid client for this organization'}), 400
                
                app.logger.debug(f"Valid client {client_id} ({client.name}) for organization {organization_id}")
                
            # Store in session
            session['receipt_organization_id'] = organization_id
            session['receipt_expense_type'] = expense_type
            session['receipt_client_id'] = client_id  # Store client_id in session
            
            return jsonify({
                'success': True,
                'organization_id': organization_id,
                'expense_type': expense_type,
                'client_id': client_id
            })
        else:
            return jsonify({'error': 'Organization ID is required'}), 400
            
    except Exception as e:
        app.logger.error(f"Error saving receipt context: {str(e)}")
        return jsonify({'error': 'Failed to save receipt context'}), 500
        
@csrf.exempt
@app.route('/api/receipt/get-context')
@login_required
def api_get_receipt_context():
    """API endpoint to get the current receipt context."""
    try:
        # Get the context from session
        organization_id = session.get('receipt_organization_id')
        expense_type = session.get('receipt_expense_type', 'personal')
        client_id = session.get('receipt_client_id')
        
        app.logger.debug(f"Getting receipt context: org={organization_id}, type={expense_type}, client={client_id}")
        
        # If no organization ID is set, use the user's default organization
        if not organization_id:
            default_org = OrganizationUser.query.filter_by(
                user_id=current_user.id,
                is_default=True
            ).first()
            
            if default_org:
                organization_id = default_org.organization_id
                session['receipt_organization_id'] = organization_id
            else:
                # No default org, try to find any organization the user belongs to
                any_org = OrganizationUser.query.filter_by(
                    user_id=current_user.id
                ).first()
                
                if any_org:
                    organization_id = any_org.organization_id
                    session['receipt_organization_id'] = organization_id
        
        # Get organization details
        org_details = None
        if organization_id:
            org = Organization.query.get(organization_id)
            if org:
                org_details = {
                    'id': org.id,
                    'name': org.name
                }
        
        # Get client details if client_id exists
        client_details = None
        if client_id:
            client = Client.query.get(client_id)
            if client:
                client_details = {
                    'id': client.id,
                    'name': client.name
                }
                app.logger.debug(f"Found client details: {client.id} - {client.name}")
            else:
                app.logger.warning(f"Client with ID {client_id} not found")
                # Clear invalid client_id from session
                session.pop('receipt_client_id', None)
        
        return jsonify({
            'organization_id': organization_id,
            'organization': org_details,
            'expense_type': expense_type,
            'client_id': client_id,
            'client': client_details
        })
            
    except Exception as e:
        app.logger.error(f"Error fetching receipt context: {str(e)}")
        return jsonify({'error': 'Failed to fetch receipt context'}), 500