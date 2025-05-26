"""
Document Processing Models for Enhanced Bank Statement Processing
Provides complete audit trail and context tracking for PDF extraction.
"""

from datetime import datetime
from decimal import Decimal
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import json

from sqlalchemy import Column, Integer, String, Text, DateTime, Decimal as SQLDecimal, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import relationship

from models import db


class DocumentContext(db.Model):
    """Stores PDF metadata and extraction context for complete lineage tracking."""
    __tablename__ = 'document_context'
    
    id = Column(Integer, primary_key=True)
    bank_statement_id = Column(Integer, ForeignKey('bank_statement.id'), nullable=False)
    
    # PDF metadata
    account_number = Column(String(50))
    statement_start_date = Column(DateTime)
    statement_end_date = Column(DateTime)
    page_count = Column(Integer)
    header_text = Column(Text)  # Store header patterns for rule matching
    
    # Processing metadata
    pdf_size_bytes = Column(Integer)
    extraction_method = Column(String(20))  # 'pdfplumber', 'camelot', 'ocr'
    processing_timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Context data as JSON
    metadata_json = Column(JSON)  # Store additional context like detected tables, fonts, etc.
    
    # Relationships
    bank_statement = relationship("BankStatement", backref="document_context")
    extraction_events = relationship("ExtractionEvent", backref="document_context")


class ExtractionEvent(db.Model):
    """Logs every rule decision during extraction for complete audit trail."""
    __tablename__ = 'extraction_event'
    
    id = Column(Integer, primary_key=True)
    document_context_id = Column(Integer, ForeignKey('document_context.id'), nullable=False)
    
    # Source data identification
    row_hash = Column(String(64), nullable=False)  # Hash of raw extracted row
    stage = Column(String(20), nullable=False)  # 'extraction', 'validation', 'transformation'
    
    # Rule processing
    rule_id = Column(String(50), nullable=False)  # Reference to rule in ruleset.yml
    decision = Column(String(20), nullable=False)  # 'accept', 'reject', 'transform', 'flag'
    confidence_score = Column(SQLDecimal(5, 4))  # 0.0000 to 1.0000
    
    # Audit information
    message = Column(Text)  # Human-readable reason for decision
    page_index = Column(Integer)  # Which PDF page (0-based)
    bounding_box = Column(JSON)  # PDF coordinates for UI highlighting
    
    # Processing metadata
    processing_timestamp = Column(DateTime, default=datetime.utcnow)
    rule_version = Column(String(20))  # Track rule changes over time
    
    # Raw data for reconstruction
    raw_data_json = Column(JSON)  # Original extracted data before processing
    transformed_data_json = Column(JSON)  # Data after rule application


@dataclass
class DocContext:
    """Document context dataclass for passing between processing stages."""
    account_number: Optional[str]
    date_range: tuple
    page_count: int
    header_text: str
    extraction_method: str
    metadata: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return {
            'account_number': self.account_number,
            'date_range': [str(self.date_range[0]), str(self.date_range[1])],
            'page_count': self.page_count,
            'header_text': self.header_text,
            'extraction_method': self.extraction_method,
            'metadata': self.metadata
        }


@dataclass
class ExtractionResult:
    """Result from document processing with complete audit trail."""
    transactions: List[Dict[str, Any]]
    context: DocContext
    events: List[Dict[str, Any]]
    statistics: Dict[str, int]
    
    def get_accepted_count(self) -> int:
        """Count of accepted transactions."""
        return len([e for e in self.events if e['decision'] == 'accept'])
    
    def get_rejected_count(self) -> int:
        """Count of rejected transactions."""
        return len([e for e in self.events if e['decision'] == 'reject'])
    
    def get_rejection_reasons(self) -> List[str]:
        """List of unique rejection reasons."""
        return list(set([e['message'] for e in self.events if e['decision'] == 'reject']))