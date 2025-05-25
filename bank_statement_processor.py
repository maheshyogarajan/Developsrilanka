"""
Bank Statement PDF Processing Engine
This service extracts transaction data from bank statement PDFs using multiple parsing strategies.
"""

import re
import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Optional, Tuple, Any
import PyPDF2
from io import BytesIO

logger = logging.getLogger(__name__)

class BankStatementProcessor:
    """Process bank statement PDFs and extract transaction data."""
    
    def __init__(self):
        self.supported_banks = {
            'commercial_bank': 'Commercial Bank of Ceylon',
            'people_bank': 'Peoples Bank',
            'sampath_bank': 'Sampath Bank',
            'hnb': 'Hatton National Bank',
            'standard_chartered': 'Standard Chartered Bank',
            'dfcc': 'DFCC Bank',
            'generic': 'Generic Bank Parser'
        }
    
    def process_pdf(self, pdf_file_data: bytes, bank_hint: str = None) -> Dict[str, Any]:
        """
        Process a bank statement PDF and extract transaction data.
        
        Args:
            pdf_file_data: PDF file as bytes
            bank_hint: Optional bank name hint for better parsing
            
        Returns:
            Dictionary containing extracted data and metadata
        """
        try:
            # Extract text from PDF
            pdf_text = self._extract_pdf_text(pdf_file_data)
            if not pdf_text:
                raise ValueError("Could not extract text from PDF")
            
            # Detect bank type
            bank_type = self._detect_bank_type(pdf_text, bank_hint)
            
            # Extract basic statement information
            statement_info = self._extract_statement_info(pdf_text, bank_type)
            
            # Extract transactions
            transactions = self._extract_transactions(pdf_text, bank_type)
            
            # Validate extracted data
            validation_result = self._validate_extracted_data(statement_info, transactions)
            
            return {
                'bank_type': bank_type,
                'statement_info': statement_info,
                'transactions': transactions,
                'validation': validation_result,
                'raw_text_preview': pdf_text[:500],  # First 500 chars for debugging
                'total_pages': self._count_pdf_pages(pdf_file_data),
                'processing_timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error processing PDF: {str(e)}")
            raise
    
    def _extract_pdf_text(self, pdf_data: bytes) -> str:
        """Extract text content from PDF."""
        try:
            pdf_file = BytesIO(pdf_data)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text_content = ""
            for page in pdf_reader.pages:
                text_content += page.extract_text() + "\n"
            
            return text_content.strip()
            
        except Exception as e:
            logger.error(f"PDF text extraction failed: {str(e)}")
            raise ValueError(f"Failed to extract text from PDF: {str(e)}")
    
    def _count_pdf_pages(self, pdf_data: bytes) -> int:
        """Count total pages in PDF."""
        try:
            pdf_file = BytesIO(pdf_data)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            return len(pdf_reader.pages)
        except:
            return 1  # Default to 1 page if counting fails
    
    def _detect_bank_type(self, text: str, bank_hint: str = None) -> str:
        """Detect bank type from PDF text content."""
        text_lower = text.lower()
        
        # Use hint if provided
        if bank_hint:
            bank_hint_lower = bank_hint.lower()
            for key, name in self.supported_banks.items():
                if bank_hint_lower in name.lower():
                    return key
        
        # Bank detection patterns
        bank_patterns = {
            'commercial_bank': ['commercial bank', 'combank', 'cbc'],
            'people_bank': ['peoples bank', 'people\'s bank'],
            'sampath_bank': ['sampath bank', 'sampath'],
            'hnb': ['hatton national', 'hnb'],
            'standard_chartered': ['standard chartered', 'stanchart'],
            'dfcc': ['dfcc bank', 'dfcc']
        }
        
        for bank_type, patterns in bank_patterns.items():
            for pattern in patterns:
                if pattern in text_lower:
                    return bank_type
        
        return 'generic'
    
    def _extract_statement_info(self, text: str, bank_type: str) -> Dict[str, Any]:
        """Extract basic statement information."""
        info = {
            'account_number': None,
            'account_holder': None,
            'statement_period_start': None,
            'statement_period_end': None,
            'opening_balance': None,
            'closing_balance': None,
            'currency': 'LKR'  # Default to LKR
        }
        
        try:
            # Extract account number
            account_patterns = [
                r'account\s*(?:no|number)?\s*:?\s*(\d{10,})',
                r'a/c\s*(?:no|number)?\s*:?\s*(\d{10,})',
                r'account\s*(\d{10,})'
            ]
            
            for pattern in account_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    info['account_number'] = match.group(1)
                    break
            
            # Extract dates
            date_patterns = [
                r'from\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*to\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'period\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*to\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'statement\s*period\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*to\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
            ]
            
            for pattern in date_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    try:
                        start_date = self._parse_date(match.group(1))
                        end_date = self._parse_date(match.group(2))
                        info['statement_period_start'] = start_date
                        info['statement_period_end'] = end_date
                        break
                    except:
                        continue
            
            # Extract balances
            balance_patterns = [
                r'opening\s*balance\s*:?\s*rs?\.?\s*([\d,]+\.?\d*)',
                r'closing\s*balance\s*:?\s*rs?\.?\s*([\d,]+\.?\d*)',
                r'balance\s*b/f\s*:?\s*rs?\.?\s*([\d,]+\.?\d*)',
                r'balance\s*c/f\s*:?\s*rs?\.?\s*([\d,]+\.?\d*)'
            ]
            
            for pattern in balance_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    try:
                        if 'opening' in pattern or 'b/f' in pattern:
                            info['opening_balance'] = self._parse_amount(matches[0])
                        elif 'closing' in pattern or 'c/f' in pattern:
                            info['closing_balance'] = self._parse_amount(matches[-1])
                    except:
                        continue
            
            # Extract currency
            currency_match = re.search(r'currency\s*:?\s*([A-Z]{3})', text, re.IGNORECASE)
            if currency_match:
                info['currency'] = currency_match.group(1).upper()
            elif 'usd' in text.lower() or '$' in text:
                info['currency'] = 'USD'
            elif 'eur' in text.lower() or '€' in text:
                info['currency'] = 'EUR'
            
        except Exception as e:
            logger.warning(f"Error extracting statement info: {str(e)}")
        
        return info
    
    def _extract_transactions(self, text: str, bank_type: str) -> List[Dict[str, Any]]:
        """Extract transaction data from statement text."""
        transactions = []
        
        try:
            # Split text into lines for processing
            lines = text.split('\n')
            
            # Generic transaction pattern
            # Matches: DATE DESCRIPTION AMOUNT [BALANCE]
            transaction_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]?\d{0,4})\s+(.+?)\s+([\d,]+\.?\d*)\s*([DR|CR]?)?\s*([\d,]+\.?\d*)?'
            
            for line_num, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                
                # Skip header lines
                if any(keyword in line.lower() for keyword in ['date', 'description', 'debit', 'credit', 'balance']):
                    continue
                
                # Try to match transaction pattern
                match = re.search(transaction_pattern, line)
                if match:
                    try:
                        transaction = self._parse_transaction_match(match, line, line_num)
                        if transaction:
                            transactions.append(transaction)
                    except Exception as e:
                        logger.debug(f"Failed to parse transaction on line {line_num}: {str(e)}")
                        continue
            
            # Sort transactions by date
            transactions.sort(key=lambda x: x.get('transaction_date', date.min))
            
        except Exception as e:
            logger.error(f"Error extracting transactions: {str(e)}")
        
        return transactions
    
    def _parse_transaction_match(self, match: re.Match, full_line: str, line_num: int) -> Optional[Dict[str, Any]]:
        """Parse a matched transaction line into structured data."""
        try:
            date_str = match.group(1)
            description = match.group(2).strip()
            amount_str = match.group(3)
            dr_cr = match.group(4) if match.group(4) else None
            balance_str = match.group(5) if match.group(5) else None
            
            # Parse date
            transaction_date = self._parse_date(date_str)
            if not transaction_date:
                return None
            
            # Parse amount
            amount = self._parse_amount(amount_str)
            if amount is None:
                return None
            
            # Determine transaction type
            is_debit = dr_cr == 'DR' if dr_cr else self._guess_transaction_type(description, amount)
            
            # Parse balance if available
            balance = None
            if balance_str:
                balance = self._parse_amount(balance_str)
            
            return {
                'transaction_date': transaction_date,
                'description': description,
                'amount': float(amount),
                'is_debit': is_debit,
                'balance_after': float(balance) if balance else None,
                'raw_line': full_line,
                'line_number': line_num,
                'reference_number': self._extract_reference_number(description)
            }
            
        except Exception as e:
            logger.debug(f"Error parsing transaction match: {str(e)}")
            return None
    
    def _parse_date(self, date_str: str) -> Optional[date]:
        """Parse date string into date object."""
        date_formats = [
            '%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y',
            '%d/%m', '%d-%m', '%Y-%m-%d', '%m/%d/%Y'
        ]
        
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(date_str.strip(), fmt).date()
                # Handle two-digit years
                if parsed_date.year < 50:
                    parsed_date = parsed_date.replace(year=parsed_date.year + 2000)
                elif parsed_date.year < 100:
                    parsed_date = parsed_date.replace(year=parsed_date.year + 1900)
                return parsed_date
            except ValueError:
                continue
        
        return None
    
    def _parse_amount(self, amount_str: str) -> Optional[Decimal]:
        """Parse amount string into Decimal."""
        try:
            # Remove currency symbols and spaces
            cleaned = re.sub(r'[^\d,.-]', '', amount_str)
            # Remove thousands separators
            cleaned = cleaned.replace(',', '')
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None
    
    def _guess_transaction_type(self, description: str, amount: Decimal) -> bool:
        """Guess if transaction is debit based on description."""
        debit_keywords = [
            'atm', 'withdrawal', 'purchase', 'fee', 'charge', 'interest charged',
            'payment', 'transfer out', 'cheque', 'direct debit'
        ]
        
        credit_keywords = [
            'deposit', 'credit', 'salary', 'transfer in', 'interest earned',
            'dividend', 'refund'
        ]
        
        desc_lower = description.lower()
        
        for keyword in debit_keywords:
            if keyword in desc_lower:
                return True
        
        for keyword in credit_keywords:
            if keyword in desc_lower:
                return False
        
        # Default: positive amounts are credits, negative are debits
        return amount < 0
    
    def _extract_reference_number(self, description: str) -> Optional[str]:
        """Extract reference number from description."""
        ref_patterns = [
            r'ref\s*:?\s*([A-Z0-9]+)',
            r'reference\s*:?\s*([A-Z0-9]+)',
            r'(\d{6,})',  # Any 6+ digit number
        ]
        
        for pattern in ref_patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _validate_extracted_data(self, statement_info: Dict, transactions: List[Dict]) -> Dict[str, Any]:
        """Validate extracted data for consistency."""
        validation = {
            'is_valid': True,
            'warnings': [],
            'errors': [],
            'transaction_count': len(transactions),
            'date_range_valid': False,
            'balance_reconciled': False
        }
        
        try:
            # Check date range
            if statement_info.get('statement_period_start') and statement_info.get('statement_period_end'):
                start_date = statement_info['statement_period_start']
                end_date = statement_info['statement_period_end']
                validation['date_range_valid'] = start_date <= end_date
                
                if not validation['date_range_valid']:
                    validation['errors'].append("Statement period start date is after end date")
            
            # Check transaction dates are within statement period
            if transactions and validation['date_range_valid']:
                for txn in transactions:
                    txn_date = txn.get('transaction_date')
                    if txn_date and (txn_date < start_date or txn_date > end_date):
                        validation['warnings'].append(f"Transaction on {txn_date} is outside statement period")
            
            # Check balance reconciliation if we have opening/closing balances
            if (statement_info.get('opening_balance') is not None and 
                statement_info.get('closing_balance') is not None and 
                transactions):
                
                calculated_balance = float(statement_info['opening_balance'])
                for txn in transactions:
                    if txn['is_debit']:
                        calculated_balance -= txn['amount']
                    else:
                        calculated_balance += txn['amount']
                
                expected_balance = float(statement_info['closing_balance'])
                balance_diff = abs(calculated_balance - expected_balance)
                
                validation['balance_reconciled'] = balance_diff < 0.01  # Within 1 cent
                validation['calculated_closing_balance'] = calculated_balance
                validation['balance_difference'] = balance_diff
                
                if not validation['balance_reconciled']:
                    validation['warnings'].append(f"Balance mismatch: calculated {calculated_balance:.2f}, expected {expected_balance:.2f}")
            
            # Check for duplicate transactions
            seen_transactions = set()
            for txn in transactions:
                txn_key = (txn['transaction_date'], txn['amount'], txn['description'][:50])
                if txn_key in seen_transactions:
                    validation['warnings'].append(f"Potential duplicate transaction: {txn['description'][:50]}")
                seen_transactions.add(txn_key)
            
            # Overall validation
            validation['is_valid'] = len(validation['errors']) == 0
            
        except Exception as e:
            validation['errors'].append(f"Validation error: {str(e)}")
            validation['is_valid'] = False
        
        return validation

# Example usage and testing
if __name__ == "__main__":
    processor = BankStatementProcessor()
    
    # Test with sample data
    sample_text = """
    COMMERCIAL BANK OF CEYLON PLC
    ACCOUNT STATEMENT
    Account No: 1234567890
    Period: 01/01/2024 to 31/01/2024
    Opening Balance: Rs. 50,000.00
    
    Date        Description                     Amount      Balance
    01/01/2024  Salary Credit                  100,000.00   150,000.00
    02/01/2024  ATM Withdrawal                  10,000.00   140,000.00
    03/01/2024  Online Purchase                  5,000.00   135,000.00
    
    Closing Balance: Rs. 135,000.00
    """
    
    # This would be called with actual PDF bytes in production
    print("Bank Statement Processor initialized successfully!")