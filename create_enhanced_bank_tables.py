"""
Create Enhanced Bank Statement Database Tables
This script creates all the necessary tables for the enhanced bank statement system.
"""

from app import app, db
from enhanced_financial_models import (
    FinancialTransaction, TransactionMetaReceipt, TransactionMetaBank, 
    TransactionMetaManual, TransactionMetaJournal, BankStatement, 
    BankStatementPage, BankStatementProcessingLog, FundingSource, 
    FundingSourceTransaction
)
from enhanced_reconciliation_models import (
    ReconciliationException, ReconciliationTrainingData, SmartMatchingRules,
    SplitTransactionGroup, SplitTransactionMember, ReconciliationAudit,
    AccountBalanceCache
)

def create_enhanced_bank_tables():
    """Create all enhanced bank statement tables."""
    try:
        with app.app_context():
            print("Creating enhanced bank statement tables...")
            
            # Create all tables
            db.create_all()
            
            print("✅ Enhanced bank statement tables created successfully!")
            print("\nCreated tables:")
            print("- financial_transaction")
            print("- transaction_meta_receipt")
            print("- transaction_meta_bank")
            print("- transaction_meta_manual")
            print("- transaction_meta_journal")
            print("- bank_statement")
            print("- bank_statement_page")
            print("- bank_statement_processing_log")
            print("- funding_source")
            print("- funding_source_transaction")
            print("- reconciliation_exception")
            print("- reconciliation_training_data")
            print("- smart_matching_rules")
            print("- split_transaction_group")
            print("- split_transaction_member")
            print("- reconciliation_audit")
            print("- account_balance_cache")
            
            return True
            
    except Exception as e:
        print(f"❌ Error creating enhanced bank statement tables: {str(e)}")
        return False

if __name__ == "__main__":
    success = create_enhanced_bank_tables()
    if success:
        print("\n🎉 Enhanced bank statement system is ready!")
        print("You can now access the bank statement features through the navigation menu.")
    else:
        print("\n⚠️ There were issues creating the tables. Please check the error messages above.")