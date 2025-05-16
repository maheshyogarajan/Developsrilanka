/**
 * This script patches all fetch POST requests with CSRF tokens
 */

// Store the original fetch function
const originalFetch = window.fetch;

// Override the fetch function
window.fetch = function(url, options = {}) {
    // Modify state-changing request methods
    if (options && (options.method === 'POST' || options.method === 'PUT' || 
                    options.method === 'PATCH' || options.method === 'DELETE')) {
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

// Intercept clicks on action links that should be POST requests
document.addEventListener('DOMContentLoaded', function() {
    document.addEventListener('click', function(event) {
        // Match links with patterns that suggest state-changing operations
        const link = event.target.closest(
            'a[href*="/delete"], a[href*="/cancel"], a[href*="/remove"], a[href*="/logout"], ' + 
            'a[href*="/accept"], a[href*="/reject"], a[href*="/approve"], a[href*="/set-default"], ' +
            'a[href*="/mark-as"], a[href*="/enable"], a[href*="/disable"]'
        );
        
        if (link) {
            event.preventDefault();
            
            // Create a form dynamically
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = link.href;
            
            // Add CSRF token
            const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
            const csrfInput = document.createElement('input');
            csrfInput.type = 'hidden';
            csrfInput.name = 'csrf_token';
            csrfInput.value = csrfToken;
            
            form.appendChild(csrfInput);
            document.body.appendChild(form);
            form.submit();
        }
    });
});

console.log('CSRF token handler installed for all fetch requests');