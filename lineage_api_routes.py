"""
API Routes for PDF Storage-Lineage Overlay
Provides endpoints for retrieving PDF coordinate data and confidence scores
"""

from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from app import db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

# Create blueprint for lineage API
lineage_api = Blueprint('lineage_api', __name__, url_prefix='/api/lineage')

@lineage_api.route('/transaction/<int:txn_id>', methods=['GET'])
@login_required
def get_transaction_lineage(txn_id):
    """
    Get PDF lineage information for a specific transaction
    Returns bbox coordinates, page index, confidence score
    """
    try:
        # Query transaction with lineage data
        result = db.session.execute(text("""
            SELECT 
                ft.id,
                ft.amount,
                ft.description,
                ft.bbox,
                ft.page_idx,
                ft.extraction_confidence,
                ft.source_text,
                bs.id as statement_id,
                bs.bank_name
            FROM financial_transaction ft
            JOIN bank_statement bs ON ft.bank_statement_id = bs.id
            WHERE ft.id = :txn_id 
            AND bs.organization_id IN (
                SELECT organization_id FROM organization_user 
                WHERE user_id = :user_id
            )
        """), {'txn_id': txn_id, 'user_id': current_user.id})
        
        transaction = result.fetchone()
        
        if not transaction:
            return jsonify({'error': 'Transaction not found or access denied'}), 404
        
        # Get extraction events for this transaction
        events_result = db.session.execute(text("""
            SELECT 
                extraction_type,
                bbox,
                page_idx,
                confidence,
                source_text,
                parser_method,
                rule_id
            FROM extraction_event
            WHERE transaction_id = :txn_id
            ORDER BY created_at
        """), {'txn_id': txn_id})
        
        events = [dict(row._mapping) for row in events_result.fetchall()]
        
        return jsonify({
            'transaction_id': transaction.id,
            'amount': float(transaction.amount) if transaction.amount else 0,
            'description': transaction.description,
            'bbox': transaction.bbox,
            'page_idx': transaction.page_idx or 0,
            'confidence': transaction.extraction_confidence or 0.0,
            'source_text': transaction.source_text,
            'statement_id': transaction.statement_id,
            'bank_name': transaction.bank_name,
            'extraction_events': events
        })
        
    except Exception as e:
        logger.error(f"Error retrieving lineage for transaction {txn_id}: {e}")
        return jsonify({'error': 'Failed to retrieve lineage data'}), 500

@lineage_api.route('/statement/<int:statement_id>/pdf', methods=['GET'])
@login_required
def get_statement_pdf_url(statement_id):
    """
    Get PDF URL for a bank statement (for pdf.js rendering)
    """
    try:
        # Verify access to statement
        result = db.session.execute(text("""
            SELECT id, bank_name
            FROM bank_statement 
            WHERE id = :statement_id 
            AND organization_id IN (
                SELECT organization_id FROM organization_user 
                WHERE user_id = :user_id
            )
        """), {'statement_id': statement_id, 'user_id': current_user.id})
        
        statement = result.fetchone()
        
        if not statement:
            return jsonify({'error': 'Statement not found or access denied'}), 404
        
        # Generate S3 URL for PDF
        pdf_url = f"/api/statement/{statement_id}/pdf"  # Internal PDF serving endpoint
        
        return jsonify({
            'statement_id': statement_id,
            'pdf_url': pdf_url,
            'bank_name': statement.bank_name
        })
        
    except Exception as e:
        logger.error(f"Error retrieving PDF URL for statement {statement_id}: {e}")
        return jsonify({'error': 'Failed to retrieve PDF URL'}), 500

@lineage_api.route('/statement/<int:statement_id>/transactions', methods=['GET'])
@login_required
def get_statement_transactions_lineage(statement_id):
    """
    Get all transactions with lineage data for a statement
    """
    try:
        # Get all transactions for statement with lineage
        result = db.session.execute(text("""
            SELECT 
                ft.id,
                ft.amount,
                ft.description,
                ft.bbox,
                ft.page_idx,
                ft.extraction_confidence,
                ft.source_text
            FROM financial_transaction ft
            JOIN bank_statement bs ON ft.bank_statement_id = bs.id
            WHERE bs.id = :statement_id 
            AND bs.organization_id IN (
                SELECT organization_id FROM organization_user 
                WHERE user_id = :user_id
            )
            ORDER BY ft.transaction_date, ft.id
        """), {'statement_id': statement_id, 'user_id': current_user.id})
        
        transactions = []
        for row in result.fetchall():
            transactions.append({
                'id': row.id,
                'amount': float(row.amount) if row.amount else 0,
                'description': row.description,
                'bbox': row.bbox,
                'page_idx': row.page_idx or 0,
                'confidence': row.extraction_confidence or 0.0,
                'source_text': row.source_text
            })
        
        return jsonify({
            'statement_id': statement_id,
            'transactions': transactions,
            'total_count': len(transactions)
        })
        
    except Exception as e:
        logger.error(f"Error retrieving transactions lineage for statement {statement_id}: {e}")
        return jsonify({'error': 'Failed to retrieve transactions lineage'}), 500

@lineage_api.route('/overlay/demo', methods=['GET'])
@login_required
def get_overlay_demo_data():
    """
    Get demo data for testing PDF overlay functionality
    """
    try:
        # Return sample bbox data for testing
        demo_data = {
            'transactions': [
                {
                    'id': 'demo_1',
                    'bbox': {'x0': 100, 'top': 200, 'x1': 200, 'bottom': 220},
                    'page_idx': 0,
                    'confidence': 0.95,
                    'amount': 1500.00,
                    'description': 'Sample Transaction 1'
                },
                {
                    'id': 'demo_2', 
                    'bbox': {'x0': 100, 'top': 240, 'x1': 200, 'bottom': 260},
                    'page_idx': 0,
                    'confidence': 0.88,
                    'amount': 750.50,
                    'description': 'Sample Transaction 2'
                }
            ],
            'page_count': 1
        }
        
        return jsonify(demo_data)
        
    except Exception as e:
        logger.error(f"Error generating demo data: {e}")
        return jsonify({'error': 'Failed to generate demo data'}), 500