// Receipt Scanner Application - JavaScript

// Image compression settings - using namespacing to avoid conflicts
// Only define these variables if they don't already exist
if (typeof window.RECEIPT_APP === 'undefined') {
    window.RECEIPT_APP = {};
}
window.RECEIPT_APP.IMAGE_MAX_WIDTH = 1600;
window.RECEIPT_APP.IMAGE_MAX_HEIGHT = 1200;
window.RECEIPT_APP.IMAGE_QUALITY = 0.85;

/**
 * Compresses an image file to reduce size before upload
 * 
 * @param {File} file - The original file from input
 * @param {number} maxWidth - Maximum width in pixels
 * @param {number} maxHeight - Maximum height in pixels
 * @param {number} quality - JPEG quality (0-1)
 * @returns {Promise<Blob>} Promise resolving to compressed image blob
 */
function compressImage(file, maxWidth = window.RECEIPT_APP.IMAGE_MAX_WIDTH, maxHeight = window.RECEIPT_APP.IMAGE_MAX_HEIGHT, quality = window.RECEIPT_APP.IMAGE_QUALITY) {
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

/**
 * Gets the CSRF token from the hidden input field
 * @returns {string} The CSRF token value
 */
function getCsrfToken() {
    const tokenField = document.querySelector('input[name="csrf_token"]');
    return tokenField ? tokenField.value : '';
}

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
        
        // Add CSRF token
        formData.append('csrf_token', getCsrfToken());
        
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
            headers: {
                'X-CSRFToken': getCsrfToken()
            },
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
                
                // Show animation with receipt value and max savings
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
                    
                    // Show the animation
                    loadingElement.classList.add('d-none');
                    receiptValueAnimation.classList.remove('d-none');
                    receiptValueAnimation.querySelector('.card').classList.add('animate__animated', 'animate__fadeInUp');
                    
                    // Set up button event handlers
                    const seeDetailsBtn = document.getElementById('see-details-btn');
                    const scanAnotherBtn = document.getElementById('scan-another-btn');
                    
                    if (seeDetailsBtn) {
                        seeDetailsBtn.addEventListener('click', function() {
                            // Fade out animation and show details form
                            receiptValueAnimation.querySelector('.card').classList.remove('animate__fadeInUp');
                            receiptValueAnimation.querySelector('.card').classList.add('animate__fadeOutDown');
                            
                            setTimeout(() => {
                                receiptValueAnimation.classList.add('d-none');
                                resultsElement.classList.remove('d-none');
                            }, 500); // Short delay for animation
                        });
                    }
                    
                    if (scanAnotherBtn) {
                        scanAnotherBtn.addEventListener('click', function() {
                            // First save the receipt
                            const formData = collectFormData();
                            
                            // Save animation
                            scanAnotherBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Saving...';
                            scanAnotherBtn.disabled = true;
                            
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
                                
                                // Success! Reset the form and go back to scan state
                                receiptValueAnimation.classList.add('d-none');
                                previewContainer.classList.add('d-none');
                                uploadArea.classList.remove('d-none');
                                
                                // Reset file input
                                receiptInput.value = '';
                                document.dispatchEvent(new Event('resetFileInput'));
                                
                                // Reset processing flag
                                window.processingReceipt = false;
                                
                                // Show success message
                                showAlert('Receipt saved successfully! Ready for next scan.', 'success');
                            })
                            .catch(error => {
                                console.error('Error:', error);
                                showError(error.message || 'An error occurred while saving the receipt');
                                
                                // Enable button again
                                scanAnotherBtn.disabled = false;
                                scanAnotherBtn.innerHTML = '<i class="fas fa-camera me-2"></i> Save & Scan Another';
                            });
                        });
                    }
                } else {
                    // Fallback if animation element doesn't exist
                    loadingElement.classList.add('d-none');
                    resultsElement.classList.remove('d-none');
                }
                
                // Update header based on the design
                if (pageTitleElement) {
                    pageTitleElement.textContent = 'Review Receipt Data';
                }
                
                // Reset processing flag
                window.processingReceipt = false;
                
                // Show save button
                showSaveButton();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showError(error.message || 'An error occurred while processing the receipt');
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
                
                // Show animation with receipt value and max savings
                const receiptValueAnimation = document.getElementById('receipt-value-animation');
                if (receiptValueAnimation) {
                    // Get receipt values
                    const receiptTotal = parseFloat(data.result.total_amount) || 0;
                    const vatTax = parseFloat(data.result.vat_tax) || 0;
                    const ssclTax = parseFloat(data.result.sscl_tax) || 0;
                    
                    // Calculate maximum savings (36% of the receipt value, assuming all is tax deductible)
                    const maxSaving = receiptTotal * 0.36;
                    
                    // Update the animation values
                    document.getElementById('receipt-value-amount').textContent = `Rs ${receiptTotal.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                    document.getElementById('max-saving-amount').textContent = `Rs ${maxSaving.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                    document.getElementById('vat-amount').textContent = `Rs ${vatTax.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                    document.getElementById('sscl-amount').textContent = `Rs ${ssclTax.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                    
                    // Show the animation
                    loadingElement.classList.add('d-none');
                    receiptValueAnimation.classList.remove('d-none');
                    receiptValueAnimation.querySelector('.card').classList.add('animate__animated', 'animate__fadeInUp');
                    
                    // Set up button event handlers
                    const seeDetailsBtn = document.getElementById('see-details-btn');
                    const scanAnotherBtn = document.getElementById('scan-another-btn');
                    
                    if (seeDetailsBtn) {
                        seeDetailsBtn.addEventListener('click', function() {
                            // Fade out animation and show details form
                            receiptValueAnimation.querySelector('.card').classList.remove('animate__fadeInUp');
                            receiptValueAnimation.querySelector('.card').classList.add('animate__fadeOutDown');
                            
                            setTimeout(() => {
                                receiptValueAnimation.classList.add('d-none');
                                resultsElement.classList.remove('d-none');
                            }, 500); // Short delay for animation
                        });
                    }
                    
                    if (scanAnotherBtn) {
                        scanAnotherBtn.addEventListener('click', function() {
                            // First save the receipt
                            const formData = collectFormData();
                            
                            // Save animation
                            scanAnotherBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Saving...';
                            scanAnotherBtn.disabled = true;
                            
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
                                
                                // Success! Reset the form and go back to scan state
                                receiptValueAnimation.classList.add('d-none');
                                previewContainer.classList.add('d-none');
                                uploadArea.classList.remove('d-none');
                                
                                // Reset file input
                                receiptInput.value = '';
                                document.dispatchEvent(new Event('resetFileInput'));
                                
                                // Reset processing flag
                                window.processingReceipt = false;
                                
                                // Show success message
                                showAlert('Receipt saved successfully! Ready for next scan.', 'success');
                            })
                            .catch(error => {
                                console.error('Error:', error);
                                showError(error.message || 'An error occurred while saving the receipt');
                                
                                // Enable button again
                                scanAnotherBtn.disabled = false;
                                scanAnotherBtn.innerHTML = '<i class="fas fa-camera me-2"></i> Save & Scan Another';
                            });
                        });
                    }
                } else {
                    // Fallback if animation element doesn't exist
                    loadingElement.classList.add('d-none');
                    resultsElement.classList.remove('d-none');
                }
                
                // Update header based on the design
                const pageTitleElement = document.querySelector('.page-title');
                if (pageTitleElement) {
                    pageTitleElement.textContent = 'Review Receipt Data';
                }
                
                // Reset processing flag
                window.processingReceipt = false;
                
                // Show save button
                showSaveButton();
            } else if (data.state === 'FAILURE') {
                console.error('Task failed:', data.info);
                showError(data.info || 'Failed to process receipt');
                loadingElement.classList.add('d-none');
                
                // Reset processing flag
                window.processingReceipt = false;
                
                // Show reset button when there's an error
                const uploadArea = document.querySelector('.upload-area');
                if (uploadArea) {
                    uploadArea.classList.remove('d-none');
                    // Dispatch event to reattach the file input listener
                    document.dispatchEvent(new Event('resetFileInput'));
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
            
            // Reset processing flag
            window.processingReceipt = false;
            
            // Show upload area again and reset file input
            const uploadArea = document.querySelector('.upload-area');
            if (uploadArea) {
                uploadArea.classList.remove('d-none');
                // Dispatch event to reattach the file input listener
                document.dispatchEvent(new Event('resetFileInput'));
            }
        });
    }

    // Handle save button click
    if (saveButton) {
        saveButton.addEventListener('click', function() {
            // Prevent multiple submissions
            if (window.savingReceipt) {
                console.log('Already saving a receipt, ignoring duplicate request');
                return;
            }
            
            window.savingReceipt = true;
            
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
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
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
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
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
                
                // Reset saving flag
                window.savingReceipt = false;
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
        
        // Load receipt context first to ensure we have the latest organization and client data
        fetch('/api/receipt/get-context')
            .then(response => response.json())
            .then(contextData => {
                if (contextData.error) {
                    console.error('Error loading receipt context:', contextData.error);
                    return;
                }
                
                console.log('Receipt context loaded in populateForm:', contextData);
                
                // Handle company expense toggle and fields
                const isCompanyExpenseCheckbox = document.getElementById('is_company_expense');
                const companyExpenseDetails = document.getElementById('company-expense-details');
                const detailPersonalLabel = document.getElementById('detail-personal-label');
                const detailCompanyLabel = document.getElementById('detail-company-label');
                
                // Default to using context data, but if data.is_company_expense is explicitly set, use that instead
                const isCompanyExpense = data.is_company_expense !== undefined 
                    ? data.is_company_expense 
                    : (contextData.expense_type === 'company');
                
                // Update labels active state
                if (detailPersonalLabel && detailCompanyLabel) {
                    if (isCompanyExpense) {
                        detailPersonalLabel.classList.remove('active-expense-type');
                        detailCompanyLabel.classList.add('active-expense-type');
                    } else {
                        detailPersonalLabel.classList.add('active-expense-type');
                        detailCompanyLabel.classList.remove('active-expense-type');
                    }
                }
                
                // Set the toggle and show/hide company details section
                if (isCompanyExpenseCheckbox) {
                    isCompanyExpenseCheckbox.checked = isCompanyExpense;
                }
                
                if (companyExpenseDetails) {
                    companyExpenseDetails.style.display = isCompanyExpense ? 'block' : 'none';
                }
                
                // If it's a company expense, set organization and client
                if (isCompanyExpense) {
                    const expenseOrganizationSelect = document.getElementById('expense_organization_id');
                    
                    // Use organization ID from context if available, otherwise from data
                    const organizationId = data.expense_organization_id || contextData.organization_id;
                    
                    // Use client ID from context if available, otherwise from data
                    const clientId = data.expense_client_id || contextData.client_id;
                    
                    if (organizationId && expenseOrganizationSelect) {
                        // Trigger organization loading
                        loadOrganizations();
                        
                        // Set a small delay to ensure the organizations are loaded before selecting
                        setTimeout(() => {
                            // Try to select the organization
                            expenseOrganizationSelect.value = organizationId;
                            
                            // Trigger change event to load clients
                            expenseOrganizationSelect.dispatchEvent(new Event('change'));
                            
                            // Try to select client after a delay to ensure clients are loaded
                            if (clientId) {
                                setTimeout(() => {
                                    const expenseClientSelect = document.getElementById('expense_client_id');
                                    if (expenseClientSelect) {
                                        expenseClientSelect.value = clientId;
                                        console.log(`Selected client ID ${clientId} from context`);
                                        
                                        // If we have client details, display them nicely
                                        if (contextData.client && contextData.client.name) {
                                            // Check if client display already exists, create if it doesn't
                                            let clientDisplay = document.querySelector('.client-display');
                                            if (!clientDisplay) {
                                                clientDisplay = document.createElement('div');
                                                clientDisplay.className = 'client-display';
                                                // Insert it after the client dropdown
                                                const clientDropdownParent = expenseClientSelect.parentElement;
                                                clientDropdownParent.appendChild(clientDisplay);
                                            }
                                            
                                            // Update client display with name
                                            clientDisplay.innerHTML = `
                                                <strong>Selected Client:</strong> 
                                                <span class="client-chip">
                                                    <i class="fas fa-building"></i>
                                                    ${contextData.client.name}
                                                </span>
                                            `;
                                        }
                                    }
                                }, 800); // Longer delay to ensure clients are fully loaded
                            }
                        }, 300);
                    }
                }
                
                // Handle reimbursable checkbox if available
                const isReimbursableCheckbox = document.getElementById('is_reimbursable');
                if (isReimbursableCheckbox && data.is_reimbursable !== undefined) {
                    isReimbursableCheckbox.checked = data.is_reimbursable;
                }
                
                // Set expense description if available
                const expenseDescriptionField = document.getElementById('expense_description');
                if (expenseDescriptionField && data.expense_description) {
                    expenseDescriptionField.value = data.expense_description;
                }
            })
            .catch(error => {
                console.error('Error fetching receipt context:', error);
            });
        
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
        
        console.log('Form populated with data:', data);
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
            // Company expense fields
            is_company_expense: document.getElementById('is_company_expense') ? document.getElementById('is_company_expense').checked : false,
            is_reimbursable: document.getElementById('is_reimbursable') ? document.getElementById('is_reimbursable').checked : true,
            expense_description: document.getElementById('expense_description') ? document.getElementById('expense_description').value : '',
            expense_organization_id: (function() {
                // Try to get the organization ID from multiple possible sources
                const detailedFormOrgSelect = document.getElementById('expense_organization_id');
                const initialFormOrgSelect = document.getElementById('company_organization_id');
                
                // First try the detailed form's organization select
                if (detailedFormOrgSelect && detailedFormOrgSelect.value) {
                    return detailedFormOrgSelect.value;
                }
                // Then try the initial form's organization select
                else if (initialFormOrgSelect && initialFormOrgSelect.value) {
                    return initialFormOrgSelect.value;
                }
                return null;
            })(),
            expense_client_id: document.getElementById('expense_client_id') ? document.getElementById('expense_client_id').value : null,
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
                
                // Debug the structure of the first receipt if available
                if (data.receipts && data.receipts.length > 0) {
                    console.log("First receipt details:", JSON.stringify(data.receipts[0], null, 2));
                }
                
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
    
    // Company expense toggle functionality
    const isCompanyExpenseCheckbox = document.getElementById('is_company_expense');
    const companyExpenseDetails = document.getElementById('company-expense-details');
    const expenseOrganizationSelect = document.getElementById('expense_organization_id');
    const expenseClientSelect = document.getElementById('expense_client_id');
    const initialClientSelect = document.getElementById('initial_client_id');
    const companyOrganizationSelect = document.getElementById('company_organization_id');
    
    // Load organizations when company expense is checked
    function loadOrganizations() {
        fetch('/api/user/organizations')
            .then(response => response.json())
            .then(data => {
                if (data.organizations) {
                    // Clear existing options except the default
                    if (expenseOrganizationSelect) {
                        expenseOrganizationSelect.innerHTML = '<option value="" selected disabled>Select organization...</option>';
                    }
                    
                    if (companyOrganizationSelect) {
                        companyOrganizationSelect.innerHTML = '<option value="" selected disabled>Select organization...</option>';
                    }
                    
                    // Filter out personal finances organizations for company expense
                    const companyOrgs = data.organizations.filter(org => 
                        org.name.toLowerCase() !== 'personal finances');
                    
                    // Add options to the expense organization dropdown
                    if (expenseOrganizationSelect) {
                        companyOrgs.forEach(org => {
                            const option = document.createElement('option');
                            option.value = org.id;
                            option.textContent = org.name;
                            // Set default organization as selected
                            if (org.is_default) {
                                option.selected = true;
                            }
                            expenseOrganizationSelect.appendChild(option);
                        });
                    }
                    
                    // Add options to the initial company organization dropdown
                    if (companyOrganizationSelect) {
                        companyOrgs.forEach(org => {
                            const option = document.createElement('option');
                            option.value = org.id;
                            option.textContent = org.name;
                            // Set default organization as selected
                            if (org.is_default) {
                                option.selected = true;
                            }
                            companyOrganizationSelect.appendChild(option);
                        });
                    }
                    
                    // Find the default company organization
                    const defaultCompanyOrg = companyOrgs.find(org => org.is_default);
                    
                    // Set the selected organization in the expense dropdown
                    if (expenseOrganizationSelect) {
                        if (defaultCompanyOrg) {
                            expenseOrganizationSelect.value = defaultCompanyOrg.id;
                        } else if (companyOrgs.length > 0) {
                            // Otherwise, select the first company organization
                            expenseOrganizationSelect.value = companyOrgs[0].id;
                        }
                        
                        // Load clients for the selected organization
                        if (expenseOrganizationSelect.value) {
                            loadClientsForOrganization(expenseOrganizationSelect.value);
                        }
                    }
                    
                    // Set the selected organization in the initial dropdown
                    if (companyOrganizationSelect) {
                        if (defaultCompanyOrg) {
                            companyOrganizationSelect.value = defaultCompanyOrg.id;
                        } else if (companyOrgs.length > 0) {
                            // Otherwise, select the first company organization
                            companyOrganizationSelect.value = companyOrgs[0].id;
                        }
                        
                        // Load clients for the selected organization
                        if (companyOrganizationSelect.value) {
                            loadClientsForOrganization(companyOrganizationSelect.value);
                        }
                    }
                    
                    console.log('Loaded organizations for dropdowns:', companyOrgs.length);
                }
            })
            .catch(error => console.error('Error loading organizations:', error));
    }
    
    // Helper function to update the receipt context with organization and client information
    function updateReceiptContext(organizationId, clientId = null) {
        if (!organizationId) return Promise.reject(new Error('Organization ID is required'));
        
        return fetch('/api/receipt/save-context', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                organization_id: organizationId,
                expense_type: 'company',
                client_id: clientId
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error saving receipt context:', data.error);
                throw new Error(data.error);
            }
            
            console.log('Receipt context updated:', data);
            return data;
        });
    }
    
    // Helper function to load the current receipt context
    function loadReceiptContext() {
        return fetch('/api/receipt/get-context')
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    console.error('Error loading receipt context:', data.error);
                    throw new Error(data.error);
                }
                
                console.log('Receipt context loaded:', data);
                return data;
            });
    }
    
    // Load clients for a specific organization with improved error handling
    function loadClientsForOrganization(organizationId) {
        console.log(`Loading clients for organization ID: ${organizationId}`);
        
        if (!expenseClientSelect && !initialClientSelect) {
            console.error('No client dropdowns found in the DOM');
            return;
        }
        
        if (!organizationId) {
            console.error('No organization ID provided for client fetch');
            return;
        }
        
        // Clear existing options and show loading state in both dropdowns
        if (expenseClientSelect) {
            expenseClientSelect.innerHTML = '<option value="" selected>Loading clients...</option>';
        }
        
        if (initialClientSelect) {
            initialClientSelect.innerHTML = '<option value="" selected>Loading clients...</option>';
        }
        
        // Remove any existing client display component
        const existingClientDisplay = document.querySelector('.client-display');
        if (existingClientDisplay) {
            existingClientDisplay.remove();
        }
        
        // First update the receipt context with the organization ID
        updateReceiptContext(organizationId);
        
        // Try both API endpoints for backward compatibility
        fetch(`/api/organization/${organizationId}/clients`)
            .then(response => {
                // Check for HTTP errors
                if (!response.ok) {
                    // If the first endpoint fails, try the alternate endpoint
                    console.log(`First client endpoint failed with status ${response.status}, trying alternate endpoint`);
                    return fetch(`/api/organizations/${organizationId}/clients`);
                }
                return response;
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                // Log response data for debugging
                console.log(`Client data received:`, data);
                
                if (data.error) {
                    throw new Error(data.error);
                }
                
                // Function to populate a client dropdown
                const populateClientDropdown = (dropdown) => {
                    if (!dropdown) return;
                    
                    // Clear and set default option
                    dropdown.innerHTML = '<option value="" selected>Select Client (Optional)</option>';
                    
                    if (data.clients && data.clients.length > 0) {
                        // Add options for each client
                        data.clients.forEach(client => {
                            const option = document.createElement('option');
                            option.value = client.id;
                            option.textContent = client.name;
                            // Store full client data as a data attribute for later use
                            option.dataset.clientInfo = JSON.stringify({
                                id: client.id,
                                name: client.name,
                                company_name: client.company_name,
                                contact_person: client.contact_person,
                                email: client.email
                            });
                            dropdown.appendChild(option);
                        });
                    } else {
                        // Add a message when no clients exist
                        const noClientsOption = document.createElement('option');
                        noClientsOption.disabled = true;
                        noClientsOption.textContent = 'No clients found for this organization';
                        dropdown.appendChild(noClientsOption);
                    }
                };
                
                // Populate both dropdowns
                populateClientDropdown(expenseClientSelect);
                populateClientDropdown(initialClientSelect);
                
                if (data.clients && data.clients.length > 0) {
                    console.log(`Added ${data.clients.length} clients to dropdown(s)`);
                    
                    // After loading clients, check if we need to restore a previously selected client
                    loadReceiptContext()
                        .then(contextData => {
                            if (contextData && contextData.client_id) {
                                // Set the client ID in both dropdowns if they exist
                                const setDropdownValue = (dropdown) => {
                                    if (!dropdown) return;
                                    
                                    // Check if this client ID exists in the dropdown
                                    const options = Array.from(dropdown.options);
                                    const clientOption = options.find(opt => opt.value === contextData.client_id.toString());
                                    
                                    if (clientOption) {
                                        console.log(`Restoring previously selected client in dropdown: ${contextData.client_id}`);
                                        dropdown.value = contextData.client_id;
                                        // Trigger a change event to update any dependent UI
                                        const event = new Event('change', { bubbles: true });
                                        dropdown.dispatchEvent(event);
                                    }
                                };
                                
                                setDropdownValue(expenseClientSelect);
                                setDropdownValue(initialClientSelect);
                            }
                        })
                        .catch(err => {
                            console.error('Error loading receipt context:', err);
                        });
                }
            })
            .catch(error => {
                console.error('Error loading clients:', error);
                
                // Function to show error in a dropdown
                const showErrorInDropdown = (dropdown) => {
                    if (!dropdown) return;
                    
                    // Provide user feedback
                    dropdown.innerHTML = '<option value="" selected>Error loading clients</option>';
                    
                    // Add a retry option
                    const retryOption = document.createElement('option');
                    retryOption.value = "retry";
                    retryOption.textContent = "Click to retry loading clients";
                    dropdown.appendChild(retryOption);
                    
                    // Add listener for retry
                    dropdown.addEventListener('change', function(e) {
                        if (e.target.value === "retry") {
                            loadClientsForOrganization(organizationId);
                        }
                    }, { once: true });
                };
                
                // Show error in both dropdowns
                showErrorInDropdown(expenseClientSelect);
                showErrorInDropdown(initialClientSelect);
            });
    }
    
    // Load organizations immediately when the page loads
    // This will populate both organization dropdowns and their respective client dropdowns
    if (companyOrganizationSelect || expenseOrganizationSelect) {
        loadOrganizations();
    }
    
    // Set up event listener for company organization dropdown change
    if (companyOrganizationSelect) {
        companyOrganizationSelect.addEventListener('change', function() {
            if (this.value) {
                loadClientsForOrganization(this.value);
            } else {
                // Clear client dropdown if no organization selected
                if (initialClientSelect) {
                    initialClientSelect.innerHTML = '<option value="" selected>Select Client (Optional)</option>';
                }
            }
        });
    }
    
    if (isCompanyExpenseCheckbox && companyExpenseDetails) {
        isCompanyExpenseCheckbox.addEventListener('change', function() {
            if (this.checked) {
                companyExpenseDetails.style.display = 'block';
                // Load organizations when company expense is checked
                if (expenseOrganizationSelect) {
                    loadOrganizations();
                }
            } else {
                companyExpenseDetails.style.display = 'none';
            }
        });
        
        // Handle organization change to load appropriate clients
        if (expenseOrganizationSelect && expenseClientSelect) {
            expenseOrganizationSelect.addEventListener('change', function() {
                if (this.value) {
                    loadClientsForOrganization(this.value);
                } else {
                    // Clear client dropdown if no organization selected
                    expenseClientSelect.innerHTML = '<option value="" selected>Select Client (Optional)</option>';
                    
                    // Remove any existing client display
                    const existingClientDisplay = document.querySelector('.client-display');
                    if (existingClientDisplay) {
                        existingClientDisplay.remove();
                    }
                }
            });
            
            // Set up the initial client dropdown event handling
            if (initialClientSelect) {
                initialClientSelect.addEventListener('change', function() {
                    const clientId = this.value;
                    const organizationId = companyOrganizationSelect?.value;
                    
                    // Only proceed if a valid client is selected (not empty or retry option)
                    if (clientId && clientId !== 'retry' && organizationId) {
                        // Update the receipt context
                        updateReceiptContext(organizationId, clientId)
                            .then(() => {
                                console.log('Initial screen client selection updated successfully');
                                
                                // If we have the expense client dropdown on the page, sync it
                                if (expenseClientSelect && expenseClientSelect.options.length > 1) {
                                    expenseClientSelect.value = clientId;
                                }
                            })
                            .catch(error => {
                                console.error('Error updating receipt context with client from initial screen:', error);
                            });
                    }
                });
            }
            
            // Handle client dropdown change to update context and display
            expenseClientSelect.addEventListener('change', function() {
                const clientId = this.value;
                const organizationId = expenseOrganizationSelect.value;
                
                // Remove existing client display if any
                const existingClientDisplay = document.querySelector('.client-display');
                if (existingClientDisplay) {
                    existingClientDisplay.remove();
                }
                
                // Only proceed if a valid client is selected (not empty or retry option)
                if (clientId && clientId !== 'retry' && organizationId) {
                    // Update the receipt context
                    updateReceiptContext(organizationId, clientId)
                        .then(() => {
                            // Create client display element
                            const selectedOption = this.options[this.selectedIndex];
                            if (selectedOption) {
                                try {
                                    // Try to get client info from data attribute
                                    let clientInfo = null;
                                    if (selectedOption.dataset.clientInfo) {
                                        clientInfo = JSON.parse(selectedOption.dataset.clientInfo);
                                    }
                                    
                                    // Create client display element
                                    const clientDisplay = document.createElement('div');
                                    clientDisplay.className = 'client-display mt-2';
                                    
                                    // Customize display based on available info
                                    if (clientInfo && (clientInfo.company_name || clientInfo.contact_person)) {
                                        clientDisplay.innerHTML = `
                                            <div class="d-flex align-items-center">
                                                <strong class="me-2">Selected Client:</strong>
                                                <span class="client-chip">
                                                    <i class="fas fa-building me-1"></i>
                                                    ${clientInfo.name}
                                                </span>
                                            </div>
                                            <small class="text-muted d-block mt-1">
                                                ${clientInfo.company_name ? `Company: ${clientInfo.company_name}` : ''}
                                                ${clientInfo.contact_person ? `<br>Contact: ${clientInfo.contact_person}` : ''}
                                            </small>
                                        `;
                                    } else {
                                        // Simple display with just the name
                                        clientDisplay.innerHTML = `
                                            <strong>Selected Client:</strong>
                                            <span class="client-chip">
                                                <i class="fas fa-building me-1"></i>
                                                ${selectedOption.textContent}
                                            </span>
                                        `;
                                    }
                                    
                                    // Add to the DOM after the client dropdown
                                    this.parentElement.appendChild(clientDisplay);
                                } catch (error) {
                                    console.error('Error creating client display:', error);
                                }
                            }
                        })
                        .catch(error => {
                            console.error('Error updating receipt context with client:', error);
                        });
                }
            });
        }
    }
});