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

@clients_bp.route('/basic_client_debug/<int:client_id>')
def basic_client_debug(client_id):
    """Super basic debug view with minimal dependencies."""
    import traceback
    
    try:
        # Check if client exists using minimal query
        client_query = text("SELECT id, name FROM client WHERE id = :client_id LIMIT 1")
        result = db.session.execute(client_query, {'client_id': client_id}).first()
        
        if not result:
            return f"Client with ID {client_id} not found", 404
        
        # Build a very simple HTML response
        output = f"""
        <html>
        <head><title>Client Debug: {result[1]}</title></head>
        <body>
            <h1>Basic Client Debug</h1>
            <p>Client ID: {result[0]}</p>
            <p>Client Name: {result[1]}</p>
            
            <h2>Database Query Tests</h2>
        """
        
        # Test invoice count (without trying to process them)
        invoice_count_query = text("SELECT COUNT(*) FROM invoice WHERE client_id = :client_id")
        invoice_count = db.session.execute(invoice_count_query, {'client_id': client_id}).scalar() or 0
        
        output += f"<p>Invoice Count: {invoice_count}</p>"
        
        # Test if currency filter is working correctly
        try:
            from template_filters import currency_filter
            test_amount = 1250.75
            formatted = currency_filter(test_amount)
            output += f"<p>Currency filter test: {test_amount} → {formatted}</p>"
        except Exception as e:
            output += f"<p style='color:red'>Currency filter error: {str(e)}</p>"
        
        # Test client model initialization with minimal fields
        try:
            client = Client(id=result[0], name=result[1])
            output += f"<p>Client model test: successfully created client object with name {client.name}</p>"
        except Exception as e:
            output += f"<p style='color:red'>Client model error: {str(e)}</p>"
        
        output += """
            <h2>Next Troubleshooting Steps</h2>
            <p>Based on the tests above, try the following debugging paths:</p>
            <ul>
                <li>Check if invoice model is correctly defined (required fields, etc.)</li>
                <li>Check if there are any rows with NULL values in required columns</li>
                <li>Verify that template filters are correctly registered</li>
                <li>Test the simple_view_client route with DEBUG logging</li>
            </ul>
        </body>
        </html>
        """
        
        return output
    except Exception as e:
        error_detail = f"Error: {str(e)}<br><pre>{traceback.format_exc()}</pre>"
        return error_detail, 500

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
    """Ultra-simplified view for a client - no dependencies on invoices."""
    import traceback
    from datetime import datetime
    
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
        
        # Create a simple client class just for template rendering
        class SimpleClient:
            def __init__(self, data):
                self.id = data[0]
                self.user_id = data[1]
                self.organization_id = data[2]
                self.name = data[3] or "Unnamed Client"
                self.company_name = data[4] or ''
                self.contact_person = data[5] or ''
                self.email = data[6] or ''
                self.phone = data[7] or ''
                self.address = data[8] or ''
                self.tax_registration_number = data[9] or ''
                self.notes = data[10] or ''
                
                # Handle date formatting properly
                created_at = data[11]
                if isinstance(created_at, str):
                    try:
                        self.created_at = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                    except:
                        self.created_at = datetime.now()
                else:
                    self.created_at = created_at or datetime.now()
                
                updated_at = data[12]
                if isinstance(updated_at, str):
                    try:
                        self.updated_at = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S')
                    except:
                        self.updated_at = datetime.now()
                else:
                    self.updated_at = updated_at or datetime.now()
                
                # We'll handle date formatting directly in the string template
        
        # Create a client object with our safe wrapper class
        client = SimpleClient(result)
        
        # Use our ultra-simple template with no invoice dependencies
        return render_template('client_details_simple.html', client=client)
        
    except Exception as e:
        logger.error(f"Ultra-simple client view error: {str(e)}")
        logger.error(traceback.format_exc())
        flash(f"An error occurred viewing client details: {str(e)}", "danger")
        return redirect(url_for('clients.clients'))

@clients_bp.route('/minimal/<int:client_id>')
@login_required
def view_client_minimal(client_id):
    """Minimalistic client view with absolute bare essentials."""
    import traceback
    try:
        # Direct database query for this client only
        query = """
        SELECT id, name, company_name, email, phone
        FROM client 
        WHERE id = :client_id AND user_id = :user_id
        """
        client = db.session.execute(text(query), {
            'client_id': client_id, 
            'user_id': current_user.id
        }).first()
        
        if not client:
            return "Client not found", 404
            
        # Return absolute bare-minimum HTML
        html = f"""
        <html>
        <head>
            <title>Client: {client[1]}</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                h1 {{ color: #333; }}
                .info {{ margin: 20px 0; }}
                .info div {{ margin: 5px 0; }}
                .label {{ font-weight: bold; display: inline-block; width: 120px; }}
                .nav {{ margin-top: 20px; }}
                .nav a {{ margin-right: 10px; }}
            </style>
        </head>
        <body>
            <h1>Client Details</h1>
            
            <div class="info">
                <div><span class="label">ID:</span> {client[0]}</div>
                <div><span class="label">Name:</span> {client[1]}</div>
                <div><span class="label">Company:</span> {client[2] or '(None)'}</div>
                <div><span class="label">Email:</span> {client[3] or '(None)'}</div>
                <div><span class="label">Phone:</span> {client[4] or '(None)'}</div>
            </div>
            
            <div class="nav">
                <a href="/clients">Back to Clients</a>
                <a href="/clients/{client_id}/edit">Edit Client</a>
            </div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        error = f"Error: {str(e)}\n\n{traceback.format_exc()}"
        logger.error(error)
        return f"<pre>{error}</pre>", 500

@clients_bp.route('/<int:client_id>')
@login_required
def view_client(client_id):
    """Enhanced client view with invoice summary/analytics."""
    import traceback
    
    try:
        # Direct database query for this client only
        query = """
        SELECT id, name, company_name, email, phone, contact_person, address, tax_registration_number, 
               notes, created_at, updated_at, organization_id
        FROM client 
        WHERE id = :client_id AND user_id = :user_id
        """
        client = db.session.execute(text(query), {
            'client_id': client_id, 
            'user_id': current_user.id
        }).first()
        
        if not client:
            flash('Client not found', 'danger')
            return redirect(url_for('clients.clients'))
        
        # Get invoice summary data for this client
        invoice_summary_query = """
        SELECT 
            COUNT(id) as total_invoices,
            SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) as paid_invoices,
            SUM(CASE WHEN status = 'unpaid' THEN 1 ELSE 0 END) as unpaid_invoices,
            SUM(CASE WHEN status = 'overdue' THEN 1 ELSE 0 END) as overdue_invoices,
            SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) as draft_invoices,
            SUM(total_amount) as total_amount,
            SUM(CASE WHEN status = 'paid' THEN total_amount ELSE 0 END) as paid_amount,
            SUM(CASE WHEN status = 'unpaid' THEN total_amount ELSE 0 END) as unpaid_amount,
            SUM(CASE WHEN status = 'overdue' THEN total_amount ELSE 0 END) as overdue_amount,
            MAX(created_at) as last_invoice_date
        FROM invoice
        WHERE client_id = :client_id
        """
        invoice_summary = db.session.execute(text(invoice_summary_query), {
            'client_id': client_id
        }).first()
        
        # Get recent invoices for this client
        recent_invoices_query = """
        SELECT id, invoice_number, issue_date, due_date, total_amount, status
        FROM invoice
        WHERE client_id = :client_id
        ORDER BY created_at DESC
        LIMIT 5
        """
        recent_invoices = db.session.execute(text(recent_invoices_query), {
            'client_id': client_id
        }).fetchall()
        
        # Build direct HTML response with client details and invoice summary
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Client: {client[1]}</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.1.1/css/all.min.css">
            <style>
                .client-header {{
                    background-color: #f8f9fa;
                    border-bottom: 1px solid #dee2e6;
                    padding: 1.5rem 0;
                    margin-bottom: 2rem;
                }}
                .summary-card {{
                    border-radius: 0.5rem;
                    box-shadow: 0 0.125rem 0.25rem rgba(0, 0, 0, 0.075);
                    margin-bottom: 1.5rem;
                }}
                .summary-card .card-header {{
                    background-color: #f8f9fa;
                    border-bottom: 1px solid #dee2e6;
                    font-weight: 600;
                }}
                .stat-item {{
                    padding: 1rem;
                    border-bottom: 1px solid #f0f0f0;
                }}
                .stat-item:last-child {{
                    border-bottom: none;
                }}
                .stat-label {{
                    color: #6c757d;
                    font-size: 0.875rem;
                }}
                .stat-value {{
                    font-size: 1.25rem;
                    font-weight: 600;
                }}
                .nav-pills .nav-link.active {{
                    background-color: #0d6efd;
                }}
                .tab-content {{
                    padding: 1.5rem 0;
                }}
                .info-table td {{
                    padding: 0.5rem;
                }}
                .info-table td:first-child {{
                    font-weight: 600;
                    width: 30%;
                }}
            </style>
        </head>
        <body>
            <nav class="navbar navbar-expand-lg navbar-light bg-light">
                <div class="container">
                    <a class="navbar-brand" href="/">
                        <i class="fas fa-receipt me-2"></i>
                        Receipt Scanner
                    </a>
                    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                        <span class="navbar-toggler-icon"></span>
                    </button>
                    <div class="collapse navbar-collapse" id="navbarNav">
                        <ul class="navbar-nav ms-auto">
                            <li class="nav-item">
                                <a class="nav-link" href="/clients">
                                    <i class="fas fa-users me-1"></i> Clients
                                </a>
                            </li>
                            <li class="nav-item">
                                <a class="nav-link" href="/invoices">
                                    <i class="fas fa-file-invoice me-1"></i> Invoices
                                </a>
                            </li>
                        </ul>
                    </div>
                </div>
            </nav>

            <div class="client-header">
                <div class="container">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <h1 class="mb-0">{client[1]}</h1>
                            <p class="text-muted mb-0">{client[2] or ''}</p>
                        </div>
                        <div>
                            <a href="/clients/{client_id}/edit" class="btn btn-primary me-2">
                                <i class="fas fa-edit me-1"></i> Edit
                            </a>
                            <button type="button" class="btn btn-danger" 
                                onclick="if(confirm('Are you sure you want to delete this client?')) {{
                                    const form = document.createElement('form');
                                    form.method = 'POST';
                                    form.action = '/clients/{client_id}/delete';
                                    document.body.appendChild(form);
                                    form.submit();
                                }}">
                                <i class="fas fa-trash me-1"></i> Delete
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <div class="container mb-5">
                <div class="row">
                    <div class="col-lg-4 order-lg-2">
                        <!-- Invoice Summary Card -->
                        <div class="card summary-card">
                            <div class="card-header d-flex justify-content-between align-items-center">
                                <span>Invoice Summary</span>
                                <a href="/invoices/create?client_id={client_id}" class="btn btn-sm btn-primary">
                                    <i class="fas fa-plus me-1"></i> New Invoice
                                </a>
                            </div>
                            <div class="card-body p-0">
                                <div class="stat-item">
                                    <div class="stat-label">Total Invoices</div>
                                    <div class="stat-value">{invoice_summary[0] or 0}</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-label">Paid</div>
                                    <div class="stat-value text-success">{invoice_summary[1] or 0}</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-label">Unpaid</div>
                                    <div class="stat-value text-warning">{invoice_summary[2] or 0}</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-label">Overdue</div>
                                    <div class="stat-value text-danger">{invoice_summary[3] or 0}</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-label">Draft</div>
                                    <div class="stat-value text-secondary">{invoice_summary[4] or 0}</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-label">Total Amount</div>
                                    <div class="stat-value">LKR {invoice_summary[5] or 0:,.2f}</div>
                                </div>
                                <div class="stat-item">
                                    <div class="stat-label">Outstanding Amount</div>
                                    <div class="stat-value text-danger">LKR {(invoice_summary[7] or 0) + (invoice_summary[8] or 0):,.2f}</div>
                                </div>
                            </div>
                            <div class="card-footer">
                                <a href="/invoices?client_id={client_id}" class="btn btn-sm btn-outline-primary d-block">
                                    View All Invoices
                                </a>
                            </div>
                        </div>

                        <!-- Recent Invoices -->
                        <div class="card summary-card">
                            <div class="card-header">Recent Invoices</div>
                            <div class="card-body p-0">
                                {
                                "".join(f'''
                                <div class="p-3 border-bottom">
                                    <div class="d-flex justify-content-between align-items-center mb-2">
                                        <div>
                                            <span class="badge {'bg-success' if inv[5] == 'paid' else 'bg-warning' if inv[5] == 'unpaid' else 'bg-danger' if inv[5] == 'overdue' else 'bg-secondary'}">{inv[5]}</span>
                                            <span class="ms-2">{inv[1]}</span>
                                        </div>
                                        <div class="text-nowrap">LKR {inv[4]:,.2f}</div>
                                    </div>
                                    <div class="d-flex justify-content-between text-muted small">
                                        <div>Issue: {inv[2].strftime('%Y-%m-%d') if inv[2] else 'N/A'}</div>
                                        <div>Due: {inv[3].strftime('%Y-%m-%d') if inv[3] else 'N/A'}</div>
                                    </div>
                                    <div class="mt-2">
                                        <a href="/invoices/{inv[0]}" class="btn btn-sm btn-outline-secondary">View</a>
                                    </div>
                                </div>
                                ''' for inv in recent_invoices) if recent_invoices else '''
                                <div class="p-3 text-center text-muted">
                                    No invoices found for this client
                                </div>
                                '''
                                }
                            </div>
                        </div>
                    </div>

                    <div class="col-lg-8 order-lg-1">
                        <ul class="nav nav-pills mb-3" id="clientTabsNav" role="tablist">
                            <li class="nav-item" role="presentation">
                                <button class="nav-link active" id="details-tab" data-bs-toggle="tab" data-bs-target="#details" type="button">
                                    <i class="fas fa-info-circle me-1"></i> Details
                                </button>
                            </li>
                            <li class="nav-item" role="presentation">
                                <button class="nav-link" id="invoices-tab" data-bs-toggle="tab" data-bs-target="#invoices" type="button">
                                    <i class="fas fa-file-invoice me-1"></i> Invoices
                                </button>
                            </li>
                            <li class="nav-item" role="presentation">
                                <button class="nav-link" id="expenses-tab" data-bs-toggle="tab" data-bs-target="#expenses" type="button">
                                    <i class="fas fa-receipt me-1"></i> Expenses
                                </button>
                            </li>
                        </ul>

                        <div class="tab-content" id="clientTabsContent">
                            <div class="tab-pane fade show active" id="details" role="tabpanel" aria-labelledby="details-tab">
                                <div class="card">
                                    <div class="card-header">Client Information</div>
                                    <div class="card-body">
                                        <table class="table info-table table-borderless">
                                            <tr>
                                                <td>Name</td>
                                                <td>{client[1] or 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Company</td>
                                                <td>{client[2] or 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Contact Person</td>
                                                <td>{client[5] or 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Email</td>
                                                <td>{f'<a href="mailto:{client[3]}">{client[3]}</a>' if client[3] else 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Phone</td>
                                                <td>{client[4] or 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Address</td>
                                                <td>{client[6] or 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Tax Registration Number</td>
                                                <td>{client[7] or 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Notes</td>
                                                <td>{client[8] or 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Created</td>
                                                <td>{client[9].strftime('%Y-%m-%d') if client[9] else 'N/A'}</td>
                                            </tr>
                                            <tr>
                                                <td>Last Updated</td>
                                                <td>{client[10].strftime('%Y-%m-%d') if client[10] else 'N/A'}</td>
                                            </tr>
                                        </table>
                                    </div>
                                </div>
                            </div>

                            <div class="tab-pane fade" id="invoices" role="tabpanel" aria-labelledby="invoices-tab">
                                <div class="card">
                                    <div class="card-header d-flex justify-content-between align-items-center">
                                        <span>Invoices</span>
                                        <a href="/invoices/create?client_id={client_id}" class="btn btn-sm btn-primary">
                                            <i class="fas fa-plus me-1"></i> New Invoice
                                        </a>
                                    </div>
                                    <div class="card-body">
                                        {
                                        '''
                                        <div class="table-responsive">
                                            <table class="table table-hover">
                                                <thead>
                                                    <tr>
                                                        <th>Invoice #</th>
                                                        <th>Date</th>
                                                        <th>Due Date</th>
                                                        <th>Amount</th>
                                                        <th>Status</th>
                                                        <th></th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                        ''' + "".join(f'''
                                                    <tr>
                                                        <td>{inv[1]}</td>
                                                        <td>{inv[2].strftime('%Y-%m-%d') if inv[2] else 'N/A'}</td>
                                                        <td>{inv[3].strftime('%Y-%m-%d') if inv[3] else 'N/A'}</td>
                                                        <td class="text-end">LKR {inv[4]:,.2f}</td>
                                                        <td><span class="badge {'bg-success' if inv[5] == 'paid' else 'bg-warning' if inv[5] == 'unpaid' else 'bg-danger' if inv[5] == 'overdue' else 'bg-secondary'}">{inv[5]}</span></td>
                                                        <td class="text-end">
                                                            <a href="/invoices/{inv[0]}" class="btn btn-sm btn-outline-primary">View</a>
                                                        </td>
                                                    </tr>
                                        ''' for inv in recent_invoices) + '''
                                                </tbody>
                                            </table>
                                        </div>
                                        ''' if recent_invoices else '''
                                        <div class="text-center p-4">
                                            <div class="mb-3">
                                                <i class="fas fa-file-invoice fa-3x text-muted"></i>
                                            </div>
                                            <h5>No Invoices Found</h5>
                                            <p class="text-muted">This client doesn't have any invoices yet.</p>
                                            <a href="/invoices/create?client_id={client_id}" class="btn btn-primary mt-2">
                                                <i class="fas fa-plus me-1"></i> Create First Invoice
                                            </a>
                                        </div>
                                        '''
                                        }
                                    </div>
                                </div>
                            </div>

                            <div class="tab-pane fade" id="expenses" role="tabpanel" aria-labelledby="expenses-tab">
                                <div class="card">
                                    <div class="card-header">Client Expenses</div>
                                    <div class="card-body text-center p-4">
                                        <div class="mb-3">
                                            <i class="fas fa-receipt fa-3x text-muted"></i>
                                        </div>
                                        <h5>Expense Tracking</h5>
                                        <p class="text-muted">Track expenses related to this client.</p>
                                        <a href="/receipts/classify?client_id={client_id}" class="btn btn-primary mt-2">
                                            <i class="fas fa-plus me-1"></i> Add Client Expense
                                        </a>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <footer class="bg-light py-4 mt-5">
                <div class="container">
                    <div class="text-center text-muted">
                        &copy; 2025 Receipt Scanner. All rights reserved.
                    </div>
                </div>
            </footer>

            <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
            <script>
                // Simple tab functionality
                document.addEventListener('DOMContentLoaded', function() {
                    const tabButtons = document.querySelectorAll('[data-bs-toggle="tab"]');
                    tabButtons.forEach(button => {
                        button.addEventListener('click', function() {
                            const targetId = this.getAttribute('data-bs-target');
                            
                            // Hide all tab panes
                            document.querySelectorAll('.tab-pane').forEach(pane => {
                                pane.classList.remove('show', 'active');
                            });
                            
                            // Remove active class from all tab buttons
                            document.querySelectorAll('.nav-link').forEach(link => {
                                link.classList.remove('active');
                            });
                            
                            // Show the target tab pane
                            document.querySelector(targetId).classList.add('show', 'active');
                            
                            // Mark this button as active
                            this.classList.add('active');
                        });
                    });
                });
            </script>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"Error in view_client: {str(e)}")
        logger.error(traceback.format_exc())
        # Fallback to super basic view
        return f"""
        <html>
        <head>
            <title>Error: Client View</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
        </head>
        <body class="container mt-5">
            <div class="alert alert-danger">
                <h4>Error Viewing Client</h4>
                <p>{str(e)}</p>
                <pre>{traceback.format_exc()}</pre>
            </div>
            <a href="/clients" class="btn btn-primary">Return to Client List</a>
        </body>
        </html>
        """, 500

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