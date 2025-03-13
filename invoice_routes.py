from datetime import datetime
import logging
import os
from flask import render_template, request, jsonify, flash, redirect, url_for, abort
from flask_login import current_user, login_required
from flask_mail import Mail, Message
from sqlalchemy import text
from app import app, db
from models import Invoice, Client, InvoiceItem, Payment, InvoiceStatus, PaymentMethod, BankAccount

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
    # Use raw SQL to query invoices to avoid organization_id column issues
    result = db.session.execute(text("""
        SELECT i.id, i.user_id, i.client_id, i.bank_account_id, i.invoice_number, 
               i.issue_date, i.due_date, i.status, i.notes, i.currency, 
               i.subtotal, i.tax_percent, i.tax_amount, i.discount_percent, 
               i.discount_amount, i.total, i.sender_name, i.sender_company, 
               i.sender_address, i.sender_phone, i.sender_email, 
               i.sender_tax_registration, i.last_sent_at, i.created_at, i.updated_at,
               c.id as client_id, c.name as client_name, c.company_name as client_company_name
        FROM invoice i
        LEFT JOIN client c ON i.client_id = c.id
        WHERE i.user_id = :user_id
        ORDER BY i.created_at DESC
    """), {'user_id': current_user.id})
    
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
    
    return render_template('invoices.html', 
                           invoices=invoices, 
                           clients=clients, 
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
            bank_account_id = request.form.get('bank_account_id') or None
            issue_date = datetime.strptime(request.form.get('issue_date'), '%Y-%m-%d')
            due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')
            currency = request.form.get('currency', 'LKR')
            notes = request.form.get('notes', '')
            
            # Generate unique invoice number
            invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
            
            # Get the user's default organization
            result = db.session.execute(text("""
                SELECT organization_id FROM organization_user
                WHERE user_id = :user_id AND is_default = true
                LIMIT 1
            """), {'user_id': current_user.id}).first()
            
            organization_id = result[0] if result else None
            
            # Create new invoice using raw SQL to include organization_id and bank_account_id
            result = db.session.execute(text("""
                INSERT INTO invoice (
                    user_id, client_id, organization_id, bank_account_id, invoice_number, 
                    issue_date, due_date, status, currency, notes, created_at, updated_at
                ) 
                VALUES (
                    :user_id, :client_id, :organization_id, :bank_account_id, :invoice_number, 
                    :issue_date, :due_date, :status, :currency, :notes, 
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
                'notes': notes
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
                notes=notes
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
            
            db.session.commit()
            
            flash('Invoice created successfully!', 'success')
            return redirect(url_for('view_invoice', invoice_id=invoice.id))
        
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating invoice: {str(e)}")
            flash(f'Error creating invoice: {str(e)}', 'danger')
            return redirect(url_for('invoices'))
    
    # GET request - render the form
    clients = Client.query.filter_by(user_id=current_user.id).order_by(Client.name).all()
    
    if not clients:
        flash('Please add a client first before creating an invoice', 'warning')
        return redirect(url_for('clients'))
    
    # Get the user's default organization
    default_org = db.session.execute(text("""
        SELECT organization_id FROM organization_user
        WHERE user_id = :user_id AND is_default = true
        LIMIT 1
    """), {'user_id': current_user.id}).first()
    
    organization_id = default_org[0] if default_org else None
    
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
    
    return render_template('create_invoice.html', 
                           clients=clients,
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
    
    return render_template('view_invoice.html', invoice=invoice, bank_account=bank_account)

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
    clients = Client.query.filter_by(user_id=current_user.id).order_by(Client.name).all()
    
    # Get organization for this invoice
    result = db.session.execute(text("""
        SELECT organization_id FROM invoice
        WHERE id = :invoice_id
        LIMIT 1
    """), {'invoice_id': invoice.id}).first()
    
    organization_id = result[0] if result else None
    
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
    """Mark an invoice as sent."""
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    
    if invoice.status == InvoiceStatus.DRAFT.value:
        invoice.status = InvoiceStatus.SENT.value
        invoice.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Invoice has been marked as sent!', 'success')
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

@app.route('/invoices/<int:invoice_id>/email', methods=['POST'])
@login_required
def email_invoice(invoice_id):
    """Email the invoice to the client."""
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
        
    invoice = Invoice.query.get(invoice_id)
    
    # Check if invoice can be emailed (must have client and client email, and not be a draft)
    if not result[2]:  # client_id is None
        flash('This invoice is not linked to a client', 'danger')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
        
    if not result[4]:  # client_email is None
        flash('Client does not have an email address', 'danger')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    if result[3] == InvoiceStatus.DRAFT.value:  # invoice status
        flash('You cannot email a draft invoice. Please mark it as sent first.', 'warning')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    try:
        # Create email message
        subject = f"Invoice #{invoice.invoice_number} from {invoice.sender_name or current_user.name}"
        
        # Get sender name (use user's name if sender_name is not set)
        sender_name = invoice.sender_name or current_user.name
        
        # Get bank account details if available
        bank_account = None
        if invoice.bank_account_id:
            bank_account = BankAccount.query.get(invoice.bank_account_id)
        else:
            # Use default bank account if available
            bank_account = BankAccount.query.filter_by(user_id=current_user.id, is_default=True).first()
        
        # Render HTML email template
        html_body = render_template(
            'email/invoice_email.html',
            invoice=invoice,
            sender_name=sender_name,
            bank_account=bank_account,
            view_url=url_for('view_invoice', invoice_id=invoice.id, _external=True)
        )
        
        # Get client email from the query result
        client_email = result[4]  # From the raw SQL select
        
        # Create message
        msg = Message(
            subject=subject,
            recipients=[client_email],
            html=html_body,
            sender=(sender_name, app.config['MAIL_USERNAME'])
        )
        
        # Send email
        mail.send(msg)
        
        # Update invoice sent timestamp
        invoice.last_sent_at = datetime.utcnow()
        db.session.commit()
        
        flash(f'Invoice has been emailed to {client_email} successfully!', 'success')
    except Exception as e:
        logging.error(f"Error sending invoice email: {str(e)}")
        flash(f'Error sending email: {str(e)}', 'danger')
    
    return redirect(url_for('view_invoice', invoice_id=invoice.id))

# ================ Client Management Routes ================

@app.route('/clients')
@login_required
def clients():
    """Render the clients page showing list of user's clients."""
    # Use raw SQL to get clients for the current user with organization context
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
    
    return render_template('clients.html', clients=clients)

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
            
            # Get the user's default organization
            result = db.session.execute(text("""
                SELECT organization_id FROM organization_user
                WHERE user_id = :user_id AND is_default = true
                LIMIT 1
            """), {'user_id': current_user.id}).first()
            
            organization_id = result[0] if result else None
            
            # Create new client using raw SQL to include organization_id
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
            """), {
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
            
            client_id = result.first()[0]
            
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
        SELECT id, user_id, name, company_name, contact_person, email, 
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
        name=result[2],
        company_name=result[3],
        contact_person=result[4],
        email=result[5],
        phone=result[6],
        address=result[7],
        tax_registration_number=result[8],
        notes=result[9],
        created_at=result[10],
        updated_at=result[11]
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
    
    # Store the organization_id
    organization_id = result[2]
    
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
            
            # Update client using raw SQL to maintain organization_id
            db.session.execute(text("""
                UPDATE client
                SET name = :name,
                    company_name = :company_name,
                    contact_person = :contact_person,
                    email = :email,
                    phone = :phone,
                    address = :address,
                    tax_registration_number = :tax_registration_number,
                    notes = :notes,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :client_id AND user_id = :user_id
            """), {
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
            })
            
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
            
            flash('Client updated successfully!', 'success')
            return redirect(url_for('view_client', client_id=client.id))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating client: {str(e)}")
            flash(f'Error updating client: {str(e)}', 'danger')
            return redirect(url_for('view_client', client_id=client.id))
    
    # GET request - render the form
    return render_template('edit_client.html', client=client)

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