from django.db import models
from django.utils import timezone


class ScanStatusUpdate(models.Model):
    """Real-time scan progress updates"""
    scan_id = models.CharField(max_length=100, db_index=True, primary_key=True)
    phase = models.CharField(max_length=50)  # starting, discovering_files, scanning_files, completing, completed
    progress = models.IntegerField(default=0)  # 0-100

    # File tracking
    current_file = models.TextField(blank=True)
    files_discovered = models.IntegerField(default=0)
    files_scanned = models.IntegerField(default=0)
    files_remaining = models.IntegerField(default=0)

    # Threat tracking
    threats_found = models.IntegerField(default=0)
    critical_threats = models.IntegerField(default=0)
    high_threats = models.IntegerField(default=0)

    # Activity description
    activity_description = models.TextField(blank=True)

    # Timestamps
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_scanner_status_updates'
        ordering = ['-last_updated']
        indexes = [
            models.Index(fields=['scan_id', '-last_updated']),
        ]

    def __str__(self):
        return f"Status update for {self.scan_id} - {self.phase} ({self.progress}%)"

    @property
    def is_active(self):
        """Check if scan is still active"""
        return self.phase not in ['completed', 'failed', 'cancelled']