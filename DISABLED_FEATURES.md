# Temporarily Disabled Features

This document provides information about features that have been temporarily disabled in the application and will be re-enabled in future releases.

## Expense Pipeline Features

### 1. Export Feature

**Status**: Temporarily disabled

**Description**: The Export feature allows exporting expense pipeline data to Excel format.

**Location**:
- Frontend: `static/js/expense-pipeline.js` - `exportPipelineData()` function
- Backend: `expense_pipeline.py` - `/export` route

**How to re-enable**:
1. Set `FEATURES_DISABLED['EXPORT'] = False` in `expense_pipeline.py`
2. Remove the early return in `exportPipelineData()` function
3. Enable the export button in the HTML template by removing the `disabled` attribute

### 2. Batch Submit Feature

**Status**: Temporarily disabled

**Description**: The Batch Submit feature allows submitting multiple receipts as expenses at once.

**Location**:
- Frontend: `static/js/expense-pipeline.js` - `batchSubmitReceipts()` function
- Backend: `expense_pipeline.py` - `/batch-submit` route
- Template: `templates/batch_submit_expenses.html`

**How to re-enable**:
1. Set `FEATURES_DISABLED['BATCH_SUBMIT'] = False` in `expense_pipeline.py`
2. Remove the early return in `batchSubmitReceipts()` function
3. Update the route handler to process the request normally
4. Enable the batch submit button in the HTML template by removing the `disabled` attribute

## Testing Before Re-enabling

Before re-enabling these features, the following tests should be conducted:

1. **Export Testing**:
   - Test with different organization selections
   - Verify the exported data contains all relevant information
   - Check formatting and structure of the exported file
   - Test with various filter combinations

2. **Batch Submit Testing**:
   - Test with multiple receipts selected
   - Verify client selection works correctly
   - Test reimbursable flag functionality
   - Verify notes field is properly applied to all expenses
   - Test validation and error handling