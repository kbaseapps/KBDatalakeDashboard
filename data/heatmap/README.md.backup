# Genome Feature Profiler

A multi-view genome visualization tool that profiles gene features across conservation, function consistency, annotation depth, and pangenome structure. Built for KBase/BERDL integration.

## Overview

This tool takes a BERDL SQLite database (produced by [KBDatalakeApps](https://github.com/kbaseapps/KBDatalakeApps)) and renders three interactive views of the user genome's 4,617 genes:

| View | What it shows |
|------|---------------|
| **Tracks** | Multi-track heatmap — each horizontal band maps a different gene property (conservation, consistency, annotation counts, etc.) across the full genome |
| **Tree** | UPGMA dendrogram of 36 genomes (Jaccard distance on pangenome clusters) with per-clade gene/cluster/core-% stat bars |
| **Clusters** | UMAP scatter plot of genes in two embedding spaces (gene features and presence/absence), colored by any track |

## Workflow

```
SQLite DB                  Python scripts              JSON data files           Viewer
─────────────             ────────────────            ─────────────────         ────────
berdl_tables_             generate_tree_data.py  ──>  tree_data.json
  ontology_terms.db       generate_cluster_data.py -> cluster_data.json
                          (genes_data.json extracted   genes_data.json
                           from DB via notebook)
                                                       config.json  ──────────> index.html
                                                       (track definitions,       (pure HTML/
                                                        field mappings,           CSS/JS +
                                                        sort presets)             Canvas API)
```

### Step 1: Extract gene data

Gene data is extracted from the BERDL SQLite database into `genes_data.json`. Each gene is a compact array of 29 fields (see [Data Format](#data-format) below). The extraction is done via the notebook in `notebooks/`.

### Step 2: Generate derived data

```bash
# Requires: numpy, scipy
python3 generate_tree_data.py

# Requires: numpy, umap-learn
python3 generate_cluster_data.py
```

These scripts read the SQLite DB and `genes_data.json` to produce:
- `tree_data.json` — UPGMA linkage, genome metadata, Jaccard distance matrix
- `cluster_data.json` — UMAP 2D embeddings (gene-features and presence/absence)

### Step 3: Run the viewer

```bash
python3 -m http.server 8889
# Open http://localhost:8889
```

No build step required — the viewer is a single `index.html` file with inline CSS and JavaScript.

## Configuration

All genome-specific settings live in `config.json`:

```json
{
  "title": "Genome Heatmap Viewer",
  "organism": "Escherichia coli K-12 MG1655",
  "genome_id": "562.61143",
  "n_ref_genomes": 35,
  "data_files": { "genes": "genes_data.json", "tree": "tree_data.json", "cluster": "cluster_data.json" },
  "fields": { "ID": 0, "FID": 1, "LENGTH": 2, ... },
  "tracks": [ ... ],
  "sort_presets": [ ... ],
  "analysis_views": [ ... ]
}
```

To use with a different genome, update `config.json` and regenerate the three JSON data files from the new SQLite database.

## Features

### Tracks Tab
- **24 data tracks** covering conservation, consistency (6 sources), annotation counts (KO, COG, Pfam, GO, EC), localization, pangenome category, and more
- **6 placeholder tracks** for future data (flux, phenotypes, fitness)
- **6 analysis view presets** (Characterization Targets, Annotation Quality, Metabolic Landscape, etc.)
- **7 sort presets** (genome order, conservation, consistency, annotation depth, etc.)
- **Genome minimap** navigation bar with draggable viewport
- Gene search with minimap highlight markers
- Zoom slider (1x to 100x)
- Hover tooltips with full gene details

### Tree Tab
- UPGMA dendrogram from Jaccard distances on pangenome cluster presence/absence
- Sqrt-scaled branch lengths for visual clarity
- Collapsible stat bars: gene count, cluster count, core % per genome
- Click any genome leaf to see its metadata

### Clusters Tab
- UMAP 2D scatter plot with two embedding modes:
  - **Gene Features**: conservation, consistency scores, annotation counts
  - **Presence/Absence**: pangenome cluster membership across reference genomes
- Color by any track (conservation, core/accessory, hypothetical, etc.)
- Hover tooltips with gene details
- Point count and legend

## Data Format

`genes_data.json` contains an array of 4,617 gene arrays, each with 29 fields:

| Index | Field | Type | Description |
|-------|-------|------|-------------|
| 0 | id | int | Gene order in genome |
| 1 | fid | string | Feature ID (e.g., `562.61143.CDS.1234`) |
| 2 | length | int | Gene length (bp) |
| 3 | start | int | Start position |
| 4 | strand | string | `"+"` or `"-"` |
| 5 | conservation_frac | float | Fraction of reference genomes with this cluster (0-1) |
| 6 | pan_category | int | 0=Unknown, 1=Accessory, 2=Core |
| 7 | function | string | RAST functional annotation |
| 8-11 | n_ko, n_cog, n_pfam, n_go | int | Ontology term counts |
| 12 | localization | string | PSORTb prediction |
| 13-18 | rast/ko/go/ec/avg/bakta_cons | float | Consistency scores (-1=N/A, 0=disagree, 1=agree) |
| 19 | ec_avg_cons | float | EC + EC-mapped average consistency |
| 20 | specificity | float | Annotation specificity (0-1) |
| 21 | is_hypo | int | 1 if hypothetical protein |
| 22 | has_name | int | 1 if gene has a name |
| 23 | n_ec | int | EC number count |
| 24 | agreement | int | RAST/Bakta agreement (0-3) |
| 25 | cluster_size | int | Pangenome cluster size |
| 26 | n_modules | int | KEGG module hits |
| 27 | ec_map_cons | float | EC-mapped consistency |
| 28 | prot_len | int | Protein length (aa) |

### Consistency Scores

Each consistency score compares the user genome's annotation for a gene against other genes in the same pangenome cluster:
- **1.0** (green): all genes in cluster agree on annotation
- **0.0** (red): no agreement
- **-1.0** (gray): not applicable (no data)

## Data Source

Data is extracted from BERDL SQLite databases (`berdl_tables_ontology_terms.db`), produced by the [KBDatalakeApps](https://github.com/kbaseapps/KBDatalakeApps) pipeline.

Key tables:
- `genome_features` — gene positions, lengths, strands, inline ontology terms (KO, COG, GO, Pfam, EC as semicolon-separated)
- `pan_genome_features` — pangenome cluster assignments per genome
- `genome` — genome metadata (name, accession, taxonomy)

## Testing

60 Playwright tests validate both functionality and scientific correctness:

```bash
npm install
npx playwright install chromium
npx playwright test
```

Test suites:
- **Tracks Functionality** — track toggle, sort, zoom, search, minimap
- **Tracks Data Correctness** — field counts, gene IDs, value ranges
- **Tree Functionality** — SVG rendering, stat bar toggling, distance scale
- **Tree Data Correctness** — genome count, distance ranges, stat bar headers
- **Cluster Functionality** — embedding toggle, color-by selection, point rendering
- **Cluster Data Correctness** — point count, coordinate ranges, tooltip content
- **Scientific Correctness** — core fraction 60-90%, strand balance ~50%, protein lengths 20-3000aa, conservation sort ordering
- **Tab Navigation** — switching between tabs, KPI persistence

## Architecture

- **Pure vanilla HTML/CSS/JS** — no frameworks, no build step
- **Canvas API** for heatmap and minimap rendering
- **SVG** for dendrogram and stat bars
- **Canvas** for UMAP scatter plot
- **Config-driven** — all tracks, sorts, and views defined in `config.json`

## Current Organism

**Escherichia coli K-12 MG1655** (GCF_000005845.2)
- 4,617 genes
- 35 reference genomes in pangenome
- Genome ID: 562.61143

## License

See KBase license terms.
