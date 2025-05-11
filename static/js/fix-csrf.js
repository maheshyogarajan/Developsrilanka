/**
 * This script patches all fetch POST requests with CSRF tokens
 */

// Store the original fetch function
const originalFetch = window.fetch;

// Override the fetch function
window.fetch = function(url, options = {}) {
    // Only modify POST requests
    if (options && options.method === 'POST') {
        // Make sure headers exist
        options.headers = options.headers || {};
        
        // If not already set, add the CSRF token header
        if (!options.headers['X-CSRFToken']) {
            // Get the CSRF token from the page
            const tokenField = document.querySelector('input[name="csrf_token"]');
            if (tokenField) {
                options.headers['X-CSRFToken'] = tokenField.value;
            }
        }
    }
    
    // Call the original fetch with our modified options
    return originalFetch(url, options);
};

console.log('CSRF token handler installed for all fetch requests');