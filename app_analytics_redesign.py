@app.route('/analytics/redesign')
@login_required
def analytics_redesign():
    """Render the redesigned analytics page with enhanced features."""
    from models import Receipt, ReceiptItem, Invoice, InvoiceItem, Payment, UserIncome, CompanyExpense, ClientExpense, Organization, OrganizationUser
    from sqlalchemy import func, desc, case, and_, or_
    from datetime import datetime, timedelta
    import random  # Only for initial demo of redesign
    
    try:
        # Get the user's organizations
        user_orgs = db.session.query(
            Organization.id,
            Organization.name,
            OrganizationUser.is_default
        ).join(
            OrganizationUser, 
            and_(
                OrganizationUser.organization_id == Organization.id,
                OrganizationUser.user_id == current_user.id
            )
        ).all()
        
        organizations = []
        default_org_id = None
        
        for org_id, org_name, is_default in user_orgs:
            organizations.append({
                'id': org_id,
                'name': org_name,
                'is_default': is_default
            })
            if is_default:
                default_org_id = org_id
                
        if not default_org_id and organizations:
            default_org_id = organizations[0]['id']
        
        # Get selected organization from query params or use default
        selected_org_id = request.args.get('organization_id', default_org_id)
        show_all = request.args.get('show_all') == 'true'
        
        # Time range filters
        time_range = request.args.get('time_range', 'month')
        today = datetime.utcnow().date()
        
        if time_range == 'week':
            start_date = today - timedelta(days=7)
        elif time_range == 'month':
            start_date = today - timedelta(days=30)
        elif time_range == 'quarter':
            start_date = today - timedelta(days=90)
        elif time_range == 'year':
            start_date = today - timedelta(days=365)
        elif time_range == 'custom':
            # Parse custom date range if provided
            try:
                start_date = datetime.strptime(request.args.get('start_date', ''), '%Y-%m-%d').date()
                end_date = datetime.strptime(request.args.get('end_date', ''), '%Y-%m-%d').date()
            except ValueError:
                start_date = today - timedelta(days=30)
                end_date = today
        else:
            # Default to last 30 days
            start_date = today - timedelta(days=30)
        
        if 'end_date' not in locals():
            end_date = today
            
        # Date range for historical comparison (previous period)
        date_diff = (end_date - start_date).days
        prev_end_date = start_date - timedelta(days=1)
        prev_start_date = prev_end_date - timedelta(days=date_diff)
        
        # ----------------- EXPENSE ANALYTICS -----------------
        # Build the filter for expenses
        receipt_filter = [Receipt.user_id == current_user.id]
        if not show_all and selected_org_id:
            receipt_filter.append(Receipt.organization_id == selected_org_id)
        
        # Add date filter for current period
        date_filter = [Receipt.date >= start_date, Receipt.date <= end_date]
        
        # Get total expenses for the current user in the current period
        total_expenses = db.session.query(func.sum(Receipt.total_amount))\
            .filter(and_(*receipt_filter, *date_filter)).scalar() or 0
            
        # Get total tax deductible expenses
        total_tax_deductible = db.session.query(
            func.sum(
                case(
                    whens=[(ReceiptItem.tax_deductible == True, ReceiptItem.price * ReceiptItem.quantity)],
                    else_=0
                )
            )
        ).join(
            Receipt, Receipt.id == ReceiptItem.receipt_id
        ).filter(
            and_(*receipt_filter, *date_filter)
        ).scalar() or 0
        
        # Get expenses for previous period for comparison
        prev_date_filter = [Receipt.date >= prev_start_date, Receipt.date <= prev_end_date]
        prev_total_expenses = db.session.query(func.sum(Receipt.total_amount))\
            .filter(and_(*receipt_filter, *prev_date_filter)).scalar() or 0
            
        # Get major categories for this period
        major_categories = db.session.query(
            Receipt.expense_major_category,
            func.sum(Receipt.total_amount).label('total')
        ).filter(
            and_(
                *receipt_filter,
                *date_filter,
                Receipt.expense_major_category != None, 
                Receipt.expense_major_category != ''
            )
        ).group_by(Receipt.expense_major_category)\
        .order_by(func.sum(Receipt.total_amount).desc())\
        .all()
        
        # Get major categories for previous period
        prev_major_categories = db.session.query(
            Receipt.expense_major_category,
            func.sum(Receipt.total_amount).label('total')
        ).filter(
            and_(
                *receipt_filter,
                *prev_date_filter,
                Receipt.expense_major_category != None, 
                Receipt.expense_major_category != ''
            )
        ).group_by(Receipt.expense_major_category)\
        .order_by(func.sum(Receipt.total_amount).desc())\
        .all()
        
        # Create dictionaries for easier comparison
        current_categories = {cat: total for cat, total in major_categories}
        previous_categories = {cat: total for cat, total in prev_major_categories}
        
        # Find growing and decreasing categories
        growing_categories = []
        decreasing_categories = []
        
        for cat in set(list(current_categories.keys()) + list(previous_categories.keys())):
            current = current_categories.get(cat, 0)
            previous = previous_categories.get(cat, 0)
            
            if previous > 0:
                change_pct = ((current - previous) / previous) * 100
                
                if current > previous:
                    growing_categories.append((cat, change_pct))
                elif current < previous:
                    decreasing_categories.append((cat, abs(change_pct)))
        
        # Sort by percentage change
        growing_categories.sort(key=lambda x: x[1], reverse=True)
        decreasing_categories.sort(key=lambda x: x[1], reverse=True)
        
        # Get top growing and decreasing categories
        top_growing_category = growing_categories[0][0] if growing_categories else "None"
        top_growing_percentage = round(growing_categories[0][1], 1) if growing_categories else 0
        
        top_decreasing_category = decreasing_categories[0][0] if decreasing_categories else "None"
        top_decreasing_percentage = round(decreasing_categories[0][1], 1) if decreasing_categories else 0
        
        # Determine if categories are tax deductible or essential
        # For demo, we'll use a simple heuristic
        tax_deductible_categories = ['Business Expenses', 'Office Supplies', 'Travel', 'Utilities']
        essential_categories = ['Food', 'Housing', 'Utilities', 'Transportation']
        
        top_growing_category_deductible = top_growing_category in tax_deductible_categories
        top_decreasing_category_essential = top_decreasing_category in essential_categories
        
        # Get monthly data for charts
        months = []
        monthly_income = []
        monthly_expenses = []
        monthly_deductible = []
        monthly_non_deductible = []
        monthly_cash_flow = []
        
        # Get the last 6 months
        for i in range(5, -1, -1):
            # Calculate month start and end
            month_end = today.replace(day=1) - timedelta(days=1) if i == 0 else (today.replace(day=1) - timedelta(days=1)).replace(month=(today.month - i) % 12 or 12)
            month_start = month_end.replace(day=1)
            
            # Add month name
            months.append(month_start.strftime('%b'))
            
            # Get expenses for this month
            month_expenses = db.session.query(func.sum(Receipt.total_amount))\
                .filter(and_(*receipt_filter, Receipt.date >= month_start, Receipt.date <= month_end))\
                .scalar() or 0
                
            # Get tax deductible expenses for this month
            month_deductible = db.session.query(
                func.sum(
                    case(
                        whens=[(ReceiptItem.tax_deductible == True, ReceiptItem.price * ReceiptItem.quantity)],
                        else_=0
                    )
                )
            ).join(
                Receipt, Receipt.id == ReceiptItem.receipt_id
            ).filter(
                and_(*receipt_filter, Receipt.date >= month_start, Receipt.date <= month_end)
            ).scalar() or 0
            
            # Calculate other metrics
            month_non_deductible = month_expenses - month_deductible
            
            # Store values
            monthly_expenses.append(month_expenses)
            monthly_deductible.append(month_deductible)
            monthly_non_deductible.append(month_non_deductible)
            
            # We'll calculate income and cash flow after we get income data
        
        # Total count of receipts
        receipt_count = Receipt.query.filter(and_(*receipt_filter, *date_filter)).count()
        
        # Recent receipts
        recent_receipts = Receipt.query.filter(and_(*receipt_filter))\
            .order_by(Receipt.date.desc())\
            .limit(5)\
            .all()
            
        recent_receipt_data = [
            {
                'id': receipt.id,
                'date': receipt.date.strftime('%Y-%m-%d') if receipt.date else 'Unknown',
                'vendor': receipt.vendor_name,
                'amount': receipt.total_amount,
                'category': receipt.expense_major_category or 'Uncategorized'
            } for receipt in recent_receipts
        ]
        
        # Get company and client expenses
        company_expense_total = db.session.query(func.sum(Receipt.total_amount))\
            .join(CompanyExpense, CompanyExpense.receipt_id == Receipt.id)\
            .filter(and_(CompanyExpense.user_id == current_user.id, *date_filter)).scalar() or 0
            
        client_expense_total = db.session.query(func.sum(Receipt.total_amount))\
            .join(ClientExpense, ClientExpense.receipt_id == Receipt.id)\
            .filter(and_(ClientExpense.user_id == current_user.id, *date_filter)).scalar() or 0
            
        # Prepare categories for charts
        major_category_data = [{'category': cat, 'total': total} for cat, total in major_categories]
        
        # ----------------- REVENUE ANALYTICS -----------------
        # Get income data for the current user
        income = db.session.query(UserIncome).filter_by(user_id=current_user.id).first()
        
        # Income sources and amounts
        income_sources = []
        total_revenue = 0
        primary_revenue_source = "Business"  # Default
        
        if income:
            # Business income
            if income.business_income and income.business_income > 0:
                income_sources.append({
                    'source': 'Business Income',
                    'amount': income.business_income,
                    'currency': 'LKR'
                })
                total_revenue += income.business_income
            
            # Employment income
            if income.employment_income and income.employment_income > 0:
                income_sources.append({
                    'source': 'Employment Income',
                    'amount': income.employment_income,
                    'currency': 'LKR'
                })
                total_revenue += income.employment_income
            
            # Investment income
            if income.investment_income and income.investment_income > 0:
                income_sources.append({
                    'source': 'Investment Income',
                    'amount': income.investment_income,
                    'currency': 'LKR'
                })
                total_revenue += income.investment_income
            
            # USD Consulting (convert to LKR)
            if income.usd_consulting_income and income.usd_consulting_income > 0:
                # Assuming 1 USD = 320 LKR
                usd_to_lkr = income.usd_consulting_income * 320
                income_sources.append({
                    'source': 'USD Consulting',
                    'amount': usd_to_lkr,
                    'currency': 'USD',
                    'original_amount': income.usd_consulting_income
                })
                total_revenue += usd_to_lkr
            
            # Determine primary revenue source
            if income_sources:
                income_sources.sort(key=lambda x: x['amount'], reverse=True)
                primary_revenue_source = income_sources[0]['source']
                
        # Default income data for new users
        else:
            income_data_missing = True
            # For demo purposes, add some placeholder income data
            income_sources = [
                {'source': 'Business Income', 'amount': 0, 'currency': 'LKR'},
                {'source': 'Employment Income', 'amount': 0, 'currency': 'LKR'},
                {'source': 'Investment Income', 'amount': 0, 'currency': 'LKR'}
            ]
        
        # Calculate monthly income (simplified for demo)
        monthly_revenue = total_revenue / 6  # Assume even distribution over 6 months
        
        # Now fill in monthly income and cash flow data
        for i in range(6):
            # Simplify with consistent income for demo
            monthly_income.append(monthly_revenue)
            # Calculate cash flow (income - expenses)
            monthly_cash_flow.append(monthly_revenue - monthly_expenses[i])
        
        # For revenue trend, compare current total to previous
        # In real implementation, we would query income changes over time
        # For demo, we'll use a random trend
        revenue_trends = ['increased', 'decreased']
        revenue_trend = revenue_trends[0]  # Default to increased
        revenue_change_percentage = random.randint(5, 15)  # Demo value
        
        # ----------------- INVOICE ANALYTICS -----------------
        # Build the filter for invoices
        invoice_filter = [Invoice.user_id == current_user.id]
        if not show_all and selected_org_id:
            invoice_filter.append(Invoice.organization_id == selected_org_id)
            
        # Invoice counts by status
        paid_invoice_count = Invoice.query.filter(
            and_(*invoice_filter, Invoice.status == 'paid')
        ).count()
        
        pending_invoice_count = Invoice.query.filter(
            and_(*invoice_filter, or_(Invoice.status == 'sent', Invoice.status == 'partially_paid'))
        ).count()
        
        overdue_invoice_count = Invoice.query.filter(
            and_(*invoice_filter, Invoice.status == 'overdue')
        ).count()
        
        # Invoice totals by status
        paid_invoices_total = db.session.query(func.sum(Invoice.total))\
            .filter(and_(*invoice_filter, Invoice.status == 'paid'))\
            .scalar() or 0
            
        pending_invoices_total = db.session.query(func.sum(Invoice.total))\
            .filter(and_(*invoice_filter, or_(Invoice.status == 'sent', Invoice.status == 'partially_paid')))\
            .scalar() or 0
            
        overdue_invoices_total = db.session.query(func.sum(Invoice.total))\
            .filter(and_(*invoice_filter, Invoice.status == 'overdue'))\
            .scalar() or 0
            
        # ----------------- TAX ANALYTICS -----------------
        # Tax deduction potential calculations
        total_possible_deductible = total_expenses * 0.8  # Estimate that 80% could be deductible
        tax_optimization_percentage = int((total_tax_deductible / total_possible_deductible) * 100) if total_possible_deductible > 0 else 0
        
        # Tax savings calculations
        # Using simplified Sri Lankan tax rates: business income 36%, employment 24%
        business_tax_rate = 0.36
        employment_tax_rate = 0.24
        usd_income_tax_rate = 0.15
        
        # Calculate realized and potential tax savings
        realized_tax_savings = total_tax_deductible * business_tax_rate
        potential_tax_savings = total_possible_deductible * business_tax_rate
        
        # Missing tax opportunities
        missing_opportunity_count = db.session.query(Receipt)\
            .filter(and_(*receipt_filter))\
            .filter(~Receipt.id.in_(
                db.session.query(ReceiptItem.receipt_id)\
                .filter(ReceiptItem.tax_deductible == True)\
                .subquery()
            ))\
            .count()
        
        # Top tax-saving category
        top_tax_categories = db.session.query(
            Receipt.expense_major_category,
            func.sum(
                case(
                    whens=[(ReceiptItem.tax_deductible == True, ReceiptItem.price * ReceiptItem.quantity)],
                    else_=0
                )
            ).label('tax_deductible_amount')
        ).join(
            ReceiptItem, Receipt.id == ReceiptItem.receipt_id
        ).filter(
            and_(*receipt_filter, ReceiptItem.tax_deductible == True)
        ).group_by(
            Receipt.expense_major_category
        ).order_by(
            func.sum(
                case(
                    whens=[(ReceiptItem.tax_deductible == True, ReceiptItem.price * ReceiptItem.quantity)],
                    else_=0
                )
            ).desc()
        ).first()
        
        if top_tax_categories:
            top_tax_saving_category = top_tax_categories[0] or "None"
            top_tax_saving_amount = top_tax_categories[1] or 0
        else:
            top_tax_saving_category = "None"
            top_tax_saving_amount = 0
            
        # ----------------- MULTI-ORGANIZATION OVERVIEW -----------------
        # Get financial stats for each organization
        org_financials = []
        
        # Colors for organization icons
        org_colors = ['#4a6da7', '#28a745', '#fd7e14', '#6f42c1', '#e83e8c', '#17a2b8']
        
        # For each organization the user belongs to
        for i, org in enumerate(organizations):
            org_id = org['id']
            
            # Get total expenses for this organization
            org_expenses = db.session.query(func.sum(Receipt.total_amount))\
                .filter(Receipt.organization_id == org_id, *date_filter)\
                .scalar() or 0
                
            # Get total tax deductible for this organization
            org_tax_deductible = db.session.query(
                func.sum(
                    case(
                        whens=[(ReceiptItem.tax_deductible == True, ReceiptItem.price * ReceiptItem.quantity)],
                        else_=0
                    )
                )
            ).join(
                Receipt, Receipt.id == ReceiptItem.receipt_id
            ).filter(
                Receipt.organization_id == org_id, *date_filter
            ).scalar() or 0
            
            # Get total income for this organization (simplified for demo)
            # In a real implementation, we would have organization-specific income
            org_revenue = 0
            if org_id == default_org_id and income:
                org_revenue = total_revenue
                
            # Calculate tax savings
            org_tax_savings = org_tax_deductible * business_tax_rate
            
            # Add to organization financials
            org_financials.append({
                'id': org_id,
                'name': org['name'],
                'expenses': org_expenses,
                'revenue': org_revenue,
                'net': org_revenue - org_expenses,
                'tax_savings': org_tax_savings,
                'color': org_colors[i % len(org_colors)]
            })
            
        # ----------------- FINANCIAL HEALTH SCORES -----------------
        # Calculate financial health score components
        
        # 1. Tax optimization score (0-100)
        tax_optimization_score = tax_optimization_percentage
        
        # 2. Revenue stability score (40-100)
        # Based on regularity of income and diversity of sources
        revenue_stability_score = min(100, 40 + (len(income_sources) * 15))
        
        # 3. Expense management score (0-100)
        # Based on expense tracking, categorization, and allocation
        expense_categorized_pct = len([r for r in db.session.query(Receipt.id).filter(and_(*receipt_filter, Receipt.expense_major_category != None)).all()]) / max(receipt_count, 1) * 100
        expense_allocated_pct = (company_expense_total + client_expense_total) / max(total_expenses, 1) * 100
        expense_management_score = int((expense_categorized_pct + expense_allocated_pct) / 2)
        
        # Overall financial health score (weighted average)
        financial_health_score = int(
            (tax_optimization_score * 0.4) + 
            (revenue_stability_score * 0.3) + 
            (expense_management_score * 0.3)
        )
        
        # Render the redesigned analytics template with all the data
        return render_template('analytics_redesign.html',
            # Organizations
            organizations=organizations,
            selected_org_id=selected_org_id,
            
            # Financial metrics
            total_expenses=total_expenses,
            total_revenue=total_revenue,
            total_tax_deductible=total_tax_deductible,
            total_tax_savings=realized_tax_savings,
            financial_health_score=financial_health_score,
            
            # Score components
            tax_optimization_score=tax_optimization_score,
            revenue_stability_score=revenue_stability_score,
            expense_management_score=expense_management_score,
            
            # Expense data
            company_expense_total=company_expense_total,
            client_expense_total=client_expense_total,
            major_categories=major_category_data,
            receipt_count=receipt_count,
            recent_receipts=recent_receipt_data,
            
            # Trend insights
            top_growing_category=top_growing_category,
            top_growing_percentage=top_growing_percentage,
            top_growing_category_deductible=top_growing_category_deductible,
            top_decreasing_category=top_decreasing_category,
            top_decreasing_percentage=top_decreasing_percentage,
            top_decreasing_category_essential=top_decreasing_category_essential,
            
            # Revenue data
            income_sources=income_sources,
            income_data_missing=income is None,
            primary_revenue_source=primary_revenue_source,
            revenue_trend=revenue_trend,
            revenue_change_percentage=revenue_change_percentage,
            
            # Invoice data
            paid_invoice_count=paid_invoice_count,
            pending_invoice_count=pending_invoice_count,
            overdue_invoice_count=overdue_invoice_count,
            paid_invoices_total=paid_invoices_total,
            pending_invoices_total=pending_invoices_total,
            overdue_invoices_total=overdue_invoices_total,
            
            # Tax data
            tax_optimization_percentage=tax_optimization_percentage,
            realized_tax_savings=realized_tax_savings,
            potential_tax_savings=potential_tax_savings,
            missing_opportunity_count=missing_opportunity_count,
            top_tax_saving_category=top_tax_saving_category,
            top_tax_saving_amount=top_tax_saving_amount,
            
            # Multi-organization data
            org_financials=org_financials,
            
            # Chart data
            monthly_income=monthly_income,
            monthly_expenses=monthly_expenses,
            monthly_deductible=monthly_deductible,
            monthly_non_deductible=monthly_non_deductible,
            monthly_cash_flow=monthly_cash_flow,
            income_data=[s['amount'] for s in income_sources]
        )
    except Exception as e:
        logging.error(f"Error in analytics redesign: {str(e)}")
        return render_template('analytics_redesign.html', error=str(e))