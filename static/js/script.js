// Main JavaScript file for Develop Sri Lanka Receipt Scanner

document.addEventListener('DOMContentLoaded', function() {
    // Initialize Bootstrap tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Initialize Bootstrap popovers
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    var popoverList = popoverTriggerList.map(function(popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // Check for any flash messages and auto-dismiss them after 5 seconds
    setTimeout(function() {
        var alerts = document.querySelectorAll('.alert');
        alerts.forEach(function(alert) {
            var bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        });
    }, 5000);
});

// Function to format currency (LKR - Sri Lankan Rupee)
function formatCurrency(amount) {
    if (amount === null || amount === undefined) return "Rs 0.00";
    return "Rs " + parseFloat(amount).toFixed(2).replace(/\d(?=(\d{3})+\.)/g, '$&,');
}

// Function to show loading spinner
function showLoading(message = "Loading...") {
    const loadingElement = document.getElementById('loadingSpinner');
    if (loadingElement) {
        const messageElement = loadingElement.querySelector('.loading-message');
        if (messageElement) messageElement.textContent = message;
        loadingElement.style.display = 'flex';
    }
}

// Function to hide loading spinner
function hideLoading() {
    const loadingElement = document.getElementById('loadingSpinner');
    if (loadingElement) {
        loadingElement.style.display = 'none';
    }
}

// Function to show error message
function showError(message) {
    const errorElement = document.getElementById('errorAlert');
    if (errorElement) {
        const messageElement = errorElement.querySelector('.error-message');
        if (messageElement) messageElement.textContent = message;
        errorElement.style.display = 'block';
    }
}

// Function to hide error message
function hideError() {
    const errorElement = document.getElementById('errorAlert');
    if (errorElement) {
        errorElement.style.display = 'none';
    }
}