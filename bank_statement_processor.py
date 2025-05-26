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

class TransactionValidation:
    """Validation result for a single transaction"""
    def __init__(self):
        self.is_valid = True
        self.rejection_reason = None
        self.warnings = []
        self.transaction_type = None  # 'payment' or 'receipt'
        self.is_bank_charge = False

class EnhancedBankStatementProcessor:
    """Enhanced processor with improved validation and transaction type detection"""
    
    def __init__(self):
        self.detected_account_numbers = set()
        
        # More precise summary patterns to avoid false positives
        self.summary_patterns = [
            r'^TOTAL\s+(DEPOSITS?|WITHDRAWALS?|CREDITS?|DEBITS?)\s*[-\s]*\s*\d+\s*ITEMS?',
            r'^CLOSING\s+BALANCE\s*$',
            r'^OPENING\s+BALANCE\s*$',
            r'^BROUGHT\s+FORWARD\s*$',
            r'^CARRIED\s+FORWARD\s*$',
            r'^\d+\s+ITEMS?\s*$'  # Standalone "10 ITEMS"
        ]
        
        self.bank_charge_patterns = [
            r'\bEFT-?CHG?\b',
            r'\bEFT\s*CHARGE\b',
            r'\bBANK\s*CHARGE\b',
            r'\bSERVICE\s*FEE\b',
            r'\bMONTHLY\s*FEE\b'
        ]
        
    def extract_bank_metadata(self, full_text: str) -> Dict[str, str]:
        """Extract bank name, account number, and other metadata from statement header"""
        metadata = {
            'bank_name': 'Unknown Bank',
            'account_number': None,
            'statement_period_from': None,
            'statement_period_to': None
        }
        
        # Extract bank name patterns
        bank_patterns = [
            r'(Commercial Bank of Ceylon PLC)',
            r'(Hatton National Bank PLC)',
            r'(Nations Trust Bank PLC)',
            r'(Sampath Bank PLC)',
            r'(Bank of Ceylon)',
            r'(People\'s Bank)',
            r'(DFCC Bank)',
            r'(Standard Chartered Bank)',
            r'(Seylan Bank PLC)',
            r'([A-Z][a-zA-Z\s]+Bank[a-zA-Z\s]*(?:PLC|Limited)?)'
        ]
        
        for pattern in bank_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                metadata['bank_name'] = match.group(1).strip()
                break
        
        # Extract account number patterns
        account_patterns = [
            r'Account\s*(?:No\.?|Number)\s*:?\s*(\d{4,})',
            r'A/C\s*(?:No\.?|Number)\s*:?\s*(\d{4,})',
            r'Account\s*(\d{8,})',
            r'(?:^|\s)(\d{10,})(?:\s|$)',  # Standalone long numbers
        ]
        
        for pattern in account_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                account_num = match.group(1)
                # Validate it's not a transaction amount
                if len(account_num) >= 8:
                    metadata['account_number'] = account_num
                    break
        
        # Extract statement period
        period_patterns = [
            r'Period\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*(?:to|-)?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'From\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*To\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*to\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
        ]
        
        for pattern in period_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                metadata['statement_period_from'] = match.group(1)
                metadata['statement_period_to'] = match.group(2)
                break
        
        return metadata

    def extract_account_numbers_from_statement(self, full_text: str) -> List[str]:
        """Extract account numbers from complete statement text"""
        patterns = [
            r'ACCOUNT\s+(\d{10,15})',
            r'A/?C\s*NO\s*[:\s]*(\d{10,15})',
            r'Account\s*Number[:\s]+(\d{10,15})'
        ]
        
        found_numbers = set()
        for pattern in patterns:
            matches = re.findall(pattern, full_text, re.IGNORECASE)
            found_numbers.update(matches)
        
        # Store for validation
        self.detected_account_numbers = found_numbers
        logger.info(f"Detected account numbers: {list(found_numbers)}")
        return list(found_numbers)
    
    def is_summary_or_total_row(self, description: str, full_row_text: str) -> bool:
        """Check if row is a summary/total that should be excluded"""
        # Check the full row context, not just description
        combined_text = f"{description} {full_row_text}".upper().strip()
        
        for pattern in self.summary_patterns:
            if re.match(pattern, combined_text):
                return True
        
        return False
    
    def detect_column_positions(self, header_row: List[str]) -> Dict[str, int]:
        """Detect column positions with flexible matching"""
        positions = {}
        
        for i, header in enumerate(header_row):
            header_clean = header.upper().strip()
            
            # Payment/Debit column detection
            if any(word in header_clean for word in ['PAYMENT', 'DEBIT', 'WITHDRAWAL']):
                positions['payments_col'] = i
            
            # Receipt/Credit column detection  
            elif any(word in header_clean for word in ['RECEIPT', 'CREDIT', 'DEPOSIT']):
                positions['receipts_col'] = i
            
            # Balance column
            elif 'BALANCE' in header_clean:
                positions['balance_col'] = i
            
            # Date column
            elif 'DATE' in header_clean:
                positions['date_col'] = i
            
            # Description column (flexible matching)
            elif any(word in header_clean for word in ['PARTICULAR', 'DESCRIPTION', 'DETAIL', 'NARRATIVE']):
                positions['description_col'] = i
        
        logger.info(f"Detected column positions: {positions}")
        return positions
    
    def extract_amount_and_type(self, row: List[str], column_positions: Dict) -> Tuple[Optional[Decimal], Optional[str]]:
        """Extract amount and determine transaction type from appropriate column"""
        payments_col = column_positions.get('payments_col')
        receipts_col = column_positions.get('receipts_col')
        
        # Check payments column first (money going out)
        if payments_col is not None and payments_col < len(row):
            payment_value = row[payments_col].strip()
            if payment_value and self._is_numeric_value(payment_value):
                try:
                    amount = Decimal(payment_value.replace(',', ''))
                    return amount, 'payment'
                except (InvalidOperation, ValueError):
                    pass
        
        # Check receipts column (money coming in)
        if receipts_col is not None and receipts_col < len(row):
            receipt_value = row[receipts_col].strip()
            if receipt_value and self._is_numeric_value(receipt_value):
                try:
                    amount = Decimal(receipt_value.replace(',', ''))
                    return amount, 'receipt'
                except (InvalidOperation, ValueError):
                    pass
        
        return None, None
    
    def _is_numeric_value(self, value: str) -> bool:
        """Check if string represents a valid numeric amount"""
        if not value or value.isspace():
            return False
        
        # Remove common formatting
        clean_value = value.replace(',', '').replace('(', '').replace(')', '').strip()
        
        try:
            float(clean_value)
            return True
        except ValueError:
            return False
    
    def validate_transaction(self, amount: Decimal, description: str, full_row: str, transaction_type: str) -> TransactionValidation:
        """Comprehensive transaction validation"""
        validation = TransactionValidation()
        validation.transaction_type = transaction_type
        
        amount_str = str(amount)
        
        # Rule 1: Skip summary rows
        if self.is_summary_or_total_row(description, full_row):
            validation.is_valid = False
            validation.rejection_reason = "Summary/total row excluded"
            return validation
        
        # Rule 2: Reject account numbers as amounts
        if amount_str in self.detected_account_numbers:
            validation.is_valid = False
            validation.rejection_reason = f"Amount matches account number: {amount_str}"
            return validation
        
        # Rule 3: Reject date patterns
        if re.match(r'^\d{2}/\d{2}$', amount_str):
            validation.is_valid = False
            validation.rejection_reason = f"Amount appears to be date: {amount_str}"
            return validation
        
        # Rule 4: Reject year patterns  
        if re.match(r'^20\d{2}$', amount_str):
            validation.is_valid = False
            validation.rejection_reason = f"Amount appears to be year: {amount_str}"
            return validation
        
        # Rule 5: Amount limits
        if amount > Decimal('1000000000'):
            validation.is_valid = False
            validation.rejection_reason = f"Amount exceeds reasonable limit: {amount_str}"
            return validation
        
        if amount < Decimal('0.01'):
            validation.is_valid = False
            validation.rejection_reason = f"Amount below minimum threshold: {amount_str}"
            return validation
        
        # Detect bank charges
        full_text = f"{description} {full_row}".upper()
        for pattern in self.bank_charge_patterns:
            if re.search(pattern, full_text):
                validation.is_bank_charge = True
                validation.warnings.append("Bank charge - reconciliation not required")
                break
        
        return validation
    
    def process_table_data(self, table_data: List[List[str]]) -> Dict:
        """Process bank statement table with enhanced validation"""
        if not table_data or len(table_data) < 2:
            return {'transactions': [], 'rejected': [], 'summary': {}}
        
        # Detect columns
        header_row = table_data[0]
        column_positions = self.detect_column_positions(header_row)
        
        if not column_positions.get('payments_col') and not column_positions.get('receipts_col'):
            logger.warning("Could not detect payment or receipt columns")
        
        valid_transactions = []
        rejected_transactions = []
        summary_rows_skipped = 0
        
        for row_idx, row in enumerate(table_data[1:], 1):
            if len(row) < len(header_row):
                continue  # Skip incomplete rows
            
            # Extract amount and type
            amount, txn_type = self.extract_amount_and_type(row, column_positions)
            
            if amount is None or txn_type is None:
                continue  # No valid amount found
            
            # Get description
            desc_col = column_positions.get('description_col', 1)  # Default to column 1
            description = row[desc_col] if desc_col < len(row) else ' '.join(row)
            full_row_text = ' '.join(row)
            
            # Validate transaction
            validation = self.validate_transaction(amount, description, full_row_text, txn_type)
            
            if not validation.is_valid:
                if "Summary/total row" in validation.rejection_reason:
                    summary_rows_skipped += 1
                else:
                    rejected_transactions.append({
                        'row_number': row_idx,
                        'amount': str(amount),
                        'description': description,
                        'rejection_reason': validation.rejection_reason,
                        'original_row': row
                    })
                continue
            
            # Create valid transaction
            # Convert to standard format expected by existing system
            final_amount = amount if txn_type == 'receipt' else -amount
            
            transaction = {
                'amount': final_amount,
                'description': description,
                'transaction_type': txn_type,
                'is_bank_charge': validation.is_bank_charge,
                'row_number': row_idx
            }
            
            if validation.warnings:
                transaction['validation_warnings'] = validation.warnings
            
            valid_transactions.append(transaction)
        
        return {
            'transactions': valid_transactions,
            'rejected_transactions': rejected_transactions,
            'summary': {
                'total_processed': len(valid_transactions),
                'total_rejected': len(rejected_transactions),
                'summary_rows_skipped': summary_rows_skipped,
                'detected_columns': column_positions
            }
        }

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
        # Initialize enhanced processor
        self.enhanced_processor = EnhancedBankStatementProcessor()
    
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
            '%d/%m', '%d-%m', '%Y-%m-%d', '%m/%d/%Y',
            '%d %b %Y', '%d %B %Y', '%b %d %Y', '%B %d %Y'
        ]
        
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(date_str.strip(), fmt).date()
                
                # Handle two-digit years properly
                if parsed_date.year < 100:
                    # If year is 00-29, assume 2000-2029
                    # If year is 30-99, assume 1930-1999
                    if parsed_date.year <= 29:
                        parsed_date = parsed_date.replace(year=parsed_date.year + 2000)
                    else:
                        parsed_date = parsed_date.replace(year=parsed_date.year + 1900)
                
                # Handle incomplete dates (month/day only) by using current year
                if parsed_date.year == 1900:  # Default year from strptime
                    current_year = datetime.now().year
                    parsed_date = parsed_date.replace(year=current_year)
                
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