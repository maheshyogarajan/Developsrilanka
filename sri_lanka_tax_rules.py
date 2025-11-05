"""
Sri Lankan Tax Deductibility Rules Engine
Based on Inland Revenue Act No. 24 of 2017 (with amendments up to Act No. 2 of 2025)

This module provides comprehensive tax deductibility classification for business expenses
according to Sri Lankan tax law.
"""

import re
from typing import Dict, Tuple, Optional


class TaxDeductibilityClassifier:
    """
    Classifies business expenses according to Sri Lankan tax law.
    Returns deductibility percentage, tax law reference, and detailed notes.
    """
    
    # Tax rates for reference
    BUSINESS_TAX_RATE = 0.36  # 36% for LKR business income (2024/2025)
    USD_CONSULTING_TAX_RATE = 0.18  # 18% for USD consulting income
    
    def __init__(self):
        """Initialize the tax rules classifier."""
        self.rules = self._load_tax_rules()
    
    def _load_tax_rules(self) -> Dict:
        """
        Load comprehensive Sri Lankan tax deductibility rules.
        
        Returns:
            Dictionary of expense categories with their deductibility rules
        """
        return {
            # FULLY DEDUCTIBLE EXPENSES (100%)
            'operating_expenses': {
                'deductibility': 100,
                'reference': 'Section 25 - Inland Revenue Act No. 24 of 2017',
                'description': 'Expenses incurred in the production of income from business',
                'keywords': [
                    'salaries', 'wages', 'epf', 'etf', 'employee', 'staff',
                    'rent', 'lease', 'rental',
                    'utilities', 'electricity', 'water', 'internet', 'telephone',
                    'office supplies', 'stationery', 'paper', 'pens',
                    'maintenance', 'repairs', 'service',
                    'insurance', 'premium',
                    'bank charges', 'banking fees', 'transaction fee',
                    'interest', 'loan', 'financing'
                ],
                'notes': 'Must be incurred during the year in production of business income'
            },
            
            'professional_services': {
                'deductibility': 100,
                'reference': 'Section 25 - Inland Revenue Act No. 24 of 2017',
                'description': 'Professional fees for business purposes',
                'keywords': [
                    'legal', 'lawyer', 'attorney', 'advocate',
                    'accounting', 'accountant', 'audit', 'auditor',
                    'consulting', 'consultant', 'advisor',
                    'professional services', 'expert fees'
                ],
                'notes': 'Deductible if directly related to business operations'
            },
            
            'marketing_communication': {
                'deductibility': 100,
                'reference': 'Section 26A - Inland Revenue Act (effective April 1, 2021)',
                'description': 'Marketing and communication expenses (even if capital in nature)',
                'keywords': [
                    'advertising', 'advertisement', 'marketing', 'promotion',
                    'branding', 'brand', 'logo design',
                    'digital marketing', 'social media', 'facebook ads', 'google ads',
                    'promotional materials', 'flyers', 'brochures',
                    'website development', 'website design', 'web hosting',
                    'seo', 'sem', 'content marketing'
                ],
                'notes': 'Fully deductible including capital expenditure on marketing'
            },
            
            'research_development': {
                'deductibility': 100,
                'reference': 'Section 26 - Inland Revenue Act No. 24 of 2017',
                'description': 'R&D expenses for business upgrading and innovation',
                'keywords': [
                    'research', 'development', 'r&d',
                    'innovation', 'product development',
                    'scientific research', 'industrial research',
                    'business development', 'process improvement',
                    'prototype', 'testing', 'lab', 'laboratory'
                ],
                'notes': 'Must be conducted in Sri Lanka or through approved institutions; excludes expenses already included in asset costs'
            },
            
            'business_travel': {
                'deductibility': 100,
                'reference': 'Section 25 - Inland Revenue Act No. 24 of 2017',
                'description': 'Travel expenses for business purposes',
                'keywords': [
                    'travel', 'transport', 'transportation',
                    'flight', 'airfare', 'ticket',
                    'hotel', 'accommodation', 'lodging',
                    'taxi', 'uber', 'ride', 'rental car',
                    'fuel', 'petrol', 'diesel', 'gas',
                    'parking', 'toll', 'highway'
                ],
                'notes': 'Must be wholly and exclusively for business purposes; personal component not deductible'
            },
            
            'software_saas': {
                'deductibility': 100,
                'reference': 'Section 25 - Inland Revenue Act No. 24 of 2017',
                'description': 'Software subscriptions and SaaS for business',
                'keywords': [
                    'software', 'saas', 'subscription',
                    'cloud', 'hosting', 'server',
                    'license', 'app', 'application',
                    'crm', 'erp', 'accounting software',
                    'microsoft', 'google workspace', 'office 365',
                    'adobe', 'zoom', 'slack'
                ],
                'notes': 'Recurring subscription costs fully deductible; capital software may require depreciation'
            },
            
            'training_education': {
                'deductibility': 100,
                'reference': 'Section 25 - Inland Revenue Act No. 24 of 2017',
                'description': 'Training and professional development for staff',
                'keywords': [
                    'training', 'education', 'course', 'workshop',
                    'seminar', 'conference', 'certification',
                    'professional development', 'skills development',
                    'tuition', 'learning', 'coaching'
                ],
                'notes': 'Must be for business-related skills and employee development'
            },
            
            # PARTIALLY DEDUCTIBLE EXPENSES
            'meals_entertainment': {
                'deductibility': 50,  # Conservative estimate - needs case-by-case review
                'reference': 'Section 25 - Inland Revenue Act (with entertainment restrictions)',
                'description': 'Meals and entertainment - limited deductibility',
                'keywords': [
                    'meals', 'food', 'restaurant', 'dining',
                    'lunch', 'dinner', 'breakfast',
                    'catering', 'refreshments',
                    'entertainment', 'event', 'party',
                    'client entertainment', 'business meal'
                ],
                'notes': 'Entertainment expenses generally not deductible unless directly client-related and documented; meals during business travel may be fully deductible'
            },
            
            # NON-DEDUCTIBLE EXPENSES (0%)
            'penalties_fines': {
                'deductibility': 0,
                'reference': 'Section 25(2) - Inland Revenue Act No. 24 of 2017',
                'description': 'Penalties and fines are not deductible',
                'keywords': [
                    'penalty', 'fine', 'violation', 'infringement',
                    'traffic fine', 'parking ticket',
                    'late payment penalty', 'regulatory fine'
                ],
                'notes': 'All penalties and fines for legal/regulatory violations are non-deductible'
            },
            
            'personal_expenses': {
                'deductibility': 0,
                'reference': 'Section 25 - Inland Revenue Act (business purpose requirement)',
                'description': 'Personal expenses are not deductible',
                'keywords': [
                    'personal', 'private', 'family',
                    'clothing', 'gym', 'fitness', 'personal care',
                    'cosmetics', 'beauty', 'haircut',
                    'home', 'household', 'groceries',
                    'entertainment', 'movie', 'recreation'
                ],
                'notes': 'Expenses not wholly incurred for business purposes are not deductible'
            },
            
            'capital_expenditure': {
                'deductibility': 0,  # Use depreciation instead
                'reference': 'Section 25 - Inland Revenue Act (capital vs revenue distinction)',
                'description': 'Capital expenditure requires depreciation',
                'keywords': [
                    'equipment', 'machinery', 'vehicle', 'car',
                    'furniture', 'fixtures', 'building',
                    'land', 'property', 'real estate',
                    'computer', 'laptop', 'hardware'
                ],
                'notes': 'Capital assets are not immediately deductible; use depreciation allowances per IRD schedules. Exception: Marketing/communication capital expenses (Section 26A)'
            },
            
            'provisions_reserves': {
                'deductibility': 0,
                'reference': 'Section 25 - Inland Revenue Act',
                'description': 'General provisions and reserves not deductible',
                'keywords': [
                    'provision', 'reserve', 'contingency',
                    'allowance for doubtful debts', 'bad debt provision'
                ],
                'notes': 'Only specific bad debts actually written off are deductible, not general provisions'
            },
            
            'donations_unapproved': {
                'deductibility': 0,
                'reference': 'Qualifying Payments regulations',
                'description': 'Donations to non-approved entities are not deductible',
                'keywords': [
                    'donation', 'charity', 'contribution', 'gift'
                ],
                'notes': 'Only donations to IRD-approved charities/institutions qualify as deductible qualifying payments (max ⅓ of taxable income or Rs. 75,000)'
            }
        }
    
    def classify_item(self, item_name: str, item_price: float, 
                     vendor_name: str, major_category: str, 
                     minor_category: str) -> Dict:
        """
        Classify an expense item for tax deductibility.
        
        Args:
            item_name: Name/description of the item
            item_price: Price of the item
            vendor_name: Vendor/merchant name
            major_category: Major expense category
            minor_category: Minor expense category
        
        Returns:
            Dictionary with:
            - deductibility_percentage: 0-100
            - tax_law_reference: Legal reference
            - classification_confidence: 0.0-1.0
            - deduction_notes: Detailed explanation
            - tax_deductible: Boolean (True if >0% deductible)
        """
        # Combine all text for analysis
        text = f"{item_name} {vendor_name} {major_category} {minor_category}".lower()
        
        # Try to match against known categories
        matches = []
        for category_key, category_rules in self.rules.items():
            keywords = category_rules.get('keywords', [])
            match_count = sum(1 for keyword in keywords if keyword.lower() in text)
            if match_count > 0:
                # Calculate confidence based on keyword matches
                confidence = min(match_count / 3.0, 1.0)  # Max confidence at 3+ keyword matches
                matches.append({
                    'category': category_key,
                    'rules': category_rules,
                    'confidence': confidence,
                    'match_count': match_count
                })
        
        # Sort by confidence and match count
        matches.sort(key=lambda x: (x['confidence'], x['match_count']), reverse=True)
        
        if matches:
            # Use the best match
            best_match = matches[0]
            rules = best_match['rules']
            
            # Check for conflicting signals (e.g., entertainment keywords in business category)
            has_personal_signals = self._check_personal_signals(text)
            has_penalty_signals = self._check_penalty_signals(text)
            
            # Adjust confidence and deductibility based on signals
            if has_penalty_signals:
                return {
                    'deductibility_percentage': 0,
                    'tax_law_reference': 'Section 25(2) - Inland Revenue Act No. 24 of 2017',
                    'classification_confidence': 0.9,
                    'deduction_notes': 'Penalties and fines are not tax deductible under Sri Lankan tax law',
                    'tax_deductible': False
                }
            
            if has_personal_signals and rules['deductibility'] > 0:
                # Reduce confidence for potentially personal expenses
                confidence = best_match['confidence'] * 0.6
            else:
                confidence = best_match['confidence']
            
            return {
                'deductibility_percentage': rules['deductibility'],
                'tax_law_reference': rules['reference'],
                'classification_confidence': confidence,
                'deduction_notes': f"{rules['description']}. {rules['notes']}",
                'tax_deductible': rules['deductibility'] > 0
            }
        
        # Default: Conservative classification
        return {
            'deductibility_percentage': 0,
            'tax_law_reference': 'Section 25 - Inland Revenue Act (unclassified)',
            'classification_confidence': 0.3,
            'deduction_notes': 'Unable to confidently classify this expense. Default to non-deductible for safety. Review manually and provide proper classification based on business purpose.',
            'tax_deductible': False
        }
    
    def _check_personal_signals(self, text: str) -> bool:
        """Check if text contains signals of personal/non-business nature."""
        personal_signals = [
            'personal', 'private', 'family', 'home', 'household',
            'gym', 'fitness', 'beauty', 'cosmetics', 'clothing',
            'entertainment', 'recreation', 'movie', 'game'
        ]
        return any(signal in text for signal in personal_signals)
    
    def _check_penalty_signals(self, text: str) -> bool:
        """Check if text indicates a penalty or fine."""
        penalty_signals = [
            'penalty', 'fine', 'violation', 'infringement',
            'late payment', 'late fee', 'overdue charge'
        ]
        return any(signal in text for signal in penalty_signals)
    
    def get_tax_guidance(self, major_category: str, minor_category: str) -> str:
        """
        Get general tax guidance for a category.
        
        Args:
            major_category: Major expense category
            minor_category: Minor expense category
        
        Returns:
            Detailed tax guidance string
        """
        category_map = {
            'Operating Expenses': {
                'Meals and Entertainment': 'meals_entertainment',
                'Travel and Transportation': 'business_travel',
                'Software and SaaS': 'software_saas',
                'Marketing and Advertising': 'marketing_communication',
                'Office Supplies': 'operating_expenses'
            },
            'Administrative Expenses': {
                'Training, Education and Development': 'training_education',
                'Professional Services': 'professional_services',
                'Legal and Accounting': 'professional_services'
            }
        }
        
        category_key = category_map.get(major_category, {}).get(minor_category)
        if category_key and category_key in self.rules:
            rules = self.rules[category_key]
            return f"{rules['description']}\n\nReference: {rules['reference']}\n\n{rules['notes']}"
        
        return "General business expenses incurred in the production of income are deductible under Section 25 of the Inland Revenue Act."


# Singleton instance
_classifier = None

def get_classifier() -> TaxDeductibilityClassifier:
    """Get the singleton tax classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = TaxDeductibilityClassifier()
    return _classifier
