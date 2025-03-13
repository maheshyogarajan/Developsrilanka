/**
 * Receipt History Error Logging System
 * 
 * This module provides error logging functions for the receipt history page
 * that integrate with the backend error logging system.
 */

// Error Types (should match backend ErrorTypes)
const ErrorTypes = {
    DATABASE: "database_error",
    AUTHENTICATION: "authentication_error",
    AUTHORIZATION: "authorization_error",
    API: "api_error",
    VALIDATION: "validation_error",
    CLIENT: "client_error",
    SERVER: "server_error",
    FILE_OPERATION: "file_operation_error",
    RECEIPT_PROCESSING: "receipt_processing_error",
    RECEIPT_HISTORY: "receipt_history_error",
    THIRD_PARTY: "third_party_error",
    UNKNOWN: "unknown_error"
};

// Log levels
const LogLevels = {
    DEBUG: "debug",
    INFO: "info",
    WARNING: "warning",
    ERROR: "error",
    CRITICAL: "critical"
};

/**
 * Log a client-side error to the console and optionally to the server
 * 
 * @param {string|Error} error - Error message or Error object
 * @param {string} component - Component where the error occurred
 * @param {Object} additionalInfo - Any additional info to log
 * @param {string} errorType - Type of error from ErrorTypes
 * @param {boolean} sendToServer - Whether to send to server
 * @returns {string} Error reference ID
 */
function logError(error, component = "receipt_history", additionalInfo = {}, 
                 errorType = ErrorTypes.RECEIPT_HISTORY, sendToServer = true) {
    // Generate error ID
    const timestamp = new Date().toISOString().replace(/[-:.TZ]/g, "");
    const errorMsg = error instanceof Error ? error.message : String(error);
    const errorId = `${timestamp}-${btoa(errorMsg.substring(0, 10)).replace(/=/g, "")}`;
    
    // Build error data object
    const errorData = {
        error_id: errorId,
        timestamp: new Date().toISOString(),
        error_type: errorType,
        component: component,
        error_message: errorMsg,
        stack_trace: error instanceof Error ? error.stack : null,
        additional_info: additionalInfo,
        user_agent: navigator.userAgent,
        url: window.location.href
    };
    
    // Log to console
    console.error(`[${errorType}] in ${component}: ${errorMsg} (ID: ${errorId})`, 
                  error instanceof Error ? error : '', additionalInfo);
    
    // Send to server for logging if requested
    if (sendToServer) {
        fetch('/api/log_client_error', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(errorData)
        }).catch(err => {
            // If logging fails, just log to console
            console.warn('Failed to send error to server:', err);
        });
    }
    
    return errorId;
}

/**
 * Log a receipt history specific error
 * 
 * @param {string|Error} error - Error message or Error object
 * @param {string} subComponent - Specific subcomponent within receipt history
 * @param {Object} additionalInfo - Any additional info to log
 * @returns {string} Error reference ID
 */
function logReceiptHistoryError(error, subComponent = null, additionalInfo = {}) {
    const component = subComponent ? `receipt_history.${subComponent}` : "receipt_history";
    return logError(error, component, additionalInfo, ErrorTypes.RECEIPT_HISTORY);
}

/**
 * Log an API error (e.g. failed fetch)
 * 
 * @param {string|Error} error - Error message or Error object
 * @param {string} endpoint - API endpoint that failed
 * @param {string} method - HTTP method used
 * @param {Object} additionalInfo - Any additional info to log
 * @returns {string} Error reference ID
 */
function logApiError(error, endpoint, method = "GET", additionalInfo = {}) {
    const info = { ...additionalInfo, endpoint, method };
    return logError(error, "api", info, ErrorTypes.API);
}

/**
 * Handle and display a UI error message
 * 
 * @param {string|Error} error - Error message or Error object
 * @param {HTMLElement} errorContainer - Container element to show error in
 * @param {HTMLElement} errorMessage - Element to put error message in
 * @param {string} subComponent - Receipt history subcomponent
 * @param {Object} additionalInfo - Any additional info to log
 * @returns {string} Error ID
 */
function handleUIError(error, errorContainer, errorMessage, subComponent = null, additionalInfo = {}) {
    // Log the error
    const errorId = logReceiptHistoryError(error, subComponent, additionalInfo);
    
    // Update UI
    if (errorContainer && errorContainer.classList) {
        errorContainer.classList.remove('d-none');
    }
    
    if (errorMessage) {
        // Parse server error response if available
        let displayMessage = error instanceof Error ? error.message : String(error);
        
        // Try to parse JSON error response
        if (typeof displayMessage === 'string' && displayMessage.startsWith('{') && displayMessage.endsWith('}')) {
            try {
                const errorObj = JSON.parse(displayMessage);
                if (errorObj.error) {
                    displayMessage = errorObj.error;
                }
                
                // Add error ID if available
                if (errorObj.error_id) {
                    displayMessage += ` (Ref: ${errorObj.error_id})`;
                }
            } catch (e) {
                // If parsing fails, use the original message
                console.warn('Failed to parse error JSON:', e);
            }
        }
        
        // Update the error message element
        errorMessage.textContent = `${displayMessage} (Ref: ${errorId})`;
    }
    
    return errorId;
}

/**
 * Format a fetch response error based on status code
 * 
 * @param {Response} response - Fetch Response object
 * @returns {Promise<Error>} Formatted error
 */
async function formatFetchError(response) {
    let errorMessage = `Server returned ${response.status}: ${response.statusText}`;
    
    // Try to get more detailed error from response
    try {
        const errorData = await response.json();
        if (errorData.error) {
            errorMessage = errorData.error;
        }
        
        // Include error ID if available
        if (errorData.error_id) {
            errorMessage += ` (Ref: ${errorData.error_id})`;
        }
    } catch (e) {
        // If parsing fails, use default message
        console.warn('Failed to parse error response:', e);
    }
    
    const error = new Error(errorMessage);
    error.status = response.status;
    error.statusText = response.statusText;
    return error;
}

// Export functions
window.ReceiptHistoryErrorLogger = {
    logError,
    logReceiptHistoryError,
    logApiError,
    handleUIError,
    formatFetchError,
    ErrorTypes,
    LogLevels
};