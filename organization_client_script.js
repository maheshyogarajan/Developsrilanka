    // Function to load organizations
    function loadOrganizationList() {
        fetch('/api/organizations')
            .then(response => response.json())
            .then(data => {
                const organizationSelect = document.getElementById('organization_id');
                if (!organizationSelect) return;
                
                // Clear existing options
                organizationSelect.innerHTML = '';
                
                // Add organization options
                data.organizations.forEach(org => {
                    const option = document.createElement('option');
                    option.value = org.id;
                    option.text = org.name;
                    organizationSelect.appendChild(option);
                });
                
                // Set default organization if available
                if (data.default_organization_id) {
                    organizationSelect.value = data.default_organization_id;
                }
                
                // After loading organizations, load clients for the selected organization
                const selectedOrgId = organizationSelect.value;
                if (selectedOrgId) {
                    loadClientListForOrganization(selectedOrgId);
                }
            })
            .catch(error => {
                console.error('Error loading organizations:', error);
            });
            
        // Add event listener for organization change
        const organizationSelect = document.getElementById('organization_id');
        if (organizationSelect) {
            organizationSelect.addEventListener('change', function() {
                loadClientListForOrganization(this.value);
            });
        }
    }
    
    // Function to load client list from API
    function loadClientList() {
        // Load organizations first, which will then load clients
        loadOrganizationList();
    }
    
    // Function to load clients for a specific organization
    function loadClientListForOrganization(organizationId) {
        if (!organizationId) {
            // Clear client dropdowns if no organization selected
            const clientDropdowns = [
                document.getElementById('client_id'),
                document.getElementById('edit_client_id')
            ];
            
            clientDropdowns.forEach(dropdown => {
                if (!dropdown) return;
                dropdown.innerHTML = '<option value="">-- None --</option>';
            });
            return;
        }
        
        fetch(`/api/organizations/${organizationId}/clients`)
            .then(response => response.json())
            .then(data => {
                const clientDropdowns = [
                    document.getElementById('client_id'),
                    document.getElementById('edit_client_id')
                ];
                
                clientDropdowns.forEach(dropdown => {
                    if (!dropdown) return;
                    
                    // Clear any existing options except the first one
                    dropdown.innerHTML = '<option value="">-- None --</option>';
                    
                    // Add client options
                    data.clients.forEach(client => {
                        const option = document.createElement('option');
                        option.value = client.id;
                        option.text = client.name;
                        
                        // Set as selected if it matches current client_id
                        // Set as selected if it matches current client_id
                        {% if expense and expense.client_id %}
                        if (client.id == {{ expense.client_id }}) {
                            option.selected = true;
                        }
                        {% endif %}
                        
                        dropdown.appendChild(option);
                    });
                });
            })
            .catch(error => {
                console.error('Error loading clients:', error);
            });
