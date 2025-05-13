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
            // First try to get the CSRF token from a form input field
            let csrfToken = null;
            const tokenField = document.querySelector('input[name="csrf_token"]');
            if (tokenField) {
                csrfToken = tokenField.value;
            } else {
                // Fallback to meta tag if input field is not available
                const metaToken = document.querySelector('meta[name="csrf-token"]');
                if (metaToken) {
                    csrfToken = metaToken.content;
                }
            }
            
            if (csrfToken) {
                options.headers['X-CSRFToken'] = csrfToken;
            }
        }
    }
    
    // Call the original fetch with our modified options
    return originalFetch(url, options);
};

console.log('CSRF token handler installed for all fetch requests');