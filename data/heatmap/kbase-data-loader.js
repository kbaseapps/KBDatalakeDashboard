/**
 * Smart Data Loader - Works in both standalone and KBase contexts
 *
 * Standalone context: Loads from local JSON files
 * KBase context: Loads from TableScanner API using app-config.json
 *
 * Add this to index.html BEFORE the main script tag
 */

// Detect if running in KBase context
async function isKBaseContext() {
    try {
        const response = await fetch('../app-config.json');
        return response.ok;
    } catch (e) {
        return false;
    }
}

// Load genes data from TableScanner API (KBase)
async function loadGenesFromTableScanner(upa) {
    console.log('[KBase] Loading genes from TableScanner API:', upa);

    // Get KBase session token
    const token = getCookie('kbase_session') || getCookie('kbase_session_backup');
    if (!token) {
        throw new Error('No KBase session token found. Please log in.');
    }

    // Fetch from TableScanner
    const response = await fetch(
        'https://appdev.kbase.us/services/berdl_table_scanner/table-data',
        {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                upa: upa,
                table: 'genome_features',
                limit: 50000  // Should be enough for most genomes
            })
        }
    );

    if (!response.ok) {
        throw new Error(`TableScanner API error: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();
    console.log('[KBase] Loaded', data.rows.length, 'genes from TableScanner');

    // Transform TableScanner format to genes_data.json format
    return transformTableScannerToGenesData(data.rows);
}

// Transform TableScanner row format to our genes_data array format
function transformTableScannerToGenesData(rows) {
    // Field indices matching our current format
    // [id, fid, length, start, strand, cons_frac, pan_cat, func, n_ko, n_cog, ...]

    return rows.map((row, idx) => {
        // Helper: Count semicolon-separated items
        const countTerms = (str) => str ? str.split(';').filter(s => s.trim()).length : 0;

        // Helper: Parse strand
        const parseStrand = (strand) => strand === '+' ? 1 : (strand === '-' ? -1 : 0);

        return [
            idx,                                      // 0: id
            row.feature_id || '',                     // 1: fid
            row.end - row.start || 0,                 // 2: length
            row.start || 0,                           // 3: start
            parseStrand(row.strand),                  // 4: strand
            row.conservation_frac ?? null,            // 5: cons_frac
            row.pan_category || 0,                    // 6: pan_cat
            row.rast_function || '',                  // 7: func
            countTerms(row.ko),                       // 8: n_ko
            countTerms(row.cog),                      // 9: n_cog
            countTerms(row.pfam),                     // 10: n_pfam
            countTerms(row.go),                       // 11: n_go
            row.localization || 0,                    // 12: loc
            row.rast_cons ?? -1,                      // 13: rast_cons
            row.ko_cons ?? -1,                        // 14: ko_cons
            row.go_cons ?? -1,                        // 15: go_cons
            row.ec_cons ?? -1,                        // 16: ec_cons
            row.avg_cons ?? -1,                       // 17: avg_cons
            row.bakta_cons ?? -1,                     // 18: bakta_cons
            row.ec_avg_cons ?? -1,                    // 19: ec_avg_cons
            row.specificity ?? -1,                    // 20: specificity
            row.is_hypo || 0,                         // 21: is_hypo
            row.has_name || 0,                        // 22: has_name
            countTerms(row.ec),                       // 23: n_ec
            row.agreement ?? -1,                      // 24: agreement
            row.cluster_size || 0,                    // 25: cluster_size
            countTerms(row.modules),                  // 26: n_modules
            row.ec_map_cons ?? -1,                    // 27: ec_map_cons
            row.protein_length || 0,                  // 28: prot_len
            row.reactions || 0,                       // 29: reactions
            row.rich_flux ?? 0,                       // 30: rich_flux
            row.rich_class || '',                     // 31: rich_class
            row.min_flux ?? 0,                        // 32: min_flux
            row.min_class || '',                      // 33: min_class
            row.psortb_new || 0,                      // 34: psortb_new
            row.essentiality || 0,                    // 35: essentiality
            row.n_phenotypes || 0,                    // 36: n_phenotypes
            row.n_fitness || 0                        // 37: n_fitness
        ];
    });
}

// Get cookie value by name
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Main data loader - auto-detects context
async function loadAllData() {
    const inKBase = await isKBaseContext();

    if (inKBase) {
        console.log('═══════════════════════════════════════');
        console.log('  KBase Context Detected');
        console.log('═══════════════════════════════════════');

        // Load config
        const config = await fetch('../app-config.json').then(r => r.json());
        console.log('[KBase] Config loaded:', config);

        // Load genes from TableScanner
        const genes = await loadGenesFromTableScanner(config.upa);

        // TODO: Load other data (tree, reactions, summary) from TableScanner
        // For now, return minimal structure
        return {
            genes: genes,
            treeData: null,  // TODO: Load from TableScanner
            reactionsData: null,  // TODO: Load from TableScanner
            summaryData: null,  // TODO: Load from TableScanner
            metadata: {
                organism: 'Unknown',  // TODO: Get from genome object
                genome_id: config.upa,
                n_genes: genes.length,
                n_ref_genomes: 0  // TODO: Get from pangenome
            }
        };

    } else {
        console.log('═══════════════════════════════════════');
        console.log('  Standalone Context Detected');
        console.log('═══════════════════════════════════════');

        // Load from local files
        const [genes, treeData, reactionsData, summaryData, metadata] = await Promise.all([
            fetch('genes_data.json').then(r => r.json()),
            fetch('tree_data.json').then(r => r.json()).catch(() => null),
            fetch('reactions_data.json').then(r => r.json()).catch(() => null),
            fetch('summary_stats.json').then(r => r.json()).catch(() => null),
            fetch('metadata.json').then(r => r.json()).catch(() => ({
                organism: 'Unknown',
                genome_id: 'unknown',
                n_genes: 0,
                n_ref_genomes: 0
            }))
        ]);

        console.log('[Standalone] Loaded', genes.length, 'genes from local files');

        return {
            genes,
            treeData,
            reactionsData,
            summaryData,
            metadata
        };
    }
}

// Example usage in index.html:
//
// <script src="kbase-data-loader.js"></script>
// <script>
//   loadAllData()
//     .then(data => {
//       genes = data.genes;
//       treeData = data.treeData;
//       reactionsData = data.reactionsData;
//       // ... initialize viewer ...
//     })
//     .catch(error => {
//       console.error('Failed to load data:', error);
//       alert('Error loading genome data: ' + error.message);
//     });
// </script>
