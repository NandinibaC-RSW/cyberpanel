'use strict';

// DB Backup Manager — server database/site backup browser + cron scheduler.
// Data endpoints live under /databasebackup/ and are admin-only.

app.filter('encodeURIComponent', function () {
    return window.encodeURIComponent;
});

app.controller('dbBackupManagerControl', function ($scope, $http, $timeout) {

    $scope.statsLoaded = false;
    $scope.stats = {
        storagePresent: false,
        mirrorPresent: false,
        dbTotal: 0,
        siteTotal: 0,
        backupDays: 0,
        uniqueDatabases: 0,
        primary: null,
        mirror: null,
        mirrorCheck: null
    };

    $scope.dbBackups = [];
    $scope.siteBackups = [];
    $scope.dbDates = [];
    $scope.siteDates = [];
    $scope.dbLoading = true;
    $scope.siteLoading = true;

    $scope.dbTypeFilter = '';
    $scope.dbDateFilter = '';
    $scope.dbSearch = '';
    $scope.siteDateFilter = '';
    $scope.siteSearch = '';
    $scope.dbPage = 1;
    $scope.sitePage = 1;
    $scope.dbPageSize = 25;
    $scope.sitePageSize = 25;

    $scope.cronJobs = [];
    $scope.cronConfigured = false;
    $scope.cronLoading = true;
    $scope.retentionDays = 30;

    $scope.backupLogs = [];
    // object holder so ng-repeat child scopes don't shadow the primitive
    $scope.logState = {active: 'mariadb'};
    $scope.logsLoading = true;

    $scope.jobRunning = false;
    $scope.runningJob = '';
    $scope.uploadType = 'mariadb';
    $scope.uploading = false;
    $scope.uploadDrag = false;

    var csrfConfig = {
        headers: {'X-CSRFToken': getCookie('csrftoken')}
    };

    function notify(type, title, text) {
        new PNotify({title: title, text: text, type: type, delay: 4000});
    }

    function handleError(prefix) {
        return function (response) {
            var message = (response && response.data && response.data.error_message) ?
                response.data.error_message : 'Request failed';
            notify('error', 'Error', prefix + ': ' + message);
        };
    }

    // ------------------------------------------------------------------
    // Data loading
    // ------------------------------------------------------------------

    $scope.fetchStats = function () {
        $http.post('/databasebackup/fetchBackupStats', {}, csrfConfig).then(function (response) {
            if (response.data.status === 1) {
                $scope.stats = response.data;
            }
            $scope.statsLoaded = true;
        }, handleError('Could not load backup stats'));
    };

    $scope.fetchDatabaseBackups = function () {
        $scope.dbLoading = true;
        $http.post('/databasebackup/fetchDatabaseBackups', {}, csrfConfig).then(function (response) {
            if (response.data.status === 1) {
                $scope.dbBackups = response.data.backups;
                var seen = {};
                $scope.dbDates = [];
                $scope.dbBackups.forEach(function (b) {
                    if (!seen[b.dateFolder]) {
                        seen[b.dateFolder] = true;
                        $scope.dbDates.push(b.dateFolder);
                    }
                });
            }
            $scope.dbLoading = false;
        }, function (response) {
            handleError('Could not load database backups')(response);
            $scope.dbLoading = false;
        });
    };

    $scope.fetchSiteBackups = function () {
        $scope.siteLoading = true;
        $http.post('/databasebackup/fetchSiteBackups', {}, csrfConfig).then(function (response) {
            if (response.data.status === 1) {
                $scope.siteBackups = response.data.backups;
                var seen = {};
                $scope.siteDates = [];
                $scope.siteBackups.forEach(function (s) {
                    if (!seen[s.dateFolder]) {
                        seen[s.dateFolder] = true;
                        $scope.siteDates.push(s.dateFolder);
                    }
                });
            }
            $scope.siteLoading = false;
        }, function (response) {
            handleError('Could not load site backups')(response);
            $scope.siteLoading = false;
        });
    };

    $scope.fetchCron = function () {
        $scope.cronLoading = true;
        $http.post('/databasebackup/fetchCronConfig', {}, csrfConfig).then(function (response) {
            if (response.data.status === 1) {
                $scope.cronConfigured = response.data.configured;
                $scope.cronJobs = response.data.jobs;
                $scope.cronJobs.forEach(function (job) {
                    // input[type=time] binds to Date objects in AngularJS
                    job.time = new Date(2000, 0, 1,
                        parseInt(job.hour, 10) || 0,
                        parseInt(job.minute, 10) || 0);
                });
            }
            $scope.cronLoading = false;
        }, function (response) {
            handleError('Could not load the backup schedule')(response);
            $scope.cronLoading = false;
        });
    };

    $scope.fetchRetention = function () {
        $http.post('/databasebackup/fetchRetention', {}, csrfConfig).then(function (response) {
            if (response.data.status === 1 && response.data.configured) {
                $scope.retentionDays = response.data.days;
            }
        });
    };

    $scope.fetchLogs = function () {
        $scope.logsLoading = true;
        $http.post('/databasebackup/fetchBackupLogs', {lines: 100}, csrfConfig).then(function (response) {
            if (response.data.status === 1) {
                $scope.backupLogs = response.data.logs;
                    var stillThere = false;
                    $scope.backupLogs.forEach(function (log) {
                        if (log.job === $scope.logState.active) {
                            stillThere = true;
                        }
                    });
                    if (!stillThere && $scope.backupLogs.length > 0) {
                        $scope.logState.active = $scope.backupLogs[0].job;
                    }
            }
            $scope.logsLoading = false;
        }, function (response) {
            handleError('Could not load backup logs')(response);
            $scope.logsLoading = false;
        });
    };

    $scope.refreshAll = function () {
        $scope.fetchStats();
        $scope.fetchDatabaseBackups();
        $scope.fetchSiteBackups();
        $scope.fetchCron();
        $scope.fetchRetention();
        $scope.fetchLogs();
        pollJobStatus();
    };

    // ------------------------------------------------------------------
    // Storage display helpers
    // ------------------------------------------------------------------

    $scope.storageBarClass = function (usage) {
        if (!usage) {
            return '';
        }
        if (usage.percent >= 90) {
            return 'crit';
        }
        if (usage.percent >= 75) {
            return 'warn';
        }
        return '';
    };

    $scope.mirrorStateText = function () {
        var state = $scope.stats.mirrorCheck ? $scope.stats.mirrorCheck.state : 'unknown';
        if (state === 'synced') {
            return 'In sync';
        }
        if (state === 'drift') {
            return 'Drift detected';
        }
        return 'Unknown';
    };

    // ------------------------------------------------------------------
    // Table filtering / pagination
    // ------------------------------------------------------------------

    $scope.filteredDbBackups = function () {
        var type = $scope.dbTypeFilter;
        var date = $scope.dbDateFilter;
        var search = ($scope.dbSearch || '').toLowerCase();
        return $scope.dbBackups.filter(function (b) {
            if (type && b.type !== type) {
                return false;
            }
            if (date && b.dateFolder !== date) {
                return false;
            }
            if (search && b.database.toLowerCase().indexOf(search) === -1) {
                return false;
            }
            return true;
        });
    };

    $scope.filteredSiteBackups = function () {
        var date = $scope.siteDateFilter;
        var search = ($scope.siteSearch || '').toLowerCase();
        return $scope.siteBackups.filter(function (s) {
            if (date && s.dateFolder !== date) {
                return false;
            }
            if (search && s.name.toLowerCase().indexOf(search) === -1) {
                return false;
            }
            return true;
        });
    };

    $scope.resetDbPage = function () {
        $scope.dbPage = 1;
    };

    $scope.resetSitePage = function () {
        $scope.sitePage = 1;
    };

    $scope.dbTotalPages = function () {
        return Math.max(1, Math.ceil($scope.filteredDbBackups().length / $scope.dbPageSize));
    };

    $scope.siteTotalPages = function () {
        return Math.max(1, Math.ceil($scope.filteredSiteBackups().length / $scope.sitePageSize));
    };

    $scope.pagedDbBackups = function () {
        var list = $scope.filteredDbBackups();
        if ($scope.dbPage > $scope.dbTotalPages()) {
            $scope.dbPage = $scope.dbTotalPages();
        }
        var start = ($scope.dbPage - 1) * $scope.dbPageSize;
        return list.slice(start, start + $scope.dbPageSize);
    };

    $scope.pagedSiteBackups = function () {
        var list = $scope.filteredSiteBackups();
        if ($scope.sitePage > $scope.siteTotalPages()) {
            $scope.sitePage = $scope.siteTotalPages();
        }
        var start = ($scope.sitePage - 1) * $scope.sitePageSize;
        return list.slice(start, start + $scope.sitePageSize);
    };

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------

    $scope.deleteBackup = function (item, kind) {
        if (!window.confirm('Delete this backup permanently from BOTH storages?\n\n' + item.file)) {
            return;
        }
        $http.post('/databasebackup/deleteBackup', {file: item.path}, csrfConfig)
            .then(function (response) {
                if (response.data.status === 1) {
                    notify('success', 'Deleted', item.file + ' was removed.');
                    $scope.fetchStats();
                    if (kind === 'db') {
                        $scope.fetchDatabaseBackups();
                    } else {
                        $scope.fetchSiteBackups();
                    }
                } else {
                    notify('error', 'Error', response.data.error_message);
                }
            }, handleError('Delete failed'));
    };

    $scope.saveSchedule = function () {
        $scope.cronLoading = true;
        var jobs = $scope.cronJobs.map(function (job) {
            var hour, minute;
            if (job.time instanceof Date) {
                hour = job.time.getHours();
                minute = job.time.getMinutes();
            } else {
                var parts = String(job.time || '00:00').split(':');
                hour = parseInt(parts[0], 10) || 0;
                minute = parseInt(parts[1], 10) || 0;
            }
            return {
                key: job.key,
                minute: minute,
                hour: hour,
                enabled: job.enabled
            };
        });
        $http.post('/databasebackup/saveCronConfig', {jobs: jobs}, csrfConfig)
            .then(function (response) {
                if (response.data.status !== 1) {
                    $scope.cronLoading = false;
                    notify('error', 'Error', response.data.error_message);
                    return;
                }
                $http.post('/databasebackup/saveRetention', {days: $scope.retentionDays}, csrfConfig)
                    .then(function (response) {
                        $scope.cronLoading = false;
                        if (response.data.status === 1) {
                            notify('success', 'Saved', 'Backup schedule and retention updated.');
                            $scope.fetchCron();
                        } else {
                            notify('error', 'Error', response.data.error_message);
                        }
                    }, function (response) {
                        $scope.cronLoading = false;
                        handleError('Could not save retention')(response);
                    });
            }, function (response) {
                $scope.cronLoading = false;
                handleError('Could not save the schedule')(response);
            });
    };

    var statusTimer = null;

    function pollJobStatus() {
        $http.post('/databasebackup/fetchJobStatus', {}, csrfConfig).then(function (response) {
            if (response.data.status === 1) {
                var wasRunning = $scope.jobRunning;
                $scope.jobRunning = response.data.running;
                $scope.runningJob = response.data.job;
                if (wasRunning && !response.data.running) {
                    notify('success', 'Backup job finished',
                        'The ' + response.data.job + ' job completed. Refreshing data.');
                    $scope.fetchStats();
                    $scope.fetchDatabaseBackups();
                    $scope.fetchSiteBackups();
                    $scope.fetchLogs();
                }
            }
            if ($scope.jobRunning) {
                statusTimer = $timeout(pollJobStatus, 5000);
            }
        });
    }

    $scope.runJob = function (jobKey) {
        if ($scope.jobRunning) {
            notify('warning', 'Busy', 'Another backup job is already running.');
            return;
        }
        if (!window.confirm('Run the ' + jobKey + ' backup job now? It will append to the regular log.')) {
            return;
        }
        $http.post('/databasebackup/runBackupJob', {job: jobKey}, csrfConfig)
            .then(function (response) {
                if (response.data.status === 1) {
                    notify('success', 'Started', 'The ' + jobKey + ' job is running. It may take a while for large databases.');
                    $scope.jobRunning = true;
                    $scope.runningJob = jobKey;
                    statusTimer = $timeout(pollJobStatus, 3000);
                } else {
                    notify('error', 'Error', response.data.error_message);
                }
            }, handleError('Could not start the job'));
    };

    // ------------------------------------------------------------------
    // Upload
    // ------------------------------------------------------------------

    $scope.uploadBackup = function () {
        var input = document.getElementById('dbBackupUploadFile');
        if (!input || !input.files || input.files.length === 0) {
            notify('warning', 'No file', 'Choose a backup file first.');
            return;
        }
        var formData = new FormData();
        formData.append('file', input.files[0]);
        formData.append('type', $scope.uploadType);

        $scope.uploading = true;
        $http.post('/databasebackup/uploadBackup', formData, {
            headers: {'X-CSRFToken': getCookie('csrftoken'), 'Content-Type': undefined},
            transformRequest: angular.identity
        }).then(function (response) {
            $scope.uploading = false;
            if (response.data.status === 1) {
                notify('success', 'Uploaded', 'Stored on the backup SSD and mirrored to the primary SSD.');
                input.value = '';
                $scope.fetchStats();
                if ($scope.uploadType === 'sites') {
                    $scope.fetchSiteBackups();
                } else {
                    $scope.fetchDatabaseBackups();
                }
            } else {
                notify('error', 'Upload failed', response.data.error_message);
            }
        }, function (response) {
            $scope.uploading = false;
            handleError('Upload failed')(response);
        });
    };

    $scope.handleDrop = function (event) {
        event.preventDefault();
        $scope.uploadDrag = false;
        if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length > 0) {
            var input = document.getElementById('dbBackupUploadFile');
            input.files = event.dataTransfer.files;
        }
    };

    // ------------------------------------------------------------------

    $scope.refreshAll();

    $scope.$on('$destroy', function () {
        if (statusTimer) {
            $timeout.cancel(statusTimer);
        }
    });
});
