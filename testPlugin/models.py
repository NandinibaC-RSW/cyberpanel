# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.auth.models import User


class TestPluginSettings(models.Model):
    """Model to store plugin settings and enable/disable state"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    plugin_enabled = models.BooleanField(default=True, help_text="Enable or disable the plugin")
    test_count = models.IntegerField(default=0, help_text="Number of times test button was clicked")
    last_test_time = models.DateTimeField(auto_now=True, help_text="Last time test button was clicked")
    custom_message = models.TextField(default="Test plugin is working!", help_text="Custom message for popup")
    
    class Meta:
        verbose_name = "Test Plugin Settings"
        verbose_name_plural = "Test Plugin Settings"
    
    def __str__(self):
        return f"Test Plugin Settings - Enabled: {self.plugin_enabled}"


class TestPluginLog(models.Model):
    """Model to store plugin activity logs"""
    timestamp = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=100)
    message = models.TextField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    
    class Meta:
        verbose_name = "Test Plugin Log"
        verbose_name_plural = "Test Plugin Logs"
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.timestamp} - {self.action}: {self.message}"
