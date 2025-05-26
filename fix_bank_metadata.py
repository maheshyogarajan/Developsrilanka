#!/usr/bin/env python3
"""
Fix bank metadata for existing statements that have NULL bank names and account numbers
"""

import os
import boto3
from io import BytesIO
from app import app, db
from enhanced_financial_models import BankStatement
from bank_statement_processor import EnhancedBankStatementProcessor
import PyPDF2
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_bank_metadata():
    """Fix metadata for statements with missing bank names and account numbers"""
    
    with app.app_context():
        # Find statements with missing metadata
        statements_to_fix = BankStatement.query.filter(
            (BankStatement.bank_name == None) | 
            (BankStatement.bank_name == '') |
            (BankStatement.account_number == None)
        ).all()
        
        logger.info(f"Found {len(statements_to_fix)} statements needing metadata fixes")
        
        if not statements_to_fix:
            logger.info("No statements need metadata fixes")
            return
        
        # Initialize S3 and processor
        s3_client = boto3.client('s3')
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        processor = EnhancedBankStatementProcessor()
        
        for statement in statements_to_fix:
            try:
                logger.info(f"Processing statement ID {statement.id}")
                
                # Download PDF from S3
                s3_key = f"bank-statements/{statement.id}/statement.pdf"
                
                try:
                    response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                    pdf_data = response['Body'].read()
                    logger.info(f"Downloaded PDF for statement {statement.id}")
                except Exception as e:
                    logger.error(f"Failed to download PDF for statement {statement.id}: {e}")
                    continue
                
                # Extract text from PDF
                try:
                    pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_data))
                    full_text = ""
                    for page in pdf_reader.pages:
                        full_text += page.extract_text() + "\n"
                    logger.info(f"Extracted text from PDF (length: {len(full_text)})")
                except Exception as e:
                    logger.error(f"Failed to extract text from PDF for statement {statement.id}: {e}")
                    continue
                
                # Extract metadata
                try:
                    metadata = processor.extract_bank_metadata(full_text)
                    logger.info(f"Extracted metadata: {metadata}")
                    
                    # Update statement with extracted metadata
                    updated = False
                    if metadata['bank_name'] != 'Unknown Bank':
                        statement.bank_name = metadata['bank_name']
                        updated = True
                        logger.info(f"Updated bank name to: {metadata['bank_name']}")
                    
                    if metadata['account_number']:
                        statement.account_number = metadata['account_number']
                        updated = True
                        logger.info(f"Updated account number to: {metadata['account_number']}")
                    
                    if updated:
                        db.session.commit()
                        logger.info(f"Successfully updated statement {statement.id}")
                    else:
                        logger.warning(f"No metadata updates needed for statement {statement.id}")
                        
                except Exception as e:
                    logger.error(f"Failed to extract metadata for statement {statement.id}: {e}")
                    continue
                    
            except Exception as e:
                logger.error(f"Error processing statement {statement.id}: {e}")
                db.session.rollback()
                continue
        
        logger.info("Metadata fix process completed")

if __name__ == "__main__":
    fix_bank_metadata()