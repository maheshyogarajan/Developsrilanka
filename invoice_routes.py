from datetime import datetime
import logging
from flask import render_template, request, jsonify, flash, redirect, url_for
from flask_login import current_user, login_required
from app import app, db
from models import Invoice, Client, InvoiceItem, Payment, InvoiceStatus, PaymentMethod

# ================ Invoice Management Routes ================

@app.route('/invoices')
@login_required
def invoices():
    """Render the invoices page showing list of user's invoices."""
    # Get all invoices for the current user
    invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).all()
    
    # Get all clients for the current user (for creating new invoices)
    clients = Client.query.filter_by(user_id=current_user.id).order_by(Client.name).all()
    
    # Update invoice statuses based on due dates and payments
    for invoice in invoices:
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
            issue_date = datetime.strptime(request.form.get('issue_date'), '%Y-%m-%d')
            due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')
            currency = request.form.get('currency', 'LKR')
            notes = request.form.get('notes', '')
            
            # Generate unique invoice number
            invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
            
            # Create new invoice
            invoice = Invoice(
                user_id=current_user.id,
                client_id=client_id,
                invoice_number=invoice_number,
                issue_date=issue_date,
                due_date=due_date,
                status=InvoiceStatus.DRAFT.value,
                currency=currency,
                notes=notes
            )
            db.session.add(invoice)
            db.session.commit()
            
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
    
    return render_template('create_invoice.html', 
                           clients=clients,
                           today=datetime.now().strftime('%Y-%m-%d'),
                           next_month=datetime.now().replace(month=datetime.now().month + 1 if datetime.now().month < 12 else 1).strftime('%Y-%m-%d'))

@app.route('/invoices/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    """View a specific invoice."""
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=current_user.id).first_or_404()
    
    # Update invoice status
    invoice.update_status()
    db.session.commit()
    
    return render_template('view_invoice.html', invoice=invoice)

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
    
    return render_template('edit_invoice.html', 
                           invoice=invoice,
                           clients=clients)

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

# ================ Client Management Routes ================

@app.route('/clients')
@login_required
def clients():
    """Render the clients page showing list of user's clients."""
    # Get all clients for the current user
    clients = Client.query.filter_by(user_id=current_user.id).order_by(Client.name).all()
    
    return render_template('clients.html', clients=clients)

@app.route('/clients/create', methods=['GET', 'POST'])
@login_required
def create_client():
    """Create a new client."""
    if request.method == 'POST':
        try:
            # Get form data
            name = request.form.get('name')
            company_name = request.form.get('company_name', '')
            contact_person = request.form.get('contact_person', '')
            email = request.form.get('email', '')
            phone = request.form.get('phone', '')
            address = request.form.get('address', '')
            tax_registration_number = request.form.get('tax_registration_number', '')
            notes = request.form.get('notes', '')
            
            # Create new client
            client = Client(
                user_id=current_user.id,
                name=name,
                company_name=company_name,
                contact_person=contact_person,
                email=email,
                phone=phone,
                address=address,
                tax_registration_number=tax_registration_number,
                notes=notes
            )
            db.session.add(client)
            db.session.commit()
            
            flash('Client created successfully!', 'success')
            return redirect(url_for('clients'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating client: {str(e)}")
            flash(f'Error creating client: {str(e)}', 'danger')
            return redirect(url_for('clients'))
    
    # GET request - render the form
    return render_template('create_client.html')

@app.route('/clients/<int:client_id>')
@login_required
def view_client(client_id):
    """View a specific client."""
    client = Client.query.filter_by(id=client_id, user_id=current_user.id).first_or_404()
    
    # Get invoices for this client
    invoices = Invoice.query.filter_by(client_id=client.id).order_by(Invoice.created_at.desc()).all()
    
    # Calculate client stats
    total_invoiced = sum(invoice.total for invoice in invoices)
    total_paid = sum(invoice.get_amount_paid() for invoice in invoices)
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
    client = Client.query.filter_by(id=client_id, user_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        try:
            # Update client details
            client.name = request.form.get('name')
            client.company_name = request.form.get('company_name', '')
            client.contact_person = request.form.get('contact_person', '')
            client.email = request.form.get('email', '')
            client.phone = request.form.get('phone', '')
            client.address = request.form.get('address', '')
            client.tax_registration_number = request.form.get('tax_registration_number', '')
            client.notes = request.form.get('notes', '')
            client.updated_at = datetime.utcnow()
            
            db.session.commit()
            
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
    client = Client.query.filter_by(id=client_id, user_id=current_user.id).first_or_404()
    
    # Check if client has invoices
    has_invoices = Invoice.query.filter_by(client_id=client.id).first()
    
    if has_invoices:
        flash('Cannot delete client with invoices. Delete the invoices first.', 'warning')
        return redirect(url_for('view_client', client_id=client.id))
    
    try:
        db.session.delete(client)
        db.session.commit()
        
        flash('Client deleted successfully!', 'success')
        return redirect(url_for('clients'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting client: {str(e)}")
        flash(f'Error deleting client: {str(e)}', 'danger')
        return redirect(url_for('clients'))