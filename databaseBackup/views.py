# -*- coding: utf-8 -*-

import json

from django.shortcuts import redirect

from databaseBackup.databaseBackupManager import DBBackupManager
from loginSystem.views import loadLoginPage


def loadDBBackupManager(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.loadDBBackupManager(request, userID)
    except KeyError:
        return redirect(loadLoginPage)


def fetchDatabaseBackups(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.fetchDatabaseBackups(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def fetchSiteBackups(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.fetchSiteBackups(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def fetchBackupStats(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.fetchBackupStats(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def downloadBackup(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.downloadBackup(request, userID)
    except KeyError:
        return redirect(loadLoginPage)


def deleteBackup(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.deleteBackup(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def uploadBackup(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.uploadBackup(request, userID)
    except KeyError:
        return redirect(loadLoginPage)


def runBackupJob(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.runBackupJob(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def fetchJobStatus(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.fetchJobStatus(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def fetchCronConfig(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.fetchCronConfig(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def saveCronConfig(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.saveCronConfig(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def fetchRetention(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.fetchRetention(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def saveRetention(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.saveRetention(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)


def fetchBackupLogs(request):
    try:
        userID = request.session['userID']
        manager = DBBackupManager()
        return manager.fetchBackupLogs(userID, json.loads(request.body))
    except KeyError:
        return redirect(loadLoginPage)
