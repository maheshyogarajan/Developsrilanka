/**
 * CSRF Protection Enhancement
 * This script adds CSRF protection to all fetch requests and converts destructive links to POST forms
 */

// Function to get CSRF token from meta tag
function getCSRFToken() {
    const metaTag = document.querySelector('meta[name="csrf-token"]');
    return metaTag ? metaTag.getAttribute('content') : null;
}

// Wrap fetch to automatically include CSRF token in headers
const originalFetch = window.fetch;
window.fetch = function(url, options = {}) {
    // Only add CSRF token for same-origin requests
    const sameOrigin = url.startsWith('/') || url.startsWith(window.location.origin);
    
    if (sameOrigin) {
        // Initialize headers if not present
        if (!options.headers) {
            options.headers = {};
        }
        
        // Convert Headers object to plain object if needed
        if (options.headers instanceof Headers) {
            const originalHeaders = options.headers;
            options.headers = {};
            for (const [key, value] of originalHeaders.entries()) {
                options.headers[key] = value;
            }
        }
        
        // Add CSRF token to headers if not already present
        if (!options.headers['X-CSRF-Token']) {
            const csrfToken = getCSRFToken();
            if (csrfToken) {
                options.headers['X-CSRF-Token'] = csrfToken;
            }
        }
    }
    
    return originalFetch(url, options);
};

// Convert destructive GET links to POST forms with CSRF token
document.addEventListener('DOMContentLoaded', function() {
    console.log('CSRF token handler installed for all fetch requests');
    
    // Process all links that perform state-changing operations
    const destructiveLinks = document.querySelectorAll('a[data-method="post"], a[data-method="delete"]');
    
    destructiveLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            
            // Create a hidden form
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = link.href;
            form.style.display = 'none';
            
            // Add CSRF token
            const csrfToken = getCSRFToken();
            if (csrfToken) {
                const csrfInput = document.createElement('input');
                csrfInput.type = 'hidden';
                csrfInput.name = 'csrf_token';
                csrfInput.value = csrfToken;
                form.appendChild(csrfInput);
            }
            
            // Add _method if it's a DELETE or other method
            if (link.dataset.method.toUpperCase() !== 'POST') {
                const methodInput = document.createElement('input');
                methodInput.type = 'hidden';
                methodInput.name = '_method';
                methodInput.value = link.dataset.method.toUpperCase();
                form.appendChild(methodInput);
            }
            
            // Submit the form
            document.body.appendChild(form);
            form.submit();
        });
    });
});