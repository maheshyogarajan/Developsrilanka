"""
Tier-2 ML Siamese Network Matcher for Transaction Reconciliation
PyTorch-based neural network for learning transaction similarity
"""

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import logging
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
import json
import pickle
from pathlib import Path
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)

class SiameseNetwork(nn.Module):
    """Siamese neural network for transaction similarity learning"""
    
    def __init__(self, input_dim: int = 20, hidden_dim: int = 128):
        super(SiameseNetwork, self).__init__()
        
        # Shared embedding network
        self.embedding_net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim // 2, 32),
            nn.ReLU(),
            nn.Linear(32, 16)
        )
        
        # Final similarity scoring layer
        self.similarity_net = nn.Sequential(
            nn.Linear(32, 16),  # 16 + 16 concatenated features
            nn.ReLU(),
            nn.Linear(16, 8),
            nn.ReLU(),
            nn.Linear(8, 1),
            nn.Sigmoid()
        )
    
    def forward_one(self, x):
        """Forward pass for one transaction"""
        return self.embedding_net(x)
    
    def forward(self, x1, x2):
        """Forward pass for transaction pair"""
        embedding1 = self.forward_one(x1)
        embedding2 = self.forward_one(x2)
        
        # Concatenate embeddings and compute similarity
        combined = torch.cat([embedding1, embedding2], dim=1)
        similarity = self.similarity_net(combined)
        
        return similarity

class TransactionFeatureExtractor:
    """Extract numerical features from transaction pairs for ML model"""
    
    def __init__(self):
        self.feature_names = [
            'amount_diff_abs', 'amount_diff_pct', 'amount_ratio',
            'date_diff_days', 'date_diff_hours', 'same_day',
            'same_week', 'same_month',
            'desc_length_diff', 'desc_word_overlap', 'desc_char_overlap',
            'desc_jaccard_similarity', 'desc_contains_keywords',
            'bank_name_encoded', 'category_encoded', 'minor_category_encoded',
            'day_of_week_diff', 'hour_of_day_diff',
            'amount_round_match', 'amount_magnitude_similar'
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
        date_diff_hours = date_diff / 3600
        
        same_day = 1.0 if date_diff_days < 1 else 0.0
        same_week = 1.0 if date_diff_days < 7 else 0.0
        same_month = 1.0 if date_diff_days < 30 else 0.0
        
        features.extend([date_diff_days, date_diff_hours, same_day, same_week, same_month])
        
        # Description-based features
        bank_desc = str(bank_txn.get('description', '')).lower()
        expense_desc = str(expense_txn.get('description', '')).lower()
        
        desc_length_diff = abs(len(bank_desc) - len(expense_desc))
        
        bank_words = set(bank_desc.split())
        expense_words = set(expense_desc.split())
        
        word_overlap = len(bank_words.intersection(expense_words))
        desc_jaccard = word_overlap / len(bank_words.union(expense_words)) if bank_words.union(expense_words) else 0
        
        # Character-level overlap
        bank_chars = set(bank_desc)
        expense_chars = set(expense_desc)
        char_overlap = len(bank_chars.intersection(expense_chars)) / len(bank_chars.union(expense_chars)) if bank_chars.union(expense_chars) else 0
        
        # Keyword matching
        keywords = ['payment', 'transfer', 'purchase', 'refund', 'fee', 'charge']
        desc_contains_keywords = 1.0 if any(kw in bank_desc for kw in keywords) and any(kw in expense_desc for kw in keywords) else 0.0
        
        features.extend([desc_length_diff, word_overlap, char_overlap, desc_jaccard, desc_contains_keywords])
        
        # Categorical encodings (simplified)
        bank_name_encoded = hash(str(bank_txn.get('bank_name', ''))) % 100 / 100.0
        category_encoded = hash(str(expense_txn.get('category', ''))) % 100 / 100.0
        minor_category_encoded = hash(str(expense_txn.get('minor_category', ''))) % 100 / 100.0
        
        features.extend([bank_name_encoded, category_encoded, minor_category_encoded])
        
        # Temporal features
        day_of_week_diff = abs(bank_date.weekday() - expense_date.weekday())
        hour_of_day_diff = abs(bank_date.hour - expense_date.hour)
        
        features.extend([day_of_week_diff, hour_of_day_diff])
        
        # Amount pattern features
        amount_round_match = 1.0 if (bank_amount % 1 == 0) == (expense_amount % 1 == 0) else 0.0
        
        # Similar magnitude (within same order of magnitude)
        bank_magnitude = len(str(int(bank_amount))) if bank_amount > 0 else 0
        expense_magnitude = len(str(int(expense_amount))) if expense_amount > 0 else 0
        amount_magnitude_similar = 1.0 if abs(bank_magnitude - expense_magnitude) <= 1 else 0.0
        
        features.extend([amount_round_match, amount_magnitude_similar])
        
        return np.array(features, dtype=np.float32)

class MLMatcher:
    """Tier-2 ML-based transaction matcher using Siamese network"""
    
    def __init__(self, organization_id: int, model_path: str = "models/siamese_matcher.pt"):
        self.organization_id = organization_id
        self.model_path = model_path
        self.feature_extractor = TransactionFeatureExtractor()
        self.model = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # Load pre-trained model if available
        self._load_model()
    
    def _load_model(self):
        """Load pre-trained Siamese network model"""
        try:
            if Path(self.model_path).exists():
                self.model = SiameseNetwork()
                checkpoint = torch.load(self.model_path, map_location=self.device)
                self.model.load_state_dict(checkpoint['model_state_dict'])
                self.model.to(self.device)
                self.model.eval()
                logger.info(f"Loaded ML model from {self.model_path}")
            else:
                logger.warning(f"No pre-trained model found at {self.model_path}")
                self.model = None
        except Exception as e:
            logger.error(f"Error loading ML model: {e}")
            self.model = None
    
    def find_ml_matches(self, date_range_days: int = 30) -> List[Dict]:
        """
        Find ML-based matches for transactions
        
        Args:
            date_range_days: Look for matches within this many days
            
        Returns:
            List of ML match candidates with scores 0-100
        """
        if self.model is None:
            logger.warning("No ML model available, skipping ML matching")
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
            """), {'org_id': self.organization_id, 'cutoff_date': cutoff_date})
        
        return [dict(row._mapping) for row in result.fetchall()]
    
    def _score_transaction_pairs(self, bank_txn: Dict, expense_txns: List[Dict]) -> List[Dict]:
        """Score all possible pairs between a bank transaction and expenses"""
        
        matches = []
        
        for expense_txn in expense_txns:
            try:
                # Extract features
                features = self.feature_extractor.extract_features(bank_txn, expense_txn)
                
                # Create tensor inputs
                bank_features = torch.tensor(features[:10], dtype=torch.float32).unsqueeze(0).to(self.device)
                expense_features = torch.tensor(features[10:], dtype=torch.float32).unsqueeze(0).to(self.device)
                
                # Get ML similarity score
                with torch.no_grad():
                    similarity = self.model(bank_features, expense_features)
                    ml_score = float(similarity.item()) * 100  # Convert to 0-100 scale
                
                if ml_score >= 70.0:
                    matches.append({
                        'bank_txn_id': bank_txn['id'],
                        'expense_txn_id': expense_txn['id'],
                        'match_score': ml_score,
                        'match_type': 'ml_model',
                        'confidence_tier': 'tier2',
                        'features': features.tolist(),
                        'rules_applied': ['siamese_network']
                    })
                    
            except Exception as e:
                logger.error(f"Error scoring transaction pair: {e}")
                continue
        
        # Sort by score
        matches.sort(key=lambda x: x['match_score'], reverse=True)
        return matches
    
    def train_model(self, epochs: int = 100, batch_size: int = 32) -> Dict:
        """
        Train the Siamese network on organization's historical match data
        
        Args:
            epochs: Number of training epochs
            batch_size: Training batch size
            
        Returns:
            Training statistics
        """
        logger.info(f"Starting ML model training for organization {self.organization_id}")
        
        # Load training data
        training_data = self._load_training_data()
        
        if len(training_data) < 50:
            logger.warning("Insufficient training data (need at least 50 examples)")
            return {'error': 'insufficient_data', 'data_points': len(training_data)}
        
        # Initialize model
        self.model = SiameseNetwork()
        self.model.to(self.device)
        
        # Setup training
        criterion = nn.BCELoss()
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)
        
        # Training loop
        self.model.train()
        training_losses = []
        
        for epoch in range(epochs):
            epoch_loss = 0.0
            
            # Mini-batch training
            for i in range(0, len(training_data), batch_size):
                batch = training_data[i:i + batch_size]
                
                batch_loss = self._train_batch(batch, criterion, optimizer)
                epoch_loss += batch_loss
            
            avg_loss = epoch_loss / (len(training_data) // batch_size + 1)
            training_losses.append(avg_loss)
            
            if epoch % 10 == 0:
                logger.info(f"Epoch {epoch}/{epochs}, Loss: {avg_loss:.4f}")
        
        # Save trained model
        self._save_model()
        
        return {
            'epochs_trained': epochs,
            'final_loss': training_losses[-1],
            'training_data_points': len(training_data),
            'model_saved': True
        }
    
    def _load_training_data(self) -> List[Tuple]:
        """Load training data from accepted/rejected matches"""
        
        result = db.session.execute(text("""
            SELECT 
                tm.match_status,
                tm.ml_features,
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
            LIMIT 1000
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
    
    def _train_batch(self, batch: List[Tuple], criterion, optimizer) -> float:
        """Train on a batch of data"""
        
        optimizer.zero_grad()
        
        batch_loss = 0.0
        
        for features, label in batch:
            # Split features for Siamese input
            bank_features = torch.tensor(features[:10], dtype=torch.float32).unsqueeze(0).to(self.device)
            expense_features = torch.tensor(features[10:], dtype=torch.float32).unsqueeze(0).to(self.device)
            target = torch.tensor([float(label)], dtype=torch.float32).to(self.device)
            
            # Forward pass
            output = self.model(bank_features, expense_features)
            loss = criterion(output, target)
            
            # Backward pass
            loss.backward()
            batch_loss += loss.item()
        
        optimizer.step()
        
        return batch_loss / len(batch)
    
    def _save_model(self):
        """Save trained model to disk"""
        
        Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
        
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'organization_id': self.organization_id,
            'trained_at': datetime.now().isoformat(),
            'feature_names': self.feature_extractor.feature_names
        }, self.model_path)
        
        logger.info(f"Model saved to {self.model_path}")