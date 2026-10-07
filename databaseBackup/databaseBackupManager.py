# -*- coding: utf-8 -*-

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime

from django.http import HttpResponse, FileResponse
from django.views.decorators.csrf import csrf_exempt

from plogical.httpProc import httpProc
from plogical.acl import ACLManager
from plogical.CyberCPLogFileWriter import CyberCPLogFileWriter as logging


class DBBackupManager:
    """
    Manages the server-level database/site backups produced by the
    /etc/cron.d/database-backups root cron jobs.

    Primary store:   BACKUP_BASE (/mnt/backup, 2nd SSD)
    Mirror store:    MIRROR_BASE (/backup-mirror, primary SSD)
    """

    BACKUP_BASE = '/mnt/backup'
    MIRROR_BASE = '/backup-mirror'
    CRON_FILE = '/etc/cron.d/database-backups'
    LOG_DIR = '/var/log/backup'
    RETENTION_SCRIPT = '/usr/local/bin/cleanup-old-backups.sh'
    JOB_STATUS_FILE = '/tmp/cyberpanel-dbbackup-status.json'
    JOB_DONE_FILE = '/tmp/cyberpanel-dbbackup-done.json'

    # only these folders on the backup disk are managed (and mirrored);
    # legacy/unrelated content on the disk must not count as drift
    MANAGED_FOLDERS = ('mariadb', 'postgresql', 'sites')

    # cron job key -> (script path, log file)
    JOB_SCRIPTS = {
        'mariadb': '/usr/local/bin/backup-mariadb-per-db.sh',
        'postgresql': '/usr/local/bin/backup-postgresql.sh',
        'uploads': '/usr/local/bin/backup-uploads.sh',
        'cleanup': '/usr/local/bin/cleanup-old-backups.sh',
    }
    JOB_LOGS = {
        'mariadb': 'mariadb-per-db.log',
        'postgresql': 'postgresql.log',
        'uploads': 'uploads.log',
        'cleanup': 'cleanup.log',
    }
    JOB_LABELS = {
        'mariadb': 'MariaDB per-database dump',
        'postgresql': 'PostgreSQL + Docker databases',
        'uploads': 'Site uploads / content',
        'cleanup': 'Retention cleanup',
    }

    DB_FILE_RE = re.compile(r'^(.+)_(\d{4}-\d{2}-\d{2})_\d{6}\.sql\.gz$')
    DATE_DIR_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

    ###########################################################
    # Helpers
    ###########################################################

    @staticmethod
    def _json(data):
        return HttpResponse(json.dumps(data))

    @staticmethod
    def _deny(message):
        return DBBackupManager._json({'status': 0, 'error_message': str(message)})

    @staticmethod
    def _isAdmin(userID):
        try:
            currentACL = ACLManager.loadedACL(userID)
            return currentACL.get('admin', 0) == 1
        except BaseException:
            return False

    @staticmethod
    def fmtSize(bytes_size):
        if bytes_size >= 1073741824:
            return '%.2f GB' % round(bytes_size / 1073741824.0, 2)
        if bytes_size >= 1048576:
            return '%.2f MB' % round(bytes_size / 1048576.0, 2)
        if bytes_size >= 1024:
            return '%.2f KB' % round(bytes_size / 1024.0, 2)
        return '%s B' % bytes_size

    ###########################################################
    # Privileged operations
    #
    # The panel process may not run as root (staging gunicorn etc.),
    # but the targets it manages — /usr/local/bin scripts, /etc/cron.d,
    # /mnt/backup contents — are root-owned. Every privileged operation
    # therefore tries a direct write first and falls back to
    # passwordless `sudo -n` when the direct attempt is denied.
    ###########################################################

    @classmethod
    def _is_root(cls):
        return hasattr(os, 'geteuid') and os.geteuid() == 0

    @classmethod
    def _run_elevated(cls, command):
        """Run a shell command, retrying under `sudo -n` when the direct
        run fails (or immediately under sudo for non-root processes).
        Returns (ok, error_message)."""
        attempts = []
        if cls._is_root():
            attempts.append(command)
        else:
            attempts.append(command)
            attempts.append('sudo -n ' + command)
        last_error = 'permission denied'
        for cmd in attempts:
            try:
                proc = subprocess.run(['/bin/bash', '-c', cmd],
                                      capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired:
                last_error = 'command timed out: %s' % cmd
                continue
            except OSError as msg:
                last_error = str(msg)
                continue
            if proc.returncode == 0:
                return True, None
            error = (proc.stderr or proc.stdout or
                     'exit code %s' % proc.returncode).strip()
            last_error = error if last_error == 'permission denied' else \
                '%s; sudo fallback: %s' % (last_error, error)
        return False, last_error

    @classmethod
    def _privileged_write(cls, path, content, mode=0o644):
        """Write content to path; fall back to an elevated install of a
        temp copy when the direct write is denied. Returns (ok, error)."""
        direct_error = None
        try:
            with open(path, 'w') as target:
                target.write(content)
            try:
                os.chmod(path, mode)
            except OSError:
                pass
            return True, None
        except OSError as msg:
            direct_error = str(msg)

        fd, tmp_path = tempfile.mkstemp(prefix='cyberpanel-dbbackup-')
        try:
            with os.fdopen(fd, 'w') as tmp_file:
                tmp_file.write(content)
            os.chmod(tmp_path, mode)
            ok, error = cls._run_elevated('/usr/bin/install -m %o %s %s' % (
                mode, shlex.quote(tmp_path), shlex.quote(path)))
            if ok:
                return True, None
            return False, '%s (sudo fallback: %s)' % (direct_error, error)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @classmethod
    def _unlink(cls, path):
        """Remove a file, falling back to elevated rm when denied."""
        try:
            os.unlink(path)
            return True, None
        except FileNotFoundError:
            return True, None
        except OSError as direct_error:
            ok, error = cls._run_elevated('/bin/rm -f -- %s' % shlex.quote(path))
            if ok:
                return True, None
            return False, '%s; %s' % (direct_error, error)

    @classmethod
    def _validateBackupPath(cls, candidate):
        """Resolve candidate and make sure it is a file inside BACKUP_BASE."""
        try:
            real = os.path.realpath(candidate)
            base = os.path.realpath(cls.BACKUP_BASE)
        except BaseException:
            return None
        if not real.startswith(base + os.sep):
            return None
        if not os.path.isfile(real):
            return None
        return real

    @staticmethod
    def _mirrorTwin(primary_path):
        """Path of the same file on the mirror store."""
        base = os.path.realpath(DBBackupManager.BACKUP_BASE)
        mirror_base = DBBackupManager.MIRROR_BASE
        real = os.path.realpath(primary_path)
        if not real.startswith(base + os.sep):
            return None
        return os.path.join(mirror_base, real[len(base) + 1:])

    @classmethod
    def _scanType(cls, backup_base, folder):
        """Scan one backup type folder ({folder}/YYYY-MM-DD/*)."""
        results = []
        type_path = os.path.join(backup_base, folder)
        if not os.path.isdir(type_path):
            return results
        try:
            date_folders = os.listdir(type_path)
        except OSError:
            return results
        for date_entry in date_folders:
            if not cls.DATE_DIR_RE.match(date_entry):
                continue
            day_path = os.path.join(type_path, date_entry)
            if not os.path.isdir(day_path):
                continue
            try:
                files = os.listdir(day_path)
            except OSError:
                continue
            for file_name in files:
                file_path = os.path.join(day_path, file_name)
                if not os.path.isfile(file_path):
                    continue
                try:
                    stat = os.stat(file_path)
                except OSError:
                    continue
                results.append({
                    'file': file_name,
                    'path': file_path,
                    'dateFolder': date_entry,
                    'size': stat.st_size,
                    'sizeText': cls.fmtSize(stat.st_size),
                    'modified': int(stat.st_mtime),
                })
        results.sort(key=lambda item: (item['dateFolder'], item['file']), reverse=True)
        return results

    ###########################################################
    # Page
    ###########################################################

    def loadDBBackupManager(self, request, userID):
        proc = httpProc(request, 'databaseBackup/dbBackupManager.html', None, 'admin')
        return proc.render()

    ###########################################################
    # Scanning / stats
    ###########################################################

    def fetchDatabaseBackups(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can view database backups.')
        backups = []
        for folder, db_type in (('mariadb', 'MariaDB'), ('postgresql', 'PostgreSQL')):
            for item in self._scanType(self.BACKUP_BASE, folder):
                match = self.DB_FILE_RE.match(item['file'])
                if match:
                    db_name = match.group(1)
                else:
                    db_name = os.path.splitext(os.path.splitext(item['file'])[0])[0]
                item['type'] = folder
                item['typeLabel'] = db_type
                item['database'] = db_name
                try:
                    modified = datetime.fromtimestamp(item['modified'])
                    item['time'] = modified.strftime('%H:%M:%S')
                except BaseException:
                    item['time'] = ''
                backups.append(item)
        backups.sort(key=lambda b: (b['dateFolder'], b['database']), reverse=True)
        return self._json({'status': 1, 'backups': backups})

    def fetchSiteBackups(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can view site backups.')
        items = self._scanType(self.BACKUP_BASE, 'sites')
        for item in items:
            name = re.sub(r'\.(tar\.gz|sql\.gz)$', '', item['file'])
            item['name'] = name
            item['kind'] = 'DB' if '-db' in name else 'Content'
            try:
                modified = datetime.fromtimestamp(item['modified'])
                item['time'] = modified.strftime('%H:%M:%S')
            except BaseException:
                item['time'] = ''
        return self._json({'status': 1, 'backups': items})

    def fetchBackupStats(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can view backup stats.')

        storage_present = os.path.isdir(self.BACKUP_BASE)
        db_backups = []
        for folder in ('mariadb', 'postgresql'):
            db_backups.extend(self._scanType(self.BACKUP_BASE, folder))
        site_backups = self._scanType(self.BACKUP_BASE, 'sites')

        dates = sorted({b['dateFolder'] for b in db_backups}, reverse=True)
        databases = {self.DB_FILE_RE.match(b['file']).group(1)
                     for b in db_backups if self.DB_FILE_RE.match(b['file'])}
        db_bytes = sum(b['size'] for b in db_backups)
        site_bytes = sum(b['size'] for b in site_backups)

        primary_usage = self._diskUsage(self.BACKUP_BASE)
        mirror_usage = self._diskUsage(self.MIRROR_BASE)
        mirror = self._mirrorCheck()

        return self._json({
            'status': 1,
            'storagePresent': storage_present,
            'mirrorPresent': os.path.isdir(self.MIRROR_BASE),
            'dbTotal': len(db_backups),
            'dbBytes': db_bytes,
            'dbBytesText': self.fmtSize(db_bytes),
            'siteTotal': len(site_backups),
            'siteBytes': site_bytes,
            'siteBytesText': self.fmtSize(site_bytes),
            'backupDays': len(dates),
            'uniqueDatabases': len(databases),
            'oldestDate': dates[-1] if dates else '',
            'newestDate': dates[0] if dates else '',
            'primary': primary_usage,
            'mirror': mirror_usage,
            'mirrorCheck': mirror,
        })

    @classmethod
    def _diskUsage(cls, path):
        try:
            usage = shutil.disk_usage(path)
            return {
                'total': usage.total,
                'used': usage.used,
                'free': usage.free,
                'percent': round(usage.used * 100.0 / usage.total, 1) if usage.total else 0,
                'totalText': cls.fmtSize(usage.total),
                'usedText': cls.fmtSize(usage.used),
                'freeText': cls.fmtSize(usage.free),
            }
        except BaseException:
            return None

    @classmethod
    def _mirrorCheck(cls):
        """Compare file sets between primary store and mirror store,
        scoped to the managed backup folders only."""
        primary_present = os.path.isdir(cls.BACKUP_BASE)
        mirror_present = os.path.isdir(cls.MIRROR_BASE)
        if not primary_present or not mirror_present:
            return {'state': 'unknown', 'missingOnMirror': [],
                    'missingOnPrimary': [], 'missingOnMirrorCount': 0,
                    'missingOnPrimaryCount': 0}

        def walk(base):
            found = {}
            for folder in cls.MANAGED_FOLDERS:
                folder_path = os.path.join(base, folder)
                if not os.path.isdir(folder_path):
                    continue
                for root, dirs, files in os.walk(folder_path):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for file_name in files:
                        full = os.path.join(root, file_name)
                        rel = os.path.relpath(full, base)
                        try:
                            found[rel] = os.path.getsize(full)
                        except OSError:
                            pass
            return found

        primary = walk(cls.BACKUP_BASE)
        mirrored = walk(cls.MIRROR_BASE)
        missing_on_mirror = sorted(set(primary) - set(mirrored))
        missing_on_primary = sorted(set(mirrored) - set(primary))
        if not missing_on_mirror and not missing_on_primary:
            state = 'synced'
        else:
            state = 'drift'
        return {
            'state': state,
            'primaryFiles': len(primary),
            'mirrorFiles': len(mirrored),
            'missingOnMirror': missing_on_mirror[:10],
            'missingOnPrimary': missing_on_primary[:10],
            'missingOnMirrorCount': len(missing_on_mirror),
            'missingOnPrimaryCount': len(missing_on_primary),
        }

    ###########################################################
    # Download / delete
    ###########################################################

    def downloadBackup(self, request, userID):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can download backups.')
        candidate = request.GET.get('file', '')
        real = self._validateBackupPath(candidate)
        if real is None:
            return self._deny('Invalid file path.')
        response = FileResponse(open(real, 'rb'), as_attachment=True,
                                filename=os.path.basename(real))
        response['Content-Length'] = os.path.getsize(real)
        response['Cache-Control'] = 'no-cache'
        return response

    def deleteBackup(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can delete backups.')
        real = self._validateBackupPath(data.get('file', ''))
        if real is None:
            return self._deny('Invalid file path.')
        ok, error = self._unlink(real)
        if not ok:
            logging.writeToFile('DBBackupManager delete failed: %s' % error)
            return self._deny('Could not delete the file on the backup disk: %s' % error)
        # keep both storages consistent
        removed_mirror = False
        twin = self._mirrorTwin(real)
        if twin and os.path.isfile(twin):
            ok, error = self._unlink(twin)
            if ok:
                removed_mirror = True
            else:
                logging.writeToFile('DBBackupManager mirror delete failed: %s' % error)
        return self._json({'status': 1, 'mirrorRemoved': removed_mirror})

    ###########################################################
    # Upload
    ###########################################################

    def uploadBackup(self, request, userID):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can upload backups.')
        upload = request.FILES.get('file')
        target = request.POST.get('type', '')
        if upload is None:
            return self._deny('No file received.')
        if target not in ('mariadb', 'postgresql', 'sites'):
            return self._deny('Invalid backup type.')

        name = os.path.basename(upload.name or 'upload')
        name = re.sub(r'[^A-Za-z0-9._\-]', '_', name)
        lower = name.lower()
        if target in ('mariadb', 'postgresql') and not lower.endswith('.sql.gz'):
            return self._deny('Database backups must be a .sql.gz file.')
        if target == 'sites' and not (lower.endswith('.tar.gz') or lower.endswith('.sql.gz')):
            return self._deny('Site backups must be a .tar.gz or .sql.gz file.')

        if target in ('mariadb', 'postgresql') and not self.DB_FILE_RE.match(name):
            # keep the on-disk naming convention so the file shows up in listings
            stem = name[:-len('.sql.gz')] if lower.endswith('.sql.gz') else name
            stem = stem[:150]
            name = '%s_%s.sql.gz' % (stem, datetime.now().strftime('%Y-%m-%d_%H%M%S'))

        day_dir = datetime.now().strftime('%Y-%m-%d')
        primary_dir = os.path.join(self.BACKUP_BASE, target, day_dir)
        mirror_dir = os.path.join(self.MIRROR_BASE, target, day_dir)
        primary_path = os.path.join(primary_dir, name)
        mirror_path = os.path.join(mirror_dir, name)

        fd, staging_path = tempfile.mkstemp(prefix='cyberpanel-dbupload-')
        try:
            with os.fdopen(fd, 'wb') as staging:
                for chunk in upload.chunks():
                    staging.write(chunk)
            os.chmod(staging_path, 0o640)

            # fast path: the panel process owns the backup folders
            try:
                os.makedirs(primary_dir, exist_ok=True)
                os.makedirs(mirror_dir, exist_ok=True)
                shutil.copyfile(staging_path, primary_path)
                shutil.copyfile(staging_path, mirror_path)
            except OSError:
                # fallback: stage in /tmp and install with elevated copy
                ok, error = self._run_elevated('/usr/bin/mkdir -p -- %s %s' % (
                    shlex.quote(primary_dir), shlex.quote(mirror_dir)))
                if not ok:
                    return self._deny('Cannot create backup folders: %s' % error)
                for dest in (primary_path, mirror_path):
                    ok, error = self._run_elevated('/usr/bin/install -m 644 -- %s %s' % (
                        shlex.quote(staging_path), shlex.quote(dest)))
                    if not ok:
                        return self._deny('Could not store %s: %s' % (
                            os.path.basename(dest), error))
        finally:
            try:
                os.unlink(staging_path)
            except OSError:
                pass

        return self._json({'status': 1, 'path': primary_path,
                           'mirrorPath': mirror_path})

    ###########################################################
    # Run now / job status
    ###########################################################

    def runBackupJob(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can run backup jobs.')
        job = data.get('job', '')
        if job not in self.JOB_SCRIPTS:
            return self._deny('Unknown backup job.')
        script = self.JOB_SCRIPTS[job]
        if not os.path.isfile(script):
            return self._deny('Backup script not found: %s' % script)

        if self._jobRunning():
            return self._deny('Another backup job is still running. Wait for it to finish.')

        log_path = os.path.join(self.LOG_DIR, self.JOB_LOGS[job])
        try:
            os.makedirs(self.LOG_DIR, exist_ok=True)
        except OSError:
            pass  # the elevated run creates it
        try:
            if self._is_root():
                shell = (
                    '%s >> %s 2>&1; rc=$?; printf \'{"job": "%%s", "exit_code": %%s, '
                    '"finished": "%%s"}\' "%s" "$rc" "$(date +%%s)" > %s'
                    % (script, log_path, job, self.JOB_DONE_FILE)
                )
                subprocess.Popen(['/bin/bash', '-c', shell], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
            else:
                # non-root panel: the script and its log both live in
                # root-owned paths, so run the whole wrapper under sudo
                inner = '%s >> %s 2>&1; rc=$?; printf \'{"job": "%%s", "exit_code": %%s, "finished": "%%s"}\' %s "$rc" "$(date +%%s)" > %s' % (
                    script, log_path, shlex.quote(job), self.JOB_DONE_FILE)
                elevated = 'sudo -n /bin/bash -c %s' % shlex.quote(inner)
                # verify elevation is available before spawning the job
                ok, error = self._run_elevated('true')
                if not ok:
                    return self._deny(
                        'The panel process lacks permission to run backup jobs. '
                        'Give it root or passwordless sudo (failed: %s)' % error)
                subprocess.Popen(['/bin/bash', '-c', elevated], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
        except BaseException as msg:
            return self._deny('Failed to start job: %s' % str(msg))

        status = {'job': job, 'pid': None, 'started': int(datetime.now().timestamp())}
        try:
            with open(self.JOB_STATUS_FILE, 'w') as status_file:
                status_file.write(json.dumps(status))
        except OSError:
            pass
        return self._json({'status': 1, 'job': job, 'started': status['started']})

    def _jobRunning(self):
        """True while the wrapper started by runBackupJob has not written its
        done marker yet (or its timestamp is not newer than the last start)."""
        try:
            with open(self.JOB_STATUS_FILE) as status_file:
                saved = json.loads(status_file.read())
        except (IOError, ValueError):
            return False
        pid = saved.get('pid')
        if pid:
            try:
                os.kill(int(pid), 0)
                return True
            except OSError:
                return False
        try:
            with open(self.JOB_DONE_FILE) as done_file:
                done_data = json.loads(done_file.read())
            finished = int(done_data.get('finished') or 0)
        except (IOError, ValueError, TypeError):
            return True
        return finished <= saved.get('started', 0)

    def fetchJobStatus(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can query job status.')
        running = False
        job = ''
        started = 0
        done = None
        try:
            with open(self.JOB_STATUS_FILE) as status_file:
                saved = json.loads(status_file.read())
                job = saved.get('job', '')
                started = saved.get('started', 0)
                running = self._jobRunning()
        except (IOError, ValueError):
            pass
        try:
            with open(self.JOB_DONE_FILE) as done_file:
                done = json.loads(done_file.read())
        except (IOError, ValueError):
            done = None
        return self._json({'status': 1, 'running': running, 'job': job,
                           'started': started, 'done': done})

    ###########################################################
    # Cron schedule management
    ###########################################################

    def fetchCronConfig(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can view the backup schedule.')
        jobs = self._readCronConfig()
        if jobs is None:
            return self._json({'status': 1, 'configured': False,
                               'jobs': self._defaultCronJobs()})
        return self._json({'status': 1, 'configured': True, 'jobs': jobs})

    @classmethod
    def _defaultCronJobs(cls):
        """Sensible schedule shown (and savable) when no cron file exists."""
        defaults = {'mariadb': ('0', '2'), 'postgresql': ('0', '3'),
                    'cleanup': ('0', '4'), 'uploads': ('0', '5')}
        jobs = []
        for key, script in cls.JOB_SCRIPTS.items():
            minute, hour = defaults[key]
            jobs.append({'key': key, 'script': script, 'label': cls.JOB_LABELS[key],
                         'minute': minute, 'hour': hour, 'enabled': True,
                         'found': False})
        return jobs

    @classmethod
    def _readCronConfig(cls):
        try:
            with open(cls.CRON_FILE) as cron_file:
                lines = cron_file.read().splitlines()
        except IOError:
            return None
        jobs = []
        for key, script in cls.JOB_SCRIPTS.items():
            entry = {'key': key, 'script': script, 'label': cls.JOB_LABELS[key],
                     'minute': '0', 'hour': '0', 'enabled': False, 'found': False}
            for line in lines:
                stripped = line.strip()
                disabled = stripped.startswith('#')
                body = stripped.lstrip('#').strip() if disabled else stripped
                fields = body.split()
                if len(fields) >= 7 and fields[-1] == script:
                    entry['minute'] = fields[0]
                    entry['hour'] = fields[1]
                    entry['enabled'] = not disabled
                    entry['found'] = True
                    break
            jobs.append(entry)
        return jobs

    def saveCronConfig(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can change the backup schedule.')
        requested = data.get('jobs', [])
        if not isinstance(requested, list):
            return self._deny('Invalid schedule payload.')

        try:
            with open(self.CRON_FILE) as cron_file:
                lines = cron_file.read().splitlines()
        except IOError:
            lines = [
                '# Database backup schedule — managed by CyberPanel DB Backup Manager',
                '# Edited: %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ]

        updates = {}
        for entry in requested:
            key = entry.get('key', '')
            if key not in self.JOB_SCRIPTS:
                continue
            try:
                minute = int(str(entry.get('minute', 0)))
                hour = int(str(entry.get('hour', 0)))
            except ValueError:
                return self._deny('Invalid time for job %s.' % key)
            if not (0 <= minute <= 59 and 0 <= hour <= 23):
                return self._deny('Time for job %s must be a valid 24h clock time.' % key)
            updates[key] = (minute, hour, bool(entry.get('enabled', False)))

        changed = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            body = stripped.lstrip('#').strip() if stripped.startswith('#') else stripped
            fields = body.split()
            matched = None
            for key, script in self.JOB_SCRIPTS.items():
                if len(fields) >= 7 and fields[-1] == script:
                    matched = key
                    break
            if matched and matched in updates:
                minute, hour, enabled = updates.pop(matched)
                new_line = '%s %s * * * root %s' % (minute, hour, self.JOB_SCRIPTS[matched])
                if not enabled:
                    new_line = '# ' + new_line
                if new_line != line:
                    changed = True
                new_lines.append(new_line)
            else:
                new_lines.append(line)

        # jobs present in payload but missing from the cron file get appended
        for key, (minute, hour, enabled) in updates.items():
            new_line = '%s %s * * * root %s' % (minute, hour, self.JOB_SCRIPTS[key])
            if not enabled:
                new_line = '# ' + new_line
            new_lines.append(new_line)
            changed = True

        if not changed:
            return self._json({'status': 1, 'changed': False})

        ok, error = self._privileged_write(
            self.CRON_FILE, '\n'.join(new_lines) + '\n', 0o644)
        if not ok:
            logging.writeToFile('DBBackupManager cron save failed: %s' % error)
            return self._deny('Could not write the cron file: %s' % error)

        ok, error = self._run_elevated('/usr/bin/systemctl restart cron')
        if not ok:
            logging.writeToFile('DBBackupManager cron restart failed: %s' % error)

        return self._json({'status': 1, 'changed': True, 'jobs': self._readCronConfig()})

    ###########################################################
    # Retention
    ###########################################################

    def fetchRetention(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can view retention settings.')
        days = self._readRetention()
        if days is None:
            return self._json({'status': 1, 'configured': False, 'days': 0})
        return self._json({'status': 1, 'configured': True, 'days': days})

    @classmethod
    def _readRetention(cls):
        try:
            with open(cls.RETENTION_SCRIPT) as script_file:
                content = script_file.read()
        except IOError:
            return None
        match = re.search(r'^\s*RETENTION_DAYS\s*=\s*(\d+)', content, re.MULTILINE)
        return int(match.group(1)) if match else None

    def saveRetention(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can change retention settings.')
        try:
            days = int(data.get('days', 0))
        except (TypeError, ValueError):
            return self._deny('Retention days must be a number.')
        if days < 1 or days > 3650:
            return self._deny('Retention days must be between 1 and 3650.')
        try:
            with open(self.RETENTION_SCRIPT) as script_file:
                content = script_file.read()
        except IOError as msg:
            return self._deny('Cannot read the cleanup script: %s' % str(msg))
        new_content, count = re.subn(
            r'^(\s*RETENTION_DAYS\s*=\s*)\d+',
            r'\g<1>%d' % days,
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if count == 0:
            return self._deny('RETENTION_DAYS not found in the cleanup script.')
        ok, error = self._privileged_write(self.RETENTION_SCRIPT, new_content, 0o755)
        if not ok:
            logging.writeToFile('DBBackupManager retention save failed: %s' % error)
            return self._deny('Could not update the cleanup script: %s. '
                              'The panel process needs root or passwordless sudo '
                              'to manage backup scripts.' % error)
        return self._json({'status': 1, 'days': days})

    ###########################################################
    # Logs
    ###########################################################

    def fetchBackupLogs(self, userID, data):
        if not self._isAdmin(userID):
            return self._deny('Only administrators can view backup logs.')
        try:
            lines_requested = int(data.get('lines', 50))
        except (TypeError, ValueError):
            lines_requested = 50
        lines_requested = max(10, min(lines_requested, 500))

        logs = []
        for key, log_name in self.JOB_LOGS.items():
            path = os.path.join(self.LOG_DIR, log_name)
            content = ''
            modified = 0
            if os.path.isfile(path):
                try:
                    content = self._tail(path, lines_requested)
                    modified = int(os.path.getmtime(path))
                except OSError:
                    content = ''
            logs.append({'job': key, 'label': self.JOB_LABELS[key],
                         'file': path, 'modified': modified, 'content': content})
        return self._json({'status': 1, 'logs': logs})

    @staticmethod
    def _tail(path, lines_requested):
        with open(path, 'rb') as log_file:
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            block = 8192
            data = b''
            while size > 0 and data.count(b'\n') <= lines_requested:
                read_size = min(block, size)
                size -= read_size
                log_file.seek(size)
                data = log_file.read(read_size) + data
            tail_lines = data.decode(errors='replace').splitlines()
            return '\n'.join(tail_lines[-lines_requested:])
