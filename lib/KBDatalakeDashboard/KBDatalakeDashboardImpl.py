# -*- coding: utf-8 -*-
#BEGIN_HEADER
import json
import logging
import os
import uuid
import shutil

from installed_clients.KBaseReportClient import KBaseReport
from installed_clients.DataFileUtilClient import DataFileUtil
#END_HEADER


class KBDatalakeDashboard:
    '''
    Module Name:
    KBDatalakeDashboard

    Module Description:
    Dashboard viewer for KBase genome datalake tables. Generates interactive
    HTML reports from GenomeDataLakeTables objects.

    Author: chenry
    '''

    ######## WARNING FOR GEVENT USERS ####### noqa
    # Since asynchronous IO can lead to methods - even the same method -
    # interrupting each other, you must be *very* careful when using global
    # state. A method could easily clobber the state set by another while
    # the latter method is running.
    ######################################### noqa
    VERSION = "0.0.1"
    GIT_URL = ""
    GIT_COMMIT_HASH = ""

    #BEGIN_CLASS_HEADER
    def _validate_params(self, params, required_keys):
        """Validate that required parameters are present."""
        for key in required_keys:
            if key not in params or params[key] is None:
                raise ValueError(f"Required parameter '{key}' is missing")
    #END_CLASS_HEADER

    # config contains contents of config file in a hash or None if it couldn't
    # be found
    def __init__(self, config):
        #BEGIN_CONSTRUCTOR
        self.callback_url = os.environ['SDK_CALLBACK_URL']
        self.shared_folder = config['scratch']
        self.config = config
        logging.basicConfig(format='%(created)s %(levelname)s: %(message)s',
                            level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.dfu = DataFileUtil(self.callback_url)
        #END_CONSTRUCTOR
        pass

    def run_genome_datalake_dashboard(self, ctx, params):
        """
        Run the genome datalake dashboard.
        Generates an interactive HTML report from a GenomeDataLakeTables object.
        :param params: instance of type "RunGenomeDatalakeDashboardParams" ->
           structure: parameter "workspace_name" of String, parameter
           "input_ref" of String
        :returns: instance of type "ReportResults" -> structure: parameter
           "report_name" of String, parameter "report_ref" of String
        """
        # ctx is the context object
        # return variables are: output
        #BEGIN run_genome_datalake_dashboard
        self.logger.info(f"Running genome datalake dashboard with params: {params}")

        # Validate required parameters
        self._validate_params(params, ['input_ref', 'workspace_name'])

        workspace_name = params['workspace_name']
        input_ref = params['input_ref']

        # Prepare HTML report output directory
        output_directory = os.path.join(self.shared_folder, str(uuid.uuid4()))
        shutil.copytree('/kb/module/data/html', output_directory)

        # Write app-config.json so the DataTables Viewer knows which object to display
        app_config = {
            "upa": input_ref
        }
        app_config_path = os.path.join(output_directory, 'app-config.json')
        with open(app_config_path, 'w') as f:
            json.dump(app_config, f, indent=4)
        self.logger.info(f"Wrote app-config.json with UPA: {app_config['upa']}")

        # Upload HTML directory to Shock
        shock_id = self.dfu.file_to_shock({
            'file_path': output_directory,
            'pack': 'zip'
        })['shock_id']

        self.logger.info(f"HTML directory contents: {os.listdir(output_directory)}")
        self.logger.info(f"Shock ID: {shock_id}")

        html_links = [{
            'shock_id': shock_id,
            'name': 'index.html',
            'label': 'Genome Datalake Dashboard',
            'description': 'Interactive dashboard for genome datalake tables'
        }]

        # Create KBase report
        report_client = KBaseReport(self.callback_url)
        report_params = {
            'message': '',
            'workspace_name': workspace_name,
            'objects_created': [],
            'html_links': html_links,
            'direct_html_link_index': 0,
            'html_window_height': 800,
        }

        report_info = report_client.create_extended_report(report_params)

        output = {
            'report_name': report_info['name'],
            'report_ref': report_info['ref'],
        }
        #END run_genome_datalake_dashboard

        # At some point might do deeper type checking...
        if not isinstance(output, dict):
            raise ValueError('Method run_genome_datalake_dashboard return value ' +
                             'output is not type dict as required.')
        # return the results
        return [output]

    def status(self, ctx):
        #BEGIN_STATUS
        returnVal = {'state': "OK",
                     'message': "",
                     'version': self.VERSION,
                     'git_url': self.GIT_URL,
                     'git_commit_hash': self.GIT_COMMIT_HASH}
        #END_STATUS
        return [returnVal]
