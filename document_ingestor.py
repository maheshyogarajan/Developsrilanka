"""
DocumentIngestor - Smart PDF processing with library selection and complete audit trail.
Replaces PyPDF2 with intelligent library routing based on document characteristics.
"""

import io
import json
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import logging

import pdfplumber
import camelot
import pandas as pd
from PIL import Image

from document_processing_models import DocContext, ExtractionResult, ExtractionEvent

logger = logging.getLogger(__name__)


class DocumentIngestor:
    """
    Smart PDF processor that selects optimal extraction method based on document characteristics.
    Routes to pdfplumber, Camelot, or Cloud OCR as needed.
    """
    
    def __init__(self, enable_ocr: bool = False):
        self.enable_ocr = enable_ocr
        self.extraction_stats = {
            'pdfplumber_attempts': 0,
            'camelot_attempts': 0,
            'ocr_attempts': 0,
            'successful_extractions': 0
        }
    
    def process_document(self, pdf_data: bytes, bank_hint: str = None) -> ExtractionResult:
        """
        Main entry point for document processing.
        
        Args:
            pdf_data: Raw PDF bytes
            bank_hint: Optional bank name for context
            
        Returns:
            ExtractionResult with complete audit trail
        """
        logger.info(f"Starting document ingestion with {len(pdf_data)} bytes")
        
        # Step 1: Analyze document characteristics
        doc_analysis = self._analyze_document(pdf_data)
        
        # Step 2: Create document context
        context = self._create_document_context(doc_analysis, bank_hint)
        
        # Step 3: Select optimal extraction method
        extraction_method = self._select_extraction_method(doc_analysis)
        context.extraction_method = extraction_method
        
        # Step 4: Extract data using selected method
        raw_extractions = self._extract_with_method(pdf_data, extraction_method, context)
        
        # Step 5: Create extraction result with audit trail
        result = ExtractionResult(
            transactions=raw_extractions['transactions'],
            context=context,
            events=raw_extractions['events'],
            statistics=raw_extractions['statistics']
        )
        
        logger.info(f"Document ingestion complete: {len(result.transactions)} transactions extracted")
        return result
    
    def _analyze_document(self, pdf_data: bytes) -> Dict[str, Any]:
        """Analyze PDF characteristics to determine optimal processing approach."""
        try:
            with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
                analysis = {
                    'page_count': len(pdf.pages),
                    'is_encrypted': pdf.metadata.get('encrypted', False),
                    'has_text': False,
                    'has_tables': False,
                    'text_density': 0.0,
                    'metadata': dict(pdf.metadata) if pdf.metadata else {}
                }
                
                # Analyze first page for characteristics
                if pdf.pages:
                    first_page = pdf.pages[0]
                    text = first_page.extract_text() or ""
                    analysis['has_text'] = len(text.strip()) > 50
                    analysis['text_density'] = len(text) / (first_page.width * first_page.height) if first_page.width and first_page.height else 0
                    
                    # Check for table structures
                    tables = first_page.extract_tables()
                    analysis['has_tables'] = len(tables) > 0
                    analysis['table_count'] = len(tables)
                
                return analysis
                
        except Exception as e:
            logger.warning(f"Document analysis failed: {e}")
            return {
                'page_count': 0,
                'is_encrypted': False,
                'has_text': False,
                'has_tables': False,
                'text_density': 0.0,
                'metadata': {},
                'analysis_error': str(e)
            }
    
    def _create_document_context(self, analysis: Dict[str, Any], bank_hint: str) -> DocContext:
        """Create document context from analysis."""
        return DocContext(
            account_number=None,  # Will be extracted later
            date_range=(None, None),  # Will be extracted later
            page_count=analysis.get('page_count', 0),
            header_text="",  # Will be extracted later
            extraction_method="",  # Will be set by method selection
            metadata=analysis
        )
    
    def _select_extraction_method(self, analysis: Dict[str, Any]) -> str:
        """Select optimal extraction method based on document characteristics."""
        
        # If encrypted or no text, use OCR
        if analysis.get('is_encrypted') or not analysis.get('has_text'):
            if self.enable_ocr:
                return 'ocr'
            else:
                logger.warning("Document requires OCR but OCR is disabled")
                return 'pdfplumber'  # Fallback
        
        # If has clear table structure, use Camelot
        if analysis.get('has_tables') and analysis.get('text_density', 0) > 0.001:
            return 'camelot'
        
        # Default to pdfplumber for text-based extraction
        return 'pdfplumber'
    
    def _extract_with_method(self, pdf_data: bytes, method: str, context: DocContext) -> Dict[str, Any]:
        """Extract data using the selected method."""
        
        if method == 'camelot':
            return self._extract_with_camelot(pdf_data, context)
        elif method == 'ocr':
            return self._extract_with_ocr(pdf_data, context)
        else:
            return self._extract_with_pdfplumber(pdf_data, context)
    
    def _extract_with_pdfplumber(self, pdf_data: bytes, context: DocContext) -> Dict[str, Any]:
        """Extract using pdfplumber with coordinate tracking."""
        self.extraction_stats['pdfplumber_attempts'] += 1
        
        transactions = []
        events = []
        
        try:
            with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    # Extract text with coordinates
                    text = page.extract_text()
                    if not text:
                        continue
                    
                    # Extract potential transaction lines
                    lines = text.split('\n')
                    for line_idx, line in enumerate(lines):
                        if self._looks_like_transaction(line):
                            # Create transaction record with coordinates
                            transaction = self._parse_transaction_line(line, page_idx, line_idx)
                            if transaction:
                                transactions.append(transaction)
                                
                                # Log extraction event
                                event = {
                                    'row_hash': hashlib.md5(line.encode()).hexdigest(),
                                    'stage': 'extraction',
                                    'rule_id': 'pdfplumber_line_extract',
                                    'decision': 'accept',
                                    'message': f'Extracted transaction line from page {page_idx + 1}',
                                    'page_index': page_idx,
                                    'bounding_box': self._estimate_line_bbox(page, line_idx),
                                    'raw_data_json': {'line': line, 'method': 'pdfplumber'}
                                }
                                events.append(event)
            
            self.extraction_stats['successful_extractions'] += 1
            
        except Exception as e:
            logger.error(f"Pdfplumber extraction failed: {e}")
            # Log failure event
            events.append({
                'row_hash': 'extraction_failure',
                'stage': 'extraction',
                'rule_id': 'pdfplumber_error',
                'decision': 'reject',
                'message': f'Pdfplumber extraction failed: {e}',
                'page_index': 0,
                'bounding_box': None,
                'raw_data_json': {'error': str(e)}
            })
        
        return {
            'transactions': transactions,
            'events': events,
            'statistics': {
                'method': 'pdfplumber',
                'transactions_found': len(transactions),
                'events_logged': len(events)
            }
        }
    
    def _extract_with_camelot(self, pdf_data: bytes, context: DocContext) -> Dict[str, Any]:
        """Extract using Camelot for table-based documents."""
        self.extraction_stats['camelot_attempts'] += 1
        
        transactions = []
        events = []
        
        try:
            # Save PDF to temporary file for Camelot
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
                temp_file.write(pdf_data)
                temp_file.flush()
                
                # Extract tables with Camelot
                tables = camelot.read_pdf(temp_file.name, pages='all', flavor='lattice')
                
                if not tables:
                    # Fallback to stream flavor
                    tables = camelot.read_pdf(temp_file.name, pages='all', flavor='stream')
                
                for table_idx, table in enumerate(tables):
                    if table.accuracy > 0.5:  # Only process high-quality tables
                        df = table.df
                        
                        for row_idx, row in df.iterrows():
                            if self._looks_like_transaction_row(row):
                                transaction = self._parse_transaction_row(row, table_idx, row_idx)
                                if transaction:
                                    transactions.append(transaction)
                                    
                                    # Log extraction event with table coordinates
                                    event = {
                                        'row_hash': hashlib.md5(str(row.values).encode()).hexdigest(),
                                        'stage': 'extraction',
                                        'rule_id': 'camelot_table_extract',
                                        'decision': 'accept',
                                        'message': f'Extracted from table {table_idx} row {row_idx}',
                                        'page_index': table.page - 1,
                                        'bounding_box': self._get_table_bbox(table, row_idx),
                                        'raw_data_json': {'row': row.to_dict(), 'table_accuracy': table.accuracy}
                                    }
                                    events.append(event)
            
            self.extraction_stats['successful_extractions'] += 1
            
        except Exception as e:
            logger.error(f"Camelot extraction failed: {e}")
            events.append({
                'row_hash': 'camelot_failure',
                'stage': 'extraction',
                'rule_id': 'camelot_error',
                'decision': 'reject',
                'message': f'Camelot extraction failed: {e}',
                'page_index': 0,
                'bounding_box': None,
                'raw_data_json': {'error': str(e)}
            })
        
        return {
            'transactions': transactions,
            'events': events,
            'statistics': {
                'method': 'camelot',
                'transactions_found': len(transactions),
                'events_logged': len(events)
            }
        }
    
    def _extract_with_ocr(self, pdf_data: bytes, context: DocContext) -> Dict[str, Any]:
        """Extract using Cloud OCR for scanned documents."""
        self.extraction_stats['ocr_attempts'] += 1
        
        # Placeholder for OCR implementation
        # In real implementation, this would call AWS Textract, Azure Document Intelligence, etc.
        
        return {
            'transactions': [],
            'events': [{
                'row_hash': 'ocr_placeholder',
                'stage': 'extraction',
                'rule_id': 'ocr_not_implemented',
                'decision': 'reject',
                'message': 'OCR extraction not yet implemented',
                'page_index': 0,
                'bounding_box': None,
                'raw_data_json': {'note': 'OCR requires cloud service setup'}
            }],
            'statistics': {
                'method': 'ocr',
                'transactions_found': 0,
                'events_logged': 1
            }
        }
    
    def _looks_like_transaction(self, line: str) -> bool:
        """Quick heuristic to identify potential transaction lines."""
        import re
        
        # Look for patterns like: date + amount + description
        date_pattern = r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
        amount_pattern = r'\d{1,3}(?:,\d{3})*(?:\.\d{2})?'
        
        has_date = bool(re.search(date_pattern, line))
        has_amount = bool(re.search(amount_pattern, line))
        has_text = len(line.strip()) > 10
        
        return has_date and has_amount and has_text
    
    def _looks_like_transaction_row(self, row: pd.Series) -> bool:
        """Quick heuristic to identify potential transaction rows in tables."""
        # Check if row has numeric values that could be amounts
        numeric_cols = row.select_dtypes(include=['number']).count()
        text_cols = row.astype(str).str.len().sum()
        
        return numeric_cols > 0 and text_cols > 10
    
    def _parse_transaction_line(self, line: str, page_idx: int, line_idx: int) -> Optional[Dict[str, Any]]:
        """Parse a transaction line into structured data."""
        # Simplified parsing - in real implementation this would be more sophisticated
        import re
        from datetime import datetime
        
        try:
            # Extract date
            date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', line)
            if not date_match:
                return None
            
            # Extract amount (last numeric value on line)
            amount_matches = re.findall(r'\d{1,3}(?:,\d{3})*(?:\.\d{2})?', line)
            if not amount_matches:
                return None
            
            return {
                'transaction_date': date_match.group(1),
                'amount': amount_matches[-1],
                'description': line.strip(),
                'page_index': page_idx,
                'line_index': line_idx,
                'extraction_method': 'pdfplumber'
            }
            
        except Exception:
            return None
    
    def _parse_transaction_row(self, row: pd.Series, table_idx: int, row_idx: int) -> Optional[Dict[str, Any]]:
        """Parse a table row into structured transaction data."""
        try:
            # Convert row to dictionary and clean
            row_dict = row.to_dict()
            
            # Try to identify date, amount, and description columns
            # This is simplified - real implementation would be more sophisticated
            
            return {
                'transaction_date': str(row_dict.get(0, '')),  # Assume first column is date
                'amount': str(row_dict.get(1, '')),  # Assume second column is amount
                'description': ' '.join([str(v) for v in row_dict.values() if str(v).strip()]),
                'table_index': table_idx,
                'row_index': row_idx,
                'extraction_method': 'camelot'
            }
            
        except Exception:
            return None
    
    def _estimate_line_bbox(self, page, line_idx: int) -> Dict[str, float]:
        """Estimate bounding box coordinates for a text line."""
        # Simplified estimation - real implementation would use pdfplumber's char-level coordinates
        line_height = 12  # Approximate line height in points
        y_position = page.height - (line_idx * line_height)
        
        return {
            'x0': 0,
            'y0': y_position - line_height,
            'x1': page.width,
            'y1': y_position
        }
    
    def _get_table_bbox(self, table, row_idx: int) -> Dict[str, float]:
        """Get bounding box for a table row."""
        # Camelot provides table coordinates
        bbox = table._bbox
        row_height = (bbox[3] - bbox[1]) / len(table.df)
        
        return {
            'x0': bbox[0],
            'y0': bbox[3] - ((row_idx + 1) * row_height),
            'x1': bbox[2],
            'y1': bbox[3] - (row_idx * row_height)
        }