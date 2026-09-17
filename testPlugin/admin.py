# -*- coding: utf-8 -*-
from django.contrib import admin
from .models import TestPluginSettings, TestPluginLog


@admin.register(TestPluginSettings)
class TestPluginSettingsAdmin(admin.ModelAdmin):
    list_display = ['user', 'plugin_enabled', 'test_count', 'last_test_time']
    list_filter = ['plugin_enabled', 'last_test_time']
    search_fields = ['user__username', 'custom_message']
    readonly_fields = ['last_test_time']


@admin.register(TestPluginLog)
class TestPluginLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'action', 'message', 'user']
    list_filter = ['action', 'timestamp', 'user']
    search_fields = ['action', 'message', 'user__username']
    readonly_fields = ['timestamp']
    date_hierarchy = 'timestamp'
