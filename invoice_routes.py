from datetime import datetime
import logging
import os
from flask import render_template, request, jsonify, flash, redirect, url_for, abort
from flask_login import current_user, login_required
from flask_mail import Mail, Message
from sqlalchemy import text
from app import app, db
from models import Invoice, Client, InvoiceItem, Payment, InvoiceStatus, PaymentMethod, BankAccount, Organization

# Initialize Flask-Mail with Gmail settings if not already initialized
if not hasattr(app, 'mail'):
    app.config['MAIL_SERVER'] = 'smtp.gmail.com'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = os.environ.get('GMAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('GMAIL_APP_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('GMAIL_USERNAME')
    
    # Log mail configuration
    logging.info(f"Mail configuration: Server={app.config['MAIL_SERVER']}, Port={app.config['MAIL_PORT']}")
    logging.info(f"Mail username configured: {bool(app.config['MAIL_USERNAME'])}")
    logging.info(f"Mail password configured: {bool(app.config['MAIL_PASSWORD'])}")
    
    # Initialize Flask-Mail
    mail = Mail(app)
    app.mail = mail
else:
    mail = app.mail

# ================ Invoice Management Routes ================

@app.route('/invoices')
@login_required
def invoices():
    """Render the invoices page showing list of user's invoices."""
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
    
    # Use raw SQL to query invoices to avoid organization_id column issues
    sql_query = """
        SELECT i.id, i.user_id, i.client_id, i.bank_account_id, i.invoice_number, 
               i.issue_date, i.due_date, i.status, i.notes, i.currency, 
               i.subtotal, i.tax_percent, i.tax_amount, i.discount_percent, 
               i.discount_amount, i.total, i.sender_name, i.sender_company, 
               i.sender_address, i.sender_phone, i.sender_email, 
               i.sender_tax_registration, i.last_sent_at, i.created_at, i.updated_at,
               c.id as client_id, c.name as client_name, c.company_name as client_company_name,
               i.organization_id, o.name as organization_name
        FROM invoice i
        LEFT JOIN client c ON i.client_id = c.id
        LEFT JOIN organization o ON i.organization_id = o.id
        WHERE i.user_id = :user_id
    """
    
    params = {'user_id': current_user.id}
    
    # Filter by organization if one is selected
    if selected_org:
        sql_query += " AND i.organization_id = :organization_id"
        params['organization_id'] = selected_org['id']
    
    # Filter by status if specified
    status_filter = request.args.get('status')
    if status_filter:
        sql_query += " AND i.status = :status"
        params['status'] = status_filter
    
    sql_query += " ORDER BY i.created_at DESC"
    
    result = db.session.execute(text(sql_query), params)
    
    # Convert raw SQL results to Invoice objects with client info
    invoices = []
    for row in result:
        invoice = Invoice(
            id=row[0],
            user_id=row[1],
            client_id=row[2],
            bank_account_id=row[3],
            invoice_number=row[4],
            issue_date=row[5],
            due_date=row[6],
            status=row[7],
            notes=row[8],
            currency=row[9],
            subtotal=row[10],
            tax_percent=row[11],
            tax_amount=row[12],
            discount_percent=row[13],
            discount_amount=row[14],
            total=row[15],
            sender_name=row[16],
            sender_company=row[17],
            sender_address=row[18],
            sender_phone=row[19],
            sender_email=row[20],
            sender_tax_registration=row[21],
            last_sent_at=row[22],
            created_at=row[23],
            updated_at=row[24]
        )
        
        # Add client info if available
        if row[25]:  # If client_id is not None
            invoice.client_info = type('ClientInfo', (), {
                'id': row[25],
                'name': row[26],
                'company_name': row[27]
            })
        else:
            invoice.client_info = None
        
        # Add organization info
        if row[28]:  # If organization_id is not None
            invoice.organization_info = type('OrganizationInfo', (), {
                'id': row[28],
                'name': row[29]
            })
        else:
            invoice.organization_info = None
            
        invoices.append(invoice)
    
    # Get all clients for the current user (for creating new invoices)
    # Use raw SQL to avoid organization_id issues
    result = db.session.execute(text("""
        SELECT id, user_id, name, company_name, contact_person, email, 
               phone, address, tax_registration_number, notes, created_at, updated_at
        FROM client
        WHERE user_id = :user_id
        ORDER BY name
    """), {'user_id': current_user.id})
    
    # Convert raw SQL results to Client objects
    clients = []
    for row in result:
        client = Client(
            id=row[0],
            user_id=row[1],
            name=row[2],
            company_name=row[3],
            contact_person=row[4],
            email=row[5],
            phone=row[6],
            address=row[7],
            tax_registration_number=row[8],
            notes=row[9],
            created_at=row[10],
            updated_at=row[11]
        )
        clients.append(client)
    
    # Load payments for each invoice
    for invoice in invoices:
        # Query payments for this invoice
        payment_results = db.session.execute(text("""
            SELECT id, date, amount, payment_method, reference, notes, created_at
            FROM payment
            WHERE invoice_id = :invoice_id
            ORDER BY date DESC
        """), {'invoice_id': invoice.id})
        
        invoice.payments = []
        for payment_row in payment_results:
            payment = Payment(
                id=payment_row[0],
                invoice_id=invoice.id,
                date=payment_row[1],
                amount=payment_row[2],
                payment_method=payment_row[3],
                reference=payment_row[4],
                notes=payment_row[5],
                created_at=payment_row[6]
            )
            invoice.payments.append(payment)
            
        # Now update the status with correct payment info
        invoice.update_status()
    
    db.session.commit()
    
    # If this is an AJAX request, return the partial HTML
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render_template('invoices.html', 
                              invoices=invoices, 
                              clients=clients, 
                              organizations=organizations,
                              selected_org=selected_org,
                              invoice_statuses=[(status.name, status.value) for status in InvoiceStatus])
    
    # Regular full page render
    return render_template('invoices.html', 
                           invoices=invoices, 
                           clients=clients, 
                           organizations=organizations,
                           selected_org=selected_org,
                           invoice_statuses=[(status.name, status.value) for status in InvoiceStatus])

@app.route('/invoices/create', methods=['GET', 'POST'])
@login_required
def create_invoice():
    """Create a new invoice."""
    import uuid
    
    if request.method == 'POST':
        try:
            # Get form data
            client_id = request.form.get('client_id')
            organization_id = request.form.get('organization_id')
            bank_account_id = request.form.get('bank_account_id') or None
            issue_date = datetime.strptime(request.form.get('issue_date'), '%Y-%m-%d')
            due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')
            currency = request.form.get('currency', 'LKR')
            notes = request.form.get('notes', '')
            
            # Generate unique invoice number
            invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
            
            # If organization_id is not provided, get the user's default organization
            if not organization_id:
                result = db.session.execute(text("""
                    SELECT organization_id FROM organization_user
                    WHERE user_id = :user_id AND is_default = true
                    LIMIT 1
                """), {'user_id': current_user.id}).first()
                
                organization_id = result[0] if result else None
            
            # Create new invoice using raw SQL to include organization_id and bank_account_id
            # Adding required fields with default values to prevent NOT NULL constraint violations
            # Make sure all numeric fields have explicit default values
            result = db.session.execute(text("""
                INSERT INTO invoice (
                    user_id, client_id, organization_id, bank_account_id, invoice_number, 
                    issue_date, due_date, status, currency, notes, 
                    subtotal, tax_percent, tax_amount, discount_percent, discount_amount, total,
                    created_at, updated_at
                ) 
                VALUES (
                    :user_id, :client_id, :organization_id, :bank_account_id, :invoice_number, 
                    :issue_date, :due_date, :status, :currency, :notes, 
                    :subtotal, :tax_percent, :tax_amount, :discount_percent, :discount_amount, :total,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                RETURNING id
            """), {
                'user_id': current_user.id,
                'client_id': client_id,
                'organization_id': organization_id,
                'bank_account_id': bank_account_id,
                'invoice_number': invoice_number,
                'issue_date': issue_date,
                'due_date': due_date,
                'status': InvoiceStatus.DRAFT.value,
                'currency': currency,
                'notes': notes,
                'subtotal': 0.0,  # Using float value for consistency with model default
                'tax_percent': 0.0, 
                'tax_amount': 0.0, 
                'discount_percent': 0.0, 
                'discount_amount': 0.0, 
                'total': 0.0
            })
            
            invoice_id = result.first()[0]
            
            # Create an Invoice object to work with for the rest of the function
            invoice = Invoice(
                id=invoice_id,
                user_id=current_user.id,
                client_id=client_id,
                bank_account_id=bank_account_id,
                invoice_number=invoice_number,
                issue_date=issue_date,
                due_date=due_date,
                status=InvoiceStatus.DRAFT.value,
                currency=currency,
                notes=notes,
                subtotal=0.0,
                tax_percent=0.0,
                tax_amount=0.0,
                discount_percent=0.0,
                discount_amount=0.0,
                total=0.0
            )
            
            # Extract and create invoice items
            item_descriptions = request.form.getlist('item_description[]')
            item_quantities = request.form.getlist('item_quantity[]')
            item_unit_prices = request.form.getlist('item_unit_price[]')
            item_tax_percentages = request.form.getlist('item_tax_percent[]')
            
            subtotal = 0
            total_tax = 0
            
            for i in range(len(item_descriptions)):
                if item_descriptions[i]:  # Skip empty items
                    description = item_descriptions[i]
                    quantity = float(item_quantities[i])
                    unit_price = float(item_unit_prices[i])
                    tax_percent = float(item_tax_percentages[i] or 0)
                    
                    # Calculate item totals
                    item_subtotal = quantity * unit_price
                    tax_amount = item_subtotal * (tax_percent / 100)
                    item_total = item_subtotal + tax_amount
                    
                    # Create invoice item
                    invoice_item = InvoiceItem(
                        invoice_id=invoice.id,
                        description=description,
                        quantity=quantity,
                        unit_price=unit_price,
                        tax_percent=tax_percent,
                        tax_amount=tax_amount,
                        total=item_total
                    )
                    db.session.add(invoice_item)
                    
                    # Update invoice totals
                    subtotal += item_subtotal
                    total_tax += tax_amount
            
            # Update invoice totals
            invoice.subtotal = subtotal
            invoice.tax_amount = total_tax
            invoice.total = subtotal + total_tax
            
            # Apply discount if any
            discount_percent = float(request.form.get('discount_percent') or 0)
            if discount_percent > 0:
                invoice.discount_percent = discount_percent
                invoice.discount_amount = subtotal * (discount_percent / 100)
                invoice.total -= invoice.discount_amount
            
            # Update the invoice in the database with the calculated values
            db.session.execute(text("""
                UPDATE invoice SET 
                    subtotal = :subtotal,
                    tax_amount = :tax_amount,
                    discount_percent = :discount_percent,
                    discount_amount = :discount_amount,
                    total = :total,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :invoice_id
            """), {
                'subtotal': invoice.subtotal,
                'tax_amount': invoice.tax_amount,
                'discount_percent': invoice.discount_percent,
                'discount_amount': invoice.discount_amount,
                'total': invoice.total,
                'invoice_id': invoice.id
            })
            
            # Add trust points for creating an invoice
            from models import TrustActivity, User
            
            # Give 15 points for creating an invoice
            trust_activity = TrustActivity(
                user_id=current_user.id,
                activity_type='invoice_created',
                points=15,  # Higher value than receipt (15 vs 10)
                description=f'Created invoice {invoice.invoice_number} for {invoice.total} {invoice.currency}'
            )
            db.session.add(trust_activity)
            
            # Update user's trust points
            user = User.query.get(current_user.id)
            user.trust_points = user.trust_points + 15
            
            # Recalculate trust level based on new points
            user.recalculate_trust_level()
            
            db.session.commit()
            
            flash('Invoice created successfully!', 'success')
            return redirect(url_for('view_invoice', invoice_id=invoice.id))
        
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating invoice: {str(e)}")
            flash(f'Error creating invoice: {str(e)}', 'danger')
            return redirect(url_for('invoices'))
    
    # GET request - render the form
    # First get the user's organizations for the dropdown
    org_result = db.session.execute(text("""
        SELECT ou.organization_id, o.name, o.logo_path, o.website, o.email, o.phone, o.address, 
               o.tax_registration_number, ou.is_default
        FROM organization_user ou
        JOIN organization o ON ou.organization_id = o.id
        WHERE ou.user_id = :user_id
        ORDER BY ou.is_default DESC, o.name ASC
    """), {'user_id': current_user.id})
    
    organizations = []
    default_organization_id = None
    
    for row in org_result:
        organization = {
            'id': row[0],
            'name': row[1],
            'logo': row[2],
            'website': row[3],
            'email': row[4],
            'phone': row[5],
            'address': row[6],
            'tax_registration_number': row[7],
            'is_default': row[8]
        }
        organizations.append(organization)
        
        if row[8]:  # is_default is True
            default_organization_id = row[0]
    
    # Get the organization_id from request args or use the default
    organization_id = request.args.get('organization_id', default_organization_id, type=int)
    
    # Get clients for the selected organization only
    if organization_id:
        clients = Client.query.filter_by(
            organization_id=organization_id
        ).order_by(Client.name).all()
    else:
        clients = []
    
    if not clients and organization_id:
        flash('Please add a client for this organization first before creating an invoice', 'warning')
        return redirect(url_for('clients'))
    
    # Get bank accounts for the selected organization
    bank_accounts = []
    
    if organization_id:
        result = db.session.execute(text("""
            SELECT id, account_name, bank_name, account_number, 
                   branch_name, swift_code, iban, is_default
            FROM bank_account 
            WHERE organization_id = :org_id
            ORDER BY is_default DESC, account_name ASC
        """), {'org_id': organization_id})
        
        for row in result:
            bank_accounts.append({
                'id': row[0],
                'account_name': row[1],
                'bank_name': row[2],
                'account_number': row[3],
                'branch_name': row[4],
                'swift_code': row[5],
                'iban': row[6],
                'is_default': row[7]
            })
    
    return render_template('create_invoice.html', 
                           clients=clients,
                           organizations=organizations,
                           selected_organization_id=organization_id,
                           bank_accounts=bank_accounts,
                           today=datetime.now().strftime('%Y-%m-%d'),
                           next_month=datetime.now().replace(month=datetime.now().month + 1 if datetime.now().month < 12 else 1).strftime('%Y-%m-%d'))

@app.route('/invoices/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    """View a specific invoice."""
    # Use raw SQL to avoid organization_id issues with joined client info
    result = db.session.execute(text("""
        SELECT i.id, i.user_id, i.client_id, i.bank_account_id, i.invoice_number, 
               i.issue_date, i.due_date, i.status, i.notes, i.currency, 
               i.subtotal, i.tax_percent, i.tax_amount, i.discount_percent, 
               i.discount_amount, i.total, i.sender_name, i.sender_company, 
               i.sender_address, i.sender_phone, i.sender_email, 
               i.sender_tax_registration, i.last_sent_at, i.created_at, i.updated_at,
               c.id as client_id, c.name as client_name, c.company_name as client_company_name,
               c.contact_person, c.email, c.phone, c.address, c.tax_registration_number
        FROM invoice i
        LEFT JOIN client c ON i.client_id = c.id
        WHERE i.id = :invoice_id AND i.user_id = :user_id
        LIMIT 1
    """), {'invoice_id': invoice_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
    
    # Convert row to Invoice object
    invoice = Invoice(
        id=result[0],
        user_id=result[1],
        client_id=result[2],
        bank_account_id=result[3],
        invoice_number=result[4],
        issue_date=result[5],
        due_date=result[6],
        status=result[7],
        notes=result[8],
        currency=result[9],
        subtotal=result[10],
        tax_percent=result[11],
        tax_amount=result[12],
        discount_percent=result[13],
        discount_amount=result[14],
        total=result[15],
        sender_name=result[16],
        sender_company=result[17],
        sender_address=result[18],
        sender_phone=result[19],
        sender_email=result[20],
        sender_tax_registration=result[21],
        last_sent_at=result[22],
        created_at=result[23],
        updated_at=result[24]
    )
    
    # Add client info if available
    if result[25]:  # If client_id is not None
        client = Client(
            id=result[25],
            name=result[26],
            company_name=result[27],
            contact_person=result[28],
            email=result[29],
            phone=result[30],
            address=result[31],
            tax_registration_number=result[32]
        )
        invoice.client = client
    
    # Load payment information for the invoice
    payment_results = db.session.execute(text("""
        SELECT id, date, amount, payment_method, reference, notes, created_at
        FROM payment
        WHERE invoice_id = :invoice_id
        ORDER BY date DESC
    """), {'invoice_id': invoice.id}).fetchall()
    
    # Add payments to the invoice
    invoice.payments = []
    for p in payment_results:
        payment = Payment(
            id=p[0],
            invoice_id=invoice.id,
            date=p[1],
            amount=p[2],
            payment_method=p[3],
            reference=p[4],
            notes=p[5],
            created_at=p[6]
        )
        invoice.payments.append(payment)
    
    # Update invoice status
    invoice.update_status()
    db.session.commit()
    
    # Get bank account information if available
    bank_account = None
    if invoice.bank_account_id:
        bank_result = db.session.execute(text("""
            SELECT id, account_name, bank_name, account_number, 
                   branch_name, swift_code, iban, is_default
            FROM bank_account 
            WHERE id = :bank_account_id
            LIMIT 1
        """), {'bank_account_id': invoice.bank_account_id}).first()
        
        if bank_result:
            bank_account = {
                'id': bank_result[0],
                'account_name': bank_result[1],
                'bank_name': bank_result[2],
                'account_number': bank_result[3],
                'branch_name': bank_result[4],
                'swift_code': bank_result[5],
                'iban': bank_result[6],
                'is_default': bank_result[7]
            }
    
    # Get organization information
    organization = None
    org_result = db.session.execute(text("""
        SELECT o.id, o.name, o.logo_path, o.website, o.email, o.phone, o.address, 
               o.tax_registration_number, o.created_at
        FROM invoice i
        JOIN organization o ON i.organization_id = o.id
        WHERE i.id = :invoice_id
        LIMIT 1
    """), {'invoice_id': invoice.id}).first()
    
    if org_result:
        organization = {
            'id': org_result[0],
            'name': org_result[1],
            'logo': org_result[2],
            'website': org_result[3],
            'email': org_result[4],
            'phone': org_result[5],
            'address': org_result[6],
            'tax_registration_number': org_result[7],
            'created_at': org_result[8]
        }
    
    return render_template('view_invoice.html', invoice=invoice, bank_account=bank_account, organization=organization)

@app.route('/invoices/<int:invoice_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_invoice(invoice_id):
    """Edit an existing invoice."""
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    
    # Only draft invoices can be edited
    if invoice.status != InvoiceStatus.DRAFT.value:
        flash('Only draft invoices can be edited', 'warning')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    if request.method == 'POST':
        try:
            # Update invoice details
            invoice.client_id = request.form.get('client_id')
            invoice.bank_account_id = request.form.get('bank_account_id') or None
            invoice.issue_date = datetime.strptime(request.form.get('issue_date'), '%Y-%m-%d')
            invoice.due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')
            invoice.currency = request.form.get('currency', 'LKR')
            invoice.notes = request.form.get('notes', '')
            
            # Delete existing items
            for item in invoice.items:
                db.session.delete(item)
            
            # Extract and create new invoice items
            item_descriptions = request.form.getlist('item_description[]')
            item_quantities = request.form.getlist('item_quantity[]')
            item_unit_prices = request.form.getlist('item_unit_price[]')
            item_tax_percentages = request.form.getlist('item_tax_percent[]')
            
            subtotal = 0
            total_tax = 0
            
            for i in range(len(item_descriptions)):
                if item_descriptions[i]:  # Skip empty items
                    description = item_descriptions[i]
                    quantity = float(item_quantities[i])
                    unit_price = float(item_unit_prices[i])
                    tax_percent = float(item_tax_percentages[i] or 0)
                    
                    # Calculate item totals
                    item_subtotal = quantity * unit_price
                    tax_amount = item_subtotal * (tax_percent / 100)
                    item_total = item_subtotal + tax_amount
                    
                    # Create invoice item
                    invoice_item = InvoiceItem(
                        invoice_id=invoice.id,
                        description=description,
                        quantity=quantity,
                        unit_price=unit_price,
                        tax_percent=tax_percent,
                        tax_amount=tax_amount,
                        total=item_total
                    )
                    db.session.add(invoice_item)
                    
                    # Update invoice totals
                    subtotal += item_subtotal
                    total_tax += tax_amount
            
            # Update invoice totals
            invoice.subtotal = subtotal
            invoice.tax_amount = total_tax
            invoice.total = subtotal + total_tax
            
            # Apply discount if any
            discount_percent = float(request.form.get('discount_percent') or 0)
            if discount_percent > 0:
                invoice.discount_percent = discount_percent
                invoice.discount_amount = subtotal * (discount_percent / 100)
                invoice.total -= invoice.discount_amount
            else:
                invoice.discount_percent = 0
                invoice.discount_amount = 0
                
            invoice.updated_at = datetime.utcnow()
            db.session.commit()
            
            flash('Invoice updated successfully!', 'success')
            return redirect(url_for('view_invoice', invoice_id=invoice.id))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating invoice: {str(e)}")
            flash(f'Error updating invoice: {str(e)}', 'danger')
            return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    # GET request - render the form
    
    # Get organization for this invoice
    result = db.session.execute(text("""
        SELECT organization_id FROM invoice
        WHERE id = :invoice_id
        LIMIT 1
    """), {'invoice_id': invoice.id}).first()
    
    organization_id = result[0] if result else None
    
    # Get clients for the selected organization only
    clients = Client.query.filter_by(
        organization_id=organization_id
    ).order_by(Client.name).all()
    
    # Get bank accounts for this organization
    bank_accounts = []
    if organization_id:
        result = db.session.execute(text("""
            SELECT id, account_name, bank_name, account_number, 
                   branch_name, swift_code, iban, is_default
            FROM bank_account 
            WHERE organization_id = :org_id
            ORDER BY is_default DESC, account_name ASC
        """), {'org_id': organization_id})
        
        for row in result:
            bank_accounts.append({
                'id': row[0],
                'account_name': row[1],
                'bank_name': row[2],
                'account_number': row[3],
                'branch_name': row[4],
                'swift_code': row[5],
                'iban': row[6],
                'is_default': row[7]
            })
    
    return render_template('edit_invoice.html', 
                           invoice=invoice,
                           clients=clients,
                           bank_accounts=bank_accounts)

@app.route('/invoices/<int:invoice_id>/send', methods=['POST'])
@login_required
def send_invoice(invoice_id):
    """Mark an invoice as sent and optionally email it using SendGrid."""
    import traceback
    import os
    import json
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    
    # Import the SendGrid logger for detailed API logging
    from sendgrid_logger import (
        log_api_request, 
        log_api_response, 
        log_api_error, 
        log_email_content
    )
    
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    # Make sure to load the items relationship
    db.session.refresh(invoice)
    
    # Check if the email_after_send parameter is present
    email_after_send = request.form.get('email_after_send', 'false') == 'true'
    
    if invoice.status == InvoiceStatus.DRAFT.value:
        invoice.status = InvoiceStatus.SENT.value
        invoice.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Invoice has been marked as sent!', 'success')
        
        # If requested, email the invoice after marking as sent
        if email_after_send and invoice.client and invoice.client.email:
            # Instead of redirecting, call the email functionality directly
            try:
                client_email = invoice.client.email
                # Get sender name (use user's name if sender_name is not set)
                sender_name = invoice.sender_name or current_user.name
                
                # Create email subject
                subject = f"Invoice #{invoice.invoice_number} from {sender_name}"
                
                # Get bank account details if available
                bank_account = None
                if invoice.bank_account_id:
                    bank_account = BankAccount.query.get(invoice.bank_account_id)
                else:
                    # Use default bank account if available
                    bank_account = BankAccount.query.filter_by(user_id=current_user.id, is_default=True).first()
                
                # Get the app URL base
                app_url = request.host_url.rstrip('/')
                
                # Get the organization if applicable
                organization = None
                if hasattr(invoice, 'organization_id') and invoice.organization_id:
                    organization = Organization.query.get(invoice.organization_id)
                
                # Render HTML email template
                html_content = render_template(
                    'email/invoice_email.html',
                    invoice=invoice,
                    sender_name=sender_name,
                    bank_account=bank_account,
                    view_url=url_for('view_invoice', invoice_id=invoice.id, _external=True),
                    app_url=app_url,
                    current_year=datetime.utcnow().year,
                    organization=organization
                )
                
                # Get the SendGrid API key
                sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
                
                # Check if SendGrid API key is configured
                if not sendgrid_api_key:
                    logging.warning("SendGrid API key is not configured. Email will not be sent.")
                    # For development purposes, log what would have been sent
                    logging.info(f"Would have sent invoice email to: {client_email}")
                    logging.info(f"Subject: {subject}")
                    flash("Email feature requires SendGrid API key configuration", "warning")
                    return redirect(url_for('view_invoice', invoice_id=invoice.id))
                
                # Validate the recipient email address 
                import re
                recipient_email = client_email.strip().lower()
                # More comprehensive email validation pattern
                email_pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*[a-zA-Z0-9]@[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$'
                
                if not re.match(email_pattern, recipient_email):
                    logging.error(f"Invalid recipient email format: {recipient_email}")
                    flash(f"Invalid email format: {client_email}", "danger")
                    return redirect(url_for('view_invoice', invoice_id=invoice.id))
                
                # Use the verified sender email address from SendGrid account
                sender_email = 'info@developsrilanka.com'
                
                # Log which sender email we're using
                logging.info(f"Using verified SendGrid sender email address: {sender_email}")
                
                # Create SendGrid mail message
                message = Mail(
                    from_email=sender_email,
                    to_emails=recipient_email,
                    subject=subject,
                    html_content=html_content
                )
                
                # Set the friendly display name separately for better compatibility
                message.from_email.name = f"{sender_name} via FIESTA"
                
                # Send the email using SendGrid
                try:
                    # Log email sending details using both regular logger and SendGrid logger
                    logging.info(f"Preparing to send invoice email with SendGrid:")
                    logging.info(f"From: {sender_name} via FIESTA <{sender_email}>")
                    logging.info(f"To: {recipient_email}")
                    logging.info(f"Subject: {subject}")
                    
                    # Use detailed SendGrid logger to log the API request
                    log_api_request(
                        recipient_email=recipient_email, 
                        sender_email=sender_email,
                        sender_name=f"{sender_name} via FIESTA",
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
                    logging.info(f"SendGrid response status code: {response.status_code}")
                    logging.info(f"SendGrid response body: {response.body}")
                    logging.info(f"SendGrid response headers: {response.headers}")
                    
                    # Use detailed SendGrid logger for response
                    log_api_response(
                        status_code=response.status_code,
                        response_body=response.body,
                        response_headers=response.headers
                    )
                    
                    # Update invoice sent timestamp
                    invoice.last_sent_at = datetime.utcnow()
                    db.session.commit()
                    
                    # Log successful sending
                    logging.info(f"Successfully sent invoice email via SendGrid to {recipient_email}")
                    print(f"SENDGRID EMAIL SENT: Successfully sent invoice to {recipient_email}")
                    
                    # Create a success message with SPAM folder notice in red text
                    from markupsafe import Markup
                    success_message = Markup(f'Invoice has been emailed to {client_email} successfully! <span style="color: red;">(Please check your SPAM folder if not visible in inbox)</span>')
                    flash(success_message, 'success')
                    
                except Exception as sendgrid_error:
                    error_details = traceback.format_exc()
                    
                    # Standard logger
                    logging.error(f"==== SENDGRID ERROR DETAILS ====")
                    logging.error(f"Failed to send invoice via SendGrid to {recipient_email}")
                    logging.error(f"Error type: {type(sendgrid_error).__name__}")
                    logging.error(f"Error message: {str(sendgrid_error)}")
                    logging.error(f"From email: {sender_email}")
                    logging.error(f"From name: {sender_name} via FIESTA")
                    logging.error(f"Full error traceback: {error_details}")
                    logging.error(f"==== END SENDGRID ERROR DETAILS ====")
                    
                    # Use detailed SendGrid logger for error
                    log_api_error(
                        error=sendgrid_error,
                        recipient_email=recipient_email,
                        error_type=type(sendgrid_error).__name__,
                        detailed_traceback=True
                    )
                    
                    # Also print to console for immediate debugging
                    print(f"SENDGRID EMAIL ERROR: Failed to send invoice to {recipient_email}")
                    print(f"Error type: {type(sendgrid_error).__name__}")
                    print(f"Error message: {str(sendgrid_error)}")
                    
                    flash(f"Failed to send invoice: {str(sendgrid_error)}", "danger")
                    
            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"Failed to send invoice email: {str(e)}")
                logging.error(f"Error details: {error_details}")
                flash(f"Failed to send invoice: {str(e)}", "danger")
    else:
        flash('Only draft invoices can be marked as sent', 'warning')
    
    return redirect(url_for('view_invoice', invoice_id=invoice.id))

@app.route('/invoices/<int:invoice_id>/cancel', methods=['POST'])
@login_required
def cancel_invoice(invoice_id):
    """Cancel an invoice."""
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    
    if invoice.status not in [InvoiceStatus.PAID.value]:
        invoice.status = InvoiceStatus.CANCELLED.value
        invoice.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Invoice has been cancelled!', 'success')
    else:
        flash('Paid invoices cannot be cancelled', 'warning')
    
    return redirect(url_for('view_invoice', invoice_id=invoice.id))





@app.route('/invoices/<int:invoice_id>/delete', methods=['POST'])
@login_required
def delete_invoice(invoice_id):
    """Delete an invoice."""
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    
    if invoice.status in [InvoiceStatus.DRAFT.value, InvoiceStatus.CANCELLED.value]:
        db.session.delete(invoice)
        db.session.commit()
        flash('Invoice has been deleted!', 'success')
        return redirect(url_for('invoices'))
    else:
        flash('Only draft or cancelled invoices can be deleted', 'warning')
    
    return redirect(url_for('view_invoice', invoice_id=invoice.id))

@app.route('/invoices/<int:invoice_id>/record-payment', methods=['GET', 'POST'])
@login_required
def record_payment(invoice_id):
    """Record a payment for an invoice."""
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    
    # Check if invoice is already fully paid
    amount_paid = invoice.get_amount_paid()
    amount_due = invoice.get_amount_due()
    
    # If the invoice is already paid, prevent recording a new payment
    if amount_paid >= invoice.total:
        flash('This invoice has already been fully paid. No additional payments can be recorded.', 'warning')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    if request.method == 'POST':
        try:
            # Get form data
            payment_date = datetime.strptime(request.form.get('payment_date'), '%Y-%m-%d')
            amount = float(request.form.get('amount'))
            payment_method = request.form.get('payment_method')
            reference = request.form.get('reference', '')
            notes = request.form.get('notes', '')
            
            # Validate amount
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero")
                
            # Validate that payment doesn't exceed remaining amount
            if amount > amount_due:
                raise ValueError(f"Payment amount (${amount}) exceeds the remaining balance (${amount_due})")
            
            # Create new payment
            payment = Payment(
                invoice_id=invoice.id,
                date=payment_date,
                amount=amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes
            )
            db.session.add(payment)
            
            # Update invoice status
            invoice.update_status()
            
            # Force refresh the invoice object to ensure latest status
            db.session.flush()  # Ensure the payment is written to DB
            db.session.refresh(invoice)  # Reload the invoice with fresh data
            db.session.commit()
            
            flash('Payment recorded successfully!', 'success')
            return redirect(url_for('view_invoice', invoice_id=invoice.id))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error recording payment: {str(e)}")
            flash(f'Error recording payment: {str(e)}', 'danger')
            return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    # GET request - render the form
    remaining_amount = invoice.get_amount_due()
    
    return render_template('record_payment.html', 
                           invoice=invoice,
                           remaining_amount=remaining_amount,
                           payment_methods=[(method.name, method.value) for method in PaymentMethod],
                           today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/invoices/<int:invoice_id>/delete-payment/<int:payment_id>', methods=['POST'])
@login_required
def delete_payment(invoice_id, payment_id):
    """Delete a payment for an invoice."""
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    payment = Payment.query.filter_by(id=payment_id, invoice_id=invoice.id).first_or_404()
    
    try:
        db.session.delete(payment)
        
        # Update invoice status
        invoice.update_status()
        db.session.commit()
        
        flash('Payment has been deleted!', 'success')
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting payment: {str(e)}")
        flash(f'Error deleting payment: {str(e)}', 'danger')
    
    return redirect(url_for('view_invoice', invoice_id=invoice.id))

@app.route('/invoices/<int:invoice_id>/email', methods=['POST', 'GET'])
@login_required
def email_invoice(invoice_id):
    """Email the invoice to the client."""
    import traceback
    
    # Use raw SQL to get invoice with client info
    result = db.session.execute(text("""
        SELECT i.id, i.user_id, i.client_id, i.status,
               c.email as client_email
        FROM invoice i
        LEFT JOIN client c ON i.client_id = c.id
        WHERE i.id = :invoice_id AND i.user_id = :user_id
        LIMIT 1
    """), {'invoice_id': invoice_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
        
    # Get the invoice with its items
    invoice = Invoice.query.get(invoice_id)
    # Make sure to load the items relationship
    if invoice:
        db.session.refresh(invoice)
        
    # Explicitly load the client data for the email template
    client = None
    if result[2]:  # if client_id exists
        client = Client.query.get(result[2])
        # Attach client to invoice for template access
        invoice.client = client
    
    # Check if invoice can be emailed (must have client and client email, and not be a draft)
    if not result[2]:  # client_id is None
        flash('This invoice is not linked to a client', 'danger')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
        
    if not result[4]:  # client_email is None
        flash('Client does not have an email address', 'danger')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    # If the invoice is still in draft status, mark it as sent
    if result[3] == InvoiceStatus.DRAFT.value:  # invoice status
        invoice.status = InvoiceStatus.SENT.value
        invoice.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Invoice has been marked as sent!', 'success')
    
    try:
        # Get client email from the query result
        client_email = result[4]  # From the raw SQL select
        
        # Get sender name (use user's name if sender_name is not set)
        sender_name = invoice.sender_name or current_user.name
        
        # Create email message
        subject = f"Invoice #{invoice.invoice_number} from {sender_name}"
        
        # Get bank account details if available
        bank_account = None
        if invoice.bank_account_id:
            bank_account = BankAccount.query.get(invoice.bank_account_id)
        else:
            # Use default bank account if available
            bank_account = BankAccount.query.filter_by(user_id=current_user.id, is_default=True).first()
        
        # Get the app URL base
        app_url = request.host_url.rstrip('/')
        
        # Render HTML email template
        html_body = render_template(
            'email/invoice_email.html',
            invoice=invoice,
            sender_name=sender_name,
            bank_account=bank_account,
            view_url=url_for('view_invoice', invoice_id=invoice.id, _external=True),
            app_url=app_url,
            current_year=datetime.utcnow().year
        )
        
        # Log the email attempt
        logging.info(f"Sending invoice email to: {client_email}")
        logging.info(f"From: {sender_name} <{app.config['MAIL_USERNAME']}>")
        logging.info(f"Subject: {subject}")
        
        # Check if Gmail credentials are configured
        if not app.config['MAIL_USERNAME'] or not app.config['MAIL_PASSWORD']:
            logging.warning("Gmail credentials are not configured. Email will not be sent.")
            # For development purposes, log what would have been sent
            logging.info(f"Would have sent email to: {client_email}")
            logging.info(f"Subject: {subject}")
            logging.info(f"From: {sender_name} <{app.config['MAIL_USERNAME']}>")
            flash("Email feature requires Gmail credentials configuration", "warning")
            return redirect(url_for('view_invoice', invoice_id=invoice.id))
        
        # Create message
        msg = Message(
            subject=subject,
            recipients=[client_email],
            html=html_body,
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        
        # Send the email using Flask-Mail with verbose logging
        try:
            # Log detailed email headers and info
            logging.info(f"Preparing to send email with the following details:")
            logging.info(f"From: {app.config['MAIL_DEFAULT_SENDER']}")
            logging.info(f"To: {client_email}")
            logging.info(f"Subject: {subject}")
            logging.info(f"SMTP Server: {app.config['MAIL_SERVER']}:{app.config['MAIL_PORT']}")
            logging.info(f"TLS Enabled: {app.config['MAIL_USE_TLS']}")
            logging.info(f"SSL Enabled: {app.config['MAIL_USE_SSL']}")
            
            # Actually send the email
            mail.send(msg)
            
            # Update invoice sent timestamp
            invoice.last_sent_at = datetime.utcnow()
            db.session.commit()
            
            # Log successful sending
            logging.info(f"Successfully sent invoice email to {client_email}")
            logging.info(f"Email delivered to SMTP server for {client_email}")
            print(f"EMAIL SENT: Successfully delivered invoice to SMTP server for {client_email}")  # Console log for immediate visibility
            
            flash(f'Invoice has been emailed to {client_email} successfully!', 'success')
        except Exception as mail_error:
            error_details = traceback.format_exc()
            logging.error(f"Failed to send email to {client_email}: {str(mail_error)}")
            logging.error(f"Error details: {error_details}")
            logging.error(f"Mail configuration: USERNAME={app.config['MAIL_USERNAME']}, SERVER={app.config['MAIL_SERVER']}")
            print(f"EMAIL ERROR: Failed to send to {client_email}: {str(mail_error)}")  # Console log for immediate visibility
            
            flash(f"Failed to send invoice: {str(mail_error)}", "danger")
    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Failed to send invoice email: {str(e)}")
        logging.error(f"Error details: {error_details}")
        flash(f"Invoice email system error: {str(e)}", "danger")
    
    return redirect(url_for('view_invoice', invoice_id=invoice.id))

# ================ Client Management Routes ================

@app.route('/clients')
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
    
    logging.debug(f"Listing clients for user_id: {current_user.id}")
    if selected_org:
        logging.debug(f"Filtered by organization_id: {selected_org['id']}")
    else:
        logging.debug("Showing clients for all organizations")
    
    # First check if there are any clients at all for debugging
    all_clients_count = db.session.execute(text("""
        SELECT COUNT(*) FROM client
    """)).first()[0]
    
    user_clients_count = db.session.execute(text("""
        SELECT COUNT(*) FROM client WHERE user_id = :user_id
    """), {'user_id': current_user.id}).first()[0]
    
    logging.debug(f"Database has {all_clients_count} total clients")
    logging.debug(f"User has {user_clients_count} clients")
    
    # Use raw SQL to get clients for the current user with organization filtering
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
        
        # Add organization name
        if row[13]:  # If organization_name is not None
            client.organization_name = row[13]
        else:
            client.organization_name = "No Organization"
            
        clients.append(client)
        logging.debug(f"Added client: {client.id}, {client.name}, org: {client.organization_id}")
    
    logging.debug(f"Query returned {row_count} rows, processed {len(clients)} clients")
    
    # If this is an AJAX request, return the partial HTML
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render_template('clients.html', 
                               clients=clients,
                               organizations=organizations,
                               selected_org=selected_org)
    
    # Regular full page render
    return render_template('clients.html', 
                          clients=clients,
                          organizations=organizations,
                          selected_org=selected_org)

@app.route('/clients/create', methods=['GET', 'POST'])
@login_required
def create_client():
    """Create a new client."""
    logging.debug("Starting client creation process")
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
            
            logging.debug(f"Create client form data - Name: {name}, Company: {company_name}, Contact: {contact_person}")
            
            # Check if organization_id was provided in the form
            form_org_id = request.form.get('organization_id')
            
            if form_org_id:
                # Verify this organization belongs to the user
                result = db.session.execute(text("""
                    SELECT organization_id FROM organization_user
                    WHERE user_id = :user_id AND organization_id = :org_id
                    LIMIT 1
                """), {'user_id': current_user.id, 'org_id': form_org_id}).first()
                
                organization_id = result[0] if result else None
                logging.debug(f"Using organization_id from form: {organization_id}")
            else:
                # Get the user's default organization
                result = db.session.execute(text("""
                    SELECT organization_id FROM organization_user
                    WHERE user_id = :user_id AND is_default = true
                    LIMIT 1
                """), {'user_id': current_user.id}).first()
                
                organization_id = result[0] if result else None
                logging.debug(f"Using default organization_id: {organization_id}")
            
            # Create new client using raw SQL to include organization_id
            logging.debug(f"Inserting client with organization_id: {organization_id}")
            
            insert_params = {
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
            }
            
            logging.debug(f"Insert params: {insert_params}")
            
            try:
                result = db.session.execute(text("""
                    INSERT INTO client (
                        user_id, organization_id, name, company_name, contact_person, 
                        email, phone, address, tax_registration_number, notes, 
                        created_at, updated_at
                    ) 
                    VALUES (
                        :user_id, :organization_id, :name, :company_name, :contact_person, 
                        :email, :phone, :address, :tax_registration_number, :notes, 
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING id
                """), insert_params)
                
                client_id = result.first()[0]
                logging.debug(f"Client inserted successfully with ID: {client_id}")
                
                # Verify the client was created
                verify_result = db.session.execute(text("""
                    SELECT id, organization_id FROM client WHERE id = :client_id
                """), {'client_id': client_id}).first()
                
                if verify_result:
                    logging.debug(f"Verification successful - Client exists with ID: {verify_result[0]}, organization_id: {verify_result[1]}")
                else:
                    logging.error(f"Verification failed - Client with ID {client_id} not found after insert")
                
                # Explicitly commit to make sure the transaction is completed
                db.session.commit()
            except Exception as e:
                logging.error(f"Error during client insert: {str(e)}")
                db.session.rollback()
                raise
            
            flash('Client created successfully!', 'success')
            return redirect(url_for('clients'))
            
        except Exception as e:
            db.session.rollback()
            import traceback
            error_traceback = traceback.format_exc()
            logging.error(f"Error creating client: {str(e)}")
            logging.error(f"Traceback: {error_traceback}")
            flash(f'Error creating client: {str(e)}', 'danger')
            return redirect(url_for('clients'))
    
    # GET request - render the form
    return render_template('create_client.html')

@app.route('/clients/<int:client_id>')
@login_required
def view_client(client_id):
    """View a specific client."""
    # Use raw SQL to get a specific client by ID
    result = db.session.execute(text("""
        SELECT id, user_id, organization_id, name, company_name, contact_person, email, 
               phone, address, tax_registration_number, notes, created_at, updated_at
        FROM client
        WHERE id = :client_id AND user_id = :user_id
        LIMIT 1
    """), {'client_id': client_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
    
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
    
    # Get invoices for this client using raw SQL
    invoice_results = db.session.execute(text("""
        SELECT id, user_id, client_id, bank_account_id, invoice_number, 
               issue_date, due_date, status, notes, currency, 
               subtotal, tax_percent, tax_amount, discount_percent, 
               discount_amount, total, sender_name, sender_company, 
               sender_address, sender_phone, sender_email, 
               sender_tax_registration, last_sent_at, created_at, updated_at
        FROM invoice
        WHERE client_id = :client_id AND user_id = :user_id
        ORDER BY created_at DESC
    """), {'client_id': client.id, 'user_id': current_user.id})
    
    # Convert raw SQL results to Invoice objects
    invoices = []
    for row in invoice_results:
        invoice = Invoice(
            id=row[0],
            user_id=row[1],
            client_id=row[2],
            bank_account_id=row[3],
            invoice_number=row[4],
            issue_date=row[5],
            due_date=row[6],
            status=row[7],
            notes=row[8],
            currency=row[9],
            subtotal=row[10],
            tax_percent=row[11],
            tax_amount=row[12],
            discount_percent=row[13],
            discount_amount=row[14],
            total=row[15],
            sender_name=row[16],
            sender_company=row[17],
            sender_address=row[18],
            sender_phone=row[19],
            sender_email=row[20],
            sender_tax_registration=row[21],
            last_sent_at=row[22],
            created_at=row[23],
            updated_at=row[24]
        )
        invoices.append(invoice)
    
    # Manually calculate client stats
    total_invoiced = sum(invoice.total for invoice in invoices)
    
    # Get total payments for this client's invoices
    payment_results = db.session.execute(text("""
        SELECT SUM(p.amount)
        FROM payment p
        JOIN invoice i ON p.invoice_id = i.id
        WHERE i.client_id = :client_id AND i.user_id = :user_id
    """), {'client_id': client.id, 'user_id': current_user.id}).first()
    
    total_paid = payment_results[0] or 0
    total_due = total_invoiced - total_paid
    
    return render_template('view_client.html', 
                           client=client, 
                           invoices=invoices,
                           total_invoiced=total_invoiced,
                           total_paid=total_paid,
                           total_due=total_due)

@app.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_client(client_id):
    """Edit an existing client."""
    # Get the client using raw SQL
    result = db.session.execute(text("""
        SELECT id, user_id, organization_id, name, company_name, contact_person, email, 
               phone, address, tax_registration_number, notes, created_at, updated_at
        FROM client
        WHERE id = :client_id AND user_id = :user_id
        LIMIT 1
    """), {'client_id': client_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
    
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
            form_org_id = request.form.get('organization_id')
            organization_id = None
            
            if form_org_id:
                # Verify this organization belongs to the user
                result = db.session.execute(text("""
                    SELECT organization_id FROM organization_user
                    WHERE user_id = :user_id AND organization_id = :org_id
                    LIMIT 1
                """), {'user_id': current_user.id, 'org_id': form_org_id}).first()
                
                organization_id = result[0] if result else None
                logging.debug(f"Using organization_id from form: {organization_id}")
            
            # Update client using raw SQL, including organization_id if provided
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
            
            # Add organization_id to the update if it was provided
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
            return redirect(url_for('view_client', client_id=client.id))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating client: {str(e)}")
            flash(f'Error updating client: {str(e)}', 'danger')
            return redirect(url_for('view_client', client_id=client.id))
    
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
    
    return render_template('edit_client.html', client=client, organizations=organizations)

@app.route('/clients/<int:client_id>/delete', methods=['POST'])
@login_required
def delete_client(client_id):
    """Delete a client."""
    # Check if client exists using raw SQL
    result = db.session.execute(text("""
        SELECT id FROM client
        WHERE id = :client_id AND user_id = :user_id
        LIMIT 1
    """), {'client_id': client_id, 'user_id': current_user.id}).first()
    
    if not result:
        abort(404)
    
    # Check if client has invoices using raw SQL
    has_invoices = db.session.execute(text("""
        SELECT id FROM invoice
        WHERE client_id = :client_id
        LIMIT 1
    """), {'client_id': client_id}).first()
    
    if has_invoices:
        flash('Cannot delete client with invoices. Delete the invoices first.', 'warning')
        return redirect(url_for('view_client', client_id=client_id))
    
    try:
        # Delete client using raw SQL
        db.session.execute(text("""
            DELETE FROM client
            WHERE id = :client_id AND user_id = :user_id
        """), {'client_id': client_id, 'user_id': current_user.id})
        
        db.session.commit()
        
        flash('Client deleted successfully!', 'success')
        return redirect(url_for('clients'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting client: {str(e)}")
        flash(f'Error deleting client: {str(e)}', 'danger')
        return redirect(url_for('clients'))