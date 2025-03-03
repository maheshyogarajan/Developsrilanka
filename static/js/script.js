// Receipt Scanner Application - Main JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // DOM Elements
    const receiptForm = document.getElementById('receiptForm');
    const receiptInput = document.getElementById('receipt');
    const previewContainer = document.getElementById('preview-container');
    const receiptPreview = document.getElementById('receipt-preview');
    const resultsContainer = document.getElementById('results-container');
    const loadingElement = document.getElementById('loading');
    const resultsElement = document.getElementById('results');
    const errorMessageElement = document.getElementById('error-message');
    const scanButton = document.getElementById('scanButton');
    const updateButton = document.getElementById('update-btn');
    const exportButton = document.getElementById('export-btn');
    const addItemButton = document.getElementById('add-item-btn');
    const itemsContainer = document.getElementById('items-container');
    const itemTemplate = document.getElementById('item-template');

    // Show preview when file is selected
    receiptInput.addEventListener('change', function(e) {
        if (this.files && this.files[0]) {
            const file = this.files[0];
            
            // Check if the file is an image
            if (!file.type.match('image.*')) {
                showError('Please select an image file (JPG, PNG, etc.)');
                this.value = '';
                previewContainer.classList.add('d-none');
                return;
            }
            
            // Display preview
            const reader = new FileReader();
            reader.onload = function(e) {
                receiptPreview.src = e.target.result;
                previewContainer.classList.remove('d-none');
            };
            reader.readAsDataURL(file);
            
            // Clear any previous error messages
            hideError();
        } else {
            previewContainer.classList.add('d-none');
        }
    });

    // Handle form submission
    receiptForm.addEventListener('submit', function(e) {
        e.preventDefault();
        
        // Check if a file is selected
        if (!receiptInput.files || !receiptInput.files[0]) {
            showError('Please select a receipt image to scan');
            return;
        }
        
        // Show loading state and results container
        resultsContainer.classList.remove('d-none');
        loadingElement.classList.remove('d-none');
        resultsElement.classList.add('d-none');
        hideError();
        
        // Disable scan button during processing
        scanButton.disabled = true;
        scanButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processing...';
        
        // Create form data for upload
        const formData = new FormData();
        formData.append('receipt', receiptInput.files[0]);
        
        // Send request to server
        fetch('/scan', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            // Fill form with extracted data
            populateForm(data.data);
            
            // Show results
            loadingElement.classList.add('d-none');
            resultsElement.classList.remove('d-none');
        })
        .catch(error => {
            console.error('Error:', error);
            showError(error.message || 'An error occurred while processing the receipt');
            loadingElement.classList.add('d-none');
        })
        .finally(() => {
            // Re-enable scan button
            scanButton.disabled = false;
            scanButton.innerHTML = '<i class="fas fa-search"></i> Scan Receipt';
        });
    });

    // Handle update button click
    updateButton.addEventListener('click', function() {
        // Collect all form data
        const formData = collectFormData();
        
        // Send update request to server
        fetch('/update_data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(formData)
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            // Show success message
            showAlert('Data updated successfully!', 'success');
        })
        .catch(error => {
            console.error('Error:', error);
            showError(error.message || 'An error occurred while updating the data');
        });
    });

    // Handle export button click
    exportButton.addEventListener('click', function() {
        fetch('/export')
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    throw new Error(data.error);
                }
                
                // Convert data to downloadable file
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                
                // Create download link
                const a = document.createElement('a');
                const vendorName = data.vendor_name ? data.vendor_name.replace(/\s+/g, '_') : 'receipt';
                const date = data.date ? data.date : new Date().toISOString().split('T')[0];
                a.href = url;
                a.download = `${vendorName}_${date}.json`;
                document.body.appendChild(a);
                a.click();
                
                // Clean up
                setTimeout(() => {
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                }, 100);
            })
            .catch(error => {
                console.error('Error:', error);
                showError(error.message || 'An error occurred while exporting the data');
            });
    });

    // Handle add item button click
    addItemButton.addEventListener('click', function() {
        addItemRow();
    });

    // Handle remove item button click (using event delegation)
    itemsContainer.addEventListener('click', function(e) {
        if (e.target.classList.contains('remove-item-btn') || e.target.closest('.remove-item-btn')) {
            const itemRow = e.target.closest('.item-row');
            if (itemRow) {
                itemRow.remove();
            }
        }
    });

    // Helper function to show error message
    function showError(message) {
        errorMessageElement.textContent = message;
        errorMessageElement.classList.remove('d-none');
    }

    // Helper function to hide error message
    function hideError() {
        errorMessageElement.classList.add('d-none');
        errorMessageElement.textContent = '';
    }

    // Helper function to show alert message
    function showAlert(message, type = 'danger') {
        // Create alert element
        const alertElement = document.createElement('div');
        alertElement.className = `alert alert-${type} alert-dismissible fade show`;
        alertElement.role = 'alert';
        alertElement.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        `;
        
        // Add to the page before the form
        resultsElement.insertBefore(alertElement, document.getElementById('editDataForm'));
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            alertElement.remove();
        }, 5000);
    }

    // Helper function to populate the form with extracted data
    function populateForm(data) {
        // Set simple text fields
        document.getElementById('vendor_name').value = data.vendor_name || '';
        document.getElementById('vendor_address').value = data.vendor_address || '';
        document.getElementById('vendor_contact').value = data.vendor_contact || '';
        document.getElementById('date').value = data.date || '';
        document.getElementById('total_amount').value = data.total_amount || '';
        document.getElementById('service_charge').value = data.service_charge || '';
        document.getElementById('vat_registration_number').value = data.vat_registration_number || '';
        document.getElementById('sscl_tax').value = data.sscl_tax || '';
        document.getElementById('vat_tax').value = data.vat_tax || '';
        
        // Clear existing items
        itemsContainer.innerHTML = '';
        
        // Add items
        if (data.items && Array.isArray(data.items) && data.items.length > 0) {
            data.items.forEach(item => {
                addItemRow(item.name, item.quantity, item.price);
            });
        } else {
            // Add one empty row if no items
            addItemRow();
        }
    }

    // Helper function to add an item row to the form
    function addItemRow(name = '', quantity = '', price = '') {
        // Clone the template
        const template = itemTemplate.content.cloneNode(true);
        
        // Set values if provided
        template.querySelector('.item-name').value = name;
        template.querySelector('.item-quantity').value = quantity;
        template.querySelector('.item-price').value = price;
        
        // Add to container
        itemsContainer.appendChild(template);
    }

    // Helper function to collect all form data
    function collectFormData() {
        // Get all simple text fields
        const formData = {
            vendor_name: document.getElementById('vendor_name').value,
            vendor_address: document.getElementById('vendor_address').value,
            vendor_contact: document.getElementById('vendor_contact').value,
            date: document.getElementById('date').value,
            total_amount: parseFloat(document.getElementById('total_amount').value) || 0,
            service_charge: parseFloat(document.getElementById('service_charge').value) || 0,
            vat_registration_number: document.getElementById('vat_registration_number').value,
            sscl_tax: parseFloat(document.getElementById('sscl_tax').value) || 0,
            vat_tax: parseFloat(document.getElementById('vat_tax').value) || 0,
            items: []
        };
        
        // Collect all items
        const itemRows = itemsContainer.querySelectorAll('.item-row');
        itemRows.forEach(row => {
            const name = row.querySelector('.item-name').value;
            const quantity = parseInt(row.querySelector('.item-quantity').value) || 1;
            const price = parseFloat(row.querySelector('.item-price').value) || 0;
            
            if (name) {
                formData.items.push({
                    name: name,
                    quantity: quantity,
                    price: price
                });
            }
        });
        
        return formData;
    }
});
