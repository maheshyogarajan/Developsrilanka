// Receipt Scanner Preview Application - JavaScript

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
    
    // Make the upload area clickable to trigger file input
    if (uploadZone && receiptInput) {
        // Use a flag to prevent multiple clicks within a short time period
        let canTriggerFileInput = true;
        uploadZone.addEventListener('click', function() {
            if (canTriggerFileInput) {
                canTriggerFileInput = false;
                
                // On mobile, we're using the capture attribute which will open the camera
                // For desktop, this will open a file selector
                receiptInput.click();
                
                // Re-enable after a short delay
                setTimeout(() => { canTriggerFileInput = true; }, 1000);
            }
        });
        
        // Check if the device has camera capability
        const checkCameraAvailability = () => {
            return new Promise((resolve) => {
                // Feature detection for mediaDevices
                if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
                    return resolve(false);
                }
                
                navigator.mediaDevices.enumerateDevices()
                    .then(devices => {
                        const hasCamera = devices.some(device => device.kind === 'videoinput');
                        resolve(hasCamera);
                    })
                    .catch(() => resolve(false));
            });
        };
        
        // Initialize to detect camera
        checkCameraAvailability().then(hasCamera => {
            // Add a visual indicator if camera is available
            if (hasCamera) {
                const cameraIcon = document.querySelector('.camera-icon');
                if (cameraIcon) {
                    cameraIcon.style.display = 'inline-flex';
                }
            }
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
        // Use once:true to prevent the file dialog from reopening after selection
        receiptInput.addEventListener('change', handleFileSelect, { once: true });
        
        // We'll reattach the listener when needed to allow for future selections
        document.addEventListener('resetFileInput', function() {
            receiptInput.value = ''; // Clear the input
            receiptInput.addEventListener('change', handleFileSelect, { once: true });
        });
    }

    // Process receipt image and send to server
    function processReceiptImage(file) {
        // Prevent multiple submissions of the same file
        if (window.processingReceipt) {
            console.log('Already processing a receipt, ignoring duplicate request');
            return;
        }
        
        window.processingReceipt = true;
        
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
        
        // Send request to server - using the preview endpoint
        fetch('/preview/scan', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            // Synchronous processing completed
            console.log('Synchronous processing completed');
            
            // Fill form with extracted data
            populateForm(data.data);
            
            // Show results
            loadingElement.classList.add('d-none');
            resultsElement.classList.remove('d-none');
            
            // Update header based on the design
            if (pageTitleElement) {
                pageTitleElement.textContent = 'Preview Results';
            }
            
            // Reset processing flag
            window.processingReceipt = false;
        })
        .catch(error => {
            // Special error handling for JSON parse errors
            if (error.toString().includes("Unexpected token")) {
                console.error("JSON parse error:", error);
                showError("Error processing receipt. Please try again.");
            } else {
                console.error('Error:', error);
                showError(error.message || 'An error occurred while processing the receipt');
            }
            loadingElement.classList.add('d-none');
            
            // Reset processing flag
            window.processingReceipt = false;
            
            // Show reset button when there's an error
            if (uploadArea) {
                uploadArea.classList.remove('d-none');
                // Dispatch event to reattach the file input listener
                document.dispatchEvent(new Event('resetFileInput'));
            }
        });
    }

    // No "Try Saving" button in preview mode anymore, only "Sign Up to Save" link

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
            const label = taxDeductibleInput.nextElementSibling;
            if (label) {
                label.htmlFor = uniqueId;
            }
        }
        
        // Append to container
        itemsContainer.appendChild(templateContent);
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
});