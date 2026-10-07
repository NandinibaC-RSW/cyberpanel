# -*- coding: utf-8 -*-

import json
import os
import re
import shutil
import subprocess
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
    JOB_STATUS_FILE = '/home/cyberpanel/dbbackup-job.json'
    JOB_DONE_FILE = '/home/cyberpanel/dbbackup-job-done.json'

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
        """Compare file sets between primary store and mirror store."""
        primary_present = os.path.isdir(cls.BACKUP_BASE)
        mirror_present = os.path.isdir(cls.MIRROR_BASE)
        if not primary_present or not mirror_present:
            return {'state': 'unknown', 'missingOnMirror': [], 'missingOnPrimary': []}

        def walk(base):
            found = {}
            for root, dirs, files in os.walk(base):
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
        try:
            os.unlink(real)
        except OSError as msg:
            logging.writeToFile('DBBackupManager delete failed: %s' % str(msg))
            return self._deny('Could not delete the file on the backup disk.')
        # keep both storages consistent
        removed_mirror = False
        twin = self._mirrorTwin(real)
        if twin and os.path.isfile(twin):
            try:
                os.unlink(twin)
                removed_mirror = True
            except OSError as msg:
                logging.writeToFile('DBBackupManager mirror delete failed: %s' % str(msg))
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
        try:
            os.makedirs(primary_dir, exist_ok=True)
            os.makedirs(mirror_dir, exist_ok=True)
        except OSError as msg:
            return self._deny('Cannot create backup folder: %s' % str(msg))

        primary_path = os.path.join(primary_dir, name)
        try:
            with open(primary_path, 'wb') as dest:
                for chunk in upload.chunks():
                    dest.write(chunk)
            shutil.copyfile(primary_path, os.path.join(mirror_dir, name))
        except OSError as msg:
            logging.writeToFile('DBBackupManager upload failed: %s' % str(msg))
            return self._deny('Could not store the uploaded file: %s' % str(msg))

        try:
            os.chmod(primary_path, 0o640)
            os.chmod(os.path.join(mirror_dir, name), 0o640)
        except OSError:
            pass

        return self._json({'status': 1, 'path': primary_path,
                           'mirrorPath': os.path.join(mirror_dir, name)})

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
            os.makedirs(os.path.dirname(self.JOB_STATUS_FILE), exist_ok=True)
            os.makedirs(self.LOG_DIR, exist_ok=True)
            shell = (
                '%s >> %s 2>&1; rc=$?; printf \'{"job": "%%s", "exit_code": %%s, '
                '"finished": "%%s"}\' "%s" "$rc" "$(date +%%s)" > %s'
                % (script, log_path, job, self.JOB_DONE_FILE)
            )
            subprocess.Popen(['/bin/bash', '-c', shell], start_new_session=True,
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
            return self._json({'status': 1, 'configured': False, 'jobs': []})
        return self._json({'status': 1, 'configured': True, 'jobs': jobs})

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

        try:
            tmp_path = self.CRON_FILE + '.cyberpanel.tmp'
            with open(tmp_path, 'w') as tmp_file:
                tmp_file.write('\n'.join(new_lines) + '\n')
            os.replace(tmp_path, self.CRON_FILE)
            os.chmod(self.CRON_FILE, 0o644)
        except OSError as msg:
            logging.writeToFile('DBBackupManager cron save failed: %s' % str(msg))
            return self._deny('Could not write the cron file: %s' % str(msg))

        try:
            subprocess.run(['systemctl', 'restart', 'cron'], timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except BaseException:
            pass

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
        try:
            tmp_path = self.RETENTION_SCRIPT + '.cyberpanel.tmp'
            with open(tmp_path, 'w') as tmp_file:
                tmp_file.write(new_content)
            os.replace(tmp_path, self.RETENTION_SCRIPT)
            os.chmod(self.RETENTION_SCRIPT, 0o755)
        except OSError as msg:
            return self._deny('Could not update the cleanup script: %s' % str(msg))
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
