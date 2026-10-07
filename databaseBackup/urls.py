from django.urls import re_path
from . import views

urlpatterns = [
    re_path(r'^$', views.loadDBBackupManager, name='loadDBBackupManager'),
    re_path(r'^fetchBackupStats$', views.fetchBackupStats, name='dbfetchBackupStats'),
    re_path(r'^fetchDatabaseBackups$', views.fetchDatabaseBackups, name='dbfetchDatabaseBackups'),
    re_path(r'^fetchSiteBackups$', views.fetchSiteBackups, name='dbfetchSiteBackups'),
    re_path(r'^downloadBackup$', views.downloadBackup, name='dbdownloadBackup'),
    re_path(r'^deleteBackup$', views.deleteBackup, name='dbdeleteBackup'),
    re_path(r'^uploadBackup$', views.uploadBackup, name='dbuploadBackup'),
    re_path(r'^runBackupJob$', views.runBackupJob, name='dbrunBackupJob'),
    re_path(r'^fetchJobStatus$', views.fetchJobStatus, name='dbfetchJobStatus'),
    re_path(r'^fetchCronConfig$', views.fetchCronConfig, name='dbfetchCronConfig'),
    re_path(r'^saveCronConfig$', views.saveCronConfig, name='dbsaveCronConfig'),
    re_path(r'^fetchRetention$', views.fetchRetention, name='dbfetchRetention'),
    re_path(r'^saveRetention$', views.saveRetention, name='dbsaveRetention'),
    re_path(r'^fetchBackupLogs$', views.fetchBackupLogs, name='dbfetchBackupLogs'),
]
