/**
 * Improved Organization Context Functionality
 * Simplifies the organization selection with Personal/Company toggle
 */

// Cache for organizations data
let userOrganizations = [];
let personalFinanceOrgId = null;

/**
 * Initialize the organization context UI
 */
function initOrganizationContextUI() {
    // Load user's organizations
    fetchUserOrganizations();
    
    // Set up event listeners
    setupEventListeners();
}

/**
 * Fetch user's organizations from the server
 */
function fetchUserOrganizations() {
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
                console.error('Error loading organizations:', data.error);
                return;
            }
            
            userOrganizations = data.organizations || [];
            
            // Find the Personal Finances organization
            const personalFinanceOrg = userOrganizations.find(org => 
                org.name.toLowerCase() === 'personal finances');
                
            if (personalFinanceOrg) {
                personalFinanceOrgId = personalFinanceOrg.id;
                console.log('Found Personal Finances organization:', personalFinanceOrgId);
            }
            
            // Initialize UI based on organizations
            updateOrganizationDropdown();
            
            // Load the current context after initializing UI
            loadReceiptContext();
        })
        .catch(error => {
            console.error('Error fetching organizations:', error);
        });
}

/**
 * Update the organization dropdown with user's organizations
 * Filters out Personal Finances for company expense dropdown
 * Updates both initial form and detailed form organization dropdowns
 */
function updateOrganizationDropdown() {
    // Get both organization dropdowns
    const initialOrgDropdown = document.getElementById('company_organization_id');
    const detailOrgDropdown = document.getElementById('expense_organization_id');
    
    // Exit if no organizations or neither dropdown exists
    if ((!initialOrgDropdown && !detailOrgDropdown) || !userOrganizations.length) return;
    
    // Filter out Personal Finances for company expense dropdowns
    const companyOrgs = userOrganizations.filter(org => 
        org.name.toLowerCase() !== 'personal finances');
    
    // Find the default company organization
    const defaultCompanyOrg = companyOrgs.find(org => org.is_default);
    const firstOrgId = companyOrgs.length > 0 ? companyOrgs[0].id : '';
    const defaultOrgId = defaultCompanyOrg ? defaultCompanyOrg.id : firstOrgId;
    
    // Helper function to populate a dropdown
    function populateDropdown(dropdown) {
        if (!dropdown) return;
        
        // Clear existing options
        dropdown.innerHTML = '';
        
        // Add a placeholder option
        const placeholderOption = document.createElement('option');
        placeholderOption.value = '';
        placeholderOption.textContent = 'Select Organization';
        placeholderOption.disabled = true;
        placeholderOption.selected = true;
        dropdown.appendChild(placeholderOption);
        
        // Add options for companies
        companyOrgs.forEach(org => {
            const option = document.createElement('option');
            option.value = org.id;
            option.textContent = org.name;
            dropdown.appendChild(option);
        });
        
        // Set default value
        if (defaultOrgId) {
            dropdown.value = defaultOrgId;
        }
    }
    
    // Update both dropdowns
    populateDropdown(initialOrgDropdown);
    populateDropdown(detailOrgDropdown);
    
    // Log for debugging
    console.log('Updated organization dropdowns with:', companyOrgs.length, 'organizations');
}

/**
 * Set up event listeners for the organization context UI
 */
function setupEventListeners() {
    // Toggle between personal and company expense
    const expenseTypeToggle = document.getElementById('expense_type_toggle');
    if (expenseTypeToggle) {
        expenseTypeToggle.addEventListener('change', function() {
            toggleExpenseType(this.checked);
            updateReceiptContext();
        });
    }
    
    // Make the labels clickable too
    const personalLabel = document.getElementById('personal-label');
    const companyLabel = document.getElementById('company-label');
    
    if (personalLabel && expenseTypeToggle) {
        personalLabel.addEventListener('click', function() {
            expenseTypeToggle.checked = false;
            toggleExpenseType(false);
            updateReceiptContext();
        });
    }
    
    if (companyLabel && expenseTypeToggle) {
        companyLabel.addEventListener('click', function() {
            expenseTypeToggle.checked = true;
            toggleExpenseType(true);
            updateReceiptContext();
        });
    }
    
    // Organization dropdown change
    const orgDropdown = document.getElementById('company_organization_id');
    if (orgDropdown) {
        orgDropdown.addEventListener('change', function() {
            // Update client dropdown when organization changes
            fetchOrganizationClients(this.value);
            updateReceiptContext();
        });
    }
    
    // Client dropdown change
    const clientDropdown = document.getElementById('expense_client_id');
    if (clientDropdown) {
        clientDropdown.addEventListener('change', function() {
            // Only update context when a client is selected (not during loading or error states)
            if (this.value && this.value !== 'retry') {
                updateReceiptContext();
            }
        });
    }
}

/**
 * Toggle between personal and company expense types
 * @param {boolean} isCompany - Whether this is a company expense
 */
function toggleExpenseType(isCompany) {
    const companyFields = document.getElementById('company-expense-fields');
    const orgDropdown = document.getElementById('company_organization_id');
    const personalLabel = document.getElementById('personal-label');
    const companyLabel = document.getElementById('company-label');
    
    if (companyFields) {
        if (isCompany) {
            companyFields.classList.remove('d-none');
            
            // Update active styling for labels
            if (personalLabel && companyLabel) {
                personalLabel.classList.remove('active-expense-type');
                companyLabel.classList.add('active-expense-type');
            }
            
            // Focus on organization dropdown if available
            if (orgDropdown) {
                setTimeout(() => orgDropdown.focus(), 100);
            }
        } else {
            companyFields.classList.add('d-none');
            
            // Update active styling for labels
            if (personalLabel && companyLabel) {
                personalLabel.classList.add('active-expense-type');
                companyLabel.classList.remove('active-expense-type');
            }
        }
    }
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
            
            const expenseTypeToggle = document.getElementById('expense_type_toggle');
            const orgDropdown = document.getElementById('company_organization_id');
            const clientDropdown = document.getElementById('expense_client_id');
            
            // Set the expense type toggle
            if (expenseTypeToggle && data.expense_type) {
                const isCompany = (data.expense_type === 'company');
                expenseTypeToggle.checked = isCompany;
                toggleExpenseType(isCompany);
            }
            
            // Set the organization if it's a company expense
            if (orgDropdown && data.organization_id && data.expense_type === 'company') {
                orgDropdown.value = data.organization_id;
                
                // Fetch clients for the selected organization
                fetchOrganizationClients(data.organization_id);
                
                // After fetching clients is complete, select the client if it exists
                if (data.client_id && clientDropdown) {
                    // We need to set this with a short delay to ensure client options are loaded
                    setTimeout(() => {
                        // Set the client if it exists in the dropdown
                        if (Array.from(clientDropdown.options).some(opt => opt.value === data.client_id.toString())) {
                            clientDropdown.value = data.client_id;
                            console.log(`Selected client ID ${data.client_id} from context`);
                        } else {
                            console.log(`Client ID ${data.client_id} not found in dropdown options`);
                        }
                    }, 500);
                }
            }
        })
        .catch(error => {
            console.error('Error loading receipt context:', error);
        });
}

/**
 * Save the current receipt context to the server
 */
function updateReceiptContext() {
    const expenseTypeToggle = document.getElementById('expense_type_toggle');
    const orgDropdown = document.getElementById('company_organization_id');
    const clientDropdown = document.getElementById('expense_client_id');
    
    if (!expenseTypeToggle) return;
    
    const isCompany = expenseTypeToggle.checked;
    const expenseType = isCompany ? 'company' : 'personal';
    
    // For personal expenses, use Personal Finances organization
    // For company expenses, use selected organization
    let organizationId;
    if (isCompany && orgDropdown) {
        organizationId = orgDropdown.value;
    } else {
        organizationId = personalFinanceOrgId;
    }
    
    // Don't proceed if we don't have an organization ID
    if (!organizationId) {
        console.error('No organization ID available for context update');
        return;
    }
    
    // Get the client ID if available
    let clientId = null;
    if (isCompany && clientDropdown && clientDropdown.value) {
        clientId = clientDropdown.value;
    }
    
    fetch('/api/receipt/save-context', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({
            organization_id: organizationId,
            expense_type: expenseType,
            client_id: clientId
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            console.error('Error saving receipt context:', data.error);
            return;
        }
        
        console.log('Receipt context updated:', data);
    })
    .catch(error => {
        console.error('Error saving receipt context:', error);
    });
}

/**
 * Fetch clients for the selected organization
 * @param {string} organizationId - The ID of the selected organization
 */
function fetchOrganizationClients(organizationId) {
    if (!organizationId) {
        console.log('No organization ID provided for client fetch');
        return;
    }
    
    console.log(`Fetching clients for organization ID: ${organizationId}`);
    
    const clientDropdown = document.getElementById('expense_client_id');
    if (!clientDropdown) {
        console.log('Client dropdown not found in DOM');
        return;
    }
    
    // Clear existing options and show loading state
    clientDropdown.innerHTML = '';
    
    // Add a loading option
    const loadingOption = document.createElement('option');
    loadingOption.value = '';
    loadingOption.textContent = 'Loading clients...';
    loadingOption.selected = true;
    clientDropdown.appendChild(loadingOption);
    
    // Fetch clients from the server with improved error handling
    fetch(`/api/organization/${organizationId}/clients`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            // Clear loading state
            clientDropdown.innerHTML = '';
            
            // Add a placeholder option
            const placeholderOption = document.createElement('option');
            placeholderOption.value = '';
            placeholderOption.textContent = 'Select Client (Optional)';
            placeholderOption.selected = true;
            clientDropdown.appendChild(placeholderOption);
            
            if (data.error) {
                console.error('Error loading clients:', data.error);
                throw new Error(data.error);
            }
            
            const clients = data.clients || [];
            console.log(`Received ${clients.length} clients for organization ${organizationId}`);
            
            // Add options for clients
            clients.forEach(client => {
                const option = document.createElement('option');
                option.value = client.id;
                option.textContent = client.name;
                clientDropdown.appendChild(option);
            });
            
            if (clients.length === 0) {
                // Add a message when no clients exist
                const noClientsOption = document.createElement('option');
                noClientsOption.disabled = true;
                noClientsOption.textContent = 'No clients found for this organization';
                clientDropdown.appendChild(noClientsOption);
            }
        })
        .catch(error => {
            console.error('Error fetching clients:', error);
            
            // Provide user feedback
            clientDropdown.innerHTML = '';
            
            const errorOption = document.createElement('option');
            errorOption.value = '';
            errorOption.textContent = 'Error loading clients';
            errorOption.selected = true;
            clientDropdown.appendChild(errorOption);
            
            // Add a retry option
            const retryOption = document.createElement('option');
            retryOption.value = 'retry';
            retryOption.textContent = 'Click to retry loading clients';
            clientDropdown.appendChild(retryOption);
            
            // Add retry handler
            clientDropdown.addEventListener('change', function(e) {
                if (e.target.value === 'retry') {
                    fetchOrganizationClients(organizationId);
                }
            }, { once: true });
        });
}

// Initialize when the DOM is loaded
document.addEventListener('DOMContentLoaded', initOrganizationContextUI);