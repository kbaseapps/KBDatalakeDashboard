# -*- coding: utf-8 -*-
import os
import time
import unittest
from configparser import ConfigParser

from KBDatalakeDashboard.KBDatalakeDashboardImpl import KBDatalakeDashboard
from installed_clients.WorkspaceClient import Workspace


class KBDatalakeDashboardTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        token = os.environ.get('KB_AUTH_TOKEN', None)
        config_file = os.environ.get('KB_DEPLOYMENT_CONFIG', None)
        cls.cfg = {}
        config = ConfigParser()
        config.read(config_file)
        for nameval in config.items('KBDatalakeDashboard'):
            cls.cfg[nameval[0]] = nameval[1]
        # Getting username from Auth profile for token
        cls.wsURL = cls.cfg['workspace-url']
        cls.wsClient = Workspace(cls.wsURL, token=token)
        cls.serviceImpl = KBDatalakeDashboard(cls.cfg)
        cls.scratch = cls.cfg['scratch']
        cls.callback_url = os.environ['SDK_CALLBACK_URL']
        suffix = int(time.time() * 1000)
        cls.wsName = "test_KBDatalakeDashboard_" + str(suffix)
        ret = cls.wsClient.create_workspace({'workspace': cls.wsName})

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'wsName'):
            cls.wsClient.delete_workspace({'workspace': cls.wsName})
            print('Test workspace was deleted')

    def test_run_genome_datalake_dashboard(self):
        # TODO: Add test with actual GenomeDataLakeTables object
        pass
