/*
A KBase module: KBDatalakeDashboard
This module provides a dashboard view for genome datalake tables,
generating interactive HTML reports from GenomeDataLakeTables objects.
*/
module KBDatalakeDashboard {

    /* Standard KBase report output */
    typedef structure {
        string report_name;
        string report_ref;
    } ReportResults;

    /* Input parameters for run_genome_datalake_dashboard */
    typedef structure {
        string workspace_name;
        string input_ref;
    } RunGenomeDatalakeDashboardParams;

    /*
    Run the genome datalake dashboard.

    Generates an interactive HTML report from a GenomeDataLakeTables object,
    providing a dashboard view of genome annotation, modeling, and phenotype data.
    */
    funcdef run_genome_datalake_dashboard(RunGenomeDatalakeDashboardParams params)
        returns (ReportResults output)
        authentication required;
};
