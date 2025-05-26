"""
Reconciliation Review Routes
UX for reviewing and managing auto-reconciliation matches
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import db
from sqlalchemy import text
import logging
from matching_engine import MatchingEngine
from ml_siamese_matcher import MLMatcher
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

reconciliation = Blueprint('reconciliation', __name__, url_prefix='/reconcile')

@reconciliation.route('/review')
@login_required
def review_matches():
    """Display pending matches for manual review (70-95% confidence)"""
    try:
        # Get user's current organization
        user_orgs = [org.organization_id for org in current_user.organization_memberships]
        
        if not user_orgs:
            flash('No organizations found', 'error')
            return redirect(url_for('app.dashboard'))
        
        org_id = request.args.get('org_id', user_orgs[0])
        if int(org_id) not in user_orgs:
            flash('Access denied to this organization', 'error')
            return redirect(url_for('app.dashboard'))
        
        # Get pending matches for review
        pending_matches = get_pending_matches(org_id)
        
        # Get organization name
        org_result = db.session.execute(text("""
            SELECT name FROM organization WHERE id = :org_id
        """), {'org_id': org_id})
        org_name = org_result.fetchone()[0] if org_result.fetchone() else "Unknown"
        
        return render_template('reconciliation/review.html',
                             pending_matches=pending_matches,
                             organization_id=org_id,
                             organization_name=org_name)
        
    except Exception as e:
        logger.error(f"Error loading review page: {e}")
        flash('Error loading reconciliation review', 'error')
        return redirect(url_for('app.dashboard'))

@reconciliation.route('/api/accept_match', methods=['POST'])
@login_required
def accept_match():
    """Accept a proposed match and update training data"""
    try:
        data = request.get_json()
        match_id = data.get('match_id')
        
        if not match_id:
            return jsonify({'error': 'Match ID required'}), 400
        
        # Verify user has access to this match
        user_orgs = [org.organization_id for org in current_user.organization_memberships]
        
        match_result = db.session.execute(text("""
            SELECT organization_id, ml_features, bank_transaction_id, expense_transaction_id
            FROM transaction_match 
            WHERE id = :match_id AND organization_id = ANY(:org_ids)
        """), {'match_id': match_id, 'org_ids': user_orgs})
        
        match_row = match_result.fetchone()
        if not match_row:
            return jsonify({'error': 'Match not found or access denied'}), 404
        
        # Update match status
        db.session.execute(text("""
            UPDATE transaction_match 
            SET match_status = 'accepted',
                reviewed_by = :user_id,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE id = :match_id
        """), {'match_id': match_id, 'user_id': current_user.id})
        
        # Update transaction status
        db.session.execute(text("""
            UPDATE financial_transaction 
            SET match_status = 'matched',
                auto_matched = FALSE,
                last_matched_at = NOW()
            WHERE id = :txn_id
        """), {'txn_id': match_row.bank_transaction_id})
        
        # Store training data for ML improvement
        if match_row.ml_features:
            db.session.execute(text("""
                INSERT INTO ml_training_data (
                    match_id, feature_vector, label, organization_id
                ) VALUES (:match_id, :features, 1, :org_id)
            """), {
                'match_id': match_id,
                'features': match_row.ml_features,
                'org_id': match_row.organization_id
            })
        
        db.session.commit()
        
        logger.info(f"Match {match_id} accepted by user {current_user.id}")
        return jsonify({'success': True, 'message': 'Match accepted successfully'})
        
    except Exception as e:
        logger.error(f"Error accepting match: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to accept match'}), 500

@reconciliation.route('/api/reject_match', methods=['POST'])
@login_required
def reject_match():
    """Reject a proposed match and update training data"""
    try:
        data = request.get_json()
        match_id = data.get('match_id')
        
        if not match_id:
            return jsonify({'error': 'Match ID required'}), 400
        
        # Verify user has access to this match
        user_orgs = [org.organization_id for org in current_user.organization_memberships]
        
        match_result = db.session.execute(text("""
            SELECT organization_id, ml_features
            FROM transaction_match 
            WHERE id = :match_id AND organization_id = ANY(:org_ids)
        """), {'match_id': match_id, 'org_ids': user_orgs})
        
        match_row = match_result.fetchone()
        if not match_row:
            return jsonify({'error': 'Match not found or access denied'}), 404
        
        # Update match status
        db.session.execute(text("""
            UPDATE transaction_match 
            SET match_status = 'rejected',
                reviewed_by = :user_id,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE id = :match_id
        """), {'match_id': match_id, 'user_id': current_user.id})
        
        # Store training data for ML improvement
        if match_row.ml_features:
            db.session.execute(text("""
                INSERT INTO ml_training_data (
                    match_id, feature_vector, label, organization_id
                ) VALUES (:match_id, :features, 0, :org_id)
            """), {
                'match_id': match_id,
                'features': match_row.ml_features,
                'org_id': match_row.organization_id
            })
        
        db.session.commit()
        
        logger.info(f"Match {match_id} rejected by user {current_user.id}")
        return jsonify({'success': True, 'message': 'Match rejected successfully'})
        
    except Exception as e:
        logger.error(f"Error rejecting match: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to reject match'}), 500

@reconciliation.route('/api/run_auto_match', methods=['POST'])
@login_required
def run_auto_reconciliation():
    """Trigger auto-reconciliation process for the organization"""
    try:
        data = request.get_json()
        org_id = data.get('organization_id')
        
        # Verify user has access to this organization
        user_orgs = [org.organization_id for org in current_user.organization_memberships]
        
        if int(org_id) not in user_orgs:
            return jsonify({'error': 'Access denied to this organization'}), 403
        
        # Run the matching engine
        engine = MatchingEngine(org_id)
        stats = engine.run_auto_reconciliation()
        
        # Also run ML matching if model is available
        ml_matcher = MLMatcher(org_id)
        ml_matches = ml_matcher.find_ml_matches()
        
        # Store ML matches for review
        ml_stored = 0
        for ml_match in ml_matches:
            if 70.0 <= ml_match['match_score'] < 95.0:
                try:
                    db.session.execute(text("""
                        INSERT INTO transaction_match (
                            bank_transaction_id,
                            expense_transaction_id,
                            match_type,
                            match_score,
                            confidence_tier,
                            match_status,
                            match_rules_applied,
                            ml_features,
                            organization_id
                        ) VALUES (
                            :bank_txn_id,
                            :expense_txn_id,
                            :match_type,
                            :match_score,
                            :confidence_tier,
                            'proposed',
                            :rules_applied,
                            :features,
                            :org_id
                        )
                    """), {
                        'bank_txn_id': ml_match['bank_txn_id'],
                        'expense_txn_id': ml_match['expense_txn_id'],
                        'match_type': ml_match['match_type'],
                        'match_score': ml_match['match_score'],
                        'confidence_tier': ml_match['confidence_tier'],
                        'rules_applied': str(ml_match['rules_applied']),
                        'features': str(ml_match['features']),
                        'org_id': org_id
                    })
                    ml_stored += 1
                except Exception as e:
                    logger.error(f"Error storing ML match: {e}")
                    continue
        
        db.session.commit()
        
        combined_stats = {
            **stats,
            'ml_matches_found': len(ml_matches),
            'ml_matches_stored': ml_stored
        }
        
        logger.info(f"Auto-reconciliation completed for org {org_id}: {combined_stats}")
        return jsonify({
            'success': True,
            'message': 'Auto-reconciliation completed',
            'stats': combined_stats
        })
        
    except Exception as e:
        logger.error(f"Error running auto-reconciliation: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to run auto-reconciliation'}), 500

@reconciliation.route('/api/train_ml_model', methods=['POST'])
@login_required
def train_ml_model():
    """Trigger ML model training for the organization"""
    try:
        data = request.get_json()
        org_id = data.get('organization_id')
        
        # Verify user has access to this organization
        user_orgs = [org.organization_id for org in current_user.organization_memberships]
        
        if int(org_id) not in user_orgs:
            return jsonify({'error': 'Access denied to this organization'}), 403
        
        # Check if we have enough training data
        training_count_result = db.session.execute(text("""
            SELECT COUNT(*) FROM ml_training_data 
            WHERE organization_id = :org_id
        """), {'org_id': org_id})
        
        training_count = training_count_result.fetchone()[0]
        
        if training_count < 50:
            return jsonify({
                'error': 'Insufficient training data',
                'message': f'Need at least 50 training examples, have {training_count}',
                'current_count': training_count
            }), 400
        
        # Train the ML model
        ml_matcher = MLMatcher(org_id)
        training_stats = ml_matcher.train_model(epochs=50, batch_size=16)
        
        logger.info(f"ML model training completed for org {org_id}: {training_stats}")
        return jsonify({
            'success': True,
            'message': 'ML model training completed',
            'stats': training_stats
        })
        
    except Exception as e:
        logger.error(f"Error training ML model: {e}")
        return jsonify({'error': 'Failed to train ML model'}), 500

def get_pending_matches(org_id: int) -> list:
    """Get pending matches for review interface"""
    
    result = db.session.execute(text("""
        SELECT 
            tm.id as match_id,
            tm.match_score,
            tm.match_type,
            tm.confidence_tier,
            tm.created_at,
            
            -- Bank transaction details
            ft.id as bank_txn_id,
            ft.amount as bank_amount,
            ft.transaction_date as bank_date,
            ft.description as bank_description,
            bs.bank_name,
            
            -- Expense transaction details
            ce.id as expense_txn_id,
            ce.amount as expense_amount,
            ce.expense_date,
            ce.description as expense_description,
            ce.category,
            ce.minor_category
            
        FROM transaction_match tm
        JOIN financial_transaction ft ON tm.bank_transaction_id = ft.id
        JOIN company_expense ce ON tm.expense_transaction_id = ce.id
        JOIN bank_statement bs ON ft.bank_statement_id = bs.id
        
        WHERE tm.organization_id = :org_id
        AND tm.match_status = 'proposed'
        AND tm.match_score BETWEEN 70.0 AND 94.9
        
        ORDER BY tm.match_score DESC, tm.created_at DESC
        LIMIT 50
    """), {'org_id': org_id})
    
    matches = []
    for row in result.fetchall():
        row_dict = dict(row._mapping)
        matches.append(row_dict)
    
    return matches