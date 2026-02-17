# Implementation Roadmap: Dynamic Data Loading

**Date:** 2026-02-17
**Status:** ✅ Async execution working, ⚠️ Data extraction needed
**Commits:** 8e1e473 (async fix), 154c7a3 (data files), 59eed7b (architecture doc)

---

## Current Status

### What Works ✅
1. **Async execution** - Jobs run successfully, no more hanging
2. **BERDL Tables Viewer** - Dynamically loads data from selected GenomeDataLakeTables object
3. **Heatmap Viewer loads** - Shows hardcoded Acinetobacter ADP1 data

### What Needs To Be Done ⚠️
**Heatmap viewer must extract data from the GenomeDataLakeTables SQLite database**

---

## GenomeDataLakeTables Object Structure

From the KBase type spec (shared 2026-02-16):

```javascript
typedef structure {
    string description;
    string name;
    GenomeSet_ref genomeset_ref;          // Reference to input genome set
    list<PangenomeData> pangenome_data;   // ⚠️ MULTIPLE pangenomes!
} GenomeDataLakeTables;

typedef structure {
    string pangenome_id;
    string pangenome_taxonomy;             // e.g., "Acinetobacter"
    list<genome_ref> user_genomes;         // ⚠️ MULTIPLE user genomes possible!
    list<string> datalake_genomes;         // Reference genome IDs
    table_handle_ref sqllite_tables_handle_ref;  // 🔑 KEY: Handle to SQLite file in Shock
} PangenomeData;
```

### Critical Insights from Chris Henry (feedback_r3.txt):

1. **Multiple clades per object:**
   - A single GenomeDataLakeTables can contain 10, 20, 50+ pangenomes
   - Example: User submits 100 genomes → get 50 different clades
   - **Must iterate over `pangenome_data` array**

2. **SQLite database per clade:**
   - Each pangenome has its own SQLite database
   - Downloaded from Shock using `sqllite_tables_handle_ref`
   - Contains all gene data, annotations, pangenome clusters, etc.

3. **Multiple user genomes per clade:**
   - Each clade can have multiple user genomes
   - `user_genomes` list contains workspace references
   - For MVP: focus on single user genome (most common case)

4. **Output format:**
   - Create an **index page** listing all clades
   - Create **separate dashboard per clade**
   - Index shows: clade name, number of genomes, link to dashboard

---

## Implementation Plan

### Step 1: Fetch GenomeDataLakeTables Object

```python
# In KBDatalakeDashboardImpl.py
print(f"Fetching GenomeDataLakeTables object: {input_ref}")
datalake_obj = self.dfu.get_objects({
    'object_refs': [input_ref]
})['data'][0]['data']

print(f"Found {len(datalake_obj['pangenome_data'])} pangenome(s)")
```

---

### Step 2: Download SQLite File from Handle

For each pangenome in `datalake_obj['pangenome_data']`:

```python
from installed_clients.AbstractHandleClient import AbstractHandle

handle_service = AbstractHandle(self.config['handle-service-url'], token=self.token)

for idx, pangenome in enumerate(datalake_obj['pangenome_data']):
    print(f"Processing pangenome {idx+1}: {pangenome['pangenome_taxonomy']}")

    # Get handle metadata
    handle_id = pangenome['sqllite_tables_handle_ref']
    handle_info = handle_service.hids_to_handles([handle_id])[0]

    # Download SQLite file from Shock
    shock_id = handle_info['id']
    db_file = self.dfu.shock_to_file({
        'shock_id': shock_id,
        'file_path': self.shared_folder
    })['file_path']

    print(f"Downloaded SQLite database: {db_file}")
```

**Note:** DataFileUtil has `shock_to_file` method for downloading from Shock.

---

### Step 3: Extract Data Using data_extractor.py

```python
from .data_extractor import (
    extract_genes_data,
    extract_metadata,
    extract_tree_data,
    extract_reactions_data,
    extract_summary_stats,
    extract_ref_genomes_data,
    extract_cluster_data,
    get_user_genome_id
)

# Get user genome ID from database
user_genome_id = get_user_genome_id(db_file)
print(f"User genome ID: {user_genome_id}")

# Extract data
genes_data = extract_genes_data(db_file, user_genome_id)
print(f"Extracted {len(genes_data)} genes")

metadata = extract_metadata(db_file, user_genome_id, pangenome['pangenome_taxonomy'])
tree_data = extract_tree_data(db_file)
reactions_data = extract_reactions_data(db_file, user_genome_id)
summary_stats = extract_summary_stats(db_file, user_genome_id)
ref_genomes = extract_ref_genomes_data(db_file)
cluster_data = extract_cluster_data(db_file)
```

---

### Step 4: Create Separate Dashboard Per Pangenome

```python
# Create subdirectory for this pangenome
pangenome_slug = pangenome['pangenome_taxonomy'].replace(' ', '_').lower()
pangenome_dir = os.path.join(output_directory, f"pangenome_{idx}_{pangenome_slug}")
os.makedirs(pangenome_dir)

# Copy BERDL tables viewer
shutil.copytree('/kb/module/data/html', os.path.join(pangenome_dir, 'tables'))

# Copy heatmap viewer
heatmap_dir = os.path.join(pangenome_dir, 'heatmap')
shutil.copytree('/kb/module/data/heatmap', heatmap_dir)

# Write extracted data to JSON files
with open(os.path.join(heatmap_dir, 'genes_data.json'), 'w') as f:
    json.dump(genes_data, f)

with open(os.path.join(heatmap_dir, 'metadata.json'), 'w') as f:
    json.dump(metadata, f)

# ... write other data files
```

---

### Step 5: Create Index Page

```python
# Create index.html that lists all pangenomes
index_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Genome Datalake Dashboards</title>
    <link rel="stylesheet" href="https://unpkg.com/@kbase/ui-assets/dist/css/kbase-ui.css">
</head>
<body>
    <div class="container">
        <h1>Genome Datalake Dashboards</h1>
        <p>This GenomeDataLakeTables object contains {n_pangenomes} pangenome(s).</p>
        <table class="table">
            <thead>
                <tr>
                    <th>Clade</th>
                    <th>User Genomes</th>
                    <th>Reference Genomes</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
""".format(n_pangenomes=len(datalake_obj['pangenome_data']))

for idx, pangenome in enumerate(datalake_obj['pangenome_data']):
    pangenome_slug = pangenome['pangenome_taxonomy'].replace(' ', '_').lower()
    index_html += f"""
                <tr>
                    <td><strong>{pangenome['pangenome_taxonomy']}</strong></td>
                    <td>{len(pangenome['user_genomes'])}</td>
                    <td>{len(pangenome['datalake_genomes'])}</td>
                    <td>
                        <a href="pangenome_{idx}_{pangenome_slug}/tables/index.html">Tables</a> |
                        <a href="pangenome_{idx}_{pangenome_slug}/heatmap/index.html">Heatmap</a>
                    </td>
                </tr>
"""

index_html += """
            </tbody>
        </table>
    </div>
</body>
</html>
"""

with open(os.path.join(output_directory, 'index.html'), 'w') as f:
    f.write(index_html)
```

---

### Step 6: Create KBase Report

```python
html_links = [
    {
        'shock_id': shock_id,
        'name': 'index.html',
        'label': 'Genome Datalake Dashboards',
        'description': f'Index of {len(datalake_obj["pangenome_data"])} pangenome dashboard(s)'
    }
]

# Add links for each pangenome
for idx, pangenome in enumerate(datalake_obj['pangenome_data']):
    pangenome_slug = pangenome['pangenome_taxonomy'].replace(' ', '_').lower()
    taxonomy = pangenome['pangenome_taxonomy']

    html_links.append({
        'shock_id': shock_id,
        'name': f'pangenome_{idx}_{pangenome_slug}/tables/index.html',
        'label': f'{taxonomy} - Tables',
        'description': f'Data tables for {taxonomy} pangenome'
    })

    html_links.append({
        'shock_id': shock_id,
        'name': f'pangenome_{idx}_{pangenome_slug}/heatmap/index.html',
        'label': f'{taxonomy} - Heatmap',
        'description': f'Interactive heatmap for {taxonomy} pangenome'
    })
```

---

## Modified Implementation Flow

```
User runs app with GenomeDataLakeTables object
  ↓
Fetch object from workspace
  ↓
For each pangenome in pangenome_data:
  ↓
  Download SQLite database from Shock (via handle)
  ↓
  Extract genes, metadata, tree, reactions, etc.
  ↓
  Create subdirectory: pangenome_{idx}_{taxonomy}/
  ↓
  Copy tables viewer to tables/
  ↓
  Copy heatmap viewer to heatmap/
  ↓
  Write extracted JSON files to heatmap/
  ↓
Create index.html listing all pangenomes
  ↓
Upload entire directory to Shock
  ↓
Create report with links to index and all dashboards
```

---

## Database Schema (Expected)

Based on genome-heatmap-viewer extraction scripts, the SQLite database should have:

### `genome_features` table:
- `genome_id` - e.g., 'user_genome'
- `feature_id` - e.g., 'fig|123.45.peg.1'
- `protein_length` - amino acid length
- `start`, `strand` - genomic coordinates
- `rast_function`, `bakta_function` - annotations
- `ko`, `cog`, `pfam`, `go`, `ec` - semicolon-separated ontology terms
- `pangenome_cluster_id` - cluster assignment
- `pangenome_conservation_fraction` - 0.0-1.0
- `pangenome_category` - 0=unknown, 1=accessory, 2=core
- `pangenome_is_core` - boolean
- `rast_annotation_consistency` - 0.0-1.0 or -1 (N/A)
- `ko_annotation_consistency`, `go_annotation_consistency`, etc.
- `avg_annotation_consistency` - average across sources
- `annotation_specificity` - 0.0-1.0
- `psortb_localization` - subcellular location

### `genome` table:
- `genome_id`
- `organism_name`
- `ncbi_taxonomy`
- `n_contigs`, `n_features`
- `is_user_genome` - boolean flag

### `phylogenetic_tree` table (optional):
- `newick` - tree in Newick format

### `model_reactions` table (optional):
- `reaction_id`, `name`, `equation`
- `genes` - semicolon-separated gene IDs
- `flux_min`, `flux_max`
- `is_essential`, `is_gapfilled`

### `cluster_embeddings` table (optional):
- `cluster_id`, `umap_x`, `umap_y`

---

## Questions for Chris/Philippe

1. **Handle service access:**
   - Do we need to install AbstractHandle client?
   - Or can DataFileUtil download from handle directly?

2. **Database schema:**
   - Are all expected columns present in the SQLite files?
   - Any differences from the berdl_tables.db structure?

3. **User genome identification:**
   - How is the user genome labeled in `genome_features.genome_id`?
   - Is it always 'user_genome' or does it vary?

4. **Multiple user genomes:**
   - If `user_genomes` list has multiple entries, should we:
     - Create separate heatmap for each?
     - Show only the first one?
     - Combine them somehow?

5. **Testing:**
   - What's a good test UPA with multiple pangenomes?
   - What's the workspace reference for the example ADP1 data?

---

## Testing Strategy

### Local Testing

1. **Get sample GenomeDataLakeTables object:**
   ```bash
   # From feedback: workspace 76990, object 8/1
   # UPA: 76990/8/1
   ```

2. **Download SQLite file manually:**
   ```python
   # Use DataFileUtil to download
   # Or use handle service to get Shock ID and download
   ```

3. **Test data extraction:**
   ```python
   from lib.KBDatalakeDashboard.data_extractor import *

   db_path = './test_datalake.db'
   user_genome_id = get_user_genome_id(db_path)
   genes = extract_genes_data(db_path, user_genome_id)

   print(f"Extracted {len(genes)} genes")
   print(f"First gene: {genes[0]}")
   ```

4. **Validate format:**
   ```bash
   # Compare with hardcoded genes_data.json
   python -c "import json; print(len(json.load(open('genes_data.json'))[0]))"
   # Should print: 21 (fields per gene)
   ```

### KBase Testing

1. Deploy to AppDev
2. Run with test object (76990/8/1)
3. Verify:
   - Index page lists all pangenomes
   - Each heatmap shows correct organism data
   - All tracks render properly
   - No hardcoded ADP1 data

---

## Files to Create/Modify

### Created ✅
- `lib/KBDatalakeDashboard/data_extractor.py` - Data extraction functions

### To Modify
- `lib/KBDatalakeDashboard/KBDatalakeDashboardImpl.py` - Add data extraction logic

### To Remove (after dynamic loading works)
- `data/heatmap/genes_data.json` (static)
- `data/heatmap/metadata.json` (static)
- `data/heatmap/tree_data.json` (static)
- `data/heatmap/reactions_data.json` (static)
- `data/heatmap/summary_stats.json` (static)

Keep for now as fallback in case extraction fails.

---

## Timeline

**Phase 1: Single Pangenome (MVP)** - 1 day
- Assume single pangenome in object
- Download SQLite, extract data, generate heatmap
- Test with ADP1 example

**Phase 2: Multiple Pangenomes** - 0.5 day
- Iterate over pangenome_data array
- Create index page
- Generate separate dashboards

**Phase 3: Polish & Test** - 0.5 day
- Error handling (missing tables, etc.)
- Test with multiple clades
- Verify with Chris/Philippe

**Total:** 2 days

---

## Next Immediate Actions

1. **Ask Chris/Philippe:**
   - How to download SQLite file from handle?
   - Test UPA with multiple pangenomes?
   - Database schema confirmation?

2. **Test data_extractor.py:**
   - Get sample SQLite file
   - Run extraction functions
   - Validate output format

3. **Implement Phase 1:**
   - Modify KBDatalakeDashboardImpl.py
   - Add SQLite download logic
   - Test with single pangenome first

4. **Deploy and validate:**
   - Push to AppDev
   - Run with test data
   - Verify heatmap shows correct organism

---

## Success Criteria

✅ Implementation is complete when:
1. User selects GenomeDataLakeTables with N pangenomes
2. Gets index page listing all N pangenomes
3. Each heatmap shows data from the CORRECT genome (not hardcoded ADP1)
4. All tracks, trees, and metabolic maps work correctly
5. Multiple users can run simultaneously with different objects
6. No errors when database is missing optional tables

🎉 **Ready for Chris Henry review and poster presentation!**
