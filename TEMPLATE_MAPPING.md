# Template to Route Mapping

## Purpose

This document maps each route to its corresponding template. It's designed to prevent confusion about which templates are used where and streamline future development.

## How to Use This Document

1. **When adding a new route**: Add it to this document with the corresponding template
2. **When modifying a template**: Use this document to find all routes that might be affected
3. **When debugging template issues**: Check this document to confirm which template should be serving a particular URL
4. **When refactoring**: Use this as a reference to ensure all route-template connections stay intact

## Template Conventions

- Templates should be named to match their function (e.g., `view_invoice.html` for the invoice view page)
- Templates should be organized in folders based on their section when possible
- Each template should begin with a comment indicating which routes use it
- Never create redundant templates that serve the same function

## Receipt and Expense History

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/receipts/history` | unified_view_bp | history() | unified_history.html | Main receipt and expense history page with filter options |
| `/receipt/<int:receipt_id>` | unified_view_bp | view_unified_receipt() | unified_receipt_view.html | Detailed view of a receipt and any associated expenses |
| `/receipts/update/<int:receipt_id>` | unified_view_bp | update_receipt() | N/A (POST only) | Updates receipt and expense details |
| `/receipts/update-items/<int:receipt_id>` | unified_view_bp | update_receipt_items() | N/A (POST only) | Updates receipt items and their tax deductible status |
| `/receipts/bulk-action` | unified_view_bp | bulk_action() | N/A (POST only) | Handles bulk actions like delete and export |
| `/receipts/export_excel` | unified_view_bp | export_excel() | N/A (File download) | Exports receipts as Excel file |

## Business Expense Management

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/expenses/create/<int:receipt_id>` | expense_bp | create_expense() | create_expense.html | Creates a business expense from a receipt |
| `/expenses/view/<int:expense_id>` | expense_bp | view_expense() | view_expense.html | View a specific business expense |
| `/expenses/update/<int:expense_id>` | expense_bp | update_expense() | N/A (POST only) | Updates a business expense |
| `/expenses/delete/<int:expense_id>` | expense_bp | delete_expense() | N/A (POST only) | Deletes a business expense |

## Client Expense Management

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/client-expenses/allocate/<int:receipt_id>` | client_expense_bp | allocate_to_client() | allocate_to_client.html | Allocates a receipt to a client |
| `/client-expenses/view/<int:expense_id>` | client_expense_bp | view_client_expense() | view_client_expense.html | Views a client expense |
| `/client-expenses/update/<int:expense_id>` | client_expense_bp | update_client_expense() | N/A (POST only) | Updates a client expense |
| `/client-expenses/delete/<int:expense_id>` | client_expense_bp | delete_client_expense() | N/A (POST only) | Deletes a client expense |

## Classification

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/classify/receipt/<int:receipt_id>` | classify_bp | classify_receipt() | classify_receipt.html | Classifies a receipt as personal, business, or client expense |
| `/classify/bulk` | classify_bp | bulk_classify() | N/A (POST only) | Bulk classification of receipts |

## Invoice Management

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/invoices` | invoice_bp | invoice_list() | invoice_list.html | List of all invoices |
| `/invoices/create` | invoice_bp | create_invoice() | create_invoice.html | Create a new invoice |
| `/invoices/view/<int:invoice_id>` | invoice_bp | view_invoice() | view_invoice.html | View a specific invoice |
| `/invoices/update/<int:invoice_id>` | invoice_bp | update_invoice() | N/A (POST only) | Updates an invoice |
| `/invoices/delete/<int:invoice_id>` | invoice_bp | delete_invoice() | N/A (POST only) | Deletes an invoice |
| `/invoices/send/<int:invoice_id>` | invoice_bp | send_invoice() | N/A (POST only) | Sends an invoice via email |

## Organization Management

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/organizations` | org_bp | organization_list() | organization_list.html | List of organizations |
| `/organizations/create` | org_bp | create_organization() | create_organization.html | Create a new organization |
| `/organizations/<int:org_id>` | org_bp | view_organization() | view_organization.html | View an organization |
| `/organizations/<int:org_id>/edit` | org_bp | edit_organization() | edit_organization.html | Edit an organization |
| `/organizations/<int:org_id>/members` | org_bp | organization_members() | organization_members.html | Manage organization members |
| `/organizations/<int:org_id>/invite` | org_bp | invite_member() | invite_member.html | Invite new members |

## Bank Account Management

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/bank-accounts` | bank_account_bp | bank_accounts() | bank_accounts.html | List of user's bank accounts |
| `/bank-accounts/create` | bank_account_bp | create_bank_account() | create_bank_account.html | Create a new bank account |
| `/bank-accounts/<int:account_id>` | bank_account_bp | view_bank_account() | view_bank_account.html | View bank account details |
| `/bank-accounts/<int:account_id>/edit` | bank_account_bp | edit_bank_account() | edit_bank_account.html | Edit a bank account |
| `/bank-accounts/<int:account_id>/delete` | bank_account_bp | delete_bank_account() | N/A (POST only) | Delete a bank account |
| `/organizations/<int:org_id>/bank-accounts` | bank_account_bp | organization_bank_accounts() | organization_bank_accounts.html | List of organization bank accounts |

## Client Management

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/clients` | client_bp | client_list() | client_list.html | List of clients |
| `/clients/create` | client_bp | create_client() | create_client.html | Create a new client |
| `/clients/<int:client_id>` | client_bp | view_client() | view_client.html | View client details |
| `/clients/<int:client_id>/edit` | client_bp | edit_client() | edit_client.html | Edit a client |
| `/clients/<int:client_id>/delete` | client_bp | delete_client() | N/A (POST only) | Delete a client |
| `/organizations/<int:org_id>/clients` | client_bp | organization_clients() | organization_clients.html | List of organization clients |

## API Endpoints

| Route | Blueprint | Function | Template | Description |
|-------|-----------|----------|----------|-------------|
| `/api/organizations` | unified_view_bp | api_organizations() | N/A (JSON response) | Returns list of organizations |
| `/api/organizations/<int:org_id>/clients` | unified_view_bp | api_organization_clients() | N/A (JSON response) | Returns list of clients for an organization |
| `/api/bank-accounts` | bank_account_bp | api_bank_accounts() | N/A (JSON response) | Returns list of bank accounts |
| `/api/default-bank-account` | bank_account_bp | api_default_bank_account() | N/A (JSON response) | Returns default bank account |