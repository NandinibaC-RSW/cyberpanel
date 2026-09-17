# -*- coding: utf-8 -*-
from django.apps import AppConfig


class TestPluginConfig(AppConfig):
    name = 'testPlugin'
    verbose_name = 'Test Plugin'
    
    def ready(self):
        # Import signal handlers
        import testPlugin.signals
