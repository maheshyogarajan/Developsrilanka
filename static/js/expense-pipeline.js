// Expense Pipeline JavaScript

// Global variables to store pipeline state
let pipelineData = null;
let activeDropzone = null;
let draggedItem = null;
let sortableInstances = [];
let loadingTimeout = null;
let expenseDetailsCache = {};

// Configuration
const LOADING_TIMEOUT_MS = 10000; // 10 seconds
const DEBOUNCE_DELAY_MS = 300; // 300ms for debouncing

document.addEventListener('DOMContentLoaded', function() {
    console.log('Initializing expense pipeline kanban board...');
    
    try {
        // Check if pipeline element exists
        const pipelineContainer = document.getElementById('expensePipeline');
        if (!pipelineContainer) {
            console.warn("Pipeline container not found, aborting initialization");
            return;
        }
        
        console.log("Pipeline element found:", !!pipelineContainer);
        console.log("Pipeline script element found:", !!document.getElementById('expensePipelineJS'));
        
        // Verify SortableJS is available
        if (typeof Sortable === 'undefined') {
            console.error("SortableJS not available, check script inclusion");
            console.log("SortableJS available:", typeof Sortable !== 'undefined');
            return;
        }
        
        // Manual test to log important info
        const urlParams = new URLSearchParams(window.location.search);
        const orgId = urlParams.get('org_id') || document.getElementById('organizationSelector')?.value || 'default';
        const view = urlParams.get('view') || document.getElementById('viewSelector')?.value || 'default';
        console.log("Manual test: Fetching data for org " + orgId + ", view " + view);
        
        // Initialize pipeline data
        loadPipelineData();
        
        // Set up event listeners
        const organizationSelector = document.getElementById('organizationSelector');
        if (organizationSelector) {
            organizationSelector.addEventListener('change', function() {
                debounce(loadPipelineData, DEBOUNCE_DELAY_MS)();
                updateURLParams({org_id: this.value});
            });
        }
        
        const viewSelector = document.getElementById('viewSelector');
        if (viewSelector) {
            viewSelector.addEventListener('change', function() {
                debounce(loadPipelineData, DEBOUNCE_DELAY_MS)();
                updateURLParams({view: this.value});
            });
        }
        
        const clientSelector = document.getElementById('clientSelector');
        if (clientSelector) {
            clientSelector.addEventListener('change', function() {
                debounce(loadPipelineData, DEBOUNCE_DELAY_MS)();
                updateURLParams({client_id: this.value});
            });
        }
        
        const refreshButton = document.getElementById('refreshButton');
        if (refreshButton) {
            refreshButton.addEventListener('click', function() {
                loadPipelineData(true); // Force refresh
            });
        }
        
        const batchSubmitButton = document.getElementById('batchSubmitButton');
        if (batchSubmitButton) {
            batchSubmitButton.addEventListener('click', function() {
                openBatchSubmissionModal();
            });
        }
        
        const exportButton = document.getElementById('exportButton');
        if (exportButton) {
            exportButton.addEventListener('click', function() {
                exportPipelineData();
            });
        }
        
        // Handle date filter changes
        const dateFromFilter = document.getElementById('dateFromFilter');
        if (dateFromFilter) {
            dateFromFilter.addEventListener('change', function() {
                debounce(loadPipelineData, DEBOUNCE_DELAY_MS)();
                updateURLParams({date_from: this.value});
            });
        }
        
        const dateToFilter = document.getElementById('dateToFilter');
        if (dateToFilter) {
            dateToFilter.addEventListener('change', function() {
                debounce(loadPipelineData, DEBOUNCE_DELAY_MS)();
                updateURLParams({date_to: this.value});
            });
        }
        
        // Set up modal action buttons
        const submitBatchButton = document.getElementById('submitBatchButton');
        if (submitBatchButton) {
            submitBatchButton.addEventListener('click', function() {
                submitBatchExpenses();
            });
        }
        
        const saveReimbursementButton = document.getElementById('saveReimbursementButton');
        if (saveReimbursementButton) {
            saveReimbursementButton.addEventListener('click', function() {
                saveReimbursementDetails();
            });
        }
        
        const confirmRejectButton = document.getElementById('confirmRejectButton');
        if (confirmRejectButton) {
            confirmRejectButton.addEventListener('click', function() {
                confirmRejectExpense();
            });
        }
        
        // Set up window resize handler for mobile optimizations
        window.addEventListener('resize', handleWindowResize);
        
        // Initialize mobile indicators if needed
        handleWindowResize();
        
        // Listen for URL parameter changes (browser back/forward buttons)
        window.addEventListener('popstate', function() {
            loadPipelineDataFromURL();
        });
    } catch (error) {
        console.error("Error initializing expense pipeline:", error);
        // Display a user-friendly error
        const pipelineContainer = document.getElementById('expensePipeline');
        if (pipelineContainer) {
            pipelineContainer.innerHTML = `
                <div class="alert alert-danger">
                    <h4>Error Loading Pipeline</h4>
                    <p>There was a problem loading the expense pipeline. Please try refreshing the page.</p>
                    <button class="btn btn-outline-primary" onclick="location.reload()">Refresh Page</button>
                </div>
            `;
        }
    }
});

/**
 * Debounce function to prevent excessive API calls
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} - Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function() {
        const context = this;
        const args = arguments;
        clearTimeout(timeout);
        timeout = setTimeout(function() {
            func.apply(context, args);
        }, wait);
    };
}

/**
 * Update URL parameters without reloading the page
 * @param {Object} params - Parameters to update
 */
function updateURLParams(params) {
    const url = new URL(window.location.href);
    
    // Update or add each parameter
    Object.keys(params).forEach(key => {
        if (params[key] === null || params[key] === '') {
            url.searchParams.delete(key);
        } else {
            url.searchParams.set(key, params[key]);
        }
    });
    
    // Replace the current URL without reloading
    window.history.pushState({}, '', url.toString());
}

/**
 * Load pipeline data from URL parameters
 */
function loadPipelineDataFromURL() {
    const urlParams = new URLSearchParams(window.location.search);
    
    // Update form field values from URL
    const orgSelector = document.getElementById('organizationSelector');
    if (urlParams.has('org_id') && orgSelector) {
        orgSelector.value = urlParams.get('org_id');
    }
    
    const viewSelector = document.getElementById('viewSelector');
    if (urlParams.has('view') && viewSelector) {
        viewSelector.value = urlParams.get('view');
    }
    
    const clientSelector = document.getElementById('clientSelector');
    if (urlParams.has('client_id') && clientSelector) {
        clientSelector.value = urlParams.get('client_id');
    }
    
    const dateFromFilter = document.getElementById('dateFromFilter');
    if (urlParams.has('date_from') && dateFromFilter) {
        dateFromFilter.value = urlParams.get('date_from');
    }
    
    const dateToFilter = document.getElementById('dateToFilter');
    if (urlParams.has('date_to') && dateToFilter) {
        dateToFilter.value = urlParams.get('date_to');
    }
    
    // Load the data
    loadPipelineData();
}

/**
 * Load pipeline data from the server
 * @param {boolean} force - Force refresh even if data is already loaded
 */
function loadPipelineData(force = false) {
    const organizationId = document.getElementById('organizationSelector').value;
    const viewType = document.getElementById('viewSelector').value;
    const clientId = document.getElementById('clientSelector')?.value || '';
    const dateFrom = document.getElementById('dateFromFilter')?.value || '';
    const dateTo = document.getElementById('dateToFilter')?.value || '';
    
    const pipelineContainer = document.getElementById('expensePipeline');
    
    // Clear any existing loading timeout
    if (loadingTimeout) {
        clearTimeout(loadingTimeout);
    }
    
    // Show loading indicator
    pipelineContainer.innerHTML = `
        <div class="loading-indicator text-center py-5 w-100">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-2">Loading expense pipeline data...</p>
        </div>
    `;
    
    // Set a timeout for loading
    loadingTimeout = setTimeout(() => {
        pipelineContainer.innerHTML = `
            <div class="alert alert-warning w-100" role="alert">
                <strong>Loading is taking longer than expected.</strong> The server might be busy.
                <button class="btn btn-sm btn-outline-secondary ms-2" onclick="loadPipelineData(true)">
                    <i class="fas fa-sync-alt"></i> Try Again
                </button>
            </div>
        `;
    }, LOADING_TIMEOUT_MS);
    
    // Build the query string
    let queryParams = `org_id=${organizationId}&view=${viewType}`;
    if (clientId) queryParams += `&client_id=${clientId}`;
    if (dateFrom) queryParams += `&date_from=${dateFrom}`;
    if (dateTo) queryParams += `&date_to=${dateTo}`;
    
    // Fetch pipeline data from the server
    console.log(`Fetching pipeline data from: /expenses/api/pipeline-data?${queryParams}`);
    // Add X-Requested-With header for AJAX detection
    const fetchOptions = {
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    };
    
    fetch(`/expenses/api/pipeline-data?${queryParams}`, fetchOptions)
        .then(response => {
            console.log('Pipeline API response status:', response.status);
            clearTimeout(loadingTimeout);
            
            return response.json().then(data => {
                // If not successful, handle error
                if (!response.ok) {
                    console.error('Error details:', data);
                    
                    // Handle authentication errors specifically
                    if (response.status === 401 && data.redirect) {
                        console.log('Authentication required, redirecting to login page');
                        pipelineContainer.innerHTML = `
                            <div class="alert alert-warning w-100" role="alert">
                                <strong>Your session has expired.</strong> Please <a href="${data.redirect}" class="alert-link">log in</a> again.
                            </div>
                        `;
                        // Wait 2 seconds before redirecting
                        setTimeout(() => {
                            window.location.href = data.redirect;
                        }, 2000);
                        throw new Error('Authentication required');
                    }
                    
                    // Handle other errors
                    if (data.error) {
                        throw new Error(data.error);
                    } else if (data.details) {
                        throw new Error(data.details);
                    } else {
                        throw new Error('Failed to load pipeline data: ' + response.status);
                    }
                }
                
                return data;
            }).catch(jsonError => {
                // If we can't parse the error as JSON, just throw a generic error
                if (response.status === 401) {
                    pipelineContainer.innerHTML = `
                        <div class="alert alert-warning w-100" role="alert">
                            <strong>Your session has expired.</strong> Please <a href="/login" class="alert-link">log in</a> again.
                        </div>
                    `;
                    setTimeout(() => {
                        window.location.href = '/login';
                    }, 2000);
                    throw new Error('Authentication required');
                }
                throw new Error('Failed to load pipeline data: ' + response.status);
            });
        })
        .then(data => {
            // Check if the response is an error message
            if (data.error || data.type === 'api_error') {
                console.error('API returned error:', data);
                throw new Error(data.details || data.error || 'Unknown error');
            }
            
            console.log('Pipeline data received:', data);
            pipelineData = data;
            renderPipeline(data);
            initializeDragAndDrop();
            updateMobileIndicators(data);
        })
        .catch(error => {
            clearTimeout(loadingTimeout);
            
            // Try to parse the error response
            let errorMessage = error.message || 'Unknown error';
            let detailedError = '';
            
            if (error.response) {
                // Try to extract detailed error information from our enhanced error format
                error.response.json().then(errorData => {
                    console.error('Detailed error:', errorData);
                    if (errorData.details) {
                        errorMessage = errorData.details;
                    }
                    if (errorData.trace) {
                        detailedError = errorData.trace;
                    }
                    displayErrorMessage(errorMessage, detailedError);
                }).catch(e => {
                    // If we can't parse the JSON, just use what we have
                    displayErrorMessage(errorMessage);
                });
            } else {
                displayErrorMessage(errorMessage);
            }
        });
}

function displayErrorMessage(errorMessage, detailedError = '') {
    console.error('Error loading pipeline data:', errorMessage);
    
    try {
        // Get the pipeline container
        const pipelineContainer = document.getElementById('expensePipeline');
        
        if (!pipelineContainer) {
            console.error('Pipeline container not found');
            return;
        }
        
        // Clear any remaining loading timeouts
        if (loadingTimeout) {
            clearTimeout(loadingTimeout);
            loadingTimeout = null;
        }
        
        // Create error elements manually to avoid potential issues with templating
        // Clear the container first
        while (pipelineContainer.firstChild) {
            pipelineContainer.removeChild(pipelineContainer.firstChild);
        }
        
        // Create the alert div
        const alertDiv = document.createElement('div');
        alertDiv.className = 'alert alert-danger w-100';
        alertDiv.setAttribute('role', 'alert');
        
        // Create the error message
        const strongElement = document.createElement('strong');
        strongElement.textContent = 'Error loading pipeline data: ';
        alertDiv.appendChild(strongElement);
        
        // Add the error message text
        const errorTextNode = document.createTextNode(errorMessage);
        alertDiv.appendChild(errorTextNode);
        
        // Create the try again button
        const tryAgainButton = document.createElement('button');
        tryAgainButton.className = 'btn btn-sm btn-outline-secondary ms-2';
        tryAgainButton.innerHTML = '<i class="fas fa-sync-alt"></i> Try Again';
        tryAgainButton.addEventListener('click', function() {
            loadPipelineData(true);
        });
        alertDiv.appendChild(tryAgainButton);
        
        // Add to container
        pipelineContainer.appendChild(alertDiv);
        
        // Add detailed error if available
        if (detailedError) {
            // Create details container
            const detailsContainer = document.createElement('div');
            detailsContainer.className = 'mt-2';
            
            // Create details toggle button
            const detailsButton = document.createElement('button');
            detailsButton.className = 'btn btn-sm btn-outline-secondary';
            detailsButton.setAttribute('type', 'button');
            detailsButton.setAttribute('data-bs-toggle', 'collapse');
            detailsButton.setAttribute('data-bs-target', '#errorDetails');
            detailsButton.setAttribute('aria-expanded', 'false');
            detailsButton.setAttribute('aria-controls', 'errorDetails');
            detailsButton.textContent = 'Show Error Details';
            
            detailsContainer.appendChild(detailsButton);
            
            // Create collapsible content
            const collapseDiv = document.createElement('div');
            collapseDiv.className = 'collapse mt-2';
            collapseDiv.id = 'errorDetails';
            
            const cardDiv = document.createElement('div');
            cardDiv.className = 'card card-body';
            
            const preElement = document.createElement('pre');
            preElement.className = 'text-danger';
            preElement.style.maxHeight = '200px';
            preElement.style.overflowY = 'auto';
            preElement.textContent = detailedError;
            
            cardDiv.appendChild(preElement);
            collapseDiv.appendChild(cardDiv);
            detailsContainer.appendChild(collapseDiv);
            
            // Add to container
            pipelineContainer.appendChild(detailsContainer);
        }
    } catch (err) {
        console.error('Error displaying error message:', err);
    }
}

/**
 * Render the pipeline with the provided data
 * @param {Object} data - Pipeline data from the server
 */
function renderPipeline(data) {
    console.log('Rendering pipeline with data:', data);
    const pipelineContainer = document.getElementById('expensePipeline');
    
    if (!pipelineContainer) {
        console.error('Pipeline container not found!');
        return;
    }
    
    // Create HTML for each column
    let columnsHTML = '';
    
    for (const [columnId, column] of Object.entries(data.columns)) {
        console.log(`Rendering column: ${columnId} with ${column.items?.length || 0} items`);
        const items = column.items || [];
        
        columnsHTML += `
            <div class="pipeline-column column-${columnId} flex-shrink-0" data-column-id="${columnId}">
                <div class="pipeline-column-header">
                    <div class="column-summary">
                        <div class="column-title" style="color: ${column.color}">
                            <i class="${column.icon} me-1"></i>
                            ${column.title}
                            <span class="column-count">${column.count}</span>
                        </div>
                        <div class="column-total">${formatCurrency(column.total)}</div>
                    </div>
                </div>
                <div class="pipeline-column-body" id="${columnId}-dropzone">
                    ${items.length > 0 ? renderColumnItems(items, columnId) : renderEmptyState(columnId)}
                </div>
                ${renderColumnActions(columnId, data.user_role)}
            </div>
        `;
    }
    
    // Instead of changing innerHTML directly, create a document fragment
    // to ensure we don't lose the flexbox structure
    
    // First, clear the container properly
    while (pipelineContainer && pipelineContainer.firstChild) {
        pipelineContainer.removeChild(pipelineContainer.firstChild);
    }
    
    // Apply explicit flexbox styling using setAttribute to avoid cssText issues
    if (pipelineContainer) {
        pipelineContainer.className = 'expense-pipeline d-flex flex-row overflow-auto';
        pipelineContainer.style.display = 'flex';
        pipelineContainer.style.flexDirection = 'row';
        pipelineContainer.style.flexWrap = 'nowrap';
        pipelineContainer.style.overflowX = 'auto';
        pipelineContainer.style.width = '100%';
        pipelineContainer.style.minHeight = '600px';
    } else {
        console.error("Pipeline container not found");
        return;
    }
    
    // Create a temporary div to parse the HTML
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = columnsHTML;
    
    // Get all columns from the temporary container
    const columns = tempDiv.children;
    console.log(`Found ${columns.length} columns in the temporary container`);
    
    // Convert HTMLCollection to an array to safely iterate
    const columnsArray = Array.from(columns);
    
    // Process each column
    columnsArray.forEach(column => {
        try {
            // Apply explicit flexbox styling to each column using individual properties
            if (column && column.style) {
                column.style.display = 'flex';
                column.style.flexDirection = 'column';
                column.style.flex = '0 0 300px';
                column.style.minWidth = '300px';
                column.style.width = '300px';
                column.style.marginRight = '15px';
                column.style.float = 'none';
            }
            
            // Clone the node to avoid potential issues with removing it from tempDiv
            const columnClone = column.cloneNode(true);
            
            // Append the clone to pipeline
            pipelineContainer.appendChild(columnClone);
        } catch (error) {
            console.error('Error processing column:', error);
        }
    });
    
    // We no longer need to remove anything since we're using clones
    
    // Add event listeners to cards
    document.querySelectorAll('.pipeline-card').forEach(card => {
        card.addEventListener('click', function(event) {
            // Only open the modal if the click wasn't on a button or link
            if (!event.target.closest('button, a')) {
                const itemId = this.getAttribute('data-item-id');
                const itemType = this.getAttribute('data-item-type');
                const columnId = this.closest('.pipeline-column').getAttribute('data-column-id');
                openExpenseDetail(itemId, itemType, columnId);
            }
        });
    });
}

/**
 * Render items within a column
 * @param {Array} items - Array of items to render
 * @param {string} columnId - ID of the column
 * @returns {string} - HTML string for the items
 */
function renderColumnItems(items, columnId) {
    let itemsHTML = '';
    
    items.forEach(item => {
        const itemId = item.id;
        const itemType = item.type;
        const isReceipt = itemType === 'receipt';
        
        // Determine the category class
        let categoryClass = 'category-other';
        if (item.category) {
            const category = item.category.toLowerCase();
            if (category.includes('travel')) categoryClass = 'category-travel';
            else if (category.includes('meal') || category.includes('food') || category.includes('restaurant')) categoryClass = 'category-meals';
            else if (category.includes('office') || category.includes('supplies')) categoryClass = 'category-office';
            else if (category.includes('equipment') || category.includes('computer') || category.includes('hardware')) categoryClass = 'category-equipment';
            else if (category.includes('utilities') || category.includes('internet') || category.includes('phone')) categoryClass = 'category-utilities';
        }
        
        // Generate stars based on priority
        const priority = item.priority || 2;
        let stars = '';
        for (let i = 0; i < 3; i++) {
            stars += i < priority ? '<i class="fas fa-star"></i>' : '<i class="far fa-star"></i>';
        }
        
        // Build the card HTML
        itemsHTML += `
            <div class="pipeline-card" 
                data-item-id="${itemId}" 
                data-item-type="${itemType}" 
                draggable="true">
                <div class="pipeline-card-header">
                    <div class="pipeline-card-title">${escapeHtml(item.description || 'Unnamed Expense')}</div>
                    <div class="pipeline-card-amount">${formatCurrency(item.amount)}</div>
                </div>
                
                <div>
                    <small class="text-muted">
                        ${item.date ? formatDate(item.date) : 'No date'} • 
                        ${escapeHtml(item.user?.name || 'Unknown')}
                    </small>
                </div>
                
                <div class="d-flex justify-content-between align-items-center mt-2">
                    <span class="category-tag ${categoryClass}">${escapeHtml(item.category || 'Uncategorized')}</span>
                    <div class="priority-rating">${stars}</div>
                </div>
                
                ${renderItemSpecificInfo(item, columnId)}
            </div>
        `;
    });
    
    return itemsHTML;
}

/**
 * Render item-specific information based on column and item type
 * @param {Object} item - The item data
 * @param {string} columnId - The column ID
 * @returns {string} - HTML string for the item-specific information
 */
function renderItemSpecificInfo(item, columnId) {
    if (columnId === 'pending_submission') {
        return `
            <div class="mt-2 text-end">
                <button class="btn btn-sm btn-outline-primary submit-receipt-btn" 
                    data-receipt-id="${item.receipt_id}" 
                    onclick="submitReceiptAsExpense(event, '${item.receipt_id}')">
                    Submit as Expense
                </button>
            </div>
        `;
    } else if (columnId === 'approved') {
        return `
            <div class="mt-2 d-flex justify-content-between align-items-center">
                <small class="text-success">
                    <i class="fas fa-check-circle"></i> 
                    Approved ${item.approved_date ? formatDate(item.approved_date) : ''}
                </small>
                ${item.is_reimbursable ? `
                <button class="btn btn-sm btn-outline-primary reimburse-btn" 
                    data-expense-id="${item.id}" 
                    onclick="openReimbursementModal(event, ${item.id})">
                    Reimburse
                </button>` : ''}
            </div>
        `;
    } else if (columnId === 'reimbursed') {
        return `
            <div class="mt-2">
                <small class="text-purple">
                    <i class="fas fa-money-bill-wave"></i> 
                    ${item.reimbursement_method || 'Reimbursed'} 
                    ${item.reimbursed_date ? formatDate(item.reimbursed_date) : ''}
                </small>
                ${item.reimbursement_reference ? `
                <div class="text-muted small">Ref: ${escapeHtml(item.reimbursement_reference)}</div>` : ''}
            </div>
        `;
    } else if (columnId === 'rejected') {
        return `
            <div class="mt-2">
                <small class="text-danger">
                    <i class="fas fa-times-circle"></i> 
                    Rejected ${item.rejected_date ? formatDate(item.rejected_date) : ''}
                </small>
                ${item.rejection_reason ? `
                <div class="text-muted small text-truncate" title="${escapeHtml(item.rejection_reason)}">
                    Reason: ${escapeHtml(item.rejection_reason)}
                </div>` : ''}
            </div>
        `;
    } else if (columnId === 'submitted') {
        // For submitted expenses, just show when it was submitted
        return `
            <div class="mt-2">
                <small class="text-primary">
                    <i class="fas fa-file-upload"></i> 
                    Submitted ${item.created_at ? formatRelativeTime(item.created_at) : ''}
                </small>
                ${item.client_name ? `
                <div class="text-muted small">Client: ${escapeHtml(item.client_name)}</div>` : ''}
            </div>
        `;
    }
    
    return '';
}

/**
 * Render empty state for a column
 * @param {string} columnId - ID of the column
 * @returns {string} - HTML string for the empty state
 */
function renderEmptyState(columnId) {
    let message = 'No items';
    let icon = 'fas fa-inbox';
    
    if (columnId === 'pending_submission') {
        message = 'No receipts awaiting submission';
        icon = 'far fa-file-alt';
    } else if (columnId === 'submitted') {
        message = 'No expenses under review';
        icon = 'fas fa-file-upload';
    } else if (columnId === 'approved') {
        message = 'No approved expenses pending payment';
        icon = 'fas fa-check-circle';
    } else if (columnId === 'reimbursed') {
        message = 'No reimbursed expenses';
        icon = 'fas fa-money-bill-wave';
    } else if (columnId === 'rejected') {
        message = 'No rejected expenses';
        icon = 'fas fa-times-circle';
    }
    
    return `
        <div class="column-empty-state">
            <i class="${icon}"></i>
            <p>${message}</p>
        </div>
    `;
}

/**
 * Render actions for a column
 * @param {string} columnId - ID of the column
 * @param {string} userRole - Role of the current user
 * @returns {string} - HTML string for the column actions
 */
function renderColumnActions(columnId, userRole) {
    let actionsHTML = '<div class="column-actions">';
    
    if (columnId === 'pending_submission' && pipelineData.columns[columnId].items.length > 0) {
        actionsHTML += `
            <button class="btn btn-sm btn-outline-primary" onclick="batchSubmitReceipts('${columnId}')">
                <i class="fas fa-file-upload"></i> Submit All
            </button>
        `;
    } else if (columnId === 'submitted' && userRole === 'owner' || userRole === 'admin') {
        if (pipelineData.columns[columnId].items.length > 0) {
            actionsHTML += `
                <button class="btn btn-sm btn-outline-success" onclick="batchApproveExpenses('${columnId}')">
                    <i class="fas fa-check"></i> Approve All
                </button>
            `;
        }
    } else if (columnId === 'approved' && userRole === 'owner' && pipelineData.columns[columnId].items.length > 0) {
        actionsHTML += `
            <button class="btn btn-sm btn-outline-primary" onclick="batchReimburseExpenses('${columnId}')">
                <i class="fas fa-money-bill-wave"></i> Reimburse All
            </button>
        `;
    }
    
    actionsHTML += '</div>';
    return actionsHTML;
}

/**
 * Initialize drag and drop functionality
 */
function initializeDragAndDrop() {
    console.log('Initializing drag and drop functionality');
    // Clean up any existing sortable instances
    sortableInstances.forEach(instance => {
        if (instance && typeof instance.destroy === 'function') {
            instance.destroy();
        }
    });
    sortableInstances = [];
    
    // Get all dropzone elements
    const dropzones = document.querySelectorAll('.pipeline-column-body');
    console.log(`Found ${dropzones.length} dropzones for drag-and-drop`);
    
    // Initialize Sortable for each dropzone
    dropzones.forEach(dropzone => {
        const columnId = dropzone.id.replace('-dropzone', '');
        
        const sortable = new Sortable(dropzone, {
            group: 'expenses',
            animation: 150,
            ghostClass: 'card-placeholder',
            dragClass: 'dragging',
            onStart: function(evt) {
                const item = evt.item;
                draggedItem = {
                    id: item.getAttribute('data-item-id'),
                    type: item.getAttribute('data-item-type'),
                    fromColumn: columnId
                };
                activeDropzone = columnId;
            },
            onEnd: function(evt) {
                // Only process if the item was moved to a different column
                if (evt.to.id !== evt.from.id) {
                    const toColumn = evt.to.id.replace('-dropzone', '');
                    handleItemMove(draggedItem.id, draggedItem.type, draggedItem.fromColumn, toColumn);
                }
                
                // Reset active state
                draggedItem = null;
                activeDropzone = null;
            }
        });
        
        sortableInstances.push(sortable);
    });
}

/**
 * Handle an item being moved between columns
 * @param {string} itemId - ID of the moved item
 * @param {string} itemType - Type of the moved item (receipt or expense)
 * @param {string} fromColumn - ID of the source column
 * @param {string} toColumn - ID of the destination column
 */
function handleItemMove(itemId, itemType, fromColumn, toColumn) {
    console.log(`Moving ${itemType} ${itemId} from ${fromColumn} to ${toColumn}`);
    
    // Convert column IDs to expense statuses
    const statusMap = {
        'pending_submission': null, // Special case for receipts
        'submitted': 'SUBMITTED',
        'approved': 'APPROVED',
        'reimbursed': 'REIMBURSED',
        'rejected': 'REJECTED'
    };
    
    // Handle receipt to expense conversion
    if (itemType === 'receipt' && fromColumn === 'pending_submission' && toColumn !== 'pending_submission') {
        const receiptId = itemId.replace('receipt_', '');
        createExpenseFromReceipt(receiptId, toColumn);
        return;
    }
    
    // Handle expense status changes
    if (itemType === 'expense') {
        const newStatus = statusMap[toColumn];
        if (newStatus) {
            updateExpenseStatus(itemId, newStatus, toColumn);
        } else {
            // Cannot move an expense to pending_submission
            showToast('Error', 'Expenses cannot be moved back to pending submission', 'error');
            loadPipelineData(); // Reload to reset position
        }
    }
}

/**
 * Create an expense from a receipt
 * @param {string} receiptId - ID of the receipt
 * @param {string} targetColumn - ID of the target column
 */
function createExpenseFromReceipt(receiptId, targetColumn) {
    const organizationId = document.getElementById('organizationSelector').value;
    
    // Convert column to status
    const statusMap = {
        'submitted': 'SUBMITTED',
        'approved': 'APPROVED',
        'reimbursed': 'REIMBURSED',
        'rejected': 'REJECTED'
    };
    
    const newStatus = statusMap[targetColumn] || 'SUBMITTED';
    
    // Show loading toast
    showToast('Processing', 'Creating expense from receipt...', 'info');
    
    // Send request to create expense
    fetch('/expenses/api/update-expense-status', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            expense_id: `receipt_${receiptId}`,
            new_status: newStatus,
            organization_id: organizationId
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => {
                throw new Error(err.error || 'Failed to create expense');
            });
        }
        return response.json();
    })
    .then(data => {
        showToast('Success', 'Receipt converted to expense successfully', 'success');
        loadPipelineData(); // Reload to show changes
    })
    .catch(error => {
        console.error('Error creating expense:', error);
        showToast('Error', error.message, 'error');
        loadPipelineData(); // Reload to reset position
    });
}

/**
 * Update an expense's status
 * @param {string} expenseId - ID of the expense
 * @param {string} newStatus - New status for the expense
 * @param {string} targetColumn - ID of the target column
 */
function updateExpenseStatus(expenseId, newStatus, targetColumn) {
    const organizationId = document.getElementById('organizationSelector').value;
    
    // Special handling for statuses that need additional info
    if (newStatus === 'REJECTED') {
        // Show rejection reason modal
        openRejectionModal(expenseId);
        loadPipelineData(); // Reload to reset position
        return;
    }
    
    if (newStatus === 'REIMBURSED') {
        // Show reimbursement details modal
        openReimbursementModal(null, expenseId);
        loadPipelineData(); // Reload to reset position
        return;
    }
    
    // For other statuses, make the API call directly
    showToast('Processing', 'Updating expense status...', 'info');
    
    fetch('/expenses/api/update-expense-status', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            expense_id: expenseId,
            new_status: newStatus,
            organization_id: organizationId
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => {
                throw new Error(err.error || 'Failed to update expense status');
            });
        }
        return response.json();
    })
    .then(data => {
        showToast('Success', 'Expense status updated successfully', 'success');
        loadPipelineData(); // Reload to show changes
    })
    .catch(error => {
        console.error('Error updating expense status:', error);
        showToast('Error', error.message, 'error');
        loadPipelineData(); // Reload to reset position
    });
}

/**
 * Submit a receipt as an expense
 * @param {Event} event - Click event
 * @param {string} receiptId - ID of the receipt
 */
function submitReceiptAsExpense(event, receiptId) {
    event.stopPropagation(); // Prevent card click from firing
    
    createExpenseFromReceipt(receiptId, 'submitted');
}

/**
 * Open the expense detail modal
 * @param {string} itemId - ID of the expense or receipt
 * @param {string} itemType - Type of item (expense or receipt)
 * @param {string} columnId - ID of the column containing the item
 */
function openExpenseDetail(itemId, itemType, columnId) {
    const modal = document.getElementById('expenseDetailModal');
    const modalTitle = document.getElementById('expenseDetailTitle');
    const modalBody = document.getElementById('expenseDetailBody');
    const actionButtons = document.getElementById('expenseActionButtons');
    
    // Set modal title
    modalTitle.textContent = itemType === 'receipt' ? 'Receipt Details' : 'Expense Details';
    
    // Show loading state
    modalBody.innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-2">Loading details...</p>
        </div>
    `;
    
    // Clear action buttons
    actionButtons.innerHTML = '';
    
    // Initialize the modal
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    
    // Get the item data from pipeline data
    let item = null;
    
    if (pipelineData && pipelineData.columns && pipelineData.columns[columnId]) {
        item = pipelineData.columns[columnId].items.find(i => String(i.id) === String(itemId));
    }
    
    if (item) {
        renderExpenseDetail(item, columnId);
    } else {
        modalBody.innerHTML = `
            <div class="alert alert-danger">
                <i class="fas fa-exclamation-circle"></i>
                Item not found. Try refreshing the page.
            </div>
        `;
    }
}

/**
 * Render expense details in the modal
 * @param {Object} item - The expense or receipt data
 * @param {string} columnId - ID of the column containing the item
 */
function renderExpenseDetail(item, columnId) {
    const modalBody = document.getElementById('expenseDetailBody');
    const actionButtons = document.getElementById('expenseActionButtons');
    const isReceipt = item.type === 'receipt';
    const organizationId = document.getElementById('organizationSelector').value;
    
    // Build the HTML for the expense detail
    let detailHTML = `
        <div class="row">
            <div class="col-md-6">
                <div class="card mb-3">
                    <div class="card-body">
                        <h5 class="card-title">${escapeHtml(item.description || 'Unnamed')}</h5>
                        <h6 class="card-subtitle mb-2 text-muted">
                            ${item.date ? formatDate(item.date) : 'No date'} • 
                            ${escapeHtml(item.user?.name || 'Unknown')}
                        </h6>
                        
                        <div class="mb-3">
                            <strong>Amount:</strong> ${formatCurrency(item.amount)}
                        </div>
                        
                        <div class="mb-3">
                            <strong>Category:</strong> ${escapeHtml(item.category || 'Uncategorized')}
                        </div>
                        
                        ${isReceipt ? '' : `
                        <div class="mb-3">
                            <strong>Status:</strong> 
                            <span class="badge ${getStatusClass(item.status)}">
                                ${getStatusText(item.status)}
                            </span>
                        </div>`}
                        
                        ${item.vendor_name ? `
                        <div class="mb-3">
                            <strong>Vendor:</strong> ${escapeHtml(item.vendor_name)}
                        </div>` : ''}
                        
                        ${!isReceipt && item.client_name ? `
                        <div class="mb-3">
                            <strong>Client:</strong> ${escapeHtml(item.client_name)}
                        </div>` : ''}
                        
                        ${!isReceipt && item.is_reimbursable !== undefined ? `
                        <div class="mb-3">
                            <strong>Reimbursable:</strong> ${item.is_reimbursable ? 'Yes' : 'No'}
                        </div>` : ''}
                        
                        ${item.notes ? `
                        <div class="mb-3">
                            <strong>Notes:</strong>
                            <p class="card-text">${escapeHtml(item.notes)}</p>
                        </div>` : ''}
                    </div>
                </div>
                
                ${renderTimelineSection(item)}
            </div>
            
            <div class="col-md-6">
                <div class="card mb-3">
                    <div class="card-header">
                        <h5 class="card-title mb-0">Receipt Image</h5>
                    </div>
                    <div class="card-body">
                        <div class="receipt-image-container">
                            <!-- Loading indicator -->
                            <div class="receipt-image-loading">
                                <div class="receipt-image-spinner"></div>
                                <div>Loading receipt image...</div>
                                <div class="receipt-image-progress-container">
                                    <div class="receipt-image-progress-bar"></div>
                                </div>
                            </div>
                            
                            <!-- Placeholder when no image available -->
                            <div class="receipt-image-placeholder" ${item.has_image ? 'style="display:none;"' : ''}>
                                <i class="fas fa-exclamation-triangle text-warning fa-2x mb-2"></i>
                                <p>No receipt image available</p>
                            </div>
                            
                            <!-- Actual image element, hidden until loaded -->
                            <img src="" alt="Receipt Image" class="receipt-image-actual">
                        </div>
                        
                        <div class="text-center mt-3" id="receipt-image-controls" style="display: none;">
                            <button type="button" class="btn btn-sm btn-outline-primary" id="view-full-image">
                                <i class="fas fa-search-plus"></i> View Full Image
                            </button>
                        </div>
                    </div>
                </div>
                
                ${!isReceipt && item.has_image && item.receipt_id ? `
                <script>
                    // Load the receipt image with progress indicator
                    loadReceiptImage(${item.receipt_id}, ${item.id});
                </script>
                ` : ''}
                
                ${!isReceipt && item.status === 'REIMBURSED' && (item.reimbursement_method || item.reimbursement_reference) ? `
                <div class="card mb-3">
                    <div class="card-header">
                        <h5 class="card-title mb-0">Reimbursement Details</h5>
                    </div>
                    <div class="card-body">
                        ${item.reimbursement_method ? `
                        <div class="mb-2">
                            <strong>Method:</strong> ${escapeHtml(item.reimbursement_method)}
                        </div>` : ''}
                        
                        ${item.reimbursement_reference ? `
                        <div class="mb-2">
                            <strong>Reference:</strong> ${escapeHtml(item.reimbursement_reference)}
                        </div>` : ''}
                        
                        ${item.reimbursed_date ? `
                        <div class="mb-2">
                            <strong>Date:</strong> ${formatDate(item.reimbursed_date)}
                        </div>` : ''}
                        
                        ${item.reimbursed_by?.name ? `
                        <div class="mb-2">
                            <strong>Processed by:</strong> ${escapeHtml(item.reimbursed_by.name)}
                        </div>` : ''}
                    </div>
                </div>` : ''}
                
                ${!isReceipt && item.status === 'REJECTED' && item.rejection_reason ? `
                <div class="card mb-3 border-danger">
                    <div class="card-header bg-danger text-white">
                        <h5 class="card-title mb-0">Rejection Reason</h5>
                    </div>
                    <div class="card-body">
                        <p class="card-text">${escapeHtml(item.rejection_reason)}</p>
                        
                        ${item.rejected_date ? `
                        <div class="mb-2 small text-muted">
                            Rejected on ${formatDate(item.rejected_date)}
                        </div>` : ''}
                        
                        ${item.rejected_by?.name ? `
                        <div class="small text-muted">
                            Rejected by ${escapeHtml(item.rejected_by.name)}
                        </div>` : ''}
                    </div>
                </div>` : ''}
            </div>
        </div>
    `;
    
    // Update the modal body
    modalBody.innerHTML = detailHTML;
    
    // Add action buttons based on item type and status
    let actionsHTML = '';
    
    if (isReceipt) {
        // Actions for receipts
        actionsHTML += `
            <button type="button" class="btn btn-primary" onclick="submitReceiptAsExpense(event, '${item.receipt_id}')">
                <i class="fas fa-file-upload"></i> Submit as Expense
            </button>
        `;
    } else {
        // Actions for expenses based on status
        if (item.status === 'SUBMITTED') {
            actionsHTML += `
                <button type="button" class="btn btn-success" onclick="updateExpenseStatus('${item.id}', 'APPROVED', 'approved')">
                    <i class="fas fa-check"></i> Approve
                </button>
                <button type="button" class="btn btn-danger" onclick="openRejectionModal('${item.id}')">
                    <i class="fas fa-times"></i> Reject
                </button>
            `;
        } else if (item.status === 'APPROVED' && item.is_reimbursable) {
            actionsHTML += `
                <button type="button" class="btn btn-primary" onclick="openReimbursementModal(null, '${item.id}')">
                    <i class="fas fa-money-bill-wave"></i> Mark as Reimbursed
                </button>
                <button type="button" class="btn btn-danger" onclick="openRejectionModal('${item.id}')">
                    <i class="fas fa-times"></i> Reject
                </button>
            `;
        } else if (item.status === 'REJECTED') {
            actionsHTML += `
                <button type="button" class="btn btn-primary" onclick="updateExpenseStatus('${item.id}', 'SUBMITTED', 'submitted')">
                    <i class="fas fa-redo"></i> Resubmit
                </button>
            `;
        }
        
        // Add view in receipt detail button for all expenses
        if (item.receipt_id) {
            // Get current pipeline filter parameters
            const orgId = getSelectedOrgId();
            const viewType = document.getElementById('viewType')?.value || 'default';
            const clientId = document.getElementById('clientFilter')?.value || '';
            const statusFilter = document.getElementById('statusFilter')?.value || '';
            const dateRange = document.getElementById('dateRangeFilter')?.value || '';
            const searchTerm = document.getElementById('expenseSearch')?.value || '';
            
            // Construct the URL with pipeline state parameters
            const pipelineParams = new URLSearchParams({
                from_pipeline: 'true',
                org_id: orgId,
                view: viewType
            });
            
            // Add optional parameters if they exist
            if (clientId) pipelineParams.append('client_id', clientId);
            if (statusFilter) pipelineParams.append('status', statusFilter);
            if (dateRange) pipelineParams.append('date_range', dateRange);
            if (searchTerm) pipelineParams.append('search', searchTerm);
            
            actionsHTML += `
                <a href="/receipts/view/${item.receipt_id}?${pipelineParams.toString()}" class="btn btn-outline-secondary ms-2">
                    <i class="fas fa-file-alt"></i> View Receipt Details
                </a>
            `;
        }
    }
    
    // Update action buttons
    actionButtons.innerHTML = actionsHTML;
}

/**
 * Render the timeline section for an expense
 * @param {Object} item - The expense data
 * @returns {string} - HTML string for the timeline section
 */
function renderTimelineSection(item) {
    if (item.type === 'receipt') {
        return ''; // No timeline for receipts
    }
    
    let timelineHTML = `
        <div class="card mb-3">
            <div class="card-header">
                <h5 class="card-title mb-0">Activity Timeline</h5>
            </div>
            <div class="card-body">
                <div class="expense-timeline">
    `;
    
    // Add created event
    if (item.created_at) {
        timelineHTML += `
            <div class="timeline-item">
                <div class="timeline-date">${formatDate(item.created_at)}</div>
                <div class="timeline-title">Expense Created</div>
                <div class="timeline-content text-muted">
                    Created by ${escapeHtml(item.user?.name || 'Unknown')}
                </div>
            </div>
        `;
    }
    
    // Add submitted event
    if (item.status === 'SUBMITTED' || item.status === 'APPROVED' || item.status === 'REIMBURSED') {
        timelineHTML += `
            <div class="timeline-item">
                <div class="timeline-date">${item.created_at ? formatDate(item.created_at) : 'Unknown date'}</div>
                <div class="timeline-title">Submitted for Approval</div>
                <div class="timeline-content text-muted">
                    Submitted by ${escapeHtml(item.user?.name || 'Unknown')}
                </div>
            </div>
        `;
    }
    
    // Add approved event
    if ((item.status === 'APPROVED' || item.status === 'REIMBURSED') && item.approved_date) {
        timelineHTML += `
            <div class="timeline-item">
                <div class="timeline-date">${formatDate(item.approved_date)}</div>
                <div class="timeline-title">Expense Approved</div>
                <div class="timeline-content text-muted">
                    Approved by ${escapeHtml(item.approved_by?.name || 'Unknown')}
                </div>
            </div>
        `;
    }
    
    // Add reimbursed event
    if (item.status === 'REIMBURSED' && item.reimbursed_date) {
        timelineHTML += `
            <div class="timeline-item">
                <div class="timeline-date">${formatDate(item.reimbursed_date)}</div>
                <div class="timeline-title">Payment Processed</div>
                <div class="timeline-content text-muted">
                    ${item.reimbursement_method ? `Method: ${escapeHtml(item.reimbursement_method)}` : ''}
                    ${item.reimbursement_reference ? `<br>Reference: ${escapeHtml(item.reimbursement_reference)}` : ''}
                    ${item.reimbursed_by?.name ? `<br>Processed by: ${escapeHtml(item.reimbursed_by.name)}` : ''}
                </div>
            </div>
        `;
    }
    
    // Add rejected event
    if (item.status === 'REJECTED' && item.rejected_date) {
        timelineHTML += `
            <div class="timeline-item">
                <div class="timeline-date">${formatDate(item.rejected_date)}</div>
                <div class="timeline-title">Expense Rejected</div>
                <div class="timeline-content text-muted">
                    ${item.rejected_by?.name ? `Rejected by ${escapeHtml(item.rejected_by.name)}` : ''}
                    ${item.rejection_reason ? `<br>Reason: ${escapeHtml(item.rejection_reason)}` : ''}
                </div>
            </div>
        `;
    }
    
    timelineHTML += `
                </div>
            </div>
        </div>
    `;
    
    return timelineHTML;
}

/**
 * Open the reimbursement details modal
 * @param {Event} event - Click event (optional)
 * @param {string} expenseId - ID of the expense
 */
function openReimbursementModal(event, expenseId) {
    if (event) event.stopPropagation();
    
    const modal = document.getElementById('reimbursementModal');
    const expenseIdField = document.getElementById('reimbursementExpenseId');
    
    // Set the expense ID
    expenseIdField.value = expenseId;
    
    // Show the modal
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
}

/**
 * Save reimbursement details
 */
function saveReimbursementDetails() {
    const expenseId = document.getElementById('reimbursementExpenseId').value;
    const method = document.getElementById('reimbursementMethod').value;
    const reference = document.getElementById('reimbursementReference').value;
    const notes = document.getElementById('reimbursementNotes').value;
    const organizationId = document.getElementById('organizationSelector').value;
    
    // Validate inputs
    if (!method) {
        showToast('Error', 'Please select a reimbursement method', 'error');
        return;
    }
    
    // Show loading toast
    showToast('Processing', 'Recording reimbursement...', 'info');
    
    // Send request to update expense status
    fetch('/expenses/api/update-expense-status', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            expense_id: expenseId,
            new_status: 'REIMBURSED',
            organization_id: organizationId,
            reimbursement_method: method,
            reimbursement_reference: reference,
            notes: notes
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => {
                throw new Error(err.error || 'Failed to record reimbursement');
            });
        }
        return response.json();
    })
    .then(data => {
        // Hide the modal
        const modal = document.getElementById('reimbursementModal');
        bootstrap.Modal.getInstance(modal).hide();
        
        // Reset form
        document.getElementById('reimbursementForm').reset();
        
        showToast('Success', 'Reimbursement recorded successfully', 'success');
        loadPipelineData(); // Reload to show changes
    })
    .catch(error => {
        console.error('Error recording reimbursement:', error);
        showToast('Error', error.message, 'error');
    });
}

/**
 * Open the rejection reason modal
 * @param {string} expenseId - ID of the expense
 */
function openRejectionModal(expenseId) {
    const modal = document.getElementById('rejectionModal');
    const expenseIdField = document.getElementById('rejectionExpenseId');
    
    // Set the expense ID
    expenseIdField.value = expenseId;
    
    // Show the modal
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
}

/**
 * Confirm rejection of an expense
 */
function confirmRejectExpense() {
    const expenseId = document.getElementById('rejectionExpenseId').value;
    const reason = document.getElementById('rejectionReason').value;
    const organizationId = document.getElementById('organizationSelector').value;
    
    // Validate inputs
    if (!reason) {
        showToast('Error', 'Please provide a reason for rejection', 'error');
        return;
    }
    
    // Show loading toast
    showToast('Processing', 'Rejecting expense...', 'info');
    
    // Send request to update expense status
    fetch('/expenses/api/update-expense-status', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            expense_id: expenseId,
            new_status: 'REJECTED',
            organization_id: organizationId,
            rejection_reason: reason
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => {
                throw new Error(err.error || 'Failed to reject expense');
            });
        }
        return response.json();
    })
    .then(data => {
        // Hide the modal
        const modal = document.getElementById('rejectionModal');
        bootstrap.Modal.getInstance(modal).hide();
        
        // Reset form
        document.getElementById('rejectionForm').reset();
        
        showToast('Success', 'Expense rejected successfully', 'success');
        loadPipelineData(); // Reload to show changes
    })
    .catch(error => {
        console.error('Error rejecting expense:', error);
        showToast('Error', error.message, 'error');
    });
}

/**
 * Batch submit receipts from a column
 * @param {string} columnId - ID of the column
 */
function batchSubmitReceipts(columnId) {
    const organizationId = document.getElementById('organizationSelector').value;
    
    // Get all receipt IDs from the column
    const receipts = pipelineData.columns[columnId].items.filter(item => item.type === 'receipt');
    if (!receipts.length) {
        showToast('Info', 'No receipts available for submission', 'info');
        return;
    }
    
    // Extract receipt IDs
    const receiptIds = receipts.map(receipt => receipt.receipt_id).join(',');
    
    // Redirect to batch submission page
    window.location.href = `/expenses/batch-submit?org_id=${organizationId}&receipt_ids=${receiptIds}`;
}

/**
 * Handle window resize for mobile optimizations
 */
function handleWindowResize() {
    const isMobile = window.innerWidth < 768;
    const indicatorsContainer = document.querySelector('.column-indicators');
    
    if (isMobile && pipelineData) {
        // Create mobile column indicators for quicker navigation
        let indicatorsHTML = '';
        for (const [columnId, column] of Object.entries(pipelineData.columns)) {
            indicatorsHTML += `
                <div class="column-indicator" style="background-color: ${column.color}; color: white;" 
                    onclick="scrollToColumn('${columnId}')">
                    ${column.title} <span class="badge bg-white text-dark">${column.count}</span>
                </div>
            `;
        }
        if (indicatorsContainer) {
            indicatorsContainer.innerHTML = indicatorsHTML;
            indicatorsContainer.classList.remove('d-none');
        }
    } else {
        // Clear indicators on desktop
        if (indicatorsContainer) {
            indicatorsContainer.innerHTML = '';
            indicatorsContainer.classList.add('d-none');
        }
    }
}

/**
 * Update mobile column indicators
 * @param {Object} data - Pipeline data
 */
function updateMobileIndicators(data) {
    if (window.innerWidth < 768) {
        const indicatorsContainer = document.querySelector('.column-indicators');
        if (indicatorsContainer) {
            let indicatorsHTML = '';
            for (const [columnId, column] of Object.entries(data.columns)) {
                indicatorsHTML += `
                    <div class="column-indicator" style="background-color: ${column.color}; color: white;" 
                        onclick="scrollToColumn('${columnId}')">
                        ${column.title} <span class="badge bg-white text-dark">${column.count}</span>
                    </div>
                `;
            }
            indicatorsContainer.innerHTML = indicatorsHTML;
            indicatorsContainer.classList.remove('d-none');
        }
    }
}

/**
 * Scroll to a specific column (for mobile view)
 * @param {string} columnId - ID of the column to scroll to
 */
function scrollToColumn(columnId) {
    const column = document.querySelector(`.column-${columnId}`);
    if (column) {
        column.scrollIntoView({ behavior: 'smooth' });
    }
}

/**
 * Export pipeline data as Excel or CSV
 */
function exportPipelineData() {
    const organizationId = document.getElementById('organizationSelector').value;
    const viewType = document.getElementById('viewSelector').value;
    
    // Redirect to export endpoint
    window.location.href = `/expenses/export?org_id=${organizationId}&view=${viewType}&format=excel`;
}

/**
 * Show a toast notification
 * @param {string} title - Toast title
 * @param {string} message - Toast message
 * @param {string} type - Toast type (success, error, info, warning)
 */
function showToast(title, message, type = 'info') {
    // Check if toastr is available (you may need to include toastr library)
    if (typeof toastr !== 'undefined') {
        toastr[type](message, title);
        return;
    }
    
    // Fallback to browser alert
    alert(`${title}: ${message}`);
}

/**
 * Format a date string
 * @param {string} dateString - Date string to format
 * @returns {string} - Formatted date string
 */
function formatDate(dateString) {
    if (!dateString) return 'Unknown date';
    
    try {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    } catch (e) {
        return dateString;
    }
}

/**
 * Format a datetime as relative time (e.g., "2 days ago")
 * @param {string} dateString - Date string to format
 * @returns {string} - Relative time string
 */
function formatRelativeTime(dateString) {
    if (!dateString) return '';
    
    try {
        const date = new Date(dateString);
        const now = new Date();
        const diffMs = now - date;
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        
        if (diffDays === 0) {
            const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
            if (diffHours === 0) {
                const diffMinutes = Math.floor(diffMs / (1000 * 60));
                return diffMinutes === 0 ? 'just now' : `${diffMinutes} minutes ago`;
            }
            return `${diffHours} hours ago`;
        } else if (diffDays === 1) {
            return 'yesterday';
        } else if (diffDays < 7) {
            return `${diffDays} days ago`;
        } else {
            return formatDate(dateString);
        }
    } catch (e) {
        return dateString;
    }
}

/**
 * Format a number as currency
 * @param {number} amount - Amount to format
 * @returns {string} - Formatted currency string
 */
function formatCurrency(amount) {
    if (amount === null || amount === undefined) return '$0.00';
    
    try {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD'
        }).format(amount);
    } catch (e) {
        return `$${amount}`;
    }
}

/**
 * Get CSS class for an expense status
 * @param {string} status - Expense status
 * @returns {string} - CSS class
 */
function getStatusClass(status) {
    switch (status) {
        case 'SUBMITTED': return 'bg-primary';
        case 'APPROVED': return 'bg-success';
        case 'REJECTED': return 'bg-danger';
        case 'REIMBURSED': return 'bg-purple';
        default: return 'bg-secondary';
    }
}

/**
 * Get display text for an expense status
 * @param {string} status - Expense status
 * @returns {string} - Display text
 */
function getStatusText(status) {
    switch (status) {
        case 'SUBMITTED': return 'Submitted';
        case 'APPROVED': return 'Approved';
        case 'REJECTED': return 'Rejected';
        case 'REIMBURSED': return 'Reimbursed';
        default: return status;
    }
}

/**
 * Load receipt image with progress indicator
 * @param {number} receiptId - The ID of the receipt to load
 * @param {number} expenseId - The ID of the expense
 */
function loadReceiptImage(receiptId, expenseId) {
    if (!receiptId) {
        // If no receipt ID, just show the placeholder
        $('.receipt-image-placeholder').show();
        $('.receipt-image-loading').removeClass('active');
        $('.receipt-image-actual').removeClass('loaded');
        $('#receipt-image-controls').hide();
        return;
    }
    
    // Show loading indicator
    $('.receipt-image-placeholder').hide();
    $('.receipt-image-loading').addClass('active');
    $('.receipt-image-actual').removeClass('loaded');
    $('#receipt-image-controls').hide();
    
    // Reset progress bar
    $('.receipt-image-progress-bar').css('width', '0%');
    
    // Use existing endpoint to get secure image URL
    $.ajax({
        url: `/expenses/api/expense-image/${expenseId}`,
        type: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        },
        xhr: function() {
            const xhr = new window.XMLHttpRequest();
            
            // Add progress listener for progress bar updates
            xhr.addEventListener('progress', function(e) {
                if (e.lengthComputable) {
                    const percentComplete = (e.loaded / e.total) * 100;
                    $('.receipt-image-progress-bar').css('width', percentComplete + '%');
                } else {
                    // If progress not computable, show indeterminate progress
                    $('.receipt-image-progress-bar').css('width', '100%');
                    $('.receipt-image-progress-bar').css('opacity', '0.5');
                }
            }, false);
            
            return xhr;
        },
        success: function(response) {
            if (response.image_url) {
                // Set progress to 90% - still need to load the actual image
                $('.receipt-image-progress-bar').css('width', '90%');
                
                // Preload the image to ensure it's fully loaded before showing
                const img = new Image();
                img.onload = function() {
                    // Complete the progress bar
                    $('.receipt-image-progress-bar').css('width', '100%');
                    
                    // Hide loading indicator, show the image
                    setTimeout(function() {
                        $('.receipt-image-loading').removeClass('active');
                        $('.receipt-image-actual').attr('src', response.image_url).addClass('loaded');
                        $('#receipt-image-controls').show();
                        
                        // Set up full image view functionality
                        $('#view-full-image').off('click').on('click', function() {
                            window.open(response.image_url, '_blank');
                        });
                    }, 300); // Small delay to show the completed progress bar
                };
                
                img.onerror = function() {
                    // Handle image loading error
                    $('.receipt-image-loading').removeClass('active');
                    $('.receipt-image-placeholder').show();
                    $('.receipt-image-placeholder i').removeClass('fa-exclamation-triangle').addClass('fa-times-circle text-danger');
                    $('.receipt-image-placeholder p').text('Error loading receipt image');
                };
                
                // Start loading the image
                img.src = response.image_url;
            } else {
                // No image URL in response
                $('.receipt-image-loading').removeClass('active');
                $('.receipt-image-placeholder').show();
            }
        },
        error: function() {
            // API call failed
            $('.receipt-image-loading').removeClass('active');
            $('.receipt-image-placeholder').show();
            $('.receipt-image-placeholder i').removeClass('fa-exclamation-triangle').addClass('fa-times-circle text-danger');
            $('.receipt-image-placeholder p').text('Failed to retrieve receipt image');
        }
    });
}

/**
 * Escape HTML special characters to prevent XSS
 * @param {string} unsafe - Unsafe string
 * @returns {string} - Escaped string
 */
function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe
        .toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}