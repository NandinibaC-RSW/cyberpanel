# -*- coding: utf-8 -*-
"""
Security middleware for the Test Plugin
Provides additional security measures and monitoring
"""
import time
import hashlib
from django.http import JsonResponse
from django.core.cache import cache
from django.conf import settings
from .security import SecurityManager


class TestPluginSecurityMiddleware:
    """
    Security middleware for the Test Plugin
    Provides additional protection against various attacks
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Only apply security measures to testPlugin URLs
        if not request.path.startswith('/testPlugin/'):
            return self.get_response(request)
        
        # Security checks
        if not self._security_checks(request):
            return JsonResponse({
                'status': 0,
                'error_message': 'Security violation detected. Access denied.'
            }, status=403)
        
        response = self.get_response(request)
        
        # Add security headers
        self._add_security_headers(response)
        
        return response
    
    def _security_checks(self, request):
        """Perform security checks on the request"""
        
        # Check for suspicious patterns
        if self._is_suspicious_request(request):
            SecurityManager.log_security_event(request, "Suspicious request pattern detected", "suspicious_request")
            return False
        
        # Check for SQL injection attempts
        if self._has_sql_injection_patterns(request):
            SecurityManager.log_security_event(request, "SQL injection attempt detected", "sql_injection")
            return False
        
        # Check for XSS attempts
        if self._has_xss_patterns(request):
            SecurityManager.log_security_event(request, "XSS attempt detected", "xss_attempt")
            return False
        
        # Check for path traversal attempts
        if self._has_path_traversal_patterns(request):
            SecurityManager.log_security_event(request, "Path traversal attempt detected", "path_traversal")
            return False
        
        return True
    
    def _is_suspicious_request(self, request):
        """Check for suspicious request patterns"""
        suspicious_patterns = [
            '..', '//', '\\', 'cmd', 'exec', 'system', 'eval',
            'base64', 'decode', 'encode', 'hex', 'binary',
            'union', 'select', 'insert', 'update', 'delete',
            'drop', 'create', 'alter', 'grant', 'revoke'
        ]
        
        # Check URL
        url_lower = request.path.lower()
        for pattern in suspicious_patterns:
            if pattern in url_lower:
                return True
        
        # Check query parameters
        for key, value in request.GET.items():
            if isinstance(value, str):
                value_lower = value.lower()
                for pattern in suspicious_patterns:
                    if pattern in value_lower:
                        return True
        
        # Check POST data
        if request.method == 'POST':
            for key, value in request.POST.items():
                if isinstance(value, str):
                    value_lower = value.lower()
                    for pattern in suspicious_patterns:
                        if pattern in value_lower:
                            return True
        
        return False
    
    def _has_sql_injection_patterns(self, request):
        """Check for SQL injection patterns"""
        sql_patterns = [
            "'", '"', ';', '--', '/*', '*/', 'xp_', 'sp_',
            'union', 'select', 'insert', 'update', 'delete',
            'drop', 'create', 'alter', 'exec', 'execute',
            'waitfor', 'delay', 'benchmark', 'sleep'
        ]
        
        # Check all request data
        all_data = []
        all_data.extend(request.GET.values())
        all_data.extend(request.POST.values())
        
        for value in all_data:
            if isinstance(value, str):
                value_lower = value.lower()
                for pattern in sql_patterns:
                    if pattern in value_lower:
                        return True
        
        return False
    
    def _has_xss_patterns(self, request):
        """Check for XSS patterns"""
        xss_patterns = [
            '<script', '</script>', 'javascript:', 'vbscript:',
            'onload=', 'onerror=', 'onclick=', 'onmouseover=',
            'onfocus=', 'onblur=', 'onchange=', 'onsubmit=',
            'onreset=', 'onselect=', 'onkeydown=', 'onkeyup=',
            'onkeypress=', 'onmousedown=', 'onmouseup=',
            'onmousemove=', 'onmouseout=', 'oncontextmenu='
        ]
        
        # Check all request data
        all_data = []
        all_data.extend(request.GET.values())
        all_data.extend(request.POST.values())
        
        for value in all_data:
            if isinstance(value, str):
                value_lower = value.lower()
                for pattern in xss_patterns:
                    if pattern in value_lower:
                        return True
        
        return False
    
    def _has_path_traversal_patterns(self, request):
        """Check for path traversal patterns"""
        traversal_patterns = [
            '../', '..\\', '..%2f', '..%5c', '%2e%2e%2f',
            '%2e%2e%5c', '..%252f', '..%255c'
        ]
        
        # Check URL and all request data
        all_data = [request.path]
        all_data.extend(request.GET.values())
        all_data.extend(request.POST.values())
        
        for value in all_data:
            if isinstance(value, str):
                for pattern in traversal_patterns:
                    if pattern in value.lower():
                        return True
        
        return False
    
    def _add_security_headers(self, response):
        """Add security headers to the response"""
        # Prevent clickjacking
        response['X-Frame-Options'] = 'DENY'
        
        # Prevent MIME type sniffing
        response['X-Content-Type-Options'] = 'nosniff'
        
        # Enable XSS protection
        response['X-XSS-Protection'] = '1; mode=block'
        
        # Strict Transport Security (if HTTPS)
        if request.is_secure():
            response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        
        # Content Security Policy
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        
        # Referrer Policy
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Permissions Policy
        response['Permissions-Policy'] = (
            "geolocation=(), "
            "microphone=(), "
            "camera=(), "
            "payment=(), "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=(), "
            "accelerometer=()"
        )
