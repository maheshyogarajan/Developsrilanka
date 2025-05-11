// Receipt Scanner Preview Application - JavaScript

// Image compression settings
const IMAGE_MAX_WIDTH = 1600;
const IMAGE_MAX_HEIGHT = 1200;
const IMAGE_QUALITY = 0.85;

/**
 * Compresses an image file to reduce size before upload
 * 
 * @param {File} file - The original file from input
 * @param {number} maxWidth - Maximum width in pixels
 * @param {number} maxHeight - Maximum height in pixels
 * @param {number} quality - JPEG quality (0-1)
 * @returns {Promise<Blob>} Promise resolving to compressed image blob
 */
function compressImage(file, maxWidth = IMAGE_MAX_WIDTH, maxHeight = IMAGE_MAX_HEIGHT, quality = IMAGE_QUALITY) {
  return new Promise((resolve, reject) => {
    // Skip compression for non-image files
    if (!file.type.match('image.*')) {
      resolve(file);
      return;
    }
    
    console.log("Starting image compression for file:", file.name, "size:", Math.round(file.size/1024), "KB");
    
    const reader = new FileReader();
    reader.onload = function(e) {
      const img = new Image();
      img.onload = function() {
        // Check if compression is needed based on file size and dimensions
        if (file.size < 500000 && img.width <= maxWidth && img.height <= maxHeight) {
          console.log("Image already small enough, skipping compression");
          resolve(file);
          return;
        }
        
        // Calculate new dimensions while maintaining aspect ratio
        let width = img.width;
        let height = img.height;
        
        if (width > maxWidth || height > maxHeight) {
          if (width > height) {
            height = Math.round(height * (maxWidth / width));
            width = maxWidth;
          } else {
            width = Math.round(width * (maxHeight / height));
            height = maxHeight;
          }
        }
        
        // Create canvas for compression
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        
        // Draw and compress
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, width, height);
        
        // Convert to blob
        canvas.toBlob((blob) => {
          console.log("Compression complete - Original:", Math.round(file.size/1024), "KB, Compressed:", Math.round(blob.size/1024), "KB", 
                      "Reduction:", Math.round((1 - (blob.size / file.size)) * 100), "%");
          
          // Create a new File object from the blob to maintain filename
          const compressedFile = new File([blob], file.name, {
            type: file.type,
            lastModified: file.lastModified
          });
          
          resolve(compressedFile);
        }, file.type, quality);
      };
      
      img.onerror = function() {
        console.error("Error loading image for compression");
        resolve(file); // Fall back to original file on error
      };
      
      img.src = e.target.result;
    };
    
    reader.onerror = function() {
      console.error("Error reading file for compression");
      resolve(file); // Fall back to original on error
    };
    
    reader.readAsDataURL(file);
  });
}

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
            
            // Display preview immediately
            const reader = new FileReader();
            reader.onload = function(e) {
                receiptPreview.src = e.target.result;
                previewContainer.classList.remove('d-none');
                uploadArea.classList.add('d-none'); // Hide the upload area
                
                // Start compression after preview is shown
                const compressionStart = Date.now();
                const loadingText = document.querySelector('#loading p');
                if (loadingText) {
                    loadingText.innerHTML = 'Optimizing image for upload... <br>This will improve performance.';
                }
                
                // Compress the image before sending it for processing
                compressImage(file)
                    .then(compressedFile => {
                        const compressionTime = Date.now() - compressionStart;
                        console.log(`Image compression took ${compressionTime}ms`);
                        
                        // Log compression results
                        if (file.size !== compressedFile.size) {
                            console.log(`Original size: ${Math.round(file.size/1024)}KB, Compressed: ${Math.round(compressedFile.size/1024)}KB, Reduction: ${Math.round((1 - (compressedFile.size / file.size)) * 100)}%`);
                        } else {
                            console.log("Image didn't need compression");
                        }
                        
                        // Update loading text for processing
                        if (loadingText) {
                            loadingText.innerHTML = 'Processing receipt... <br>This might take a moment.';
                        }
                        
                        // Proceed with the compressed file
                        processReceiptImage(compressedFile);
                    })
                    .catch(error => {
                        console.error("Compression failed:", error);
                        // Fall back to original file if compression fails
                        processReceiptImage(file);
                    });
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
        // Reset any stuck processing state
        if (window.processingReceipt) {
            console.log('Resetting previous processing state');
            resetProcessingState();
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
        
        // Function to reset the processing state and UI
        function resetProcessingState() {
            window.processingReceipt = false;
            
            if (uploadArea) {
                uploadArea.classList.remove('d-none');
            }
            
            if (loadingElement) {
                loadingElement.classList.add('d-none');
            }
            
            // Reattach the file input listener
            document.dispatchEvent(new Event('resetFileInput'));
        }
        
        // Get CSRF token from the page
        const csrfToken = document.querySelector('input[name="csrf_token"]')?.value;
        
        // Send request to server - using the preview endpoint
        fetch('/preview/scan', {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => {
            // Log the response status and headers for debugging
            console.log('Preview scan response status:', response.status);
            console.log('Preview scan response headers:', [...response.headers.entries()].reduce((obj, [key, value]) => {
                obj[key] = value;
                return obj;
            }, {}));
            
            // Check if the response is OK first
            if (!response.ok) {
                return response.text().then(text => {
                    console.error('Server returned error response:', text);
                    throw new Error(`Server error: ${response.status} ${response.statusText}`);
                });
            }
            
            // Check the content type to make sure it's JSON
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                return response.text().then(text => {
                    console.error('Response is not JSON:', text.substring(0, 500)); // Log first 500 chars
                    throw new Error('Server returned non-JSON response. Please try again or try with a different image.');
                });
            }
            
            // Add extra error handling for JSON parsing
            return response.text().then(text => {
                try {
                    return JSON.parse(text);
                } catch (e) {
                    console.error('Error parsing JSON:', e, 'Raw response text starts with:', text.substring(0, 100));
                    throw new Error('Unexpected token in JSON response. Please try again with a different image.');
                }
            });
        })
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            // Synchronous processing completed
            console.log('Synchronous processing completed. Data:', data);
            
            // Get receipt animation elements
            const receiptValueAnimation = document.getElementById('receipt-value-animation');
            
            if (receiptValueAnimation) {
                // Get receipt values
                const receiptTotal = parseFloat(data.data.total_amount) || 0;
                const vatTax = parseFloat(data.data.vat_tax) || 0;
                const ssclTax = parseFloat(data.data.sscl_tax) || 0;
                
                // Calculate maximum savings (36% of the receipt value, assuming all is tax deductible)
                const maxSaving = receiptTotal * 0.36;
                
                // Update the animation values
                document.getElementById('receipt-value-amount').textContent = `Rs ${receiptTotal.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                document.getElementById('max-saving-amount').textContent = `Rs ${maxSaving.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                document.getElementById('vat-amount').textContent = `Rs ${vatTax.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                document.getElementById('sscl-amount').textContent = `Rs ${ssclTax.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                
                // Setup the expense type toggle
                const expenseTypeToggle = document.getElementById('expense_type_toggle');
                const personalLabel = document.getElementById('personal-label');
                const companyLabel = document.getElementById('company-label');
                const companyExpenseFields = document.getElementById('company-expense-fields');
                
                if (expenseTypeToggle && personalLabel && companyLabel && companyExpenseFields) {
                    expenseTypeToggle.addEventListener('change', function() {
                        if (this.checked) {
                            personalLabel.classList.remove('active-expense-type');
                            companyLabel.classList.add('active-expense-type');
                            companyExpenseFields.classList.remove('d-none');
                        } else {
                            personalLabel.classList.add('active-expense-type');
                            companyLabel.classList.remove('active-expense-type');
                            companyExpenseFields.classList.add('d-none');
                        }
                    });
                }
                
                // Show the animation
                loadingElement.classList.add('d-none');
                receiptValueAnimation.classList.remove('d-none');
                receiptValueAnimation.querySelector('.card').classList.add('animate__animated', 'animate__fadeInUp');
                
                // Setup Next button
                const nextBtn = document.getElementById('next-btn');
                if (nextBtn) {
                    // Remove any existing event listeners
                    nextBtn.replaceWith(nextBtn.cloneNode(true));
                    
                    // Get the new reference after replacing
                    const newNextBtn = document.getElementById('next-btn');
                    
                    newNextBtn.addEventListener('click', function() {
                        // Fade out animation and show details form
                        receiptValueAnimation.querySelector('.card').classList.remove('animate__fadeInUp');
                        receiptValueAnimation.querySelector('.card').classList.add('animate__fadeOutDown');
                        
                        setTimeout(() => {
                            // Fill form with extracted data
                            populateForm(data.data);
                            
                            receiptValueAnimation.classList.add('d-none');
                            resultsElement.classList.remove('d-none');
                            
                            // Update header
                            if (pageTitleElement) {
                                pageTitleElement.textContent = 'Preview Results';
                            }
                        }, 500); // Short delay for animation
                    });
                }
            } else {
                // Fallback if animation elements aren't found
                // Fill form with extracted data
                populateForm(data.data);
                
                // Show results directly
                loadingElement.classList.add('d-none');
                resultsElement.classList.remove('d-none');
                
                // Update header based on the design
                if (pageTitleElement) {
                    pageTitleElement.textContent = 'Preview Results';
                }
            }
            
            // Reset processing flag
            window.processingReceipt = false;
        })
        .catch(error => {
            console.error('Detailed error information:', error.toString());
            
            // Special error handling for different error types
            if (error.toString().includes("Unexpected token")) {
                console.error("JSON parse error:", error);
                showError("Error processing receipt: The server response was not in the expected format. Please try again or with a different image.");
            } else if (error.toString().includes("non-JSON response")) {
                console.error("Content type error:", error);
                showError("The server returned an unexpected response type. Please try with a JPEG or PNG image.");
            } else if (error.toString().includes("Failed to fetch")) {
                console.error("Network error:", error);
                showError("Network error while communicating with the server. Please check your connection and try again.");
            } else {
                console.error('General error:', error);
                showError(error.message || 'An error occurred while processing the receipt');
            }
            
            // Hide loading indicator
            loadingElement.classList.add('d-none');
            
            // Reset processing flag
            window.processingReceipt = false;
            
            // Completely reset the UI to initial state
            resetProcessingState();
            
            // Additionally, try to reset the file input element itself
            const fileInput = document.getElementById('receipt-input');
            if (fileInput) {
                // Clone and replace to completely reset the file input
                const newFileInput = fileInput.cloneNode(true);
                fileInput.parentNode.replaceChild(newFileInput, fileInput);
                
                // Add change event listener to the new file input
                newFileInput.addEventListener('change', handleFileSelect, { once: true });
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
    
    // Debounce function for limiting API calls
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
    
    // Create a single update function to avoid duplicate code
    const updateFormData = debounce(function() {
        // Don't update if we're in the middle of scanning or if there was an error
        if (window.processingReceipt || document.getElementById('error-message').textContent) {
            return;
        }
        
        // Collect the updated form data
        const updatedData = collectFormData();
        
        // Get CSRF token from the page
        const csrfToken = document.querySelector('input[name="csrf_token"]')?.value;
        
        // Send a request to update the data in the session
        fetch('/preview/update_data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(updatedData)
        })
        .then(response => {
            // Check if the response is OK first
            if (!response.ok) {
                return response.text().then(text => {
                    console.error('Server returned error response:', text);
                    throw new Error(`Server error: ${response.status} ${response.statusText}`);
                });
            }
            
            // Check the content type to make sure it's JSON
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                return response.text().then(text => {
                    // Don't throw an error here, just log it to avoid cluttering the UI
                    console.warn('Response is not JSON:', text.substring(0, 150) + '...');
                    return { success: true }; // Return a dummy object to avoid breaking the chain
                });
            }
            
            // Add extra error handling for JSON parsing
            return response.text().then(text => {
                try {
                    return JSON.parse(text);
                } catch (e) {
                    console.error('Error parsing JSON:', e, 'Raw response:', text.substring(0, 100));
                    return { success: true }; // Return a dummy object to avoid breaking the chain
                }
            });
        })
        .then(data => {
            if (data && data.success) {
                console.log('Data updated successfully');
            }
        })
        .catch(error => {
            console.error('Error updating data:', error);
            // Don't show errors for data updates to avoid annoying users
        });
    }, 500); // 500ms debounce time
    
    // Add an event listener to the form to update data when changes are made
    const editDataForm = document.getElementById('editDataForm');
    if (editDataForm) {
        const formInputs = editDataForm.querySelectorAll('input, select');
        formInputs.forEach(input => {
            input.addEventListener('change', updateFormData);
        });
    }
    
    // Also monitor changes to dynamically added item rows
    if (itemsContainer) {
        itemsContainer.addEventListener('change', function(event) {
            if (event.target.classList.contains('item-name') || 
                event.target.classList.contains('item-quantity') || 
                event.target.classList.contains('item-price') || 
                event.target.classList.contains('item-tax-deductible')) {
                updateFormData();
            }
        });
    }
});