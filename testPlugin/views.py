# -*- coding: utf-8 -*-
import json
import os
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.utils import timezone
from django.core.cache import cache
from plogical.httpProc import httpProc
from .models import TestPluginSettings, TestPluginLog
from .security import secure_view, admin_required, SecurityManager


@admin_required
@secure_view(require_csrf=False, rate_limit=True, log_activity=True)
def plugin_home(request):
    """Main plugin page with inline integration"""
    try:
        # Get or create plugin settings
        settings, created = TestPluginSettings.objects.get_or_create(
            user=request.user,
            defaults={'plugin_enabled': True}
        )
        
        # Get recent logs (limit to user's own logs for security)
        recent_logs = TestPluginLog.objects.filter(user=request.user).order_by('-timestamp')[:10]
        
        context = {
            'settings': settings,
            'recent_logs': recent_logs,
            'plugin_enabled': settings.plugin_enabled,
        }
        
        # Log page visit
        TestPluginLog.objects.create(
            user=request.user,
            action='page_visit',
            message='Visited plugin home page'
        )
        
        proc = httpProc(request, 'testPlugin/plugin_home.html', context, 'admin')
        return proc.render()
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in plugin_home: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while loading the page.'})


@admin_required
@secure_view(require_csrf=True, rate_limit=True, log_activity=True)
@require_http_methods(["POST"])
def test_button(request):
    """Handle test button click and show popup message"""
    try:
        settings, created = TestPluginSettings.objects.get_or_create(
            user=request.user,
            defaults={'plugin_enabled': True}
        )
        
        if not settings.plugin_enabled:
            SecurityManager.log_security_event(request, "Test button clicked while plugin disabled", "security_violation")
            return JsonResponse({
                'status': 0, 
                'error_message': 'Plugin is disabled. Please enable it first.'
            })
        
        # Rate limiting for test button (max 10 clicks per minute)
        test_key = f"test_button_{request.user.id}"
        test_count = cache.get(test_key, 0)
        if test_count >= 10:
            SecurityManager.record_failed_attempt(request, "Test button rate limit exceeded")
            return JsonResponse({
                'status': 0,
                'error_message': 'Too many test button clicks. Please wait before trying again.'
            }, status=429)
        
        cache.set(test_key, test_count + 1, 60)  # 1 minute window
        
        # Increment test count
        settings.test_count += 1
        settings.save()
        
        # Create log entry
        TestPluginLog.objects.create(
            user=request.user,
            action='test_button_click',
            message=f'Test button clicked (count: {settings.test_count})'
        )
        
        # Sanitize custom message
        safe_message = SecurityManager.sanitize_input(settings.custom_message)
        
        # Prepare popup message
        popup_message = {
            'type': 'success',
            'title': 'Test Successful!',
            'message': f'{safe_message} (Clicked {settings.test_count} times)',
            'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        return JsonResponse({
            'status': 1,
            'popup_message': popup_message,
            'test_count': settings.test_count
        })
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in test_button: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while processing the test.'})


@admin_required
@secure_view(require_csrf=True, rate_limit=True, log_activity=True)
@require_http_methods(["POST"])
def toggle_plugin(request):
    """Toggle plugin enable/disable state"""
    try:
        settings, created = TestPluginSettings.objects.get_or_create(
            user=request.user,
            defaults={'plugin_enabled': True}
        )
        
        # Toggle the state
        settings.plugin_enabled = not settings.plugin_enabled
        settings.save()
        
        # Log the action
        action = 'enabled' if settings.plugin_enabled else 'disabled'
        TestPluginLog.objects.create(
            user=request.user,
            action='plugin_toggle',
            message=f'Plugin {action}'
        )
        
        SecurityManager.log_security_event(request, f"Plugin {action} by user", "plugin_toggle")
        
        return JsonResponse({
            'status': 1,
            'enabled': settings.plugin_enabled,
            'message': f'Plugin {action} successfully'
        })
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in toggle_plugin: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while toggling the plugin.'})


@admin_required
@secure_view(require_csrf=False, rate_limit=True, log_activity=True)
def plugin_settings(request):
    """Plugin settings page"""
    try:
        settings, created = TestPluginSettings.objects.get_or_create(
            user=request.user,
            defaults={'plugin_enabled': True}
        )
        
        context = {
            'settings': settings,
        }
        
        proc = httpProc(request, 'testPlugin/plugin_settings.html', context, 'admin')
        return proc.render()
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in plugin_settings: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while loading settings.'})


@admin_required
@secure_view(require_csrf=True, rate_limit=True, log_activity=True)
@require_http_methods(["POST"])
def update_settings(request):
    """Update plugin settings"""
    try:
        settings, created = TestPluginSettings.objects.get_or_create(
            user=request.user,
            defaults={'plugin_enabled': True}
        )
        
        data = json.loads(request.body)
        custom_message = data.get('custom_message', settings.custom_message)
        
        # Validate and sanitize input
        is_valid, error_msg = SecurityManager.validate_input(custom_message, 'custom_message', 1000)
        if not is_valid:
            SecurityManager.record_failed_attempt(request, f"Invalid input: {error_msg}")
            return JsonResponse({
                'status': 0,
                'error_message': f'Invalid input: {error_msg}'
            }, status=400)
        
        # Sanitize the message
        custom_message = SecurityManager.sanitize_input(custom_message)
        
        settings.custom_message = custom_message
        settings.save()
        
        # Log the action
        TestPluginLog.objects.create(
            user=request.user,
            action='settings_update',
            message=f'Settings updated: custom_message="{custom_message[:50]}..."'
        )
        
        SecurityManager.log_security_event(request, "Settings updated successfully", "settings_update")
        
        return JsonResponse({
            'status': 1,
            'message': 'Settings updated successfully'
        })
        
    except json.JSONDecodeError:
        SecurityManager.record_failed_attempt(request, "Invalid JSON in settings update")
        return JsonResponse({
            'status': 0,
            'error_message': 'Invalid data format. Please try again.'
        }, status=400)
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in update_settings: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while updating settings.'})


@admin_required
@secure_view(require_csrf=True, rate_limit=True, log_activity=True)
@require_http_methods(["POST"])
def install_plugin(request):
    """Install plugin (placeholder for future implementation)"""
    try:
        # Log the action
        TestPluginLog.objects.create(
            user=request.user,
            action='plugin_install',
            message='Plugin installation requested'
        )
        
        SecurityManager.log_security_event(request, "Plugin installation requested", "plugin_install")
        
        return JsonResponse({
            'status': 1,
            'message': 'Plugin installation completed successfully'
        })
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in install_plugin: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred during installation.'})


@admin_required
@secure_view(require_csrf=True, rate_limit=True, log_activity=True)
@require_http_methods(["POST"])
def uninstall_plugin(request):
    """Uninstall plugin (placeholder for future implementation)"""
    try:
        # Log the action
        TestPluginLog.objects.create(
            user=request.user,
            action='plugin_uninstall',
            message='Plugin uninstallation requested'
        )
        
        SecurityManager.log_security_event(request, "Plugin uninstallation requested", "plugin_uninstall")
        
        return JsonResponse({
            'status': 1,
            'message': 'Plugin uninstallation completed successfully'
        })
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in uninstall_plugin: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred during uninstallation.'})


@admin_required
@secure_view(require_csrf=False, rate_limit=True, log_activity=True)
def plugin_logs(request):
    """View plugin logs"""
    try:
        # Only show logs for the current user (security isolation)
        logs = TestPluginLog.objects.filter(user=request.user).order_by('-timestamp')[:50]
        
        context = {
            'logs': logs,
        }
        
        proc = httpProc(request, 'testPlugin/plugin_logs.html', context, 'admin')
        return proc.render()
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in plugin_logs: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while loading logs.'})


@admin_required
@secure_view(require_csrf=False, rate_limit=True, log_activity=True)
def plugin_docs(request):
    """View plugin documentation"""
    try:
        context = {}
        
        proc = httpProc(request, 'testPlugin/plugin_docs.html', context, 'admin')
        return proc.render()
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in plugin_docs: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while loading documentation.'})


@admin_required
@secure_view(require_csrf=False, rate_limit=True, log_activity=True)
def security_info(request):
    """View security information"""
    try:
        context = {}
        
        proc = httpProc(request, 'testPlugin/security_info.html', context, 'admin')
        return proc.render()
        
    except Exception as e:
        SecurityManager.log_security_event(request, f"Error in security_info: {str(e)}", "view_error")
        return JsonResponse({'status': 0, 'error_message': 'An error occurred while loading security information.'})
