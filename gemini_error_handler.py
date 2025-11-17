"""
Gemini API Error Handling and Logging Utilities

This module provides comprehensive error handling for Google Gemini API integration,
including structured logging, error categorization, and user-friendly error messages.
"""

import logging
import json
import time
import random
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from collections import deque

# Configure logging
logger = logging.getLogger(__name__)


class GeminiErrorLogger:
    """
    Structured error logging for Gemini API issues.
    
    Categorizes errors by type and provides detailed context for debugging
    and monitoring Gemini API reliability.
    """
    
    # Error code to category mapping
    ERROR_CATEGORIES = {
        '429': 'RATE_LIMIT',
        '503': 'MODEL_OVERLOAD', 
        '504': 'TIMEOUT',
        '500': 'SERVER_ERROR',
        '400': 'INVALID_REQUEST',
        '401': 'AUTH_ERROR',
        '403': 'PERMISSION_ERROR'
    }
    
    @staticmethod
    def categorize_error(error: Exception) -> str:
        """
        Determine error category from exception.
        
        Args:
            error: Exception raised by Gemini API
            
        Returns:
            Error category string (e.g., 'RATE_LIMIT', 'TIMEOUT')
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Check for specific error codes in message
        for code, category in GeminiErrorLogger.ERROR_CATEGORIES.items():
            if code in error_str:
                return category
        
        # Check for timeout-related exceptions
        if 'timeout' in error_type.lower() or 'timeout' in error_str:
            return 'TIMEOUT'
        
        # Check for resource exhausted (rate limit)
        if 'resource' in error_str and 'exhaust' in error_str:
            return 'RATE_LIMIT'
        
        # Check for overload
        if 'overload' in error_str or 'unavailable' in error_str:
            return 'MODEL_OVERLOAD'
        
        return 'UNKNOWN'
    
    @staticmethod
    def log_error(error: Exception, context: Optional[Dict[str, Any]] = None, store_in_session: bool = True) -> Dict[str, Any]:
        """
        Log Gemini API error with structured metadata.
        
        Args:
            error: The exception that occurred
            context: Additional context (user_id, organization_id, receipt_id, etc.)
            store_in_session: If True, store error category in Flask session for API access
            
        Returns:
            Dictionary containing structured error information
        """
        error_str = str(error)
        error_type = type(error).__name__
        category = GeminiErrorLogger.categorize_error(error)
        
        # Store error category in session for API endpoints to access
        # Only store category and timestamp, NOT the full error message (security)
        if store_in_session:
            try:
                from flask import session
                session['last_gemini_error'] = category
                session['last_gemini_error_time'] = datetime.utcnow().isoformat()
            except (RuntimeError, ImportError):
                # Session not available (outside request context or Flask not imported)
                pass
        
        # Build structured log entry
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'error_category': category,
            'error_type': error_type,
            'error_message': error_str[:500],  # Truncate long messages
            'context': context or {},
            'user_id': context.get('user_id') if context else None,
            'organization_id': context.get('organization_id') if context else None,
            'receipt_id': context.get('receipt_id') if context else None,
            'model': context.get('model') if context else None,
            'function': context.get('function') if context else None
        }
        
        # Log with appropriate level based on category
        if category == 'RATE_LIMIT':
            logger.warning(f"GEMINI_RATE_LIMIT: {json.dumps(log_entry, indent=2)}")
        elif category in ['TIMEOUT', 'MODEL_OVERLOAD']:
            logger.error(f"GEMINI_AVAILABILITY: {json.dumps(log_entry, indent=2)}")
        elif category in ['AUTH_ERROR', 'PERMISSION_ERROR']:
            logger.critical(f"GEMINI_AUTH: {json.dumps(log_entry, indent=2)}")
        else:
            logger.error(f"GEMINI_ERROR: {json.dumps(log_entry, indent=2)}")
        
        return log_entry
    
    @staticmethod
    def get_user_friendly_message(error_category: str) -> str:
        """
        Convert technical error category to user-friendly message.
        
        Args:
            error_category: Error category from categorize_error()
            
        Returns:
            User-friendly error message string
        """
        messages = {
            'RATE_LIMIT': (
                "Our AI service is experiencing high demand. Your receipt will be processed shortly. "
                "Please wait a moment and try again."
            ),
            'TIMEOUT': (
                "Receipt processing is taking longer than expected. "
                "Please try with a smaller or clearer image."
            ),
            'MODEL_OVERLOAD': (
                "Our AI service is temporarily busy. Please try again in a few minutes."
            ),
            'AUTH_ERROR': (
                "Configuration error. Please contact support."
            ),
            'PERMISSION_ERROR': (
                "Configuration error. Please contact support."
            ),
            'INVALID_REQUEST': (
                "The receipt image couldn't be processed. "
                "Please try a different image with clearer text."
            ),
            'SERVER_ERROR': (
                "Our AI service encountered an error. Please try again in a few moments."
            ),
            'UNKNOWN': (
                "An unexpected error occurred. Please try again or contact support if the issue persists."
            )
        }
        
        return messages.get(error_category, messages['UNKNOWN'])


class GeminiCircuitBreaker:
    """
    Circuit breaker pattern implementation for Gemini API calls.
    
    Prevents cascading failures by temporarily blocking requests when
    the Gemini API experiences persistent failures.
    
    States:
        CLOSED: Normal operation, requests pass through
        OPEN: Too many failures, requests are blocked
        HALF_OPEN: Testing if service has recovered
    """
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 300, window: int = 300):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures to trip the breaker
            timeout: Seconds to wait before trying again (OPEN → HALF_OPEN)
            window: Seconds for failure tracking window
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout  # Seconds in OPEN state
        self.window = window    # Failure tracking window
        self.failures = deque()  # Timestamps of failures
        self.state = "CLOSED"    # CLOSED, OPEN, HALF_OPEN
        self.open_until = 0
    
    def _clean_old_failures(self):
        """Remove failures older than the tracking window."""
        current_time = time.time()
        while self.failures and (current_time - self.failures[0] > self.window):
            self.failures.popleft()
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result from func
            
        Raises:
            Exception: If circuit is OPEN or func raises
        """
        current_time = time.time()
        
        # Clean old failures
        self._clean_old_failures()
        
        # Check circuit state
        if self.state == "OPEN":
            if current_time < self.open_until:
                wait_seconds = int(self.open_until - current_time)
                error_msg = (
                    f"Circuit breaker OPEN: Gemini API temporarily disabled due to repeated failures. "
                    f"Retry after {wait_seconds} seconds."
                )
                logger.warning(error_msg)
                raise Exception(error_msg)
            else:
                # Transition to HALF_OPEN to test recovery
                self.state = "HALF_OPEN"
                logger.info("Circuit breaker entering HALF_OPEN state - testing Gemini API recovery")
        
        try:
            # Execute the function
            result = func(*args, **kwargs)
            
            # Success - reset if in HALF_OPEN
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.failures.clear()
                logger.info("Circuit breaker CLOSED: Gemini API recovered successfully")
            
            return result
            
        except Exception as e:
            # Record failure
            self.failures.append(current_time)
            
            # Check if we should trip the breaker
            if len(self.failures) >= self.failure_threshold:
                self.state = "OPEN"
                self.open_until = current_time + self.timeout
                logger.critical(
                    f"Circuit breaker OPEN: {len(self.failures)} Gemini API failures in "
                    f"{self.window}s window. API disabled for {self.timeout}s"
                )
            
            # Re-raise the original exception
            raise


def generate_with_retry(
    model,
    prompt: Any,
    image: Any,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    request_options: Optional[Any] = None,
    global_timeout: float = 180.0  # 3 minutes total budget
) -> Any:
    """
    Generate content with exponential backoff retry logic and global timeout.
    
    Implements intelligent retry strategy with time budget enforcement:
    - Global timeout: All retries must complete within global_timeout seconds
    - Rate limits (429): Retry with exponential backoff + jitter
    - Model overload (503): Signal to try fallback model
    - Timeouts (504): Don't retry, log and raise
    - Invalid requests (400): Don't retry, log for debugging
    
    Args:
        model: Gemini GenerativeModel instance
        prompt: Text prompt or list of content parts
        image: PIL Image or image data
        max_retries: Maximum number of retry attempts for rate limits
        initial_delay: Initial delay in seconds (doubled each retry)
        request_options: Optional RequestOptions with per-request timeout
        global_timeout: Total time budget in seconds for all retries (default: 180s)
        
    Returns:
        GenerateContentResponse from Gemini API
        
    Raises:
        Exception: If max retries exceeded, global timeout exceeded, or non-retryable error
    """
    start_time = time.time()
    
    for attempt in range(max_retries):
        # Check if we've exceeded the global timeout budget
        elapsed = time.time() - start_time
        if elapsed >= global_timeout:
            error_msg = (
                f"Global timeout exceeded ({global_timeout}s). "
                f"Elapsed: {elapsed:.2f}s, Attempt: {attempt + 1}/{max_retries}"
            )
            logger.error(error_msg)
            raise TimeoutError(error_msg)
        try:
            # Make the API call with timeout if provided
            if request_options:
                response = model.generate_content([prompt, image], request_options=request_options)
            else:
                response = model.generate_content([prompt, image])
            
            # Success - log if this was a retry
            if attempt > 0:
                logger.info(f"Gemini API call succeeded on retry attempt {attempt + 1}/{max_retries}")
            
            return response
            
        except Exception as e:
            error_str = str(e).lower()
            error_type = type(e).__name__
            category = GeminiErrorLogger.categorize_error(e)
            
            # Log the error
            logger.warning(
                f"Gemini API error on attempt {attempt + 1}/{max_retries}: "
                f"{error_type} - {category}"
            )
            
            # Handle based on error category
            if category == 'RATE_LIMIT':
                # Rate limit - retry with exponential backoff
                if attempt < max_retries - 1:
                    # Exponential backoff: initial_delay * (2^attempt) + random jitter
                    wait_time = (initial_delay * (2 ** attempt)) + random.uniform(0, 1)
                    
                    # Cap wait time to not exceed global timeout budget
                    elapsed = time.time() - start_time
                    remaining = global_timeout - elapsed
                    if wait_time > remaining:
                        wait_time = max(0, remaining - 5)  # Leave 5s for next attempt
                        logger.warning(f"Capping wait time to {wait_time:.2f}s due to global timeout budget")
                    
                    logger.warning(
                        f"Rate limited (429). Retry {attempt + 1}/{max_retries} "
                        f"after {wait_time:.2f}s (elapsed: {elapsed:.2f}s/{global_timeout}s)"
                    )
                    
                    if wait_time > 0:
                        time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"Max retries ({max_retries}) exceeded for rate limit")
                    raise
                    
            elif category == 'MODEL_OVERLOAD':
                # Model overload (503) - signal caller to try fallback model
                logger.warning(
                    f"Model overloaded (503) on attempt {attempt + 1}. "
                    f"Caller should try fallback model."
                )
                # Return None to signal fallback needed
                return None
                
            elif category == 'TIMEOUT':
                # Timeout (504) - don't retry, these won't improve
                logger.error(f"Gemini API timeout (504) after configured timeout period")
                raise
                
            elif category == 'INVALID_REQUEST':
                # Invalid request (400) - don't retry, log for debugging
                logger.error(
                    f"Invalid request to Gemini API (400). Check image format and prompt. "
                    f"Error: {str(e)[:200]}"
                )
                raise
                
            else:
                # Unknown or server error - retry a couple times then give up
                if attempt < min(2, max_retries - 1):
                    wait_time = (initial_delay * (2 ** attempt)) + random.uniform(0, 1)
                    logger.warning(f"Retrying after {wait_time:.2f}s due to {category}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"Non-retryable error or max retries exceeded: {error_type}")
                    raise
    
    # Should never reach here, but just in case
    raise Exception(f"Failed after {max_retries} retry attempts")


# Module-level circuit breaker instance
# Shared across all Gemini API calls in the application
gemini_circuit_breaker = GeminiCircuitBreaker(
    failure_threshold=5,  # Trip after 5 failures
    timeout=300,          # Block for 5 minutes
    window=300            # Track failures in 5-minute window
)
