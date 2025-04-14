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

@clients_bp.route('/')
@login_required
def clients():
    """Render the clients page showing list of user's clients."""
    # Get user's organizations
    org_result = db.session.execute(text("""
        SELECT ou.organization_id, o.name, ou.is_default
        FROM organization_user ou
        JOIN organization o ON ou.organization_id = o.id
        WHERE ou.user_id = :user_id
        ORDER BY ou.is_default DESC, o.name
    """), {'user_id': current_user.id})
    
    organizations = []
    for org_row in org_result:
        org = {
            'id': org_row[0],
            'name': org_row[1],
            'is_default': org_row[2]
        }
        organizations.append(org)
    
    # Get the selected organization from query params or use default
    selected_org_id = request.args.get('organization_id')
    selected_org = None
    
    if selected_org_id:
        # Find the selected organization
        for org in organizations:
            if str(org['id']) == selected_org_id:
                selected_org = org
                break
    
    # Check if we're explicitly requesting "All" organizations
    show_all_organizations = request.args.get('show_all') == 'true' or not selected_org_id
    
    # If no org is selected and we're not explicitly showing all organizations, use the default org
    if not selected_org and not show_all_organizations and organizations and not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        for org in organizations:
            if org['is_default']:
                selected_org = org
                break
        
        # If no default, just use the first one
        if not selected_org and organizations:
            selected_org = organizations[0]
    
    logger.debug(f"Listing clients for user_id: {current_user.id}")
    if selected_org:
        logger.debug(f"Filtered by organization_id: {selected_org['id']}")
    else:
        logger.debug("Showing clients for all organizations")
    
    # First check if there are any clients at all for debugging
    all_clients_count = db.session.execute(text("""
        SELECT COUNT(*) FROM client
    """)).first()[0]
    
    # Get clients for the current user
    query = """
        SELECT c.id, c.user_id, c.organization_id, c.name, c.company_name, c.contact_person, 
               c.email, c.phone, c.address, c.tax_registration_number, c.notes, c.created_at, 
               c.updated_at, o.name as organization_name
        FROM client c
        LEFT JOIN organization o ON c.organization_id = o.id
        WHERE c.user_id = :user_id
    """
    
    params = {'user_id': current_user.id}
    
    # Filter by organization if one is selected
    if selected_org:
        query += " AND c.organization_id = :organization_id"
        params['organization_id'] = selected_org['id']
    
    query += " ORDER BY c.name"
    
    result = db.session.execute(text(query), params)
    
    # Convert raw SQL results to Client objects
    clients = []
    row_count = 0
    for row in result:
        row_count += 1
        client = Client(
            id=row[0],
            user_id=row[1],
            organization_id=row[2],
            name=row[3],
            company_name=row[4],
            contact_person=row[5],
            email=row[6],
            phone=row[7],
            address=row[8],
            tax_registration_number=row[9],
            notes=row[10],
            created_at=row[11],
            updated_at=row[12]
        )
        
        # Add the organization name as an attribute
        client.organization_name = row[13]
        
        clients.append(client)
    
    logger.debug(f"Found {row_count} clients for user")
    
    return render_template('clients.html', 
                          title='Clients',
                          clients=clients,
                          organizations=organizations,
                          selected_org=selected_org)

@clients_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_client():
    """Create a new client."""
    logger.debug("Starting client creation process")
    if request.method == 'POST':
        try:
            # Get form data
            name = request.form.get('name')
            # Get the new fields that we added to the form
            company_name = request.form.get('company_name', '')
            contact_person = request.form.get('contact_person', '')
            email = request.form.get('email', '')
            phone = request.form.get('phone', '')
            address = request.form.get('address', '')
            tax_registration_number = request.form.get('tax_registration_number', '')
            notes = request.form.get('notes', '')
            
            logger.debug(f"Create client form data - Name: {name}, Company: {company_name}, Contact: {contact_person}")
            
            # Check if organization_id was provided in the form
            form_org_id = request.form.get('organization_id')
            
            if form_org_id:
                # Verify this organization belongs to the user
                result = db.session.execute(text("""
                    SELECT organization_id FROM organization_user
                    WHERE user_id = :user_id AND organization_id = :organization_id
                """), {'user_id': current_user.id, 'organization_id': form_org_id}).first()
                
                if not result:
                    # Organization doesn't belong to user
                    flash('Invalid organization selected', 'danger')
                    return redirect(url_for('clients.clients'))
                
                organization_id = int(form_org_id)
            else:
                # Get the user's default organization
                default_org = db.session.execute(text("""
                    SELECT organization_id FROM organization_user
                    WHERE user_id = :user_id AND is_default = TRUE
                    LIMIT 1
                """), {'user_id': current_user.id}).first()
                
                if default_org:
                    organization_id = default_org[0]
                else:
                    # If no default, get the first organization
                    first_org = db.session.execute(text("""
                        SELECT organization_id FROM organization_user
                        WHERE user_id = :user_id
                        LIMIT 1
                    """), {'user_id': current_user.id}).first()
                    
                    if first_org:
                        organization_id = first_org[0]
                    else:
                        # User has no organizations, which shouldn't happen
                        organization_id = None
            
            # Create the client
            insert_sql = """
                INSERT INTO client (user_id, organization_id, name, company_name, contact_person, email, phone, address, tax_registration_number, notes, created_at, updated_at)
                VALUES (:user_id, :organization_id, :name, :company_name, :contact_person, :email, :phone, :address, :tax_registration_number, :notes, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            """
            
            result = db.session.execute(text(insert_sql), {
                'user_id': current_user.id,
                'organization_id': organization_id,
                'name': name,
                'company_name': company_name,
                'contact_person': contact_person,
                'email': email,
                'phone': phone,
                'address': address,
                'tax_registration_number': tax_registration_number,
                'notes': notes
            })
            
            db.session.commit()
            client_id = result.first()[0]
            
            flash('Client created successfully!', 'success')
            return redirect(url_for('clients.view_client', client_id=client_id))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating client: {str(e)}")
            flash(f'Error creating client: {str(e)}', 'danger')
            
    # Get user's organizations for the dropdown
    organizations_result = db.session.execute(text("""
        SELECT o.id, o.name, o.logo_path, o.primary_color, o.email, ou.is_default
        FROM organization o
        JOIN organization_user ou ON o.id = ou.organization_id
        WHERE ou.user_id = :user_id
        ORDER BY ou.is_default DESC, o.name ASC
    """), {'user_id': current_user.id})
    
    organizations = [
        {
            'id': row[0],
            'name': row[1],
            'logo_path': row[2],
            'primary_color': row[3],
            'email': row[4],
            'is_default': row[5]
        }
        for row in organizations_result
    ]
    
    return render_template('create_client.html', organizations=organizations)

@clients_bp.route('/debug_client/<int:client_id>')
def debug_client(client_id):
    """Debug view for a client (no auth required for testing)."""
    # Get the client using raw SQL
    from app import db
    from sqlalchemy import text
    import traceback
    
    try:
        # Use raw SQL to get a specific client by ID (no user_id filter for debugging)
        result = db.session.execute(text("""
            SELECT id, user_id, organization_id, name, company_name, contact_person, email, 
                phone, address, tax_registration_number, notes, created_at, updated_at
            FROM client
            WHERE id = :client_id
            LIMIT 1
        """), {'client_id': client_id}).first()
        
        if not result:
            return f"Client with ID {client_id} not found", 404
            
        # Get client info
        client_info = {
            'id': result[0],
            'user_id': result[1],
            'organization_id': result[2],
            'name': result[3],
            'company_name': result[4],
            'contact_person': result[5],
            'email': result[6],
            'phone': result[7],
            'address': result[8],
            'tax_registration_number': result[9],
            'notes': result[10],
            'created_at': result[11],
            'updated_at': result[12]
        }
        
        # Get organization info if applicable
        org_info = None
        if result[2]:  # organization_id
            org_result = db.session.execute(text("""
                SELECT id, name, logo_path, logo_s3_key, primary_color, email
                FROM organization
                WHERE id = :organization_id
                LIMIT 1
            """), {'organization_id': result[2]}).first()
            
            if org_result:
                org_info = {
                    'id': org_result[0],
                    'name': org_result[1],
                    'logo_path': org_result[2],
                    'logo_s3_key': org_result[3],
                    'primary_color': org_result[4],
                    'email': org_result[5]
                }
        
        # Get invoice count
        invoice_count = db.session.execute(text("""
            SELECT COUNT(*) FROM invoice
            WHERE client_id = :client_id
        """), {'client_id': client_id}).scalar()
        
        # Build debug output
        output = "<h1>Client Debug Information</h1>"
        output += "<h2>Client Details</h2>"
        output += "<pre>" + str(client_info) + "</pre>"
        output += "<h2>Organization Details</h2>"
        output += "<pre>" + str(org_info) + "</pre>"
        output += f"<h2>Invoice Count</h2><p>{invoice_count}</p>"
        
        return output
    except Exception as e:
        return f"Error: {str(e)}<br><pre>{traceback.format_exc()}</pre>", 500

@clients_bp.route('/client_debug/<int:client_id>')
def client_debug(client_id):
    """Debug view for a client (no auth required for testing)."""
    import traceback
    import json
    
    try:
        output = "<h1>Client Debug Info</h1>"
        
        # Check if client exists
        result = db.session.execute(text("""
            SELECT id, user_id, organization_id, name, company_name, contact_person, email, 
                   phone, address, tax_registration_number, notes, created_at, updated_at
            FROM client
            WHERE id = :client_id
            LIMIT 1
        """), {'client_id': client_id}).first()
        
        if not result:
            return f"<div style='color: red'>Client with ID {client_id} not found</div>"
        
        output += "<h2>Client Data</h2>"
        output += "<table border='1' cellpadding='5'>"
        output += "<tr><th>Field</th><th>Value</th><th>Type</th></tr>"
        
        field_names = ["id", "user_id", "organization_id", "name", "company_name", "contact_person", 
                      "email", "phone", "address", "tax_registration_number", "notes", "created_at", "updated_at"]
        
        for i, field in enumerate(field_names):
            val = result[i]
            val_display = val if val is not None else '<b style="color:red">NULL</b>'
            output += f"<tr><td>{field}</td><td>{val_display}</td><td>{type(val).__name__}</td></tr>"
        
        output += "</table>"
        
        # Get invoices 
        invoice_results = db.session.execute(text("""
            SELECT id, invoice_number, issue_date, due_date, status, total
            FROM invoice
            WHERE client_id = :client_id
            ORDER BY issue_date DESC
        """), {'client_id': client_id}).fetchall()
        
        output += f"<h2>Invoices ({len(invoice_results)})</h2>"
        
        if invoice_results:
            output += "<table border='1' cellpadding='5'>"
            output += "<tr><th>ID</th><th>Number</th><th>Issue Date</th><th>Due Date</th><th>Status</th><th>Total</th></tr>"
            
            for row in invoice_results:
                output += "<tr>"
                for i, val in enumerate(row):
                    style = ""
                    if val is None:
                        style = " style='color:red;font-weight:bold'"
                        val = "NULL"
                    output += f"<td{style}>{val}</td>"
                output += "</tr>"
            
            output += "</table>"
        else:
            output += "<p>No invoices found</p>"
        
        # Check registered routes
        output += "<h2>Template Test</h2>"
        output += "<p>Testing template rendering...</p>"
        
        try:
            # Try to render a simple template
            test_client = Client(
                id=result[0],
                user_id=result[1],
                organization_id=result[2],
                name=result[3],
                company_name=result[4] or '',
                contact_person=result[5] or '',
                email=result[6] or '',
                phone=result[7] or '',
                address=result[8] or '',
                tax_registration_number=result[9] or '',
                notes=result[10] or '',
                created_at=result[11],
                updated_at=result[12]
            )
            
            # Create test invoices with safe data
            test_invoices = []
            if invoice_results:
                for row in invoice_results:
                    invoice = {
                        'id': row[0] or 0,
                        'invoice_number': row[1] or 'No number',
                        'issue_date': row[2] or None,
                        'due_date': row[3] or None,
                        'status': row[4] or 'unknown',
                        'total': row[5] or 0
                    }
                    test_invoices.append(invoice)
            
            # Try rendering just part of the template as test
            from flask import render_template_string
            test_template = """
            <h3>Test Client Information</h3>
            <p>Name: {{ client.name }}</p>
            <p>Email: {{ client.email }}</p>
            
            <h3>Test Invoices</h3>
            <ul>
            {% for invoice in invoices %}
                <li>{{ invoice.invoice_number }} - {{ invoice.status }} - {{ invoice.total }}</li>
            {% else %}
                <li>No invoices found</li>
            {% endfor %}
            </ul>
            """
            
            rendered = render_template_string(test_template, client=test_client, invoices=test_invoices)
            output += rendered
            output += "<p style='color: green'>✓ Template rendering successful</p>"
            
        except Exception as e:
            output += f"<p style='color: red'>Error rendering test template: {str(e)}</p>"
            output += f"<pre>{traceback.format_exc()}</pre>"
        
        # Output all the debug info
        return output
    except Exception as e:
        return f"Error: {str(e)}<br><pre>{traceback.format_exc()}</pre>", 500

@clients_bp.route('/simple_view/<int:client_id>')
@login_required
def simple_view_client(client_id):
    """Simplified view for a client - robust and minimal."""
    try:
        # Get the client using raw SQL - simplest possible implementation
        result = db.session.execute(text("""
            SELECT id, user_id, organization_id, name, company_name, contact_person, email, 
                   phone, address, tax_registration_number, notes, created_at, updated_at
            FROM client
            WHERE id = :client_id AND user_id = :user_id
            LIMIT 1
        """), {'client_id': client_id, 'user_id': current_user.id}).first()
        
        if not result:
            flash("Client not found", "danger")
            return redirect(url_for('clients.clients'))
        
        # Convert row to Client object with defensive coding
        client = Client(
            id=result[0],
            user_id=result[1],
            organization_id=result[2],
            name=result[3] or "Unnamed Client",
            company_name=result[4] or '',
            contact_person=result[5] or '',
            email=result[6] or '',
            phone=result[7] or '',
            address=result[8] or '',
            tax_registration_number=result[9] or '',
            notes=result[10] or '',
            created_at=result[11],
            updated_at=result[12]
        )
        
        # Get invoices with minimal fields needed
        invoice_results = db.session.execute(text("""
            SELECT id, invoice_number, issue_date, due_date, status, total 
            FROM invoice
            WHERE client_id = :client_id AND user_id = :user_id
            ORDER BY issue_date DESC
        """), {'client_id': client_id, 'user_id': current_user.id})
        
        # Convert to simple invoice objects - very defensive approach
        invoices = []
        for row in invoice_results:
            # Create dictionary instead of Invoice object to avoid possible model issues
            invoice = {
                'id': row[0],
                'invoice_number': row[1] or 'No number',
                'issue_date': row[2],
                'due_date': row[3],
                'status': row[4] or 'unknown',
                'total': row[5] or 0
            }
            invoices.append(invoice)
        
        # Render the simple template
        return render_template('client_view_simple.html', 
                               client=client, 
                               invoices=invoices)
    except Exception as e:
        import traceback
        logger.error(f"Error in simple_view_client: {str(e)}")
        logger.error(traceback.format_exc())
        flash(f"An error occurred: {str(e)}", "danger")
        return redirect(url_for('clients.clients'))

@clients_bp.route('/<int:client_id>')
@login_required
def view_client(client_id):
    """View a specific client."""
    # Redirect to the simple client view to avoid issues
    return redirect(url_for('clients.simple_view_client', client_id=client_id))

@clients_bp.route('/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_client(client_id):
    """Edit an existing client."""
    try:
        # Get the client using raw SQL
        result = db.session.execute(text("""
            SELECT id, user_id, organization_id, name, company_name, contact_person, email, 
                   phone, address, tax_registration_number, notes, created_at, updated_at
            FROM client
            WHERE id = :client_id AND user_id = :user_id
            LIMIT 1
        """), {'client_id': client_id, 'user_id': current_user.id}).first()
        
        if not result:
            flash('Client not found', 'danger')
            return redirect(url_for('clients.clients'))
        
        # Convert row to Client object
        client = Client(
            id=result[0],
            user_id=result[1],
            organization_id=result[2],
            name=result[3],
            company_name=result[4],
            contact_person=result[5],
            email=result[6],
            phone=result[7],
            address=result[8],
            tax_registration_number=result[9],
            notes=result[10],
            created_at=result[11],
            updated_at=result[12]
        )
        
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
                        SELECT organization_id FROM organization_user
                        WHERE user_id = :user_id AND organization_id = :organization_id
                    """), {'user_id': current_user.id, 'organization_id': form_org_id}).first()
                    
                    if not result:
                        # Organization doesn't belong to user
                        flash('Invalid organization selected', 'danger')
                        return redirect(url_for('clients.view_client', client_id=client_id))
                    
                    organization_id = int(form_org_id)
                
                # Update the client
                update_sql = """
                    UPDATE client
                    SET name = :name, company_name = :company_name, contact_person = :contact_person,
                        email = :email, phone = :phone, address = :address, 
                        tax_registration_number = :tax_registration_number, notes = :notes
                """
                
                # Only add organization_id to the update if it was provided
                if organization_id:
                    update_sql += ", organization_id = :organization_id"
                    
                update_sql += """
                    , updated_at = CURRENT_TIMESTAMP
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
                
                # Update client object with new values for template rendering
                client.name = name
                client.company_name = company_name
                client.contact_person = contact_person
                client.email = email
                client.phone = phone
                client.address = address
                client.tax_registration_number = tax_registration_number
                client.notes = notes
                client.updated_at = datetime.utcnow()
                
                # Update organization_id in the client object if it was changed
                if organization_id:
                    client.organization_id = organization_id
                
                flash('Client updated successfully!', 'success')
                return redirect(url_for('clients.view_client', client_id=client.id))
                
            except Exception as e:
                db.session.rollback()
                logger.error(f"Error updating client: {str(e)}")
                flash(f'Error updating client: {str(e)}', 'danger')
                return redirect(url_for('clients.view_client', client_id=client.id))
        
        # GET request - render the form
        # Get user's organizations for the dropdown
        organizations_result = db.session.execute(text("""
            SELECT o.id, o.name, o.logo_path, o.primary_color, o.email, ou.is_default
            FROM organization o
            JOIN organization_user ou ON o.id = ou.organization_id
            WHERE ou.user_id = :user_id
            ORDER BY ou.is_default DESC, o.name ASC
        """), {'user_id': current_user.id})
        
        organizations = [
            {
                'id': row[0],
                'name': row[1],
                'logo_path': row[2],
                'primary_color': row[3],
                'email': row[4],
                'is_default': row[5]
            }
            for row in organizations_result
        ]
        
        # Use the fixed edit client template
        return render_template('edit_client_fixed.html', client=client, organizations=organizations)
    except Exception as e:
        logger.error(f"Error in edit_client route: {str(e)}")
        flash(f"An error occurred while editing client: {str(e)}", "danger")
        return redirect(url_for('clients.clients'))

@clients_bp.route('/<int:client_id>/delete', methods=['POST'])
@login_required
def delete_client(client_id):
    """Delete a client."""
    try:
        # Check if client exists and belongs to user
        result = db.session.execute(text("""
            SELECT id FROM client
            WHERE id = :client_id AND user_id = :user_id
            LIMIT 1
        """), {'client_id': client_id, 'user_id': current_user.id}).first()
        
        if not result:
            flash('Client not found', 'danger')
            return redirect(url_for('clients.clients'))
        
        # Check if client has invoices
        invoice_count = db.session.execute(text("""
            SELECT COUNT(*) FROM invoice
            WHERE client_id = :client_id
        """), {'client_id': client_id}).first()[0]
        
        if invoice_count > 0:
            flash('Cannot delete client with associated invoices', 'danger')
            return redirect(url_for('clients.view_client', client_id=client_id))
        
        # Delete the client
        db.session.execute(text("""
            DELETE FROM client
            WHERE id = :client_id AND user_id = :user_id
        """), {'client_id': client_id, 'user_id': current_user.id})
        
        db.session.commit()
        
        flash('Client deleted successfully!', 'success')
        return redirect(url_for('clients.clients'))
        
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
        SELECT c.id, c.name, c.company_name, c.organization_id
        FROM client c
        WHERE c.user_id = :user_id
    """
    
    params = {'user_id': current_user.id}
    
    # Filter by organization if one is provided
    if organization_id:
        query += " AND c.organization_id = :organization_id"
        params['organization_id'] = organization_id
    
    query += " ORDER BY c.name"
    
    result = db.session.execute(text(query), params)
    
    clients = []
    for row in result:
        clients.append({
            'id': row[0],
            'name': row[1],
            'company_name': row[2] if row[2] else '',
            'organization_id': row[3]
        })
    
    return jsonify({
        'success': True,
        'clients': clients
    })

def register_blueprint(app):
    """Register the clients blueprint with the app."""
    app.register_blueprint(clients_bp)
    logger.info("Client management blueprint registered")