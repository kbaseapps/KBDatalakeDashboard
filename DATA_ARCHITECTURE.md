# KBDatalakeDashboard Data Architecture

**Status:** Interim solution deployed (commit 154c7a3)
**Date:** 2026-02-17

---

## Current Implementation (Hardcoded Data)

### What's Deployed Now

The KBase app currently uses **hardcoded static data files** from a single Acinetobacter baylyi ADP1 genome:

**Data files in `data/heatmap/`:**
- `genes_data.json` - 4,617 genes × 21 fields (570KB)
- `metadata.json` - Organism info (Acinetobacter baylyi ADP1)
- `tree_data.json` - Phylogenetic tree (14 reference genomes)
- `reactions_data.json` - Metabolic reactions (500KB)
- `summary_stats.json` - Precomputed statistics
- `cluster_data.json` - UMAP pangenome clusters
- `ref_genomes_data.json` - Reference genome metadata

### How It Works

```
User runs "Run Genome Datalake Dashboard" app
  ↓
KBDatalakeDashboardImpl.py
  ↓ Copies static files
  ├─ data/html/ → BERDL tables viewer
  └─ data/heatmap/ → Genome heatmap viewer (with hardcoded data)
  ↓
Uploads to Shock
  ↓
Creates KBase report with two links:
  1. BERDL Tables Viewer (interactive table browser)
  2. Genome Heatmap Viewer (heatmap + tree + metabolic map)
```

**Important:** The heatmap viewer currently shows the SAME genome data (Acinetobacter ADP1) **regardless of which GenomeDataLakeTables object you select**!

---

## The Problem

### BERDL Tables Viewer (First Link)
✅ **Works correctly** - Shows the actual data from the selected GenomeDataLakeTables object
- Uses `app-config.json` with the UPA to fetch data dynamically from KBase
- Database dropdown shows available genomes (e.g., "Acinetobacter baylyi ADP1", "E. coli K-12")
- Data is loaded from the workspace object via DataTables Viewer

### Genome Heatmap Viewer (Second Link)
❌ **Shows hardcoded data** - Always displays Acinetobacter ADP1, ignoring the selected object
- Loads static JSON files bundled with the app
- Does NOT read the UPA from `app-config.json`
- Does NOT fetch data from the GenomeDataLakeTables workspace object

**This means:**
- Selecting "E. coli" in the BERDL viewer shows E. coli data ✓
- But the heatmap viewer link still shows Acinetobacter data ✗

---

## The Goal: Dynamic Data Loading

The heatmap viewer should **extract data from the selected GenomeDataLakeTables object**, not use hardcoded files.

### Architecture Options

#### Option 1: Extract Data in Python (Recommended)

**Modify:** `lib/KBDatalakeDashboard/KBDatalakeDashboardImpl.py`

```python
def run_genome_datalake_dashboard(self, ctx, params):
    # ... existing code ...

    # NEW: Fetch GenomeDataLakeTables object from workspace
    datalake_obj = self.dfu.get_objects({
        'object_refs': [input_ref]
    })['data'][0]['data']

    # NEW: Extract data and generate JSON files
    genes_data = extract_genes_data(datalake_obj)
    metadata = extract_metadata(datalake_obj)
    tree_data = extract_tree_data(datalake_obj)
    # ... etc

    # NEW: Write JSON files to heatmap directory
    with open(os.path.join(heatmap_dir, 'genes_data.json'), 'w') as f:
        json.dump(genes_data, f)
    # ... etc

    # Upload to Shock (as before)
```

**Pros:**
- Clean separation: data extraction in Python, visualization in JavaScript
- Heatmap viewer remains a static HTML/JS app (no KBase API calls)
- Easy to test locally (just generate JSON files)

**Cons:**
- Need to understand GenomeDataLakeTables object structure
- Need to implement extraction functions for each data type
- Increases app execution time (but only by seconds)

---

#### Option 2: Fetch Data in JavaScript

**Modify:** `data/heatmap/kbase-data-loader.js`

```javascript
// Load UPA from app-config.json
const config = await fetch('app-config.json').then(r => r.json());
const upa = config.upa;

// Fetch GenomeDataLakeTables object from KBase workspace
const datalakeObj = await fetchFromWorkspace(upa);

// Extract data
const genesData = extractGenesData(datalakeObj);
const metadata = extractMetadata(datalakeObj);
// ... etc

// Use the data (no need to load JSON files)
```

**Pros:**
- No Python code changes needed
- Faster Python execution (just copies static viewer)
- Data extraction happens in the browser

**Cons:**
- Heatmap viewer now depends on KBase authentication
- Requires understanding KBase JavaScript API
- Harder to test locally (need KBase auth token)
- May expose sensitive workspace data to browser

---

## Immediate Next Steps

### Step 1: Understand GenomeDataLakeTables Structure

**Question for Chris Henry:** What is the structure of a GenomeDataLakeTables object?

Example questions:
- Does it have a `genome_features` table with gene data?
- Does it store pangenome cluster assignments?
- Does it include phylogenetic tree data?
- How are RAST/KO/GO annotations stored?
- Is there a reference to the user genome?

**Find the type spec:**
```bash
# Search for GenomeDataLakeTables type definition
grep -r "typedef.*GenomeDataLakeTables" ~/repos/
```

---

### Step 2: Write Data Extraction Functions

**Create:** `lib/KBDatalakeDashboard/data_extractor.py`

```python
def extract_genes_data(datalake_obj):
    """
    Extract gene data in the format expected by genome-heatmap-viewer.

    Expected output format (from genes_data.json):
    [
        [id, fid, length, start, strand, conservation_frac, pan_category,
         function, n_ko, n_cog, n_pfam, n_go, localization, rast_cons,
         ko_cons, go_cons, ec_cons, avg_cons, bakta_cons, ec_avg_cons,
         specificity],
        ...
    ]
    """
    genes = []
    # TODO: Extract from datalake_obj['genome_features'] or similar
    return genes

def extract_metadata(datalake_obj):
    """Extract organism metadata."""
    return {
        "organism": datalake_obj.get('organism_name', 'Unknown'),
        "genome_id": datalake_obj.get('genome_id', 'Unknown'),
        # ... etc
    }

def extract_tree_data(datalake_obj):
    """Extract phylogenetic tree data."""
    # TODO: Check if tree data is in datalake_obj
    return {}

def extract_reactions_data(datalake_obj):
    """Extract metabolic reactions."""
    # TODO: Check if metabolic model is in datalake_obj
    return []
```

---

### Step 3: Test Data Extraction

**Create:** `test_data_extraction.py`

```python
from installed_clients.DataFileUtilClient import DataFileUtil
from lib.KBDatalakeDashboard.data_extractor import extract_genes_data

# Fetch a GenomeDataLakeTables object
dfu = DataFileUtil(callback_url)
obj = dfu.get_objects({'object_refs': ['76990/8/1']})['data'][0]['data']

# Test extraction
genes = extract_genes_data(obj)
print(f"Extracted {len(genes)} genes")
print(f"First gene: {genes[0]}")
```

---

### Step 4: Integrate into App

**Modify:** `lib/KBDatalakeDashboard/KBDatalakeDashboardImpl.py`

```python
from .data_extractor import (
    extract_genes_data,
    extract_metadata,
    extract_tree_data,
    extract_reactions_data
)

def run_genome_datalake_dashboard(self, ctx, params):
    # ... existing setup ...

    # Fetch the GenomeDataLakeTables object
    print(f"Fetching GenomeDataLakeTables object: {input_ref}")
    datalake_obj = self.dfu.get_objects({
        'object_refs': [input_ref]
    })['data'][0]['data']

    # Copy static HTML (as before)
    shutil.copytree('/kb/module/data/html', output_directory)
    shutil.copytree('/kb/module/data/heatmap', heatmap_dir)

    # Extract and write data JSON files
    print("Extracting genes data...")
    genes_data = extract_genes_data(datalake_obj)
    with open(os.path.join(heatmap_dir, 'genes_data.json'), 'w') as f:
        json.dump(genes_data, f)

    print("Extracting metadata...")
    metadata = extract_metadata(datalake_obj)
    with open(os.path.join(heatmap_dir, 'metadata.json'), 'w') as f:
        json.dump(metadata, f)

    # ... etc for tree_data, reactions_data, etc

    # Upload and create report (as before)
```

---

## Testing Strategy

### Local Testing (Before KBase Deployment)

1. **Get a sample GenomeDataLakeTables object:**
   ```python
   from installed_clients.WorkspaceClient import Workspace
   ws = Workspace(workspace_url)
   obj = ws.get_objects2({'objects': [{'ref': '76990/8/1'}]})
   ```

2. **Save to local file:**
   ```python
   import json
   with open('sample_datalake_obj.json', 'w') as f:
       json.dump(obj['data'][0]['data'], f, indent=2)
   ```

3. **Test extraction:**
   ```python
   from data_extractor import extract_genes_data
   with open('sample_datalake_obj.json') as f:
       obj = json.load(f)
   genes = extract_genes_data(obj)
   assert len(genes) > 0, "No genes extracted!"
   ```

4. **Compare with expected format:**
   ```bash
   # Compare field counts
   python -c "import json; print(len(json.load(open('genes_data.json'))[0]))"
   # Should print: 21 (fields per gene)
   ```

### KBase Testing

1. **Deploy to AppDev environment**
2. **Run app with known GenomeDataLakeTables object**
3. **Check logs for extraction success**
4. **Open heatmap viewer and verify:**
   - All tracks display correctly
   - Gene counts match the selected genome
   - Organism name matches metadata
   - Tree shows correct reference genomes

---

## Questions for Chris Henry

1. **GenomeDataLakeTables structure:**
   - What is the exact object type? (Is it in KBaseGenomeAnnotations or a custom type?)
   - What tables/fields does it contain?
   - Is there a spec file or example object we can inspect?

2. **Data availability:**
   - Does every GenomeDataLakeTables object have all the data we need?
   - Genes, annotations, pangenome clusters, phylogenetic tree, metabolic model?
   - Or do some objects only have a subset?

3. **Reference genomes:**
   - How are reference genomes identified in the object?
   - Is there a list of genome IDs for the pangenome?

4. **Metabolic model:**
   - Is the metabolic model included in GenomeDataLakeTables?
   - Or do we need to fetch it separately from another object type?

5. **Testing:**
   - What's a good example UPA to use for testing?
   - Are there multiple GenomeDataLakeTables objects in AppDev we can test with?

---

## Timeline

**Current status (commit 154c7a3):**
- ✅ Async execution works
- ✅ BERDL tables viewer works correctly
- ⚠️ Heatmap viewer shows hardcoded Acinetobacter data only

**Next milestone:**
- Understand GenomeDataLakeTables structure (1-2 hours with Chris)
- Write data extraction functions (4-8 hours)
- Test locally (2-4 hours)
- Deploy and validate in KBase (1-2 hours)

**Total estimated time:** 1-2 days

---

## Files to Modify

**New files:**
- `lib/KBDatalakeDashboard/data_extractor.py` - Data extraction logic

**Modified files:**
- `lib/KBDatalakeDashboard/KBDatalakeDashboardImpl.py` - Add data extraction

**Files to remove (after dynamic loading works):**
- `data/heatmap/genes_data.json` (static)
- `data/heatmap/metadata.json` (static)
- `data/heatmap/tree_data.json` (static)
- `data/heatmap/reactions_data.json` (static)
- `data/heatmap/summary_stats.json` (static)

These will be generated dynamically per job instead.

---

## Success Criteria

The implementation is complete when:
1. ✅ User selects any GenomeDataLakeTables object
2. ✅ Heatmap viewer shows data from THAT genome (not hardcoded Acinetobacter)
3. ✅ All tracks display correctly for any genome
4. ✅ Organism name in metadata matches the selected genome
5. ✅ Multiple users can run the app simultaneously with different genomes
6. ✅ No hardcoded data files in the heatmap directory
