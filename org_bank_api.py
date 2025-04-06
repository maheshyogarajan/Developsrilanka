@bank_account_bp.route('/api/organizations/<int:org_id>/bank-accounts')
@login_required
def api_organization_bank_accounts(org_id):
    """API endpoint to get bank accounts for a specific organization."""
    # Check if user has access to this organization
    org_user = OrganizationUser.query.filter_by(
        user_id=current_user.id,
        organization_id=org_id
    ).first()
    
    if not org_user:
        return jsonify({'error': 'Access denied'}), 403
    
    # Get all bank accounts for this organization
    result = db.session.execute(text("""
        SELECT id, user_id, organization_id, account_name, bank_name, account_number, 
               branch_name, swift_code, iban, is_default
        FROM bank_account 
        WHERE organization_id = :org_id
        ORDER BY is_default DESC, account_name ASC
    """), {'org_id': org_id})
    
    # Convert to list of dictionaries
    bank_accounts = []
    for row in result:
        bank_accounts.append({
            'id': row[0],
            'user_id': row[1],
            'organization_id': row[2],
            'account_name': row[3],
            'bank_name': row[4],
            'account_number': row[5],
            'branch_name': row[6],
            'swift_code': row[7],
            'iban': row[8],
            'is_default': row[9]
        })
    
    return jsonify({'bank_accounts': bank_accounts})
