/**
 * Dashboard Statistics Updater
 * Updates the dashboard statistics with predefined values
 */
document.addEventListener('DOMContentLoaded', function() {
    // Monitor DOM changes to catch dynamically loaded dashboard elements
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            // Look for specific number updates
            updateDashboardNumbers();
        });
    });

    // Start observing the document body for DOM changes
    observer.observe(document.body, { 
        childList: true, 
        subtree: true 
    });
    
    // Initial check for numbers
    updateDashboardNumbers();
    
    // Function to update dashboard numbers
    function updateDashboardNumbers() {
        // For debug only
        console.log("Checking for dashboard numbers to update");
        
        // Direct targeting of numbers in the dashboard
        // Look for elements containing "248" and replace with "112,000+"
        document.querySelectorAll('*').forEach(element => {
            if (element.childNodes && element.childNodes.length === 1 && 
                element.childNodes[0].nodeType === Node.TEXT_NODE) {
                // Check for exact match to "248"
                if (element.textContent.trim() === "248") {
                    console.log("Found 248, replacing with 112,000+");
                    element.textContent = "112,000+";
                }
                // Check for exact match to "36"
                else if (element.textContent.trim() === "36") {
                    console.log("Found 36, replacing with 3,500+");
                    element.textContent = "3,500+";
                }
            }
        });
    }
});