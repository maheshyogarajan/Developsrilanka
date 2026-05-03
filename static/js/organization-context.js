/**
 * Organization Context Functionality
 * Handles the organization selection and expense type for receipts
 */

// Initialize organization context when document loads
document.addEventListener('DOMContentLoaded', function() {
    // Check if we're on a page that needs organization context
    const organizationContextContainer = document.getElementById('organization-context-container');
    const receiptOrganizationId = document.getElementById('receipt_organization_id');
    const receiptExpenseType = document.getElementById('receipt_expense_type');
    const isCompanyExpense = document.getElementById('is_company_expense');
    const companyExpenseDetails = document.getElementById('company-expense-details');
    
    // Only proceed if the organization context elements exist
    if (organizationContextContainer && receiptOrganizationId && receiptExpenseType) {
        // Load user organizations
        fetchUserOrganizations();
        
        // Setup event listeners
        if (receiptOrganizationId && receiptExpenseType) {
            receiptOrganizationId.addEventListener('change', updateReceiptContext);
            receiptExpenseType.addEventListener('change', function() {
                updateReceiptContext();
                
                // If we have a company expense checkbox, update it based on expense type
                if (isCompanyExpense) {
                    isCompanyExpense.checked = (this.value === 'company');
                    
                    // Show/hide company expense details
                    if (companyExpenseDetails) {
                        companyExpenseDetails.style.display = 
                            this.value === 'company' ? 'block' : 'none';
                    }
                }
            });
        }
        
        // If we have the company expense checkbox, link it to the expense type
        if (isCompanyExpense && receiptExpenseType) {
            isCompanyExpense.addEventListener('change', function() {
                receiptExpenseType.value = this.checked ? 'company' : 'personal';
                updateReceiptContext();
                
                // Show/hide company expense details
                if (companyExpenseDetails) {
                    companyExpenseDetails.style.display = 
                        this.checked ? 'block' : 'none';
                }
            });
        }
    }
});

/**
 * Fetch all organizations for the current user and populate the selection dropdown
 */
function fetchUserOrganizations() {
    const receiptOrganizationId = document.getElementById('receipt_organization_id');
    const expenseOrganizationId = document.getElementById('expense_organization_id');
    const organizationContextContainer = document.getElementById('organization-context-container');
    
    if (!receiptOrganizationId) return;
    
    fetch('/api/user/organizations')
        .then(response => {
            // When the user is not logged in, Flask-Login redirects this
            // request to the HTML login page. Detect that and bail quietly
            // instead of trying to JSON-parse HTML.
            const ct = response.headers.get('content-type') || '';
            if (!response.ok || !ct.includes('application/json')) {
                return null;
            }
            return response.json();
        })
        .then(data => {
            if (!data) {
                return;
            }
            if (data.error) {
                console.error('Error fetching organizations:', data.error);
                return;
            }
            
            const organizations = data.organizations || [];
            const currentOrganizationId = data.current_organization_id;
            
            // Clear existing options
            receiptOrganizationId.innerHTML = '';
            
            // Add organizations to the dropdown
            organizations.forEach(org => {
                const option = document.createElement('option');
                option.value = org.id;
                option.text = org.name;
                
                // Set the current organization as selected
                if (org.id.toString() === currentOrganizationId?.toString() || org.is_default) {
                    option.selected = true;
                }
                
                receiptOrganizationId.appendChild(option);
                
                // Also populate the expense organization dropdown if it exists
                if (expenseOrganizationId) {
                    const expenseOption = document.createElement('option');
                    expenseOption.value = org.id;
                    expenseOption.text = org.name;
                    
                    if (org.id.toString() === currentOrganizationId?.toString() || org.is_default) {
                        expenseOption.selected = true;
                    }
                    
                    expenseOrganizationId.appendChild(expenseOption);
                }
            });
            
            // Show or hide the organization context container based on number of organizations
            if (organizationContextContainer) {
                organizationContextContainer.style.display = 
                    organizations.length > 1 ? 'block' : 'none';
            }
            
            // Fetch clients for the selected organization
            if (expenseOrganizationId && expenseOrganizationId.value) {
                fetchOrganizationClients(expenseOrganizationId.value);
            }
            
            // Load the current context
            loadReceiptContext();
        })
        .catch(error => {
            console.error('Error fetching organizations:', error);
        });
}

/**
 * Fetch clients for a specific organization
 */
function fetchOrganizationClients(organizationId) {
    const expenseClientId = document.getElementById('expense_client_id');
    if (!expenseClientId) return;
    
    fetch(`/api/organizations/${organizationId}/clients`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error fetching clients:', data.error);
                return;
            }
            
            const clients = data.clients || [];
            
            // Clear existing options except for the default "No client" option
            expenseClientId.innerHTML = '<option value="" selected>No client selected</option>';
            
            // Add clients to the dropdown
            clients.forEach(client => {
                const option = document.createElement('option');
                option.value = client.id;
                option.text = client.name;
                expenseClientId.appendChild(option);
            });
        })
        .catch(error => {
            console.error('Error fetching clients:', error);
        });
}

/**
 * Update the receipt context with the selected organization and expense type
 */
function updateReceiptContext() {
    const receiptOrganizationId = document.getElementById('receipt_organization_id');
    const receiptExpenseType = document.getElementById('receipt_expense_type');
    const expenseOrganizationId = document.getElementById('expense_organization_id');
    
    if (!receiptOrganizationId || !receiptExpenseType) return;
    
    const organizationId = receiptOrganizationId.value;
    const expenseType = receiptExpenseType.value;
    
    fetch('/api/receipt/save-context', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({
            organization_id: organizationId,
            expense_type: expenseType
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            console.error('Error saving receipt context:', data.error);
            return;
        }
        
        console.log('Receipt context updated:', data);
        
        // Sync the organization selection with the expense organization dropdown if it exists
        if (expenseOrganizationId && organizationId) {
            expenseOrganizationId.value = organizationId;
            
            // Fetch clients for the selected organization
            fetchOrganizationClients(organizationId);
        }
    })
    .catch(error => {
        console.error('Error saving receipt context:', error);
    });
}

/**
 * Load the current receipt context from the server
 */
function loadReceiptContext() {
    fetch('/api/receipt/get-context')
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading receipt context:', data.error);
                return;
            }
            
            console.log('Receipt context loaded:', data);
            
            const receiptOrganizationId = document.getElementById('receipt_organization_id');
            const receiptExpenseType = document.getElementById('receipt_expense_type');
            const isCompanyExpense = document.getElementById('is_company_expense');
            const companyExpenseDetails = document.getElementById('company-expense-details');
            const expenseOrganizationId = document.getElementById('expense_organization_id');
            
            // Set the organization if we have it
            if (receiptOrganizationId && data.organization_id) {
                receiptOrganizationId.value = data.organization_id;
            }
            
            // Set the expense type if we have it
            if (receiptExpenseType && data.expense_type) {
                receiptExpenseType.value = data.expense_type;
            }
            
            // Set the company expense checkbox if we have it
            if (isCompanyExpense && data.expense_type) {
                isCompanyExpense.checked = (data.expense_type === 'company');
                
                // Show/hide company expense details
                if (companyExpenseDetails) {
                    companyExpenseDetails.style.display = 
                        data.expense_type === 'company' ? 'block' : 'none';
                }
            }
            
            // Set the organization for expense if we have it
            if (expenseOrganizationId && data.organization_id) {
                expenseOrganizationId.value = data.organization_id;
                
                // Fetch clients for the selected organization
                fetchOrganizationClients(data.organization_id);
            }
        })
        .catch(error => {
            console.error('Error loading receipt context:', error);
        });
}