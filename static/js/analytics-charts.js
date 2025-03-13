// Analytics Chart initialization

document.addEventListener('DOMContentLoaded', function() {
    // Set global Chart.js defaults
    Chart.defaults.font.family = "'Poppins', 'Helvetica', 'Arial', sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = '#666';
    Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(0, 0, 0, 0.7)';
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 4;
    Chart.defaults.plugins.legend.position = 'bottom';
    
    // Initialize charts when tab is shown (to ensure proper rendering)
    const tabs = document.querySelectorAll('button[data-bs-toggle="tab"]');
    tabs.forEach(tab => {
        tab.addEventListener('shown.bs.tab', function(e) {
            const targetId = e.target.getAttribute('data-bs-target');
            if (targetId === '#expense-analytics') {
                initExpenseCharts();
            } else if (targetId === '#business-revenue-analytics') {
                initBusinessRevenueCharts();
            } else if (targetId === '#personal-income-analytics') {
                initPersonalIncomeCharts();
            } else if (targetId === '#invoice-analytics') {
                initInvoiceCharts();
            } else if (targetId === '#tax-analytics') {
                initTaxCharts();
            }
        });
    });
    
    // Initialize the charts for the active tab on page load
    const activeTab = document.querySelector('.nav-link.active');
    if (activeTab) {
        const targetId = activeTab.getAttribute('data-bs-target');
        if (targetId === '#expense-analytics') {
            initExpenseCharts();
        } else if (targetId === '#business-revenue-analytics') {
            initBusinessRevenueCharts();
        } else if (targetId === '#personal-income-analytics') {
            initPersonalIncomeCharts();
        } else if (targetId === '#invoice-analytics') {
            initInvoiceCharts();
        } else if (targetId === '#tax-analytics') {
            initTaxCharts();
        }
    }
});

// 1. Expense Analytics Charts
function initExpenseCharts() {
    // Major Categories Pie Chart
    if (document.getElementById('majorCategoriesChart')) {
        const ctx = document.getElementById('majorCategoriesChart').getContext('2d');
        
        // Get data from data attributes
        const categoriesElement = document.getElementById('majorCategoriesData');
        if (!categoriesElement) return;
        
        const categoryLabels = JSON.parse(categoriesElement.getAttribute('data-labels') || '[]');
        const categoryValues = JSON.parse(categoriesElement.getAttribute('data-values') || '[]');
        
        // Generate vibrant colors
        const categoryColors = generateColors(categoryLabels.length);
        
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: categoryLabels,
                datasets: [{
                    data: categoryValues,
                    backgroundColor: categoryColors,
                    borderWidth: 1,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            boxWidth: 12
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw;
                                const total = context.dataset.data.reduce((acc, val) => acc + val, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${context.label}: Rs ${value.toLocaleString()} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
    
    // Minor Categories Chart
    if (document.getElementById('minorCategoriesChart')) {
        const ctx = document.getElementById('minorCategoriesChart').getContext('2d');
        
        // Get data from data attributes
        const categoriesElement = document.getElementById('minorCategoriesData');
        if (!categoriesElement) return;
        
        const categoryLabels = JSON.parse(categoriesElement.getAttribute('data-labels') || '[]');
        const categoryValues = JSON.parse(categoriesElement.getAttribute('data-values') || '[]');
        
        // Generate vibrant colors
        const categoryColors = generateColors(categoryLabels.length);
        
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: categoryLabels,
                datasets: [{
                    label: 'Amount (Rs)',
                    data: categoryValues,
                    backgroundColor: categoryColors,
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return 'Rs ' + value.toLocaleString();
                            }
                        }
                    },
                    x: {
                        ticks: {
                            maxRotation: 45,
                            minRotation: 45,
                            padding: 10
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw;
                                return `Amount: Rs ${value.toLocaleString()}`;
                            }
                        }
                    }
                }
            }
        });
    }
}

// 2. Business Revenue Charts
function initBusinessRevenueCharts() {
    // Income Sources Pie Chart
    if (document.getElementById('incomeSourcesChart')) {
        const ctx = document.getElementById('incomeSourcesChart').getContext('2d');
        
        // Get data from data attributes
        const incomeElement = document.getElementById('incomeData');
        if (!incomeElement) return;
        
        const incomeLabels = ["Business Income", "Employment Income", "Investment Income", "USD Consulting"];
        const incomeValues = [
            parseFloat(incomeElement.getAttribute('data-business-income') || '0'),
            parseFloat(incomeElement.getAttribute('data-employment-income') || '0'),
            parseFloat(incomeElement.getAttribute('data-investment-income') || '0'),
            parseFloat(incomeElement.getAttribute('data-usd-income') || '0')
        ];
        
        // Custom colors
        const incomeColors = ['#28a745', '#007bff', '#17a2b8', '#ffc107'];
        
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: incomeLabels,
                datasets: [{
                    data: incomeValues,
                    backgroundColor: incomeColors,
                    borderWidth: 1,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            boxWidth: 12
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw;
                                const total = context.dataset.data.reduce((acc, val) => acc + val, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${context.label}: Rs ${value.toLocaleString()} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
}

// 3. Personal Income Charts
function initPersonalIncomeCharts() {
    // Personal Income Pie Chart
    if (document.getElementById('personalIncomeChart')) {
        const ctx = document.getElementById('personalIncomeChart').getContext('2d');
        
        const employmentIncome = parseFloat(document.getElementById('employmentIncomeValue')?.textContent || '0');
        const investmentIncome = parseFloat(document.getElementById('investmentIncomeValue')?.textContent || '0');
        
        // Custom colors
        const incomeColors = ['#007bff', '#17a2b8'];
        
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: ['Employment Income', 'Investment Income'],
                datasets: [{
                    data: [employmentIncome, investmentIncome],
                    backgroundColor: incomeColors,
                    borderWidth: 1,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            boxWidth: 12
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw;
                                const total = context.dataset.data.reduce((acc, val) => acc + val, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${context.label}: Rs ${value.toLocaleString()} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
}

// 4. Invoice Analytics Charts
function initInvoiceCharts() {
    // Invoice Status Pie Chart
    if (document.getElementById('invoiceStatusChart')) {
        const ctx = document.getElementById('invoiceStatusChart').getContext('2d');
        
        // Get data from data attributes
        const invoiceElement = document.getElementById('invoiceStatusData');
        if (!invoiceElement) return;
        
        const statusLabels = ['Draft', 'Sent', 'Partially Paid', 'Paid', 'Overdue', 'Cancelled'];
        const statusValues = [
            parseInt(invoiceElement.getAttribute('data-draft') || '0'),
            parseInt(invoiceElement.getAttribute('data-sent') || '0'),
            parseInt(invoiceElement.getAttribute('data-partially-paid') || '0'),
            parseInt(invoiceElement.getAttribute('data-paid') || '0'),
            parseInt(invoiceElement.getAttribute('data-overdue') || '0'),
            parseInt(invoiceElement.getAttribute('data-cancelled') || '0')
        ];
        
        // Custom colors
        const statusColors = ['#6c757d', '#17a2b8', '#ffc107', '#28a745', '#dc3545', '#6610f2'];
        
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: statusLabels,
                datasets: [{
                    data: statusValues,
                    backgroundColor: statusColors,
                    borderWidth: 1,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            boxWidth: 12
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw;
                                const total = context.dataset.data.reduce((acc, val) => acc + val, 0);
                                const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : '0.0';
                                return `${context.label}: ${value} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
    
    // Monthly Invoiced Amount Chart
    if (document.getElementById('invoiceMonthlyChart')) {
        const ctx = document.getElementById('invoiceMonthlyChart').getContext('2d');
        
        // Get data from data attributes
        const monthlyElement = document.getElementById('invoiceMonthlyData');
        if (!monthlyElement) return;
        
        const monthlyLabels = JSON.parse(monthlyElement.getAttribute('data-labels') || '[]');
        const invoicedValues = JSON.parse(monthlyElement.getAttribute('data-invoiced') || '[]');
        const paidValues = JSON.parse(monthlyElement.getAttribute('data-paid') || '[]');
        
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: monthlyLabels,
                datasets: [
                    {
                        label: 'Invoiced',
                        data: invoicedValues,
                        backgroundColor: '#007bff',
                        borderWidth: 0
                    },
                    {
                        label: 'Paid',
                        data: paidValues,
                        backgroundColor: '#28a745',
                        borderWidth: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return 'Rs ' + value.toLocaleString();
                            }
                        }
                    }
                },
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw;
                                return `${context.dataset.label}: Rs ${value.toLocaleString()}`;
                            }
                        }
                    }
                }
            }
        });
    }
}

// 5. Tax Analytics Charts
function initTaxCharts() {
    // Tax Savings Chart
    if (document.getElementById('taxSavingsChart')) {
        const ctx = document.getElementById('taxSavingsChart').getContext('2d');
        
        // Get data from data attributes
        const taxElement = document.getElementById('taxData');
        if (!taxElement) return;
        
        const taxDeductible = parseFloat(taxElement.getAttribute('data-tax-deductible') || '0');
        const nonDeductible = parseFloat(taxElement.getAttribute('data-non-deductible') || '0');
        
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: ['Tax Deductible', 'Non-Deductible'],
                datasets: [{
                    data: [taxDeductible, nonDeductible],
                    backgroundColor: ['#28a745', '#6c757d'],
                    borderWidth: 1,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            boxWidth: 12
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw;
                                const total = context.dataset.data.reduce((acc, val) => acc + val, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${context.label}: Rs ${value.toLocaleString()} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
}

// Utility function to generate vibrant colors
function generateColors(count) {
    const baseColors = [
        '#4a6da7', '#28a745', '#dc3545', '#ffc107', '#17a2b8', 
        '#fd7e14', '#6610f2', '#20c997', '#e83e8c', '#6c757d'
    ];
    
    if (count <= baseColors.length) {
        return baseColors.slice(0, count);
    }
    
    // If we need more colors, generate them programmatically
    const colors = [...baseColors];
    for (let i = baseColors.length; i < count; i++) {
        const h = (i * 137) % 360; // Use golden ratio for evenly distributed hues
        const s = 70 + (i % 3) * 10; // Vary saturation
        const l = 40 + (i % 5) * 5;  // Vary lightness
        colors.push(`hsl(${h}, ${s}%, ${l}%)`);
    }
    
    return colors;
}