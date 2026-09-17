# -*- coding: utf-8 -*-
"""
Security utilities for the Test Plugin
Provides rate limiting, input validation, and security logging
Multi-OS compatible security implementation
"""
import time
import hashlib
import hmac
import json
import re
import os
import platform
from django.core.cache import cache
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.auth.models import User
from functools import wraps
from .models import TestPluginLog
from .os_config import get_os_config


class SecurityManager:
    """Centralized security management for the plugin"""
    
    # Rate limiting settings
    RATE_LIMIT_WINDOW = 300  # 5 minutes
    MAX_REQUESTS_PER_WINDOW = 50
    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_DURATION = 900  # 15 minutes
    
    # Input validation patterns
    SAFE_STRING_PATTERN = re.compile(r'^[a-zA-Z0-9\s\-_.,!?@#$%^&*()+=\[\]{}|\\:";\'<>?/~`]*$')
    MAX_MESSAGE_LENGTH = 1000
    
    @staticmethod
    def is_rate_limited(request):
        """Check if user has exceeded rate limits"""
        user_id = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR')
        cache_key = f"rate_limit_{user_id}"
        
        current_time = time.time()
        requests = cache.get(cache_key, [])
        
        # Remove old requests outside the window
        requests = [req_time for req_time in requests if current_time - req_time < SecurityManager.RATE_LIMIT_WINDOW]
        
        if len(requests) >= SecurityManager.MAX_REQUESTS_PER_WINDOW:
            return True
            
        # Add current request
        requests.append(current_time)
        cache.set(cache_key, requests, SecurityManager.RATE_LIMIT_WINDOW)
        return False
    
    @staticmethod
    def is_user_locked_out(request):
        """Check if user is temporarily locked out due to failed attempts"""
        user_id = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR')
        lockout_key = f"lockout_{user_id}"
        
        return cache.get(lockout_key, False)
    
    @staticmethod
    def record_failed_attempt(request, reason="Invalid request"):
        """Record a failed security attempt"""
        user_id = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR')
        failed_key = f"failed_attempts_{user_id}"
        
        attempts = cache.get(failed_key, 0) + 1
        cache.set(failed_key, attempts, SecurityManager.RATE_LIMIT_WINDOW)
        
        # Log security event
        SecurityManager.log_security_event(request, f"Failed attempt: {reason}", "security_failure")
        
        # Lock out user if too many failed attempts
        if attempts >= SecurityManager.MAX_FAILED_ATTEMPTS:
            lockout_key = f"lockout_{user_id}"
            cache.set(lockout_key, True, SecurityManager.LOCKOUT_DURATION)
            SecurityManager.log_security_event(request, "User locked out due to excessive failed attempts", "user_locked_out")
    
    @staticmethod
    def clear_failed_attempts(request):
        """Clear failed attempts for user after successful action"""
        user_id = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR')
        failed_key = f"failed_attempts_{user_id}"
        cache.delete(failed_key)
    
    @staticmethod
    def validate_input(data, field_name, max_length=None):
        """Validate input data for security"""
        if not isinstance(data, str):
            return False, f"{field_name} must be a string"
        
        if max_length and len(data) > max_length:
            return False, f"{field_name} exceeds maximum length of {max_length} characters"
        
        if not SecurityManager.SAFE_STRING_PATTERN.match(data):
            return False, f"{field_name} contains invalid characters"
        
        return True, "Valid"
    
    @staticmethod
    def sanitize_input(data):
        """Sanitize input data"""
        if isinstance(data, str):
            # Remove potential XSS vectors
            data = data.replace('<script', '&lt;script')
            data = data.replace('</script>', '&lt;/script&gt;')
            data = data.replace('javascript:', '')
            data = data.replace('onload=', '')
            data = data.replace('onerror=', '')
            data = data.replace('onclick=', '')
            data = data.replace('onmouseover=', '')
            # Remove null bytes
            data = data.replace('\x00', '')
            # Limit length
            data = data[:SecurityManager.MAX_MESSAGE_LENGTH]
        
        return data
    
    @staticmethod
    def log_security_event(request, message, event_type="security"):
        """Log security-related events"""
        try:
            user_id = request.user.id if request.user.is_authenticated else None
            ip_address = request.META.get('REMOTE_ADDR', 'unknown')
            user_agent = request.META.get('HTTP_USER_AGENT', 'unknown')
            
            TestPluginLog.objects.create(
                user_id=user_id,
                action=event_type,
                message=f"[SECURITY] {message} | IP: {ip_address} | UA: {user_agent[:100]}"
            )
        except Exception:
            # Don't let logging errors break the application
            pass
    
    @staticmethod
    def generate_csrf_token(request):
        """Generate a secure CSRF token"""
        if hasattr(request, 'csrf_token'):
            return request.csrf_token
        
        # Fallback CSRF token generation
        secret = getattr(settings, 'SECRET_KEY', 'fallback-secret')
        timestamp = str(int(time.time()))
        user_id = str(request.user.id) if request.user.is_authenticated else 'anonymous'
        
        token_data = f"{user_id}:{timestamp}"
        token = hmac.new(
            secret.encode(),
            token_data.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return f"{token}:{timestamp}"
    
    @staticmethod
    def verify_csrf_token(request, token):
        """Verify CSRF token"""
        if hasattr(request, 'csrf_token'):
            return request.csrf_token == token
        
        try:
            secret = getattr(settings, 'SECRET_KEY', 'fallback-secret')
            token_part, timestamp = token.split(':')
            
            # Check if token is not too old (1 hour)
            if time.time() - int(timestamp) > 3600:
                return False
            
            user_id = str(request.user.id) if request.user.is_authenticated else 'anonymous'
            token_data = f"{user_id}:{timestamp}"
            expected_token = hmac.new(
                secret.encode(),
                token_data.encode(),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(token_part, expected_token)
        except (ValueError, AttributeError):
            return False


def secure_view(require_csrf=True, rate_limit=True, log_activity=True):
    """Decorator for secure view functions"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Check if user is locked out
            if SecurityManager.is_user_locked_out(request):
                SecurityManager.log_security_event(request, "Blocked request from locked out user", "blocked_request")
                return JsonResponse({
                    'status': 0,
                    'error_message': 'Account temporarily locked due to security violations. Please try again later.'
                }, status=423)
            
            # Check rate limiting
            if rate_limit and SecurityManager.is_rate_limited(request):
                SecurityManager.record_failed_attempt(request, "Rate limit exceeded")
                return JsonResponse({
                    'status': 0,
                    'error_message': 'Too many requests. Please slow down and try again later.'
                }, status=429)
            
            # CSRF protection
            if require_csrf and request.method == 'POST':
                csrf_token = request.META.get('HTTP_X_CSRFTOKEN') or request.POST.get('csrfmiddlewaretoken')
                if not csrf_token or not SecurityManager.verify_csrf_token(request, csrf_token):
                    SecurityManager.record_failed_attempt(request, "Invalid CSRF token")
                    return JsonResponse({
                        'status': 0,
                        'error_message': 'Invalid security token. Please refresh the page and try again.'
                    }, status=403)
            
            # Log activity
            if log_activity:
                SecurityManager.log_security_event(request, f"Accessing {view_func.__name__}", "view_access")
            
            try:
                result = view_func(request, *args, **kwargs)
                # Clear failed attempts on successful request
                SecurityManager.clear_failed_attempts(request)
                return result
            except Exception as e:
                SecurityManager.log_security_event(request, f"Error in {view_func.__name__}: {str(e)}", "view_error")
                return JsonResponse({
                    'status': 0,
                    'error_message': 'An internal error occurred. Please try again later.'
                }, status=500)
        
        return wrapper
    return decorator


def admin_required(view_func):
    """Decorator to ensure only admin users can access the view"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({
                'status': 0,
                'error_message': 'Authentication required.'
            }, status=401)
        
        if not request.user.is_staff and not request.user.is_superuser:
            SecurityManager.log_security_event(request, "Unauthorized access attempt by non-admin user", "unauthorized_access")
            return JsonResponse({
                'status': 0,
                'error_message': 'Admin privileges required.'
            }, status=403)
        
        return view_func(request, *args, **kwargs)
    return wrapper
