"""
Cache utility for expense pipeline data.
Provides efficient caching with proper user and organization isolation.
"""

import os
import time
import json
import hashlib
import logging
from functools import wraps
from flask import request, jsonify, Response
from flask_login import current_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PipelineCache:
    """
    Cache manager for pipeline data with security isolation.
    Supports both Redis and in-memory fallback.
    """
    
    def __init__(self):
        """Initialize cache backend based on available resources."""
        self.redis_client = None
        self.memory_cache = {}
        self.cache_prefix = "pipeline"
        self.default_timeout = 60
        
        # Try to connect to Redis if configured
        redis_url = os.environ.get('REDIS_URL')
        if redis_url:
            try:
                import redis
                self.redis_client = redis.from_url(redis_url)
                # Test connection
                self.redis_client.ping()
                logger.info("Pipeline cache using Redis backend")
            except Exception as e:
                logger.warning(f"Redis connection failed, using in-memory cache: {str(e)}")
    
    def generate_key(self, user_id, org_id, params=None):
        """
        Generate a secure and isolated cache key.
        
        Args:
            user_id: User ID for isolation
            org_id: Organization ID for isolation
            params: Optional request parameters
            
        Returns:
            str: Cache key
        """
        # Basic key parts
        key_parts = [self.cache_prefix, str(user_id), str(org_id)]
        
        # Add parameters hash if provided
        if params:
            param_str = json.dumps(params, sort_keys=True)
            param_hash = hashlib.md5(param_str.encode()).hexdigest()
            key_parts.append(param_hash)
        
        # Join with colon separator
        return ":".join(key_parts)
    
    def get(self, user_id, org_id, params=None):
        """
        Get data from cache if available.
        
        Args:
            user_id: User ID
            org_id: Organization ID
            params: Optional request parameters
            
        Returns:
            dict: Cached data or None
        """
        key = self.generate_key(user_id, org_id, params)
        
        if self.redis_client:
            try:
                cached_data = self.redis_client.get(key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as e:
                logger.warning(f"Redis get error: {str(e)}")
        else:
            # In-memory fallback
            if key in self.memory_cache:
                entry = self.memory_cache[key]
                if time.time() < entry['expires']:
                    return entry['data']
                else:
                    del self.memory_cache[key]
        
        return None
    
    def set(self, user_id, org_id, data, params=None, timeout=None):
        """
        Store data in cache with timeout.
        
        Args:
            user_id: User ID
            org_id: Organization ID
            data: Data to cache
            params: Optional request parameters
            timeout: Cache timeout in seconds
        """
        if timeout is None:
            timeout = self.default_timeout
            
        key = self.generate_key(user_id, org_id, params)
        
        if self.redis_client:
            try:
                self.redis_client.setex(key, timeout, json.dumps(data))
            except Exception as e:
                logger.warning(f"Redis set error: {str(e)}")
        else:
            # In-memory fallback
            self.memory_cache[key] = {
                'data': data,
                'expires': time.time() + timeout
            }
    
    def invalidate(self, user_id=None, org_id=None):
        """
        Invalidate cache entries matching criteria.
        
        Args:
            user_id: Optional user ID to filter
            org_id: Optional organization ID to filter
        """
        if self.redis_client:
            try:
                # Build pattern based on parameters
                if user_id and org_id:
                    pattern = f"{self.cache_prefix}:{user_id}:{org_id}:*"
                elif user_id:
                    pattern = f"{self.cache_prefix}:{user_id}:*"
                elif org_id:
                    pattern = f"{self.cache_prefix}:*:{org_id}:*"
                else:
                    pattern = f"{self.cache_prefix}:*"
                    
                # Use scan for better performance
                cursor = 0
                deleted_count = 0
                while True:
                    cursor, keys = self.redis_client.scan(cursor, match=pattern, count=100)
                    if keys:
                        self.redis_client.delete(*keys)
                        deleted_count += len(keys)
                    if cursor == 0:
                        break
                        
                logger.info(f"Invalidated {deleted_count} Redis cache entries with pattern {pattern}")
            except Exception as e:
                logger.warning(f"Redis invalidate error: {str(e)}")
        else:
            # In-memory fallback
            keys_to_delete = []
            
            for key in list(self.memory_cache.keys()):
                if key.startswith(f"{self.cache_prefix}:"):
                    parts = key.split(':')
                    if len(parts) >= 3:
                        # Format is prefix:user_id:org_id:...
                        if user_id and str(user_id) != parts[1]:
                            continue
                        if org_id and str(org_id) != parts[2]:
                            continue
                        keys_to_delete.append(key)
            
            # Delete the matched keys
            for key in keys_to_delete:
                del self.memory_cache[key]
                
            logger.info(f"Invalidated {len(keys_to_delete)} in-memory cache entries")

def calculate_priority(amount):
    """
    Calculate priority level based on expense amount.
    
    Args:
        amount: Expense amount
        
    Returns:
        int: Priority level (1-3)
    """
    if amount > 1000:
        return 3
    elif amount < 100:
        return 1
    else:
        return 2

# Initialize global cache instance
pipeline_cache = PipelineCache()

def cache_pipeline_data(timeout=60):
    """
    Cache decorator for pipeline data endpoints.
    
    Args:
        timeout: Cache timeout in seconds
        
    Returns:
        Decorated function
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Skip caching for non-GET requests
            if request.method != 'GET':
                return f(*args, **kwargs)
                
            # Get current user ID
            if not current_user or not current_user.is_authenticated:
                return f(*args, **kwargs)
                
            # Extract parameters for cache key
            try:
                org_id = request.args.get('org_id')
                if org_id:
                    org_id = int(org_id)
            except (ValueError, TypeError):
                org_id = 'default'
                
            params = dict(request.args)
            
            # Try to get from cache first
            cached_data = pipeline_cache.get(current_user.id, org_id, params)
            if cached_data:
                return jsonify(cached_data)
            
            # Call the original function
            result = f(*args, **kwargs)
            
            # Cache the JSON response data
            if isinstance(result, Response):
                try:
                    response_data = result.get_data(as_text=True)
                    if response_data:
                        data = json.loads(response_data)
                        pipeline_cache.set(current_user.id, org_id, data, params, timeout)
                except Exception as e:
                    logger.warning(f"Failed to cache response: {str(e)}")
            
            return result
        return wrapper
    return decorator

def invalidate_pipeline_cache(user_id=None, org_id=None):
    """
    Invalidate pipeline cache entries.
    
    Args:
        user_id: Optional user ID to filter
        org_id: Optional organization ID to filter
    """
    pipeline_cache.invalidate(user_id, org_id)