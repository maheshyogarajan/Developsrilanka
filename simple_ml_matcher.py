"""
Simplified ML Matcher for Transaction Reconciliation
Uses scikit-learn for lightweight machine learning without PyTorch dependency
"""

import logging
import numpy as np
import json
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score
import pickle
from pathlib import Path
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)

class TransactionFeatureExtractor:
    """Extract numerical features from transaction pairs for ML model"""
    
    def __init__(self):
        self.feature_names = [
            'amount_diff_abs', 'amount_diff_pct', 'amount_ratio',
            'date_diff_days', 'same_day', 'same_week', 'same_month',
            'desc_word_overlap', 'desc_jaccard_similarity',
            'day_of_week_diff', 'amount_round_match'
        ]
    
    def extract_features(self, bank_txn: Dict, expense_txn: Dict) -> np.ndarray:
        """Extract feature vector from transaction pair"""
        
        features = []
        
        # Amount-based features
        bank_amount = float(bank_txn.get('amount', 0))
        expense_amount = float(expense_txn.get('amount', 0))
        
        amount_diff_abs = abs(bank_amount - expense_amount)
        amount_diff_pct = amount_diff_abs / max(bank_amount, expense_amount, 1)
        amount_ratio = min(bank_amount, expense_amount) / max(bank_amount, expense_amount, 1)
        
        features.extend([amount_diff_abs, amount_diff_pct, amount_ratio])
        
        # Date-based features
        bank_date = bank_txn.get('transaction_date', datetime.now())
        expense_date = expense_txn.get('expense_date', datetime.now())
        
        if isinstance(bank_date, str):
            bank_date = datetime.fromisoformat(bank_date)
        if isinstance(expense_date, str):
            expense_date = datetime.fromisoformat(expense_date)
        
        date_diff = abs((bank_date - expense_date).total_seconds())
        date_diff_days = date_diff / (24 * 3600)
        
        same_day = 1.0 if date_diff_days < 1 else 0.0
        same_week = 1.0 if date_diff_days < 7 else 0.0
        same_month = 1.0 if date_diff_days < 30 else 0.0
        
        features.extend([date_diff_days, same_day, same_week, same_month])
        
        # Description-based features
        bank_desc = str(bank_txn.get('description', '')).lower()
        expense_desc = str(expense_txn.get('description', '')).lower()
        
        bank_words = set(bank_desc.split())
        expense_words = set(expense_desc.split())
        
        word_overlap = len(bank_words.intersection(expense_words))
        desc_jaccard = word_overlap / len(bank_words.union(expense_words)) if bank_words.union(expense_words) else 0
        
        features.extend([word_overlap, desc_jaccard])
        
        # Temporal features
        day_of_week_diff = abs(bank_date.weekday() - expense_date.weekday())
        features.append(day_of_week_diff)
        
        # Amount pattern features
        amount_round_match = 1.0 if (bank_amount % 1 == 0) == (expense_amount % 1 == 0) else 0.0
        features.append(amount_round_match)
        
        return np.array(features, dtype=np.float32)

class SimplifiedMLMatcher:
    """Simplified ML-based transaction matcher using Random Forest"""
    
    def __init__(self, organization_id: int, model_path: str = "models/rf_matcher.pkl"):
        self.organization_id = organization_id
        self.model_path = model_path
        self.scaler_path = model_path.replace('.pkl', '_scaler.pkl')
        self.feature_extractor = TransactionFeatureExtractor()
        self.model = None
        self.scaler = None
        
        # Load pre-trained model if available
        self._load_model()
    
    def _load_model(self):
        """Load pre-trained Random Forest model and scaler"""
        try:
            if Path(self.model_path).exists() and Path(self.scaler_path).exists():
                with open(self.model_path, 'rb') as f:
                    self.model = pickle.load(f)
                with open(self.scaler_path, 'rb') as f:
                    self.scaler = pickle.load(f)
                logger.info(f"Loaded ML model from {self.model_path}")
            else:
                logger.info("No pre-trained model found, will train on first use")
                self.model = None
                self.scaler = None
        except Exception as e:
            logger.error(f"Error loading ML model: {e}")
            self.model = None
            self.scaler = None
    
    def find_ml_matches(self, date_range_days: int = 30) -> List[Dict]:
        """
        Find ML-based matches for transactions
        
        Args:
            date_range_days: Look for matches within this many days
            
        Returns:
            List of ML match candidates with scores 0-100
        """
        if self.model is None or self.scaler is None:
            logger.info("No ML model available, attempting to train...")
            training_result = self.train_model()
            
            if not training_result.get('success', False):
                logger.warning("ML model training failed, skipping ML matching")
                return []
        
        matches = []
        
        # Get unmatched transactions
        bank_txns = self._get_unmatched_transactions('bank', date_range_days)
        expense_txns = self._get_unmatched_transactions('expense', date_range_days)
        
        logger.info(f"Running ML matching on {len(bank_txns)} bank txns vs {len(expense_txns)} expenses")
        
        for bank_txn in bank_txns:
            candidate_matches = self._score_transaction_pairs(bank_txn, expense_txns)
            
            # Filter matches with score >= 70%
            good_matches = [m for m in candidate_matches if m['match_score'] >= 70.0]
            matches.extend(good_matches[:3])  # Top 3 matches per transaction
        
        return matches
    
    def _get_unmatched_transactions(self, txn_type: str, date_range_days: int) -> List[Dict]:
        """Get unmatched transactions of specified type"""
        
        cutoff_date = datetime.now() - timedelta(days=date_range_days)
        
        if txn_type == 'bank':
            result = db.session.execute(text("""
                SELECT 
                    ft.id,
                    ft.amount,
                    ft.transaction_date,
                    ft.description,
                    bs.bank_name
                FROM financial_transaction ft
                JOIN bank_statement bs ON ft.bank_statement_id = bs.id
                WHERE bs.organization_id = :org_id
                AND ft.match_status IN ('pending', 'unmatched')
                AND ft.transaction_date >= :cutoff_date
                ORDER BY ft.transaction_date DESC
                LIMIT 100
            """), {'org_id': self.organization_id, 'cutoff_date': cutoff_date})
        else:
            result = db.session.execute(text("""
                SELECT 
                    ce.id,
                    ce.amount,
                    ce.expense_date,
                    ce.description,
                    ce.category,
                    ce.minor_category
                FROM company_expense ce
                WHERE ce.organization_id = :org_id
                AND ce.expense_date >= :cutoff_date
                AND ce.id NOT IN (
                    SELECT DISTINCT expense_transaction_id 
                    FROM transaction_match 
                    WHERE match_status = 'accepted'
                    AND expense_transaction_id IS NOT NULL
                )
                ORDER BY ce.expense_date DESC
                LIMIT 100
            """), {'org_id': self.organization_id, 'cutoff_date': cutoff_date})
        
        return [dict(row._mapping) for row in result.fetchall()]
    
    def _score_transaction_pairs(self, bank_txn: Dict, expense_txns: List[Dict]) -> List[Dict]:
        """Score all possible pairs between a bank transaction and expenses"""
        
        matches = []
        
        for expense_txn in expense_txns:
            try:
                # Extract features
                features = self.feature_extractor.extract_features(bank_txn, expense_txn)
                
                # Scale features
                features_scaled = self.scaler.transform([features])
                
                # Get ML prediction and probability
                prediction = self.model.predict(features_scaled)[0]
                probabilities = self.model.predict_proba(features_scaled)[0]
                
                # Use probability of positive class as score
                ml_score = float(probabilities[1]) * 100  # Convert to 0-100 scale
                
                if ml_score >= 70.0:
                    matches.append({
                        'bank_txn_id': bank_txn['id'],
                        'expense_txn_id': expense_txn['id'],
                        'match_score': ml_score,
                        'match_type': 'ml_model',
                        'confidence_tier': 'tier2',
                        'features': features.tolist(),
                        'rules_applied': ['random_forest']
                    })
                    
            except Exception as e:
                logger.error(f"Error scoring transaction pair: {e}")
                continue
        
        # Sort by score
        matches.sort(key=lambda x: x['match_score'], reverse=True)
        return matches
    
    def train_model(self, test_size: float = 0.2) -> Dict:
        """
        Train the Random Forest model on organization's historical match data
        
        Args:
            test_size: Fraction of data to use for testing
            
        Returns:
            Training statistics
        """
        logger.info(f"Starting ML model training for organization {self.organization_id}")
        
        # Load training data
        training_data = self._load_training_data()
        
        if len(training_data) < 20:
            logger.warning(f"Insufficient training data: {len(training_data)} examples (need at least 20)")
            return {'success': False, 'error': 'insufficient_data', 'data_points': len(training_data)}
        
        # Prepare data
        X = np.array([features for features, _ in training_data])
        y = np.array([label for _, label in training_data])
        
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )
        
        # Scale features
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        
        # Train Random Forest model
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=42
        )
        
        self.model.fit(X_train_scaled, y_train)
        
        # Evaluate model
        train_predictions = self.model.predict(X_train_scaled)
        test_predictions = self.model.predict(X_test_scaled)
        
        train_accuracy = accuracy_score(y_train, train_predictions)
        test_accuracy = accuracy_score(y_test, test_predictions)
        test_precision = precision_score(y_test, test_predictions, zero_division=0)
        test_recall = recall_score(y_test, test_predictions, zero_division=0)
        
        # Save trained model
        self._save_model()
        
        stats = {
            'success': True,
            'training_data_points': len(training_data),
            'train_accuracy': train_accuracy,
            'test_accuracy': test_accuracy,
            'test_precision': test_precision,
            'test_recall': test_recall,
            'model_saved': True
        }
        
        logger.info(f"ML model training completed: {stats}")
        return stats
    
    def _load_training_data(self) -> List[Tuple]:
        """Load training data from accepted/rejected matches"""
        
        result = db.session.execute(text("""
            SELECT 
                tm.match_status,
                ft.id as bank_id,
                ft.amount as bank_amount,
                ft.transaction_date,
                ft.description as bank_desc,
                bs.bank_name,
                ce.id as expense_id,
                ce.amount as expense_amount,
                ce.expense_date,
                ce.description as expense_desc,
                ce.category,
                ce.minor_category
            FROM transaction_match tm
            JOIN financial_transaction ft ON tm.bank_transaction_id = ft.id
            JOIN company_expense ce ON tm.expense_transaction_id = ce.id
            JOIN bank_statement bs ON ft.bank_statement_id = bs.id
            WHERE tm.organization_id = :org_id
            AND tm.match_status IN ('accepted', 'rejected')
            ORDER BY tm.created_at DESC
            LIMIT 500
        """), {'org_id': self.organization_id})
        
        training_examples = []
        
        for row in result.fetchall():
            row_dict = dict(row._mapping)
            
            # Create feature vector
            bank_txn = {
                'id': row_dict['bank_id'],
                'amount': row_dict['bank_amount'],
                'transaction_date': row_dict['transaction_date'],
                'description': row_dict['bank_desc'],
                'bank_name': row_dict['bank_name']
            }
            
            expense_txn = {
                'id': row_dict['expense_id'],
                'amount': row_dict['expense_amount'],
                'expense_date': row_dict['expense_date'],
                'description': row_dict['expense_desc'],
                'category': row_dict['category'],
                'minor_category': row_dict['minor_category']
            }
            
            features = self.feature_extractor.extract_features(bank_txn, expense_txn)
            label = 1 if row_dict['match_status'] == 'accepted' else 0
            
            training_examples.append((features, label))
        
        return training_examples
    
    def _save_model(self):
        """Save trained model and scaler to disk"""
        
        Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Save model
        with open(self.model_path, 'wb') as f:
            pickle.dump(self.model, f)
        
        # Save scaler
        with open(self.scaler_path, 'wb') as f:
            pickle.dump(self.scaler, f)
        
        logger.info(f"Model and scaler saved to {self.model_path}")