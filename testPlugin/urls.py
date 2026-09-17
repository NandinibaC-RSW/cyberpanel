# -*- coding: utf-8 -*-
from django.urls import path
from . import views

app_name = 'testPlugin'

urlpatterns = [
    path('', views.plugin_home, name='plugin_home'),
    path('test/', views.test_button, name='test_button'),
    path('toggle/', views.toggle_plugin, name='toggle_plugin'),
    path('settings/', views.plugin_settings, name='plugin_settings'),
    path('update-settings/', views.update_settings, name='update_settings'),
    path('install/', views.install_plugin, name='install_plugin'),
    path('uninstall/', views.uninstall_plugin, name='uninstall_plugin'),
    path('logs/', views.plugin_logs, name='plugin_logs'),
    path('docs/', views.plugin_docs, name='plugin_docs'),
    path('security/', views.security_info, name='security_info'),
]
