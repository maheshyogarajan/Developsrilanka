/**
 * Organization and Client Selection Script
 * 
 * This script handles the dynamic loading of clients based on selected organization
 * for forms that require organization and client selection.
 */

document.addEventListener('DOMContentLoaded', function() {
    // Get the organization select element
    const organizationSelect = document.getElementById('organization_id');
    if (!organizationSelect) return;
    
    // Get the client select element
    const clientSelect = document.getElementById('client_id');
    if (!clientSelect) return;
    
    // Store original client options (empty state)
    const defaultOption = clientSelect.innerHTML;
    
    // Function to load clients for a specific organization
    function loadOrganizationClients(organizationId) {
        if (!organizationId) {
            // If no organization selected, reset client dropdown
            clientSelect.innerHTML = defaultOption;
            clientSelect.disabled = true;
            return;
        }
        
        // Show loading state
        clientSelect.innerHTML = '<option value="">Loading clients...</option>';
        clientSelect.disabled = true;
        
        // Fetch clients for this organization
        fetch(`/organizations/api/organizations/${organizationId}/clients`)
            .then(response => response.json())
            .then(data => {
                // Clear dropdown
                clientSelect.innerHTML = '<option value="">-- Select Client --</option>';
                
                // Add clients to dropdown
                if (data.clients && data.clients.length > 0) {
                    data.clients.forEach(client => {
                        const option = document.createElement('option');
                        option.value = client.id;
                        option.textContent = client.name;
                        clientSelect.appendChild(option);
                    });
                    clientSelect.disabled = false;
                } else {
                    // No clients found
                    clientSelect.innerHTML = '<option value="">No clients available</option>';
                    clientSelect.disabled = true;
                }
            })
            .catch(error => {
                console.error('Error loading clients:', error);
                clientSelect.innerHTML = '<option value="">Error loading clients</option>';
                clientSelect.disabled = true;
            });
    }
    
    // Load initial clients if organization is pre-selected
    if (organizationSelect.value) {
        loadOrganizationClients(organizationSelect.value);
    } else {
        // Disable client select initially if no organization selected
        clientSelect.disabled = true;
    }
    
    // Add change event listener to organization select
    organizationSelect.addEventListener('change', function() {
        loadOrganizationClients(this.value);
    });
});
