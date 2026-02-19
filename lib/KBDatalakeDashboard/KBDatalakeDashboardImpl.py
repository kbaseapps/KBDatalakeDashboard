# -*- coding: utf-8 -*-
#BEGIN_HEADER
import json
import logging
import os
import uuid
import shutil
import requests
import tempfile

from installed_clients.KBaseReportClient import KBaseReport
from installed_clients.DataFileUtilClient import DataFileUtil

from .data_extractor import extract_all, get_user_genome_id
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
    VERSION = "0.0.2"
    GIT_URL = ""
    GIT_COMMIT_HASH = ""

    #BEGIN_CLASS_HEADER
    def _validate_params(self, params, required_keys):
        """Validate that required parameters are present."""
        for key in required_keys:
            if key not in params or params[key] is None:
                raise ValueError(f"Required parameter '{key}' is missing")

    def _resolve_handle_to_shock(self, handle_id, token):
        """Resolve a handle ID (KBH_XXXXXX) to a Shock node ID.

        Uses raw HTTP call to the handle service.
        """
        handle_url = self.config.get('handle-service-url', '')
        if not handle_url:
            raise ValueError("handle-service-url not found in config")

        payload = {
            "method": "AbstractHandle.hids_to_handles",
            "params": [[handle_id]],
            "id": 1,
            "version": "1.1"
        }
        resp = requests.post(
            handle_url,
            json=payload,
            headers={"Authorization": token}
        )
        resp.raise_for_status()
        result = resp.json()
        if 'error' in result:
            raise ValueError(f"Handle service error: {result['error']}")
        return result['result'][0][0]['id']  # Shock node ID

    def _generate_index_html(self, pangenomes_info):
        """Generate index.html listing all pangenomes with links."""
        rows = ""
        for idx, info in enumerate(pangenomes_info):
            rows += f"""
                <tr>
                    <td><strong>{info['organism']}</strong></td>
                    <td>{info['pangenome_id']}</td>
                    <td>{info['n_genes']}</td>
                    <td>{info['n_ref_genomes']}</td>
                    <td>
                        <a href="{info['heatmap_path']}" class="btn">Dashboard</a>
                    </td>
                </tr>
"""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Datalake Dashboard Index</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #f5f5f5; color: #333; }}
        .container {{ max-width: 960px; margin: 40px auto; padding: 0 20px; }}
        h1 {{ color: #026DAA; margin-bottom: 8px; font-size: 24px; }}
        .subtitle {{ color: #666; margin-bottom: 24px; }}
        table {{ width: 100%; border-collapse: collapse; background: white;
                 border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th {{ background: #026DAA; color: white; padding: 12px 16px; text-align: left; font-weight: 500; }}
        td {{ padding: 12px 16px; border-bottom: 1px solid #eee; }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover td {{ background: #f0f7fc; }}
        .btn {{ display: inline-block; padding: 6px 16px; background: #026DAA; color: white;
                text-decoration: none; border-radius: 4px; font-size: 13px; }}
        .btn:hover {{ background: #034e7a; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Genome Datalake Dashboards</h1>
        <p class="subtitle">{len(pangenomes_info)} pangenome(s) found in this GenomeDataLakeTables object.</p>
        <table>
            <thead>
                <tr>
                    <th>Organism</th>
                    <th>Pangenome ID</th>
                    <th>Genes</th>
                    <th>Ref Genomes</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
{rows}
            </tbody>
        </table>
    </div>
</body>
</html>"""
    #END_CLASS_HEADER

    # config contains contents of config file in a hash or None if it couldn't
    # be found
    def __init__(self, config):
        #BEGIN_CONSTRUCTOR
        import sys
        print("=" * 80, flush=True)
        print("KBDatalakeDashboard __init__ called", flush=True)
        print(f"Config keys: {list(config.keys())}", flush=True)
        sys.stdout.flush()

        self.callback_url = os.environ['SDK_CALLBACK_URL']
        print(f"Callback URL: {self.callback_url}", flush=True)
        sys.stdout.flush()

        self.shared_folder = config['scratch']
        print(f"Shared folder: {self.shared_folder}", flush=True)
        sys.stdout.flush()

        self.config = config
        logging.basicConfig(format='%(created)s %(levelname)s: %(message)s',
                            level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        print("Initializing DataFileUtil...", flush=True)
        sys.stdout.flush()
        self.dfu = DataFileUtil(self.callback_url)
        print("DataFileUtil initialized successfully", flush=True)
        print("=" * 80, flush=True)
        sys.stdout.flush()
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
        import sys
        print("=" * 80, flush=True)
        print("START: run_genome_datalake_dashboard", flush=True)
        print(f"Params: {params}", flush=True)
        print("=" * 80, flush=True)
        sys.stdout.flush()

        # Validate required parameters
        self._validate_params(params, ['input_ref', 'workspace_name'])
        workspace_name = params['workspace_name']
        input_ref = params['input_ref']
        token = ctx['token']
        print(f"Workspace: {workspace_name}, Input ref: {input_ref}")

        # ── Step 1: Fetch GenomeDataLakeTables object ───────────────────
        print("Fetching GenomeDataLakeTables object...", flush=True)
        datalake_obj = self.dfu.get_objects({
            'object_refs': [input_ref]
        })['data'][0]['data']

        pangenome_data = datalake_obj.get('pangenome_data', [])
        n_pangenomes = len(pangenome_data)
        print(f"Found {n_pangenomes} pangenome(s)", flush=True)

        if n_pangenomes == 0:
            raise ValueError("GenomeDataLakeTables object has no pangenome_data entries")

        # ── Step 2: Create output directory structure ───────────────────
        output_directory = os.path.join(self.shared_folder, str(uuid.uuid4()))
        os.makedirs(output_directory)
        print(f"Output directory: {output_directory}", flush=True)

        # Copy BERDL tables viewer
        tables_dir = os.path.join(output_directory, 'tables')
        shutil.copytree('/kb/module/data/html', tables_dir)
        app_config = {"upa": input_ref}
        with open(os.path.join(tables_dir, 'app-config.json'), 'w') as f:
            json.dump(app_config, f, indent=4)
        print("BERDL tables viewer copied", flush=True)

        # ── Step 3: Process each pangenome ──────────────────────────────
        pangenomes_info = []
        html_links = []

        for idx, pangenome in enumerate(pangenome_data):
            pangenome_id = pangenome.get('pangenome_id', f'pangenome_{idx}')
            handle_id = pangenome.get('sqllite_tables_handle_ref', '')
            taxonomy = pangenome.get('pangenome_taxonomy', '')

            print(f"\n{'='*60}", flush=True)
            print(f"Processing pangenome {idx+1}/{n_pangenomes}: {pangenome_id}", flush=True)
            sys.stdout.flush()

            if not handle_id:
                print(f"  WARNING: No handle ref for pangenome {pangenome_id}, skipping", flush=True)
                continue

            # ── Download SQLite database from Shock ─────────────────────
            print(f"  Resolving handle {handle_id}...", flush=True)
            try:
                shock_node_id = self._resolve_handle_to_shock(handle_id, token)
                print(f"  Shock node: {shock_node_id}", flush=True)
            except Exception as e:
                print(f"  ERROR resolving handle: {e}", flush=True)
                continue

            db_download_dir = os.path.join(self.shared_folder, f'db_{pangenome_id}')
            os.makedirs(db_download_dir, exist_ok=True)

            print(f"  Downloading SQLite database...", flush=True)
            sys.stdout.flush()
            try:
                dl_result = self.dfu.shock_to_file({
                    'shock_id': shock_node_id,
                    'file_path': db_download_dir,
                    'unpack': 'none'
                })
                db_path = dl_result['file_path']
                print(f"  Downloaded: {db_path}", flush=True)
            except Exception as e:
                print(f"  ERROR downloading database: {e}", flush=True)
                continue

            # ── Extract data ────────────────────────────────────────────
            print(f"  Extracting data...", flush=True)
            sys.stdout.flush()
            try:
                all_data = extract_all(db_path, pangenome_id)
                metadata = all_data['metadata.json']
                organism = metadata['organism']
                n_genes = metadata['n_genes']
                n_ref = metadata['n_ref_genomes']
                print(f"  Organism: {organism}", flush=True)
                print(f"  Genes: {n_genes}, Ref genomes: {n_ref}", flush=True)
            except Exception as e:
                print(f"  ERROR extracting data: {e}", flush=True)
                import traceback
                traceback.print_exc()
                continue

            # ── Create heatmap directory for this pangenome ─────────────
            slug = pangenome_id.replace(' ', '_').replace('/', '_')
            pangenome_subdir = f'pangenome_{idx}_{slug}'
            heatmap_dir = os.path.join(output_directory, pangenome_subdir, 'heatmap')
            shutil.copytree('/kb/module/data/heatmap', heatmap_dir)

            # Write extracted data files
            for filename, data in all_data.items():
                filepath = os.path.join(heatmap_dir, filename)
                with open(filepath, 'w') as f:
                    json.dump(data, f, separators=(',', ':'))
                size_kb = os.path.getsize(filepath) / 1024
                print(f"  Wrote {filename} ({size_kb:.0f} KB)", flush=True)

            # Write app-config.json
            with open(os.path.join(heatmap_dir, 'app-config.json'), 'w') as f:
                json.dump(app_config, f, indent=4)

            heatmap_path = f'{pangenome_subdir}/heatmap/index.html'
            pangenomes_info.append({
                'organism': organism,
                'pangenome_id': pangenome_id,
                'n_genes': n_genes,
                'n_ref_genomes': n_ref,
                'heatmap_path': heatmap_path,
            })

            # Clean up downloaded DB to save space
            try:
                os.remove(db_path)
            except:
                pass

            print(f"  Done with {organism}!", flush=True)
            sys.stdout.flush()

        if not pangenomes_info:
            raise ValueError("No pangenomes could be processed successfully")

        # ── Step 4: Generate index page ─────────────────────────────────
        print(f"\nGenerating index page for {len(pangenomes_info)} pangenome(s)...", flush=True)

        if len(pangenomes_info) == 1:
            # Single pangenome: make heatmap the default directly
            index_redirect = f"""<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0;url={pangenomes_info[0]['heatmap_path']}">
</head><body></body></html>"""
            with open(os.path.join(output_directory, 'index.html'), 'w') as f:
                f.write(index_redirect)
        else:
            # Multiple pangenomes: generate index page
            index_html = self._generate_index_html(pangenomes_info)
            with open(os.path.join(output_directory, 'index.html'), 'w') as f:
                f.write(index_html)

        # ── Step 5: Upload to Shock ─────────────────────────────────────
        import subprocess
        try:
            du_output = subprocess.check_output(['du', '-sh', output_directory]).decode('utf-8')
            print(f"Total output size: {du_output.split()[0]}", flush=True)
        except:
            pass

        print("Uploading to Shock...", flush=True)
        sys.stdout.flush()
        shock_id = self.dfu.file_to_shock({
            'file_path': output_directory,
            'pack': 'zip'
        })['shock_id']
        print(f"Upload complete! Shock ID: {shock_id}", flush=True)

        # ── Step 6: Create KBase report ─────────────────────────────────
        # First link = default embedded view
        if len(pangenomes_info) == 1:
            # Single pangenome: dashboard is default
            html_links = [
                {
                    'shock_id': shock_id,
                    'name': pangenomes_info[0]['heatmap_path'],
                    'label': f"Datalake Dashboard - {pangenomes_info[0]['organism']}",
                    'description': f"Interactive dashboard for {pangenomes_info[0]['organism']}"
                },
                {
                    'shock_id': shock_id,
                    'name': 'tables/index.html',
                    'label': 'BERDL Tables Viewer',
                    'description': 'Interactive table viewer for genome datalake tables'
                }
            ]
        else:
            # Multiple pangenomes: index page is default
            html_links = [
                {
                    'shock_id': shock_id,
                    'name': 'index.html',
                    'label': 'Datalake Dashboard Index',
                    'description': f'Index of {len(pangenomes_info)} pangenome dashboards'
                },
                {
                    'shock_id': shock_id,
                    'name': 'tables/index.html',
                    'label': 'BERDL Tables Viewer',
                    'description': 'Interactive table viewer for genome datalake tables'
                }
            ]
            # Add individual pangenome links
            for info in pangenomes_info:
                html_links.append({
                    'shock_id': shock_id,
                    'name': info['heatmap_path'],
                    'label': f"{info['organism']}",
                    'description': f"Dashboard: {info['n_genes']} genes, {info['n_ref_genomes']} ref genomes"
                })

        print("Creating KBase report...", flush=True)
        report_client = KBaseReport(self.callback_url)
        report_info = report_client.create_extended_report({
            'message': '',
            'workspace_name': workspace_name,
            'objects_created': [],
            'html_links': html_links,
            'direct_html_link_index': 0,
            'html_window_height': 800,
        })

        output = {
            'report_name': report_info['name'],
            'report_ref': report_info['ref'],
        }
        print("=" * 80, flush=True)
        print(f"SUCCESS! {len(pangenomes_info)} pangenome(s) processed", flush=True)
        for info in pangenomes_info:
            print(f"  - {info['organism']}: {info['n_genes']} genes", flush=True)
        print(f"Report: {output['report_ref']}", flush=True)
        print("=" * 80, flush=True)
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
