# -*- coding: utf-8 -*-
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import TestPluginSettings


@receiver(post_save, sender=User)
def create_user_settings(sender, instance, created, **kwargs):
    """Create default plugin settings when a new user is created"""
    if created:
        TestPluginSettings.objects.create(
            user=instance,
            plugin_enabled=True
        )
