// Receipt Scanner Application - Main JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // DOM Elements
    const receiptForm = document.getElementById('receiptForm');
    const receiptInput = document.getElementById('receipt');
    const uploadZone = document.getElementById('upload-zone');
    const previewContainer = document.getElementById('preview-container');
    const receiptPreview = document.getElementById('receipt-preview');
    const resultsContainer = document.getElementById('results-container');
    const loadingElement = document.getElementById('loading');
    const resultsElement = document.getElementById('results');
    const errorMessageElement = document.getElementById('error-message');
    const addItemButton = document.getElementById('add-item-btn');
    const itemsContainer = document.getElementById('items-container');
    const itemTemplate = document.getElementById('item-template');
    const uploadCard = document.querySelector('.upload-card');
    const uploadArea = document.querySelector('.upload-area');
    const tipsSection = document.querySelector('.tips-section');

    // Function to process the file selection and change UI
    function handleFileSelect() {
        if (receiptInput.files && receiptInput.files[0]) {
            const file = receiptInput.files[0];
            
            // Check if the file is an image
            if (!file.type.match('image.*')) {
                showError('Please select an image file (JPG, PNG, etc.)');
                receiptInput.value = '';
                previewContainer.classList.add('d-none');
                return;
            }
            
            // Display preview
            const reader = new FileReader();
            reader.onload = function(e) {
                receiptPreview.src = e.target.result;
                previewContainer.classList.remove('d-none');
                uploadArea.classList.add('d-none'); // Hide the upload area
                
                // Prepare for scanning
                processReceiptImage(file);
            };
            reader.readAsDataURL(file);
            
            // Hide tips section after file selection
            if (tipsSection) {
                tipsSection.classList.add('d-none');
            }
            
            // Clear any previous error messages
            hideError();
        } else {
            previewContainer.classList.add('d-none');
        }
    }

    // Show preview when file is selected
    if (receiptInput) {
        receiptInput.addEventListener('change', handleFileSelect);
    }

    // Process receipt image and send to server
    function processReceiptImage(file) {
        // Show loading state and results container
        resultsContainer.classList.remove('d-none');
        loadingElement.classList.remove('d-none');
        if (resultsElement) resultsElement.classList.add('d-none');
        hideError();
        
        // Create form data for upload
        const formData = new FormData();
        formData.append('receipt', file);
        
        // Update header based on the design
        const pageTitleElement = document.querySelector('.page-title');
        if (pageTitleElement) {
            pageTitleElement.textContent = 'Processing Receipt';
        }
        
        // Show a loading message
        const loadingText = document.querySelector('#loading p');
        if (loadingText) {
            loadingText.innerHTML = 'Uploading receipt... <br>This might take a moment.';
        }
        
        // Track task polling
        let taskCheckInterval;
        
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
            
            // Check if this is an async process
            if (data.async && data.task_id) {
                console.log('Async processing started, task ID:', data.task_id);
                
                // Update loading message for async processing
                if (loadingText) {
                    loadingText.innerHTML = 'Analyzing receipt with AI...<br>This might take a few moments.';
                }
                
                // Poll for task completion
                taskCheckInterval = setInterval(() => {
                    console.log(`Checking status for task: ${data.task_id}`);
                    fetch(`/task_status/${data.task_id}`)
                        .then(response => {
                            if (!response.ok) {
                                console.error(`HTTP error ${response.status} when checking task status`);
                                throw new Error(`HTTP error ${response.status}`);
                            }
                            return response.json();
                        })
                        .then(taskData => {
                            console.log('Task status check response:', taskData);
                            
                            if (taskData.status === 'completed') {
                                // Task completed successfully
                                console.log('Task completed successfully, processing data');
                                clearInterval(taskCheckInterval);
                                
                                // Fill form with extracted data
                                populateForm(taskData.data);
                                
                                // Show results
                                loadingElement.classList.add('d-none');
                                resultsElement.classList.remove('d-none');
                                
                                // Update header
                                if (pageTitleElement) {
                                    pageTitleElement.textContent = 'Edit Data';
                                }
                                
                                // Show the history button in case user wants to cancel
                                showViewHistoryButton();
                            }
                            else if (taskData.status === 'failed') {
                                // Task failed
                                console.error('Task failed with error:', taskData.error);
                                clearInterval(taskCheckInterval);
                                throw new Error(taskData.error || 'Failed to process receipt');
                            }
                            else {
                                // Task still in progress, update loading message with timestamp
                                const timestamp = new Date().toLocaleTimeString();
                                console.log(`Task still pending at ${timestamp}, state: ${taskData.state || 'unknown'}`);
                                if (loadingText) {
                                    loadingText.innerHTML = `Analyzing receipt with AI...<br>Still working on it (${timestamp})`;
                                }
                            }
                        })
                        .catch(error => {
                            clearInterval(taskCheckInterval);
                            console.error('Error checking task status:', error);
                            showError(error.message || 'Error checking receipt processing status');
                            loadingElement.classList.add('d-none');
                            
                            // Show reset button when there's an error
                            if (uploadArea) {
                                uploadArea.classList.remove('d-none');
                            }
                        });
                }, 2000); // Check every 2 seconds
            } 
            else {
                // Synchronous processing completed
                console.log('Synchronous processing completed');
                
                // Fill form with extracted data
                populateForm(data.data);
                
                // Show results
                loadingElement.classList.add('d-none');
                resultsElement.classList.remove('d-none');
                
                // Update header based on the design
                if (pageTitleElement) {
                    pageTitleElement.textContent = 'Edit Data';
                }
                
                // Show the history button in case user wants to cancel
                showViewHistoryButton();
            }
        })
        .catch(error => {
            // Clear any interval if it exists
            if (taskCheckInterval) {
                clearInterval(taskCheckInterval);
            }
            
            console.error('Error:', error);
            showError(error.message || 'An error occurred while processing the receipt');
            loadingElement.classList.add('d-none');
            
            // Show reset button when there's an error
            if (uploadArea) {
                uploadArea.classList.remove('d-none');
            }
        });
    }

    // Handle save button click
    const saveButton = document.getElementById('save-btn');
    if (saveButton) {
        saveButton.addEventListener('click', function() {
            // Show loading state on button
            const originalButtonText = saveButton.innerHTML;
            saveButton.disabled = true;
            saveButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Saving...';
            
            // Collect form data
            const formData = collectFormData();
            
            // Update the data first
            fetch('/update_data', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            })
            .then(response => response.json())
            .then(updateData => {
                if (updateData.error) {
                    throw new Error(updateData.error);
                }
                
                // Now save to database
                return fetch('/save', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    throw new Error(data.error);
                }
                
                // Show success message
                showAlert('Receipt saved successfully!', 'success');
                
                // Update header text to indicate success
                const pageTitle = document.querySelector('.page-title');
                if (pageTitle) {
                    const originalTitle = pageTitle.textContent;
                    pageTitle.textContent = 'Receipt Saved!';
                    setTimeout(() => {
                        pageTitle.textContent = originalTitle;
                    }, 2000);
                }
                
                // Show view history button
                showViewHistoryButton();
            })
            .catch(error => {
                console.error('Error:', error);
                showError(error.message || 'An error occurred while saving the receipt');
            })
            .finally(() => {
                // Restore button state
                saveButton.disabled = false;
                saveButton.innerHTML = originalButtonText;
            });
        });
    }

    // Export functionality has been removed

    // Handle add item button click
    if (addItemButton) {
        addItemButton.addEventListener('click', function() {
            addItemRow();
        });
    }

    // Handle remove item button click (using event delegation)
    if (itemsContainer) {
        itemsContainer.addEventListener('click', function(e) {
            if (e.target.classList.contains('remove-item-btn') || e.target.closest('.remove-item-btn')) {
                const itemRow = e.target.closest('.item-row');
                if (itemRow) {
                    itemRow.remove();
                }
            }
        });
    }

    // Helper function to show error message
    function showError(message) {
        if (errorMessageElement) {
            errorMessageElement.textContent = message;
            errorMessageElement.classList.remove('d-none');
        } else {
            console.error('Error:', message);
        }
    }

    // Helper function to hide error message
    function hideError() {
        if (errorMessageElement) {
            errorMessageElement.classList.add('d-none');
            errorMessageElement.textContent = '';
        }
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
        
        // Add to the page at the top of the form container
        if (resultsElement) {
            if (resultsElement.firstChild) {
                resultsElement.insertBefore(alertElement, resultsElement.firstChild);
            } else {
                resultsElement.appendChild(alertElement);
            }
        }
        
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
        
        // Set expense categories if they exist
        if (data.expense_major_category) {
            const majorCategorySelect = document.getElementById('expense_major_category');
            for (let i = 0; i < majorCategorySelect.options.length; i++) {
                if (majorCategorySelect.options[i].value === data.expense_major_category) {
                    majorCategorySelect.selectedIndex = i;
                    break;
                }
            }
        }
        
        if (data.expense_minor_category) {
            const minorCategorySelect = document.getElementById('expense_minor_category');
            for (let i = 0; i < minorCategorySelect.options.length; i++) {
                if (minorCategorySelect.options[i].value === data.expense_minor_category) {
                    minorCategorySelect.selectedIndex = i;
                    break;
                }
            }
        }
        
        // Clear existing items
        itemsContainer.innerHTML = '';
        
        // Add items
        if (data.items && Array.isArray(data.items) && data.items.length > 0) {
            data.items.forEach(item => {
                addItemRow(item.name, item.quantity, item.price, item.tax_deductible);
            });
        } else {
            // Add one empty row if no items
            addItemRow();
        }
    }

    // Helper function to add an item row to the form
    function addItemRow(name = '', quantity = '', price = '', tax_deductible = false) {
        // Clone the template
        const template = itemTemplate.content.cloneNode(true);
        
        // Set values if provided
        template.querySelector('.item-name').value = name;
        template.querySelector('.item-quantity').value = quantity;
        template.querySelector('.item-price').value = price;
        
        // Set tax deductible checkbox if available
        const taxDeductibleCheckbox = template.querySelector('.item-tax-deductible');
        if (taxDeductibleCheckbox) {
            taxDeductibleCheckbox.checked = tax_deductible;
        }
        
        // Add to container
        itemsContainer.appendChild(template);
    }

    // Helper function to show view history button after saving
    function showViewHistoryButton() {
        // Check if button already exists
        const existingButton = document.getElementById('view-history-btn');
        if (existingButton) {
            return;
        }
        
        // Create a button to view receipt history
        const historyButton = document.createElement('button');
        historyButton.id = 'view-history-btn';
        historyButton.className = 'btn btn-outline-primary mt-3';
        historyButton.innerHTML = '<i class="fas fa-history me-2"></i>View Receipt History';
        historyButton.addEventListener('click', function() {
            window.location.href = '/history';
        });
        
        // Add the button after the form
        const form = document.getElementById('editDataForm');
        if (form && form.parentNode) {
            form.insertAdjacentElement('afterend', historyButton);
        }
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
            expense_major_category: document.getElementById('expense_major_category').value,
            expense_minor_category: document.getElementById('expense_minor_category').value,
            items: []
        };
        
        // Collect all items
        const itemRows = itemsContainer.querySelectorAll('.item-row');
        itemRows.forEach(row => {
            const name = row.querySelector('.item-name').value;
            const quantity = parseInt(row.querySelector('.item-quantity').value) || 1;
            const price = parseFloat(row.querySelector('.item-price').value) || 0;
            const taxDeductible = row.querySelector('.item-tax-deductible')?.checked || false;
            
            if (name) {
                formData.items.push({
                    name: name,
                    quantity: quantity,
                    price: price,
                    tax_deductible: taxDeductible
                });
            }
        });
        
        return formData;
    }
});
