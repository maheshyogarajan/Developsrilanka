"""
Blueprint for client management functionality.
"""
import logging
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text
from app import db
from models import Client

# Configure logging
logger = logging.getLogger(__name__)

# Create blueprint
clients_bp = Blueprint('clients', __name__, url_prefix='/clients')

def register_blueprint(app):
    """Register the clients blueprint with the Flask app."""
    app.register_blueprint(clients_bp)
    return app

@clients_bp.route('/')
@login_required
def list_all_clients():
    """Render the clients page showing list of user's clients."""
    # Get user's organizations
    org_result = db.session.execute(text("""
        SELECT ou.organization_id, o.name, ou.is_default
        FROM organization_user ou
        JOIN organization o ON ou.organization_id = o.id
        WHERE ou.user_id = :user_id
        ORDER BY ou.is_default DESC, o.name
    """), {'user_id': current_user.id})
    
    organizations = [{'id': row[0], 'name': row[1], 'is_default': row[2]} for row in org_result]
    
    # Check for organization filter
    organization_id = request.args.get('organization_id', type=int)
    
    # Fetch clients with basic information
    query = """
    SELECT 
        c.id, 
        c.name, 
        c.company_name, 
        c.email, 
        c.phone, 
        COUNT(DISTINCT i.id) as invoice_count,
        COALESCE(SUM(CASE WHEN i.status = 'unpaid' OR i.status = 'overdue' THEN i.total_amount ELSE 0 END), 0) as outstanding_amount,
        o.name as organization_name
    FROM 
        client c
    LEFT JOIN 
        invoice i ON c.id = i.client_id
    LEFT JOIN
        organization o ON c.organization_id = o.id
    WHERE 
        c.user_id = :user_id
    """
    
    params = {'user_id': current_user.id}
    
    if organization_id:
        query += " AND c.organization_id = :organization_id"
        params['organization_id'] = organization_id
    
    query += """
    GROUP BY 
        c.id, c.name, c.company_name, c.email, c.phone, o.name
    ORDER BY 
        c.name
    """
    
    result = db.session.execute(text(query), params)
    
    clients = []
    for row in result:
        clients.append({
            'id': row[0],
            'name': row[1],
            'company_name': row[2] or '',
            'email': row[3] or '',
            'phone': row[4] or '',
            'invoice_count': row[5],
            'outstanding_amount': float(row[6]),
            'organization_name': row[7] or 'No Organization'
        })
    
    return render_template('clients.html', 
                          clients=clients, 
                          organizations=organizations,
                          selected_organization=organization_id)

@clients_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_client():
    """Create a new client."""
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            company_name = request.form.get('company_name', '')
            contact_person = request.form.get('contact_person', '')
            email = request.form.get('email', '')
            phone = request.form.get('phone', '')
            address = request.form.get('address', '')
            tax_registration_number = request.form.get('tax_registration_number', '')
            notes = request.form.get('notes', '')
            organization_id = request.form.get('organization_id')
            
            # Validate organization if provided
            if organization_id:
                organization_id = int(organization_id)
                # Verify this organization belongs to the user
                result = db.session.execute(text("""
                    SELECT 1 FROM organization_user 
                    WHERE user_id = :user_id AND organization_id = :org_id
                """), {'user_id': current_user.id, 'org_id': organization_id}).first()
                
                if not result:
                    flash('Invalid organization selected', 'danger')
                    return redirect(url_for('clients.create_client'))
            
            # Create the client
            new_client = Client(
                user_id=current_user.id,
                organization_id=organization_id,
                name=name,
                company_name=company_name,
                contact_person=contact_person,
                email=email,
                phone=phone,
                address=address,
                tax_registration_number=tax_registration_number,
                notes=notes
            )
            
            db.session.add(new_client)
            db.session.commit()
            
            flash('Client created successfully!', 'success')
            return redirect(url_for('clients.view_client', client_id=new_client.id))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating client: {str(e)}")
            flash(f'Error creating client: {str(e)}', 'danger')
    
    # Get user's organizations for the dropdown
    organizations = db.session.execute(text("""
        SELECT o.id, o.name, ou.is_default 
        FROM organization o
        JOIN organization_user ou ON o.id = ou.organization_id
        WHERE ou.user_id = :user_id
        ORDER BY ou.is_default DESC, o.name
    """), {'user_id': current_user.id}).fetchall()
    
    return render_template('create_client.html', organizations=organizations)

@clients_bp.route('/<int:client_id>')
@login_required
def view_client(client_id):
    """View client details with organization information and invoices."""
    try:
        # Get client data with full details
        query = """
        SELECT 
            c.id, 
            c.name, 
            c.company_name,
            c.contact_person,
            c.email, 
            c.phone,
            c.address,
            c.tax_registration_number,
            c.notes,
            c.created_at,
            c.organization_id
        FROM 
            client c
        WHERE 
            c.id = :client_id AND c.user_id = :user_id
        """
        client_result = db.session.execute(text(query), {
            'client_id': client_id, 
            'user_id': current_user.id
        }).first()
        
        if not client_result:
            flash('Client not found', 'danger')
            return redirect(url_for('clients.list_all_clients'))
        
        # Convert SQL result to a more template-friendly format
        client = {
            'id': client_result[0],
            'name': client_result[1],
            'company_name': client_result[2],
            'contact_person': client_result[3],
            'email': client_result[4],
            'phone': client_result[5],
            'address': client_result[6],
            'tax_registration_number': client_result[7],
            'notes': client_result[8],
            'created_at': client_result[9],
            'organization_id': client_result[10]
        }
        
        # Get organization name if the client belongs to an organization
        organization_name = None
        if client['organization_id']:
            org_query = "SELECT name FROM organization WHERE id = :org_id"
            org_result = db.session.execute(text(org_query), {'org_id': client['organization_id']}).first()
            if org_result:
                organization_name = org_result[0]
        
        # Try to get invoices for this client, if any exist
        invoices = []
        try:
            invoice_query = """
            SELECT 
                id, 
                invoice_number, 
                date, 
                due_date, 
                total_amount, 
                status
            FROM 
                invoice
            WHERE 
                client_id = :client_id
            ORDER BY 
                date DESC
            """
            invoice_results = db.session.execute(text(invoice_query), {'client_id': client_id}).fetchall()
            
            # Convert SQL results to a list of dictionaries
            invoices = [{
                'id': row[0],
                'invoice_number': row[1],
                'date': row[2],
                'due_date': row[3],
                'total_amount': float(row[4]),
                'status': row[5]
            } for row in invoice_results]
        except Exception as e:
            logger.error(f"Error fetching invoices for client {client_id}: {str(e)}")
            # Continue without invoices if there's an error
        
        # Check if the view_invoice route exists
        has_invoices_route = True
        
        return render_template('client_view.html', 
                              client=client, 
                              organization_name=organization_name,
                              invoices=invoices,
                              has_invoices_route=has_invoices_route)
    except Exception as e:
        logger.error(f"Error viewing client: {str(e)}")
        flash(f'Error viewing client: {str(e)}', 'danger')
        return redirect(url_for('clients.list_all_clients'))

@clients_bp.route('/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_client(client_id):
    """Edit a client."""
    # Get the client
    client = db.session.execute(text("""
        SELECT * FROM client WHERE id = :client_id AND user_id = :user_id
    """), {'client_id': client_id, 'user_id': current_user.id}).first()
    
    if not client:
        flash('Client not found', 'danger')
        return redirect(url_for('clients.list_all_clients'))
    
    if request.method == 'POST':
        try:
            # Get updated values
            name = request.form.get('name')
            company_name = request.form.get('company_name', '')
            contact_person = request.form.get('contact_person', '')
            email = request.form.get('email', '')
            phone = request.form.get('phone', '')
            address = request.form.get('address', '')
            tax_registration_number = request.form.get('tax_registration_number', '')
            notes = request.form.get('notes', '')
            
            # Check if organization_id was provided in the form
            organization_id = None
            form_org_id = request.form.get('organization_id')
            
            if form_org_id:
                # Verify this organization belongs to the user
                result = db.session.execute(text("""
                    SELECT 1 FROM organization_user 
                    WHERE user_id = :user_id AND organization_id = :organization_id
                """), {'user_id': current_user.id, 'organization_id': form_org_id}).first()
                
                if not result:
                    # Organization doesn't belong to user
                    flash('Invalid organization selected', 'danger')
                    return redirect(url_for('clients.edit_client', client_id=client_id))
                
                organization_id = int(form_org_id)
            
            # Update the client
            update_sql = """
            UPDATE client
            SET name = :name,
                company_name = :company_name,
                contact_person = :contact_person,
                email = :email,
                phone = :phone,
                address = :address,
                tax_registration_number = :tax_registration_number,
                notes = :notes
            """
            
            # Only add organization_id to the update if it was provided
            if organization_id:
                update_sql += ", organization_id = :organization_id"
                
            update_sql += """
            WHERE id = :client_id AND user_id = :user_id
            """
            
            update_params = {
                'client_id': client_id,
                'user_id': current_user.id,
                'name': name,
                'company_name': company_name,
                'contact_person': contact_person,
                'email': email,
                'phone': phone,
                'address': address,
                'tax_registration_number': tax_registration_number,
                'notes': notes
            }
            
            # Add organization_id to params if it was provided
            if organization_id:
                update_params['organization_id'] = organization_id
                
            db.session.execute(text(update_sql), update_params)
            db.session.commit()
            
            flash('Client updated successfully!', 'success')
            return redirect(url_for('clients.view_client', client_id=client_id))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating client: {str(e)}")
            flash(f'Error updating client: {str(e)}', 'danger')
    
    # Get user's organizations for the dropdown
    organizations = db.session.execute(text("""
        SELECT o.id, o.name, ou.is_default 
        FROM organization o
        JOIN organization_user ou ON o.id = ou.organization_id
        WHERE ou.user_id = :user_id
        ORDER BY ou.is_default DESC, o.name
    """), {'user_id': current_user.id}).fetchall()
    
    return render_template('edit_client.html', client=client, organizations=organizations)

@clients_bp.route('/<int:client_id>/delete', methods=['POST'])
@login_required
def delete_client(client_id):
    """Delete a client."""
    try:
        # Check if client exists and belongs to the user
        client = db.session.execute(text("""
            SELECT id FROM client WHERE id = :client_id AND user_id = :user_id
        """), {'client_id': client_id, 'user_id': current_user.id}).first()
        
        if not client:
            flash('Client not found', 'danger')
            return redirect(url_for('clients.list_all_clients'))
        
        # Check if client has invoices
        invoice_count = db.session.execute(text("""
            SELECT COUNT(*) FROM invoice WHERE client_id = :client_id
        """), {'client_id': client_id}).scalar()
        
        if invoice_count > 0:
            flash('Cannot delete client with existing invoices. Please delete the invoices first.', 'danger')
            return redirect(url_for('clients.view_client', client_id=client_id))
        
        # Delete the client
        db.session.execute(text("""
            DELETE FROM client WHERE id = :client_id AND user_id = :user_id
        """), {'client_id': client_id, 'user_id': current_user.id})
        
        db.session.commit()
        
        flash('Client deleted successfully!', 'success')
        return redirect(url_for('clients.list_all_clients'))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting client: {str(e)}")
        flash(f'Error deleting client: {str(e)}', 'danger')
        return redirect(url_for('clients.view_client', client_id=client_id))
        
@clients_bp.route('/list')
@login_required
def list_clients():
    """API endpoint to get clients for the current user, optionally filtered by organization."""
    organization_id = request.args.get('organization_id')
    
    query = """
    SELECT 
        c.id, 
        c.name, 
        COALESCE(c.company_name, '') as company_name
    FROM 
        client c
    WHERE 
        c.user_id = :user_id
    """
    
    params = {'user_id': current_user.id}
    
    if organization_id:
        query += " AND c.organization_id = :organization_id"
        params['organization_id'] = organization_id
    
    query += " ORDER BY c.name"
    
    result = db.session.execute(text(query), params)
    
    clients = [{'id': row[0], 'name': row[1], 'company_name': row[2]} for row in result]
    
    return jsonify(clients)