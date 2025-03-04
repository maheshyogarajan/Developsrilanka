// Receipt Scanner Application - JavaScript

document.addEventListener('DOMContentLoaded', function() {
    console.log("DOMContentLoaded event fired");
    
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
    const saveButton = document.getElementById('save-btn');
    const receiptHistoryContainer = document.getElementById('receipt-history-container');
    const receiptHistoryList = document.getElementById('receipt-history-list');
    const receiptDetailsContainer = document.getElementById('receipt-details-container');
    const uploadCard = document.querySelector('.upload-card');
    const uploadArea = document.querySelector('.upload-area');

    // Make the upload area clickable to trigger file input
    if (uploadZone && receiptInput) {
        uploadZone.addEventListener('click', function() {
            receiptInput.click();
        });

        // Handle drag and drop
        uploadZone.addEventListener('dragover', function(e) {
            e.preventDefault();
            e.stopPropagation();
            uploadZone.classList.add('dragover');
        });

        uploadZone.addEventListener('dragleave', function(e) {
            e.preventDefault();
            e.stopPropagation();
            uploadZone.classList.remove('dragover');
        });

        uploadZone.addEventListener('drop', function(e) {
            e.preventDefault();
            e.stopPropagation();
            uploadZone.classList.remove('dragover');
            
            if (e.dataTransfer.files.length) {
                receiptInput.files = e.dataTransfer.files;
                // Trigger change event
                const event = new Event('change', { bubbles: true });
                receiptInput.dispatchEvent(event);
            }
        });
    }

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
            const tipsSection = document.querySelector('.tips-section');
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
        receiptInput.addEventListener('change', handleFileSelect, { once: false });
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
            
            if (data.task_id) {
                // Asynchronous processing
                console.log('Task ID:', data.task_id);
                checkTaskStatus(data.task_id);
            } else {
                // Synchronous processing completed
                console.log('Synchronous processing completed');
                
                // Fill form with extracted data
                populateForm(data.data);
                
                // Show results
                loadingElement.classList.add('d-none');
                resultsElement.classList.remove('d-none');
                
                // Update header based on the design
                if (pageTitleElement) {
                    pageTitleElement.textContent = 'Review Receipt Data';
                }
                
                // Show save button
                showSaveButton();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showError(error.message || 'An error occurred while processing the receipt');
            loadingElement.classList.add('d-none');
            
            // Show reset button when there's an error
            if (uploadArea) {
                uploadArea.classList.remove('d-none');
            }
        });
    }

    // Check task status periodically
    function checkTaskStatus(taskId) {
        fetch(`/task_status/${taskId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            if (data.state === 'SUCCESS') {
                console.log('Task completed successfully');
                
                // Fill form with extracted data
                populateForm(data.result);
                
                // Show results
                loadingElement.classList.add('d-none');
                resultsElement.classList.remove('d-none');
                
                // Update header based on the design
                const pageTitleElement = document.querySelector('.page-title');
                if (pageTitleElement) {
                    pageTitleElement.textContent = 'Review Receipt Data';
                }
                
                // Show save button
                showSaveButton();
            } else if (data.state === 'FAILURE') {
                console.error('Task failed:', data.info);
                showError(data.info || 'Failed to process receipt');
                loadingElement.classList.add('d-none');
                
                // Show reset button when there's an error
                const uploadArea = document.querySelector('.upload-area');
                if (uploadArea) {
                    uploadArea.classList.remove('d-none');
                }
            } else {
                console.log('Task in progress:', data.state);
                
                // Update loading message with progress information
                const loadingText = document.querySelector('#loading p');
                if (loadingText) {
                    if (data.info && data.info.includes('Processing')) {
                        loadingText.innerHTML = data.info + '<br>This might take a moment.';
                    } else {
                        loadingText.innerHTML = 'Processing receipt...<br>This might take a moment.';
                    }
                }
                
                // Check again after a short delay
                setTimeout(() => checkTaskStatus(taskId), 2000);
            }
        })
        .catch(error => {
            console.error('Error checking task status:', error);
            showError(error.message || 'An error occurred while checking task status');
            loadingElement.classList.add('d-none');
        });
    }

    // Handle save button click
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
                
                // Then save the receipt
                return fetch('/save', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({})  // The data is already in the session
                });
            })
            .then(response => response.json())
            .then(saveData => {
                if (saveData.error) {
                    throw new Error(saveData.error);
                }
                
                // Show success message
                showAlert('Receipt saved successfully!', 'success');
                
                // Show view history button
                showViewHistoryButton();
                
                // Update header text to indicate success
                const pageTitle = document.querySelector('.page-title');
                if (pageTitle) {
                    const originalTitle = pageTitle.textContent;
                    pageTitle.textContent = 'Receipt Saved!';
                    setTimeout(() => {
                        pageTitle.textContent = originalTitle;
                    }, 2000);
                }
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
                addItemRow(
                    item.name || '', 
                    item.quantity || '', 
                    item.price || '',
                    item.tax_deductible || false
                );
            });
        }
    }

    // Helper function to add a new item row
    function addItemRow(name = '', quantity = '', price = '', tax_deductible = false) {
        if (!itemTemplate) return;
        
        // Clone the template content
        const templateContent = itemTemplate.content.cloneNode(true);
        
        // Set values if provided
        const nameInput = templateContent.querySelector('.item-name');
        const quantityInput = templateContent.querySelector('.item-quantity');
        const priceInput = templateContent.querySelector('.item-price');
        const taxDeductibleInput = templateContent.querySelector('.item-tax-deductible');
        
        if (nameInput) nameInput.value = name;
        if (quantityInput) quantityInput.value = quantity;
        if (priceInput) priceInput.value = price;
        if (taxDeductibleInput) taxDeductibleInput.checked = tax_deductible;
        
        // Generate a unique ID for the checkbox
        const uniqueId = 'tax-deduct-' + Math.random().toString(36).substring(2, 9);
        if (taxDeductibleInput) {
            taxDeductibleInput.id = uniqueId;
            const label = templateContent.querySelector('.form-check-label');
            if (label) {
                label.htmlFor = uniqueId;
            }
        }
        
        // Append to container
        itemsContainer.appendChild(templateContent);
    }

    // Helper function to show view history button
    function showViewHistoryButton() {
        const buttonContainer = document.querySelector('.button-container');
        if (!buttonContainer) return;
        
        // Remove existing view history button if any
        const existingButton = buttonContainer.querySelector('.view-history-btn');
        if (existingButton) {
            existingButton.remove();
        }
        
        // Create new button
        const viewHistoryButton = document.createElement('a');
        viewHistoryButton.href = '/history';
        viewHistoryButton.className = 'btn btn-outline-primary view-history-btn ms-2';
        viewHistoryButton.innerHTML = '<i class="fas fa-history me-2"></i>View Receipt History';
        
        // Add to container
        buttonContainer.appendChild(viewHistoryButton);
    }

    // Helper function to show save button
    function showSaveButton() {
        if (!saveButton) return;
        saveButton.classList.remove('d-none');
    }

    // Helper function to collect form data
    function collectFormData() {
        const formData = {
            vendor_name: document.getElementById('vendor_name').value,
            vendor_address: document.getElementById('vendor_address').value,
            vendor_contact: document.getElementById('vendor_contact').value,
            date: document.getElementById('date').value,
            total_amount: document.getElementById('total_amount').value,
            service_charge: document.getElementById('service_charge').value,
            vat_registration_number: document.getElementById('vat_registration_number').value,
            sscl_tax: document.getElementById('sscl_tax').value,
            vat_tax: document.getElementById('vat_tax').value,
            expense_major_category: document.getElementById('expense_major_category').value,
            expense_minor_category: document.getElementById('expense_minor_category').value,
            items: []
        };
        
        // Collect item data
        const itemRows = itemsContainer.querySelectorAll('.item-row');
        itemRows.forEach(row => {
            const nameInput = row.querySelector('.item-name');
            const quantityInput = row.querySelector('.item-quantity');
            const priceInput = row.querySelector('.item-price');
            const taxDeductibleInput = row.querySelector('.item-tax-deductible');
            
            if (nameInput && nameInput.value) {  // Only add items with a name
                formData.items.push({
                    name: nameInput.value,
                    quantity: quantityInput ? quantityInput.value : 1,
                    price: priceInput ? priceInput.value : 0,
                    tax_deductible: taxDeductibleInput ? taxDeductibleInput.checked : false
                });
            }
        });
        
        return formData;
    }

    // Load receipt history for the history page
    function loadReceipts() {
        console.log("Starting loadReceipts function");
        if (!receiptHistoryContainer || !receiptHistoryList) return;
        
        // Show loading state
        receiptHistoryList.innerHTML = '<div class="text-center py-4"><div class="spinner-border text-primary" role="status"></div><p class="mt-2">Loading receipts...</p></div>';
        
        console.log("Fetching receipts from server...");
        // Fetch receipts from server
        fetch('/list_receipts')
            .then(response => {
                console.log("Received response:", response.status);
                return response.json();
            })
            .then(data => {
                console.log("Received data:", data);
                
                if (data.error) {
                    throw new Error(data.error);
                }
                
                // Clear loading indicator
                receiptHistoryList.innerHTML = '';
                
                if (!data.receipts || data.receipts.length === 0) {
                    // No receipts found
                    receiptHistoryList.innerHTML = `
                        <div class="text-center py-4">
                            <div class="mb-3">
                                <i class="fas fa-receipt fa-3x text-muted"></i>
                            </div>
                            <h4>No receipts found</h4>
                            <p class="text-muted">Start by scanning your first receipt.</p>
                            <a href="${window.location.origin}/scan" class="btn btn-primary mt-2">
                                <i class="fas fa-plus-circle me-2"></i>Scan New Receipt
                            </a>
                        </div>
                    `;
                    return;
                }
                
                // Create receipt cards
                data.receipts.forEach(receipt => {
                    // Format date for display
                    const date = receipt.date ? new Date(receipt.date) : null;
                    const formattedDate = date ? date.toLocaleDateString('en-US', {
                        year: 'numeric',
                        month: 'short',
                        day: 'numeric'
                    }) : 'Unknown date';
                    
                    // Format amount for display
                    const formattedAmount = `Rs ${parseFloat(receipt.total_amount).toFixed(2)}`;
                    
                    // Create card element
                    const card = document.createElement('div');
                    card.className = 'card receipt-card mb-3 border-0 shadow-sm';
                    card.innerHTML = `
                        <div class="card-body p-3">
                            <div class="d-flex justify-content-between align-items-center">
                                <h5 class="card-title mb-0 text-truncate" style="max-width: 70%;">${receipt.vendor_name}</h5>
                                <span class="badge bg-primary">${receipt.expense_major_category || 'Uncategorized'}</span>
                            </div>
                            <div class="d-flex justify-content-between mt-2">
                                <div class="text-muted small">
                                    <i class="fas fa-calendar-alt me-1"></i> ${formattedDate}
                                </div>
                                <div class="fw-bold">
                                    ${formattedAmount}
                                </div>
                            </div>
                            <div class="d-flex justify-content-between align-items-center mt-3">
                                <span class="badge bg-light text-dark">${receipt.expense_minor_category || 'Uncategorized'}</span>
                                <a href="/view_receipt/${receipt.id}" class="btn btn-sm btn-outline-primary">
                                    <i class="fas fa-eye me-1"></i> View Details
                                </a>
                            </div>
                        </div>
                    `;
                    
                    // Add click handler to view details
                    card.addEventListener('click', function(e) {
                        if (!e.target.closest('a.btn')) {
                            window.location.href = `/view_receipt/${receipt.id}`;
                        }
                    });
                    
                    receiptHistoryList.appendChild(card);
                });
            })
            .catch(error => {
                console.error('Error loading receipts:', error);
                receiptHistoryList.innerHTML = `
                    <div class="alert alert-danger">
                        <i class="fas fa-exclamation-circle me-2"></i>
                        Failed to load receipts: ${error.message}
                    </div>
                    <div class="text-center mt-3">
                        <button class="btn btn-outline-primary retry-btn">
                            <i class="fas fa-sync-alt me-2"></i>Retry
                        </button>
                    </div>
                `;
                
                // Add retry handler
                const retryBtn = receiptHistoryList.querySelector('.retry-btn');
                if (retryBtn) {
                    retryBtn.addEventListener('click', loadReceipts);
                }
            });
    }

    // Load receipts if we're on the history page
    if (receiptHistoryContainer && receiptHistoryList) {
        loadReceipts();
    }

    // Handle delete receipts (using event delegation)
    if (receiptHistoryContainer) {
        receiptHistoryContainer.addEventListener('click', function(e) {
            const deleteBtn = e.target.closest('.delete-receipt-btn');
            if (!deleteBtn) return;
            
            e.preventDefault();
            
            const receiptId = deleteBtn.dataset.receiptId;
            if (!receiptId) return;
            
            if (confirm('Are you sure you want to delete this receipt? This action cannot be undone.')) {
                // Call API to delete the receipt
                fetch('/delete_receipts', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        receipt_ids: [receiptId]
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        throw new Error(data.error);
                    }
                    
                    // Remove from DOM or reload
                    const card = deleteBtn.closest('.receipt-card');
                    if (card) {
                        card.remove();
                    } else {
                        loadReceipts();
                    }
                    
                    // Show success message
                    showAlert('Receipt deleted successfully!', 'success');
                })
                .catch(error => {
                    console.error('Error deleting receipt:', error);
                    showAlert(`Failed to delete receipt: ${error.message}`);
                });
            }
        });
    }
});