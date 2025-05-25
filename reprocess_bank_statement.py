#!/usr/bin/env python3
"""
Safe Bank Statement Reprocessing Script
Reprocesses bank statement ID 2 to extract missing transactions while avoiding duplicates.
"""

import logging
import sys
from datetime import datetime
from app import app, db
from enhanced_financial_models import BankStatement, FinancialTransaction
from bank_statement_processor import BankStatementProcessor
import boto3
import os

# Set up detailed logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def safe_reprocess_statement(statement_id: int):
    """Safely reprocess a bank statement avoiding duplicates."""
    
    with app.app_context():
        # Get the bank statement
        statement = BankStatement.query.get(statement_id)
        if not statement:
            logger.error(f"Bank statement {statement_id} not found")
            return False
            
        logger.info(f"Starting reprocessing of bank statement {statement_id}")
        logger.info(f"Current status: {statement.processing_status}")
        logger.info(f"Claimed transactions: {statement.total_transactions}")
        
        # Check existing transactions
        existing_transactions = FinancialTransaction.query.filter_by(
            source_type='bank_statement',
            source_id=statement_id
        ).all()
        
        logger.info(f"Found {len(existing_transactions)} existing transactions")
        
        # Delete existing transactions to avoid duplicates
        if existing_transactions:
            logger.info("Removing existing transactions to avoid duplicates...")
            for transaction in existing_transactions:
                logger.debug(f"Removing transaction: {transaction.description} - {transaction.amount}")
                db.session.delete(transaction)
            db.session.commit()
            logger.info("Existing transactions removed successfully")
        
        # Download PDF from S3
        try:
            s3_client = boto3.client('s3')
            bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
            
            logger.info(f"Downloading PDF from S3: {statement.original_pdf_s3_key}")
            
            response = s3_client.get_object(
                Bucket=bucket_name,
                Key=statement.original_pdf_s3_key
            )
            pdf_data = response['Body'].read()
            logger.info(f"PDF downloaded successfully, size: {len(pdf_data)} bytes")
            
        except Exception as e:
            logger.error(f"Failed to download PDF from S3: {e}")
            return False
        
        # Process the PDF
        try:
            processor = BankStatementProcessor()
            logger.info("Starting PDF processing...")
            
            # Extract data from PDF
            extracted_data = processor.process_pdf(pdf_data, bank_hint=statement.bank_name)
            logger.info(f"PDF processing completed")
            logger.info(f"Extracted transactions: {len(extracted_data.get('transactions', []))}")
            
            # Store transactions in database
            transactions_created = 0
            for transaction_data in extracted_data.get('transactions', []):
                try:
                    # Create financial transaction
                    transaction = FinancialTransaction(
                        organization_id=statement.organization_id,
                        transaction_date=transaction_data['date'],
                        description=transaction_data['description'],
                        amount=transaction_data['amount'],
                        currency=statement.statement_currency or 'LKR',
                        source_type='bank_statement',
                        source_id=statement_id,
                        transaction_type='debit' if transaction_data.get('is_debit') else 'credit'
                    )
                    
                    db.session.add(transaction)
                    transactions_created += 1
                    
                    logger.debug(f"Created transaction: {transaction_data['description']} - {transaction_data['amount']}")
                    
                except Exception as e:
                    logger.error(f"Failed to create transaction: {e}")
                    logger.error(f"Transaction data: {transaction_data}")
            
            # Update statement processing status
            statement.processing_status = 'completed'
            statement.total_transactions = transactions_created
            statement.processed_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"Reprocessing completed successfully!")
            logger.info(f"Total transactions created: {transactions_created}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error during PDF processing: {e}")
            db.session.rollback()
            return False

if __name__ == "__main__":
    success = safe_reprocess_statement(2)
    if success:
        print("Bank statement reprocessed successfully!")
        sys.exit(0)
    else:
        print("Bank statement reprocessing failed!")
        sys.exit(1)