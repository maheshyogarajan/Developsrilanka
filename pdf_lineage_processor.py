"""
PDF Lineage Processor - Storage-Lineage Overlay Implementation
Captures PDF coordinates and confidence scores for visual verification
"""

import re
import logging
import PyPDF2
from typing import List, Dict, Tuple, Optional
from io import BytesIO
from decimal import Decimal

logger = logging.getLogger(__name__)

class PDFLineageProcessor:
    """Enhanced PDF processor that captures coordinate information for storage-lineage overlay"""
    
    def __init__(self):
        self.extraction_events = []
        
    def extract_with_coordinates(self, pdf_data: bytes) -> Dict:
        """
        Extract transactions with PDF coordinate tracking
        Returns both transaction data and lineage information
        """
        try:
            pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_data))
            
            results = {
                'transactions': [],
                'lineage_events': [],
                'page_count': len(pdf_reader.pages)
            }
            
            for page_idx, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                page_lineage = self._extract_page_lineage(page_text, page_idx)
                results['lineage_events'].extend(page_lineage)
                
                # Extract transactions from this page
                page_transactions = self._extract_transactions_from_page(page_text, page_idx)
                results['transactions'].extend(page_transactions)
            
            logger.info(f"Extracted {len(results['transactions'])} transactions with {len(results['lineage_events'])} lineage events")
            return results
            
        except Exception as e:
            logger.error(f"PDF lineage extraction failed: {e}")
            return {'transactions': [], 'lineage_events': [], 'page_count': 0}
    
    def _extract_page_lineage(self, page_text: str, page_idx: int) -> List[Dict]:
        """Extract lineage events from a single page with estimated coordinates"""
        lineage_events = []
        
        # Split text into lines for coordinate estimation
        lines = page_text.split('\n')
        
        for line_idx, line in enumerate(lines):
            # Estimate vertical position based on line number
            estimated_top = line_idx * 12  # Assume 12 points per line
            
            # Look for amounts in this line
            amount_matches = list(re.finditer(r'\b\d{1,3}(?:,\d{3})*\.?\d{0,2}\b', line))
            for match in amount_matches:
                # Estimate horizontal position
                char_position = match.start()
                estimated_x0 = char_position * 7  # Assume 7 points per character
                estimated_x1 = estimated_x0 + (len(match.group()) * 7)
                
                lineage_events.append({
                    'extraction_type': 'amount',
                    'bbox': {
                        'x0': estimated_x0,
                        'top': estimated_top,
                        'x1': estimated_x1,
                        'bottom': estimated_top + 12
                    },
                    'page_idx': page_idx,
                    'source_text': match.group(),
                    'confidence': self._calculate_confidence(match.group(), 'amount'),
                    'parser_method': 'regex_pattern',
                    'line_text': line.strip()
                })
            
            # Look for dates in this line
            date_matches = list(re.finditer(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', line))
            for match in date_matches:
                char_position = match.start()
                estimated_x0 = char_position * 7
                estimated_x1 = estimated_x0 + (len(match.group()) * 7)
                
                lineage_events.append({
                    'extraction_type': 'date',
                    'bbox': {
                        'x0': estimated_x0,
                        'top': estimated_top,
                        'x1': estimated_x1,
                        'bottom': estimated_top + 12
                    },
                    'page_idx': page_idx,
                    'source_text': match.group(),
                    'confidence': self._calculate_confidence(match.group(), 'date'),
                    'parser_method': 'regex_pattern',
                    'line_text': line.strip()
                })
        
        return lineage_events
    
    def _extract_transactions_from_page(self, page_text: str, page_idx: int) -> List[Dict]:
        """Extract transaction data from page text"""
        transactions = []
        lines = page_text.split('\n')
        
        for line_idx, line in enumerate(lines):
            # Simple transaction pattern matching
            # Look for lines with date and amount
            date_match = re.search(r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b', line)
            amount_matches = list(re.finditer(r'\b(\d{1,3}(?:,\d{3})*\.?\d{0,2})\b', line))
            
            if date_match and amount_matches:
                for amount_match in amount_matches:
                    try:
                        amount_str = amount_match.group().replace(',', '')
                        amount = Decimal(amount_str)
                        
                        # Only consider reasonable transaction amounts
                        if 0.01 <= amount <= 10000000:
                            transactions.append({
                                'date': date_match.group(),
                                'amount': float(amount),
                                'description': line.strip(),
                                'page_idx': page_idx,
                                'line_idx': line_idx,
                                'confidence': self._calculate_confidence(amount_str, 'amount')
                            })
                    except:
                        continue
        
        return transactions
    
    def _calculate_confidence(self, text: str, extraction_type: str) -> float:
        """Calculate confidence score for extracted text"""
        confidence = 0.5  # Base confidence
        
        if extraction_type == 'amount':
            # Higher confidence for properly formatted amounts
            if re.match(r'^\d{1,3}(?:,\d{3})*\.\d{2}$', text):
                confidence = 0.95
            elif ',' in text and '.' in text:
                confidence = 0.85
            elif '.' in text:
                confidence = 0.75
            else:
                confidence = 0.65
                
        elif extraction_type == 'date':
            # Higher confidence for standard date formats
            if re.match(r'^\d{2}/\d{2}/\d{4}$', text):
                confidence = 0.90
            elif re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', text):
                confidence = 0.80
            else:
                confidence = 0.60
        
        return confidence
    
    def get_lineage_for_transaction(self, transaction_id: int, bbox_data: Dict) -> Dict:
        """Get lineage information for a specific transaction"""
        return {
            'transaction_id': transaction_id,
            'bbox': bbox_data.get('bbox', {}),
            'page_idx': bbox_data.get('page_idx', 0),
            'confidence': bbox_data.get('confidence', 0.0),
            'source_text': bbox_data.get('source_text', ''),
            'parser_method': bbox_data.get('parser_method', 'regex'),
            'extraction_type': bbox_data.get('extraction_type', 'amount')
        }