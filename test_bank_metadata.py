#!/usr/bin/env python3
"""
Quick test script to verify bank metadata extraction functionality
"""

from bank_statement_processor import EnhancedBankStatementProcessor

# Sample bank statement text patterns
test_texts = [
    """
    Commercial Bank of Ceylon PLC
    Account No: 0800123456789
    Statement Period: 01/05/2025 to 31/05/2025
    """,
    """
    SAMPATH BANK PLC
    A/C NUMBER: 0123456789012
    From: 15/04/2025 To: 14/05/2025
    """,
    """
    Bank of Ceylon
    Account 9876543210123
    Period: 01/03/2025 - 31/03/2025
    """
]

processor = EnhancedBankStatementProcessor()

print("Testing Bank Metadata Extraction:")
print("=" * 50)

for i, text in enumerate(test_texts, 1):
    print(f"\nTest {i}:")
    print(f"Sample text: {text.strip()}")
    
    metadata = processor.extract_bank_metadata(text)
    print(f"Extracted metadata: {metadata}")
    print("-" * 30)