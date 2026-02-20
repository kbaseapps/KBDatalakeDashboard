"""
Extract data from GenomeDataLakeTables SQLite database for the Datalake Dashboard.

Supports the new DB schema (2026+) with:
- user_feature / pangenome_feature tables
- Dynamic ontology columns (ontology_KEGG, ontology_COG, etc.)
- kind='user' genome detection
- genome_reaction, genome_gene_reaction_essentially_test, gene_phenotype tables

Produces JSON files matching the format expected by the genome-heatmap-viewer:
- genes_data.json (42-field arrays)
- metadata.json
- tree_data.json (linkage matrix + genome metadata)
- reactions_data.json
- summary_stats.json
- ref_genomes_data.json
"""

import json
import logging
import os
import re
import sqlite3
from collections import defaultdict

logger = logging.getLogger(__name__)

# Localization categories (must match config.json categories.localization)
LOC_CATEGORIES = [
    "Cytoplasmic", "CytoMembrane", "Periplasmic",
    "OuterMembrane", "Extracellular", "Unknown",
]
LOC_MAP = {name: i for i, name in enumerate(LOC_CATEGORIES)}
LOC_MAP.update({
    "CytoplasmicMembrane": 1,
    "Cytoplasmic Membrane": 1,
    "Outer Membrane": 3,
})


# ── Helpers ──────────────────────────────────────────────────────────────


def count_terms(value):
    """Count semicolon-separated terms in a string."""
    if not value or not str(value).strip():
        return 0
    return len([t for t in str(value).split(";") if t.strip()])


def safe_get(row, col, default=None):
    """Safely get a column value from a sqlite3.Row, returning default if missing."""
    try:
        val = row[col]
        return val if val is not None else default
    except (IndexError, KeyError):
        return default


def parse_cluster_ids(raw):
    """Parse pangenome_cluster value, handling new format with :size suffix.

    Old format: 'clusterA; clusterB'
    New format: 'clusterA:6; clusterB:41'

    Returns list of bare cluster IDs (without size suffix).
    """
    if not raw or not str(raw).strip():
        return []
    parts = []
    for part in str(raw).split(";"):
        part = part.strip()
        if not part:
            continue
        # Strip :size suffix if present
        if ":" in part:
            part = part.rsplit(":", 1)[0].strip()
        parts.append(part)
    return parts


def get_ontology_columns(conn, table_name):
    """Discover ontology_* columns in a table via PRAGMA.

    Returns dict mapping short names to actual column names:
    {'KEGG': 'ontology_KEGG', 'COG': 'ontology_COG', ...}
    """
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    ontology_cols = {}
    for row in cursor.fetchall():
        col_name = row[1]  # column name is index 1
        if col_name.startswith("ontology_"):
            short = col_name.replace("ontology_", "")
            ontology_cols[short] = col_name
    return ontology_cols


def is_hypothetical(func):
    """Check if a function string indicates a generic hypothetical protein."""
    if not func or not func.strip():
        return True
    fl = func.strip().lower()
    if fl == "hypothetical protein":
        return True
    if fl.startswith("fig") and fl.endswith("hypothetical protein"):
        return True
    return False


def jaccard_similarity(vec_a, vec_b):
    """Compute Jaccard similarity between two binary vectors."""
    intersection = sum(1 for a, b in zip(vec_a, vec_b) if a == 1 and b == 1)
    union = sum(1 for a, b in zip(vec_a, vec_b) if a == 1 or b == 1)
    return intersection / union if union > 0 else 0.0


def build_user_pheno_vector(conn, user_genome_id, phenotype_ids):
    """Build P/N vector for user genome matching reference phenotype order."""
    pheno_map = {}
    for row in conn.execute(
        "SELECT phenotype_id, class FROM genome_phenotype WHERE genome_id = ?",
        (user_genome_id,)
    ):
        pheno_map[row["phenotype_id"]] = 1 if row["class"] == "P" else 0
    return [pheno_map.get(pid, 0) for pid in phenotype_ids]


def compute_consistency(user_annotation, cluster_annotations):
    """Compute consistency: fraction of cluster members matching user annotation."""
    if not cluster_annotations:
        return -1
    if not user_annotation or not str(user_annotation).strip():
        return -1
    matches = sum(1 for ann in cluster_annotations if ann == user_annotation)
    return round(matches / len(cluster_annotations), 4)


def compute_specificity(func, gene_names, ko, ec, cog, pfam, go):
    """Compute annotation specificity (0.0-1.0)."""
    if not func or not func.strip():
        return 0.0
    fl = func.lower().strip()
    if fl == "hypothetical protein":
        return 0.0

    signals = []
    if ec and str(ec).strip():
        signals.append(0.9)
    if ko and str(ko).strip():
        signals.append(0.7)
    if gene_names and str(gene_names).strip():
        signals.append(0.6)
    if cog and str(cog).strip():
        signals.append(0.5)
    if go and str(go).strip():
        signals.append(0.5)
    if pfam and str(pfam).strip():
        signals.append(0.4)

    base = max(signals) if signals else 0.3

    if "ec " in fl or "(ec " in fl:
        base = min(1.0, base + 0.1)

    if "conserved protein" in fl and "unknown" in fl:
        base = min(base, 0.2)
    elif any(w in fl for w in ["hypothetical", "uncharacterized", "duf"]):
        base = min(base, 0.3)
    elif any(w in fl for w in ["putative", "predicted", "probable", "possible"]):
        base = min(base, 0.5)

    return round(base, 4)


def parse_taxonomy(raw_tax):
    """Parse GTDB/NCBI taxonomy string into structured dict.

    Input:  'd__Bacteria;p__Pseudomonadota;c__Gammaproteobacteria;...'
    Output: {'domain': 'Bacteria', 'phylum': 'Pseudomonadota', ...}
    """
    if not raw_tax or raw_tax == "Unknown":
        return {}
    ranks = {"d": "domain", "p": "phylum", "c": "class", "o": "order",
             "f": "family", "g": "genus", "s": "species"}
    result = {}
    for part in str(raw_tax).split(";"):
        part = part.strip()
        if "__" in part:
            prefix, value = part.split("__", 1)
            rank = ranks.get(prefix.strip())
            if rank and value.strip():
                result[rank] = value.strip()
    return result


def extract_gene_name(aliases, fid):
    """Extract short gene name from aliases string.

    Aliases format: 'alias:GeneID:944742;alias:thrL;alias:b0001;alias:NP_414542.1;...'
    Returns the best short gene name (e.g., 'thrL'), or empty string.
    """
    if not aliases or not str(aliases).strip():
        return ""
    candidates = []
    for part in str(aliases).split(";"):
        part = part.strip()
        if part.startswith("alias:"):
            part = part[6:]  # strip 'alias:' prefix
        part = part.strip()
        if not part:
            continue
        # Skip identifiers that aren't gene names
        if part == fid:
            continue
        if ":" in part:  # UniProtKB:..., GeneID:..., ASAP:...
            continue
        if part.startswith("NP_") or part.startswith("WP_") or part.startswith("YP_"):
            continue
        if part.startswith("GI:") or part.startswith("GeneID"):
            continue
        if part.startswith("ECK") or part.startswith("JW"):  # E.coli systematic names
            continue
        if part.startswith("EcoGene"):
            continue
        candidates.append(part)
    if not candidates:
        return ""
    # Prefer the first candidate with 3+ chars (typical gene names: dnaA, recA, thrB)
    for c in candidates:
        if len(c) >= 3:
            return c
    return candidates[0]


def derive_organism_name(user_genome_id, gtdb_taxonomy, ncbi_taxonomy):
    """Derive a human-readable organism name from available metadata.

    Priority: GTDB species name > NCBI species name > parsed genome ID.
    """
    # Try GTDB taxonomy (e.g., 'd__Bacteria;...;s__Escherichia coli')
    for taxonomy in [gtdb_taxonomy, ncbi_taxonomy]:
        if taxonomy and taxonomy.strip():
            # Extract species from taxonomy string
            parts = taxonomy.split(";")
            for part in reversed(parts):
                part = part.strip()
                if part.startswith("s__") and len(part) > 3:
                    return part[3:]

    # Parse from genome ID (e.g., 'user_GCF_000005845.2.RAST')
    name = user_genome_id.replace("user_", "").replace("_RAST", "")
    name = name.replace("_", " ")
    name = re.sub(r'\bK12\b', 'K-12', name)
    return name


# ── Main extraction functions ────────────────────────────────────────────


def get_user_genome_id(db_path):
    """Determine the user genome ID from the database.

    Tries: kind='user' in genome table, then LIKE 'user_%' fallback.
    """
    conn = sqlite3.connect(db_path)

    # New schema: kind='user'
    try:
        row = conn.execute(
            "SELECT genome FROM genome WHERE kind = 'user' LIMIT 1"
        ).fetchone()
        if row:
            conn.close()
            return row[0]
    except sqlite3.OperationalError:
        pass

    # Old schema fallback: LIKE 'user_%'
    try:
        row = conn.execute(
            "SELECT genome FROM genome WHERE genome LIKE 'user_%' LIMIT 1"
        ).fetchone()
        if row:
            conn.close()
            return row[0]
    except sqlite3.OperationalError:
        pass

    # Legacy schema fallback
    try:
        row = conn.execute(
            "SELECT id FROM genome WHERE id LIKE 'user_%' LIMIT 1"
        ).fetchone()
        if row:
            conn.close()
            return row[0]
    except sqlite3.OperationalError:
        pass

    conn.close()
    raise ValueError("Could not determine user genome ID from database")


def extract_genes_data(db_path, user_genome_id):
    """Extract gene data as 42-field arrays for genes_data.json.

    Field indices match config.json:
    [0]  ID           [1]  FID          [2]  LENGTH       [3]  START
    [4]  STRAND       [5]  CONS_FRAC    [6]  PAN_CAT      [7]  FUNC
    [8]  N_KO         [9]  N_COG        [10] N_PFAM       [11] N_GO
    [12] LOC          [13] RAST_CONS    [14] KO_CONS      [15] GO_CONS
    [16] EC_CONS      [17] AVG_CONS     [18] BAKTA_CONS   [19] EC_AVG_CONS
    [20] SPECIFICITY  [21] IS_HYPO      [22] HAS_NAME     [23] N_EC
    [24] AGREEMENT    [25] CLUSTER_SIZE [26] N_MODULES     [27] EC_MAP_CONS
    [28] PROT_LEN     [29] REACTIONS    [30] RICH_FLUX     [31] RICH_CLASS
    [32] MIN_FLUX     [33] MIN_CLASS    [34] PSORTB_NEW    [35] ESSENTIALITY
    [36] GENE_NAME    [37] N_PHENOTYPES [38] N_FITNESS     [39] FITNESS_AVG
    [40] N_FITNESS_AGREE  [41] FITNESS_AGREE_PCT
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Discover ontology columns in user_feature
    ont_cols = get_ontology_columns(conn, "user_feature")
    logger.info(f"Ontology columns in user_feature: {list(ont_cols.keys())}")

    # Also discover columns in pangenome_feature for consistency computation
    pf_ont_cols = get_ontology_columns(conn, "pangenome_feature")
    logger.info(f"Ontology columns in pangenome_feature: {list(pf_ont_cols.keys())}")

    # ── Count reference genomes in pangenome ────────────────────────────
    n_ref = conn.execute(
        "SELECT COUNT(DISTINCT genome) FROM pangenome_feature"
    ).fetchone()[0]
    logger.info(f"{n_ref} reference genomes in pangenome")

    # ── Load pangenome cluster data ─────────────────────────────────────
    logger.info("Loading pangenome cluster data...")

    # cluster -> set of genome_ids (for conservation)
    cluster_genomes = defaultdict(set)
    for row in conn.execute("SELECT cluster, genome FROM pangenome_feature"):
        cluster_genomes[row["cluster"]].add(row["genome"])

    # cluster -> gene count (for cluster_size)
    cluster_size = {}
    for row in conn.execute(
        "SELECT cluster, COUNT(*) as cnt FROM pangenome_feature GROUP BY cluster"
    ):
        cluster_size[row["cluster"]] = row["cnt"]

    # cluster -> is_core flag
    cluster_is_core = {}
    for row in conn.execute(
        "SELECT DISTINCT cluster FROM pangenome_feature WHERE is_core = 1"
    ):
        cluster_is_core[row["cluster"]] = True

    # ── Load cluster annotations for consistency computation ────────────
    logger.info("Loading cluster annotations for consistency computation...")

    # Build SELECT with available ontology columns from pangenome_feature
    pf_select_cols = ["cluster", "genome"]
    consistency_sources = {}  # Maps source name to pangenome_feature column

    for source, uf_col, pf_col_key in [
        ("RAST", "RAST", "RAST"),
        ("KEGG", "KEGG", "KEGG"),
        ("GO", "GO", "GO"),
        ("EC", "EC", "EC"),
        ("bakta_product", "bakta_product", "bakta_product"),
    ]:
        if pf_col_key in pf_ont_cols:
            pf_select_cols.append(pf_ont_cols[pf_col_key])
            consistency_sources[source] = pf_ont_cols[pf_col_key]

    cluster_ref_genes = defaultdict(list)
    if len(pf_select_cols) > 2:
        query = f"SELECT {', '.join(pf_select_cols)} FROM pangenome_feature WHERE cluster IS NOT NULL"
        for row in conn.execute(query):
            cluster_ref_genes[row["cluster"]].append(dict(row))

    logger.info(f"Loaded annotations for {len(cluster_ref_genes)} clusters")

    # ── Load essentiality data ──────────────────────────────────────────
    logger.info("Loading essentiality data...")
    gene_essentiality = {}
    gene_flux = {}
    try:
        for row in conn.execute("""
            SELECT gene_id,
                   AVG(CASE WHEN rich_media_class = 'essential' THEN 1.0
                            WHEN rich_media_class = 'variable' THEN 0.5
                            ELSE 0.0 END) as avg_ess,
                   MAX(rich_media_flux) as max_rich_flux,
                   MAX(CASE WHEN rich_media_class IS NOT NULL THEN rich_media_class ELSE '' END) as rich_class,
                   MAX(minimal_media_flux) as max_min_flux,
                   MAX(CASE WHEN minimal_media_class IS NOT NULL THEN minimal_media_class ELSE '' END) as min_class
            FROM genome_gene_reaction_essentially_test
            WHERE genome_id = ?
            GROUP BY gene_id
        """, (user_genome_id,)):
            gene_essentiality[row["gene_id"]] = round(row["avg_ess"], 4) if row["avg_ess"] is not None else -1
            gene_flux[row["gene_id"]] = {
                "rich_flux": row["max_rich_flux"] if row["max_rich_flux"] is not None else -1,
                "rich_class": row["rich_class"] or "",
                "min_flux": row["max_min_flux"] if row["max_min_flux"] is not None else -1,
                "min_class": row["min_class"] or "",
            }
        logger.info(f"  {len(gene_essentiality)} genes with essentiality data")
    except sqlite3.OperationalError:
        logger.info("  (essentiality table not found)")

    # ── Load reaction assignments per gene ──────────────────────────────
    logger.info("Loading reaction assignments...")
    gene_reactions = defaultdict(set)
    try:
        for row in conn.execute("""
            SELECT genes, reaction_id
            FROM genome_reaction
            WHERE genome_id = ?
        """, (user_genome_id,)):
            gene_str = row["genes"] or ""
            rxn_id = row["reaction_id"]
            # Extract gene IDs from boolean expression like "geneA or (geneB and geneC)"
            tags = re.findall(r"[A-Za-z][A-Za-z0-9_]+", gene_str)
            tags = [t for t in tags if t.lower() not in ("or", "and")]
            for tag in tags:
                gene_reactions[tag].add(rxn_id)
        logger.info(f"  {len(gene_reactions)} genes with reaction assignments")
    except sqlite3.OperationalError:
        logger.info("  (genome_reaction table not found)")

    # ── Load phenotype data ─────────────────────────────────────────────
    logger.info("Loading phenotype data...")
    gene_phenotype_counts = defaultdict(set)
    gene_fitness_counts = defaultdict(int)
    gene_fitness_avg_sum = defaultdict(float)
    gene_fitness_avg_count = defaultdict(int)
    gene_fitness_agree = defaultdict(int)
    gene_fitness_scored = defaultdict(int)
    try:
        for row in conn.execute("""
            SELECT gene_id, phenotype_id, fitness_match, fitness_avg, essentiality_fraction
            FROM gene_phenotype
            WHERE genome_id = ?
        """, (user_genome_id,)):
            gene_phenotype_counts[row["gene_id"]].add(row["phenotype_id"])
            if row["fitness_match"] == "has_score":
                gene_fitness_counts[row["gene_id"]] += 1
                if row["fitness_avg"] is not None:
                    gene_fitness_avg_sum[row["gene_id"]] += row["fitness_avg"]
                    gene_fitness_avg_count[row["gene_id"]] += 1
                # Model-fitness agreement
                gene_fitness_scored[row["gene_id"]] += 1
                model_essential = (row["essentiality_fraction"] or 0) > 0
                fitness_harmful = (row["fitness_avg"] or 0) < 0
                if model_essential == fitness_harmful:
                    gene_fitness_agree[row["gene_id"]] += 1
        logger.info(f"  {len(gene_phenotype_counts)} genes with phenotype data")
    except sqlite3.OperationalError:
        logger.info("  (gene_phenotype table not found)")

    # ── Load user genome features ───────────────────────────────────────
    logger.info(f"Loading user features for {user_genome_id}...")
    feature_rows = conn.execute("""
        SELECT * FROM user_feature
        WHERE genome = ? AND type = 'gene'
        ORDER BY start, feature_id
    """, (user_genome_id,)).fetchall()
    logger.info(f"  {len(feature_rows)} gene features loaded")

    # ── Process each gene ───────────────────────────────────────────────
    logger.info("Processing genes...")
    flux_class_map = {"essential": 0, "variable": 1, "blocked": 2,
                      "forward_only": 1, "reverse_only": 1}
    genes = []

    for order_idx, row in enumerate(feature_rows):
        fid = row["feature_id"]
        length = row["length"]
        start = row["start"]
        strand = 1 if row["strand"] == "+" else 0

        # FUNC: use bakta_product (RAST not available in new schema)
        bakta_func = safe_get(row, ont_cols.get("bakta_product", ""), "")
        rast_func = safe_get(row, ont_cols.get("RAST", ""), "")
        func = rast_func if rast_func and str(rast_func).strip() else bakta_func
        if not func or not str(func).strip():
            func = "hypothetical protein"

        # Ontology term counts
        n_ko = count_terms(safe_get(row, ont_cols.get("KEGG", ""), ""))
        n_cog = count_terms(safe_get(row, ont_cols.get("COG", ""), ""))
        n_pfam = count_terms(safe_get(row, ont_cols.get("PFAM", ""), ""))
        n_go = count_terms(safe_get(row, ont_cols.get("GO", ""), ""))
        n_ec = count_terms(safe_get(row, ont_cols.get("EC", ""), ""))

        # Localization (PSORTb)
        psortb = safe_get(row, ont_cols.get("primary_localization_psortb", ""), "Unknown") or "Unknown"
        loc = LOC_MAP.get(psortb, LOC_MAP["Unknown"])

        # Secondary localization
        psortb_new_str = safe_get(row, ont_cols.get("secondary_localization_psortb", ""), "Unknown") or "Unknown"
        psortb_new = LOC_MAP.get(psortb_new_str, LOC_MAP["Unknown"])

        # ── Pangenome cluster data ──────────────────────────────────────
        cluster_ids = parse_cluster_ids(row["pangenome_cluster"])
        is_core_raw = row["pangenome_is_core"]

        if cluster_ids:
            best_cons = 0
            best_size = 0
            any_core = False
            for cid in cluster_ids:
                n_with = len(cluster_genomes.get(cid, set()))
                ccons = n_with / n_ref if n_ref > 0 else 0
                if ccons > best_cons:
                    best_cons = ccons
                csize = cluster_size.get(cid, 0)
                if csize > best_size:
                    best_size = csize
                if cid in cluster_is_core:
                    any_core = True
            cons_frac = round(best_cons, 4)
            clust_size = best_size

            if is_core_raw == 1:
                pan_cat = 2
            elif is_core_raw == 0:
                pan_cat = 1
            else:
                pan_cat = 2 if any_core else 1
        else:
            cons_frac = 0
            pan_cat = 0
            clust_size = 0

        # ── Consistency scores ──────────────────────────────────────────
        if cluster_ids:
            all_rast_cons = []
            all_ko_cons = []
            all_go_cons = []
            all_ec_cons = []
            all_bakta_cons = []

            for cid in cluster_ids:
                ref_genes = cluster_ref_genes.get(cid, [])
                if not ref_genes:
                    continue

                # RAST consistency (if ontology_RAST exists)
                rast_col = consistency_sources.get("RAST")
                if rast_col and rast_func:
                    ref_vals = [g[rast_col] for g in ref_genes if g.get(rast_col)]
                    if ref_vals:
                        all_rast_cons.append(compute_consistency(rast_func, ref_vals))

                # KEGG consistency
                kegg_col = consistency_sources.get("KEGG")
                user_kegg = safe_get(row, ont_cols.get("KEGG", ""), "")
                if kegg_col and user_kegg:
                    ref_vals = [g[kegg_col] for g in ref_genes if g.get(kegg_col)]
                    if ref_vals:
                        all_ko_cons.append(compute_consistency(user_kegg, ref_vals))

                # GO consistency
                go_col = consistency_sources.get("GO")
                user_go = safe_get(row, ont_cols.get("GO", ""), "")
                if go_col and user_go:
                    ref_vals = [g[go_col] for g in ref_genes if g.get(go_col)]
                    if ref_vals:
                        all_go_cons.append(compute_consistency(user_go, ref_vals))

                # EC consistency
                ec_col = consistency_sources.get("EC")
                user_ec = safe_get(row, ont_cols.get("EC", ""), "")
                if ec_col and user_ec:
                    ref_vals = [g[ec_col] for g in ref_genes if g.get(ec_col)]
                    if ref_vals:
                        all_ec_cons.append(compute_consistency(user_ec, ref_vals))

                # Bakta consistency
                bakta_col = consistency_sources.get("bakta_product")
                if bakta_col and bakta_func:
                    ref_vals = [g[bakta_col] for g in ref_genes if g.get(bakta_col)]
                    if ref_vals:
                        all_bakta_cons.append(compute_consistency(bakta_func, ref_vals))

            rast_cons = max(all_rast_cons) if all_rast_cons else -1
            ko_cons = max(all_ko_cons) if all_ko_cons else -1
            go_cons = max(all_go_cons) if all_go_cons else -1
            ec_cons = max(all_ec_cons) if all_ec_cons else -1
            bakta_cons = max(all_bakta_cons) if all_bakta_cons else -1

            cons_scores = [s for s in [rast_cons, ko_cons, go_cons, ec_cons, bakta_cons] if s >= 0]
            avg_cons = round(sum(cons_scores) / len(cons_scores), 4) if cons_scores else -1
            ec_avg_cons = ec_cons
            ec_map_cons = -1
        else:
            rast_cons = ko_cons = go_cons = ec_cons = avg_cons = bakta_cons = ec_avg_cons = ec_map_cons = -1

        # ── Annotation specificity ──────────────────────────────────────
        aliases = safe_get(row, "aliases", "")
        if cluster_ids:
            specificity = compute_specificity(
                func, aliases,
                safe_get(row, ont_cols.get("KEGG", ""), ""),
                safe_get(row, ont_cols.get("EC", ""), ""),
                safe_get(row, ont_cols.get("COG", ""), ""),
                safe_get(row, ont_cols.get("PFAM", ""), ""),
                safe_get(row, ont_cols.get("GO", ""), ""),
            )
        else:
            specificity = -1

        # ── Derived fields ──────────────────────────────────────────────
        rast_is_hypo = is_hypothetical(rast_func) if rast_func else True
        bakta_is_hypo = is_hypothetical(bakta_func)
        is_hypo_val = 1 if rast_is_hypo and bakta_is_hypo else 0

        has_name = 1 if aliases and str(aliases).strip() else 0

        # AGREEMENT: RAST/Bakta (or KEGG/Bakta if no RAST)
        if rast_func and rast_func.strip():
            if rast_is_hypo and bakta_is_hypo:
                agreement = 0
            elif rast_is_hypo or bakta_is_hypo:
                agreement = 1
            elif rast_func.strip() == (bakta_func or "").strip():
                agreement = 3
            else:
                agreement = 2
        else:
            # No RAST: use KEGG vs Bakta
            user_kegg = safe_get(row, ont_cols.get("KEGG", ""), "")
            if not user_kegg and bakta_is_hypo:
                agreement = 0
            elif not user_kegg or bakta_is_hypo:
                agreement = 1
            else:
                agreement = 2  # Both have annotations but different sources

        # N_MODULES (skip if no KEGG data)
        n_modules = 0

        # Protein length
        protein_seq = safe_get(row, "protein_sequence", "")
        if protein_seq and len(protein_seq) > 10:
            prot_len = len(protein_seq)
        else:
            prot_len = length // 3 if length else 0

        # Reactions
        reactions = ";".join(sorted(gene_reactions.get(fid, set())))

        # Flux data
        flux_data = gene_flux.get(fid, {})
        rich_flux = flux_data.get("rich_flux", -1)
        rich_class = flux_class_map.get(flux_data.get("rich_class", ""), -1)
        min_flux = flux_data.get("min_flux", -1)
        min_class = flux_class_map.get(flux_data.get("min_class", ""), -1)

        # Essentiality
        essentiality = gene_essentiality.get(fid, -1)

        # Gene name
        gene_name = extract_gene_name(aliases, fid)

        # Phenotype data
        n_phenotypes = len(gene_phenotype_counts.get(fid, set()))
        n_fitness = gene_fitness_counts.get(fid, 0)
        if gene_fitness_avg_count.get(fid, 0) > 0:
            fitness_avg = round(gene_fitness_avg_sum[fid] / gene_fitness_avg_count[fid], 4)
        else:
            fitness_avg = -1

        # Model-fitness agreement
        n_agree = gene_fitness_agree.get(fid, 0)
        n_scored = gene_fitness_scored.get(fid, 0)
        agree_pct = round(n_agree / n_scored, 4) if n_scored > 0 else -1

        # ── Build 42-field gene array ───────────────────────────────────
        gene = [
            order_idx,      # [0]  ID
            fid,            # [1]  FID
            length,         # [2]  LENGTH (bp)
            start,          # [3]  START
            strand,         # [4]  STRAND
            cons_frac,      # [5]  CONS_FRAC
            pan_cat,        # [6]  PAN_CAT
            func,           # [7]  FUNC
            n_ko,           # [8]  N_KO
            n_cog,          # [9]  N_COG
            n_pfam,         # [10] N_PFAM
            n_go,           # [11] N_GO
            loc,            # [12] LOC
            rast_cons,      # [13] RAST_CONS
            ko_cons,        # [14] KO_CONS
            go_cons,        # [15] GO_CONS
            ec_cons,        # [16] EC_CONS
            avg_cons,       # [17] AVG_CONS
            bakta_cons,     # [18] BAKTA_CONS
            ec_avg_cons,    # [19] EC_AVG_CONS
            specificity,    # [20] SPECIFICITY
            is_hypo_val,    # [21] IS_HYPO
            has_name,       # [22] HAS_NAME
            n_ec,           # [23] N_EC
            agreement,      # [24] AGREEMENT
            clust_size,     # [25] CLUSTER_SIZE
            n_modules,      # [26] N_MODULES
            ec_map_cons,    # [27] EC_MAP_CONS
            prot_len,       # [28] PROT_LEN
            reactions,      # [29] REACTIONS
            rich_flux,      # [30] RICH_FLUX
            rich_class,     # [31] RICH_CLASS
            min_flux,       # [32] MIN_FLUX
            min_class,      # [33] MIN_CLASS
            psortb_new,     # [34] PSORTB_NEW
            essentiality,   # [35] ESSENTIALITY
            gene_name,      # [36] GENE_NAME
            n_phenotypes,   # [37] N_PHENOTYPES
            n_fitness,      # [38] N_FITNESS
            fitness_avg,    # [39] FITNESS_AVG
            n_agree,        # [40] N_FITNESS_AGREE
            agree_pct,      # [41] FITNESS_AGREE_PCT
        ]
        genes.append(gene)

    conn.close()
    logger.info(f"Processed {len(genes)} genes with {len(genes[0]) if genes else 0} fields each")
    return genes


def extract_metadata(db_path, user_genome_id, pangenome_id=""):
    """Extract organism metadata for metadata.json."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Query genome table for user genome
    row = conn.execute(
        "SELECT * FROM genome WHERE genome = ? LIMIT 1", (user_genome_id,)
    ).fetchone()

    gtdb_tax = safe_get(row, "gtdb_taxonomy", "") if row else ""
    ncbi_tax = safe_get(row, "ncbi_taxonomy", "") if row else ""

    # If user genome has no taxonomy, try alternatives:
    # 1. Use pangenome_id as a genome lookup (it's typically a reference genome ID)
    # 2. Use a clade_member genome (these are the close relatives)
    # 3. Fall back to clade representatives
    if not gtdb_tax and not ncbi_tax:
        if pangenome_id:
            pg_row = conn.execute(
                "SELECT gtdb_taxonomy, ncbi_taxonomy FROM genome WHERE genome = ? LIMIT 1",
                (pangenome_id,)
            ).fetchone()
            if pg_row:
                gtdb_tax = safe_get(pg_row, "gtdb_taxonomy", "")
                ncbi_tax = safe_get(pg_row, "ncbi_taxonomy", "")

    if not gtdb_tax and not ncbi_tax:
        clade_row = conn.execute(
            "SELECT gtdb_taxonomy, ncbi_taxonomy FROM genome WHERE kind = 'clade_member' LIMIT 1"
        ).fetchone()
        if clade_row:
            gtdb_tax = safe_get(clade_row, "gtdb_taxonomy", "")
            ncbi_tax = safe_get(clade_row, "ncbi_taxonomy", "")

    organism_name = derive_organism_name(user_genome_id, gtdb_tax, ncbi_tax)

    # Count genes
    n_genes = conn.execute(
        "SELECT COUNT(*) FROM user_feature WHERE genome = ? AND type = 'gene'",
        (user_genome_id,)
    ).fetchone()[0]

    # Count reference genomes (clade_member genomes in pangenome)
    n_ref_genomes = conn.execute(
        "SELECT COUNT(DISTINCT genome) FROM pangenome_feature"
    ).fetchone()[0]

    # Count contigs
    n_contigs = conn.execute(
        "SELECT COUNT(DISTINCT contig) FROM user_feature WHERE genome = ?",
        (user_genome_id,)
    ).fetchone()[0]

    metadata = {
        "organism": organism_name,
        "genome_id": user_genome_id,
        "pangenome_id": pangenome_id,
        "genome_assembly": pangenome_id or "Unknown",
        "n_ref_genomes": n_ref_genomes,
        "n_genes": n_genes,
        "n_contigs": n_contigs,
        "taxonomy": gtdb_tax or ncbi_tax or "Unknown",
        "database_type": "GenomeDataLakeTables",
    }

    conn.close()
    return metadata


def extract_tree_data(db_path, user_genome_id):
    """Extract phylogenetic tree data (Jaccard-based UPGMA).

    Computes distances from pangenome cluster presence/absence,
    builds UPGMA linkage, and collects genome metadata + ANI.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── Load cluster sets per genome ────────────────────────────────────
    logger.info("Loading cluster sets for tree computation...")

    # Reference genomes from pangenome_feature
    ref_clusters = defaultdict(set)
    for row in conn.execute("SELECT genome, cluster FROM pangenome_feature WHERE cluster IS NOT NULL"):
        ref_clusters[row["genome"]].add(row["cluster"])

    # User genome from user_feature
    user_clusters = set()
    for row in conn.execute("""
        SELECT pangenome_cluster FROM user_feature
        WHERE genome = ? AND pangenome_cluster IS NOT NULL
    """, (user_genome_id,)):
        for cid in parse_cluster_ids(row["pangenome_cluster"]):
            user_clusters.add(cid)

    logger.info(f"  User genome has {len(user_clusters)} clusters, {len(ref_clusters)} ref genomes")

    # Combine: user first, then refs sorted
    all_clusters_by_genome = {user_genome_id: user_clusters}
    for gid in sorted(ref_clusters.keys()):
        all_clusters_by_genome[gid] = ref_clusters[gid]

    genome_ids = list(all_clusters_by_genome.keys())
    n_genomes = len(genome_ids)

    if n_genomes < 2:
        conn.close()
        return {"genome_ids": genome_ids, "user_genome_id": user_genome_id,
                "stats": {"n_genomes": n_genomes, "n_clusters": len(user_clusters)}}

    # ── Build binary matrix and compute distances ───────────────────────
    all_cluster_ids = set()
    for clusters in all_clusters_by_genome.values():
        all_cluster_ids.update(clusters)
    all_cluster_ids = sorted(all_cluster_ids)
    cluster_to_idx = {cid: i for i, cid in enumerate(all_cluster_ids)}
    n_clusters = len(all_cluster_ids)

    try:
        import numpy as np
        from scipy.cluster.hierarchy import leaves_list, linkage
        from scipy.spatial.distance import pdist, squareform

        matrix = np.zeros((n_genomes, n_clusters), dtype=np.uint8)
        for gi, gid in enumerate(genome_ids):
            for cid in all_clusters_by_genome[gid]:
                if cid in cluster_to_idx:
                    matrix[gi, cluster_to_idx[cid]] = 1

        condensed = pdist(matrix, metric="jaccard")
        dist_matrix = squareform(condensed)
        Z = linkage(condensed, method="average")
        leaf_order = [genome_ids[i] for i in leaves_list(Z)]
        linkage_data = Z.tolist()

        stats = {
            "n_genomes": n_genomes,
            "n_clusters": n_clusters,
            "n_reference": n_genomes - 1,
            "max_distance": round(float(condensed.max()), 4),
            "min_distance": round(float(condensed.min()), 4),
        }
    except ImportError:
        logger.warning("numpy/scipy not available, skipping tree computation")
        leaf_order = genome_ids
        linkage_data = []
        stats = {"n_genomes": n_genomes, "n_clusters": n_clusters, "n_reference": n_genomes - 1}

    # ── Genome metadata ─────────────────────────────────────────────────
    genome_table = {}
    for row in conn.execute("SELECT * FROM genome"):
        genome_table[row["genome"]] = dict(row)

    # ANI data
    ani_data = {}
    try:
        for row in conn.execute(
            "SELECT genome1, genome2, ani FROM ani WHERE genome1 = ? OR genome2 = ?",
            (user_genome_id, user_genome_id)
        ):
            other = row["genome2"] if row["genome1"] == user_genome_id else row["genome1"]
            ani_data[other] = round(row["ani"], 4) if row["ani"] else None
    except sqlite3.OperationalError:
        pass

    # Per-genome phenotype data
    pheno_data = {}
    try:
        for row in conn.execute("""
            SELECT genome_id,
                   COUNT(CASE WHEN class = 'P' THEN 1 END) as positive,
                   COUNT(CASE WHEN class = 'N' THEN 1 END) as negative,
                   COUNT(*) as total
            FROM genome_phenotype GROUP BY genome_id
        """):
            pheno_data[row["genome_id"]] = {
                "positive_growth": row["positive"],
                "negative_growth": row["negative"],
                "total": row["total"],
            }
        logger.info(f"  Phenotype data for {len(pheno_data)} genomes")
    except sqlite3.OperationalError:
        logger.info("  (genome_phenotype table not found, skipping)")

    metadata = {}
    for gid in genome_ids:
        gdata = genome_table.get(gid, {})
        raw_tax = gdata.get("gtdb_taxonomy") or gdata.get("ncbi_taxonomy") or "Unknown"
        meta = {
            "taxonomy": raw_tax,
            "tax": parse_taxonomy(raw_tax),
            "n_features": gdata.get("size", 0),
            "ani_to_user": ani_data.get(gid) if gid != user_genome_id else 1.0,
        }
        if "kind" in gdata:
            meta["kind"] = gdata["kind"]
        if gdata.get("checkm_completeness") is not None:
            meta["checkm_completeness"] = round(gdata["checkm_completeness"], 2)
        if gdata.get("checkm_contamination") is not None:
            meta["checkm_contamination"] = round(gdata["checkm_contamination"], 2)
        if gid in pheno_data:
            meta["phenotype"] = pheno_data[gid]
        metadata[gid] = meta

    # Identify all core clusters (for missing_core computation)
    all_core_clusters = set()
    for row in conn.execute("SELECT DISTINCT cluster FROM pangenome_feature WHERE is_core = 1"):
        all_core_clusters.add(row["cluster"])
    # Also check user_feature
    for row in conn.execute("SELECT pangenome_cluster FROM user_feature WHERE pangenome_is_core = 1"):
        for cid in parse_cluster_ids(row["pangenome_cluster"]):
            all_core_clusters.add(cid)
    n_total_core = len(all_core_clusters)

    # Per-genome stats (enriched with contigs, KEGG coverage, metabolic genes, missing core)
    genome_stats = {}
    for gid in genome_ids:
        clusters = all_clusters_by_genome[gid]
        if gid == user_genome_id:
            row = conn.execute("""
                SELECT
                    COUNT(*) as n_genes,
                    COUNT(CASE WHEN pangenome_is_core = 1 THEN 1 END) as core_count,
                    COUNT(DISTINCT CASE WHEN contig IS NOT NULL AND contig <> '' THEN contig END) as n_contigs,
                    COUNT(CASE WHEN ontology_KEGG IS NOT NULL AND ontology_KEGG <> '' THEN 1 END) as has_kegg,
                    COUNT(CASE WHEN ontology_EC IS NOT NULL AND ontology_EC <> '' THEN 1 END) as has_ec
                FROM user_feature WHERE genome = ? AND type = 'gene'
            """, (gid,)).fetchone()
        else:
            row = conn.execute("""
                SELECT
                    COUNT(*) as n_genes,
                    COUNT(CASE WHEN is_core = 1 THEN 1 END) as core_count,
                    COUNT(DISTINCT CASE WHEN contig IS NOT NULL AND contig <> '' THEN contig END) as n_contigs,
                    COUNT(CASE WHEN ontology_KEGG IS NOT NULL AND ontology_KEGG <> '' THEN 1 END) as has_kegg,
                    COUNT(CASE WHEN ontology_EC IS NOT NULL AND ontology_EC <> '' THEN 1 END) as has_ec
                FROM pangenome_feature WHERE genome = ?
            """, (gid,)).fetchone()

        n_genes = row["n_genes"]
        core_count = row["core_count"]
        n_contigs = row["n_contigs"]
        has_kegg = row["has_kegg"]
        has_ec = row["has_ec"]

        # Missing core: core clusters not present in this genome
        genome_core = clusters & all_core_clusters
        missing_core = n_total_core - len(genome_core)

        genome_stats[gid] = {
            "n_genes": n_genes,
            "n_clusters": len(clusters),
            "core_pct": round(core_count / n_genes, 4) if n_genes > 0 else 0,
            "n_contigs": n_contigs,
            "missing_core": missing_core,
            "ko_pct": round(has_kegg / n_genes, 4) if n_genes > 0 else 0,
            "metabolic_genes": has_ec,
        }

    conn.close()

    return {
        "linkage": linkage_data,
        "genome_ids": genome_ids,
        "leaf_order": leaf_order,
        "user_genome_id": user_genome_id,
        "genome_metadata": metadata,
        "genome_stats": genome_stats,
        "stats": stats,
    }


def extract_reactions_data(db_path, user_genome_id, genes_data=None):
    """Extract metabolic reactions for reactions_data.json."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Check if genome_reaction table exists
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='genome_reaction'"
    ).fetchone():
        conn.close()
        return {"user_genome": user_genome_id, "n_genomes": 0, "reactions": {}, "gene_index": {}, "stats": {}}

    # Count total genomes with reactions
    n_genomes = conn.execute(
        "SELECT COUNT(DISTINCT genome_id) FROM genome_reaction"
    ).fetchone()[0]

    # Count genomes per reaction (for conservation)
    rxn_genomes = defaultdict(set)
    for row in conn.execute("SELECT reaction_id, genome_id FROM genome_reaction"):
        rxn_genomes[row["reaction_id"]].add(row["genome_id"])

    # Extract user genome reactions
    reactions = {}
    for row in conn.execute("""
        SELECT reaction_id, genes, equation_names, equation_ids, directionality,
               gapfilling_status, rich_media_flux, rich_media_class,
               minimal_media_flux, minimal_media_class
        FROM genome_reaction
        WHERE genome_id = ?
    """, (user_genome_id,)):
        rxn_id = row["reaction_id"]
        n_with = len(rxn_genomes.get(rxn_id, set()))
        conservation = round(n_with / n_genomes, 4) if n_genomes > 0 else 0

        flux_rich = row["rich_media_flux"] if row["rich_media_flux"] is not None else 0
        flux_min = row["minimal_media_flux"] if row["minimal_media_flux"] is not None else 0
        class_rich = row["rich_media_class"] or "blocked"
        class_min = row["minimal_media_class"] or "blocked"

        reactions[rxn_id] = {
            "genes": row["genes"] or "",
            "equation": row["equation_names"] or "",
            "equation_ids": row["equation_ids"] or "",
            "directionality": row["directionality"] or "reversible",
            "gapfilling": row["gapfilling_status"] or "none",
            "conservation": conservation,
            "flux_rich": round(flux_rich, 6),
            "flux_min": round(flux_min, 6),
            "class_rich": class_rich,
            "class_min": class_min,
        }

    conn.close()

    # Build gene index from genes_data if provided
    gene_index = {}
    if genes_data:
        fid_to_idx = {str(g[1]): i for i, g in enumerate(genes_data)}
        all_locus_tags = set()
        for rxn in reactions.values():
            gene_str = rxn["genes"]
            if gene_str:
                tags = re.findall(r"[A-Za-z][A-Za-z0-9_]+", gene_str)
                tags = [t for t in tags if t.lower() not in ("or", "and")]
                all_locus_tags.update(tags)

        for tag in all_locus_tags:
            if tag in fid_to_idx:
                gene_index[tag] = [fid_to_idx[tag]]
            else:
                matches = [idx for fid, idx in fid_to_idx.items() if tag in fid]
                if matches:
                    gene_index[tag] = matches

    # Compute stats
    active_rich = sum(1 for r in reactions.values() if r["class_rich"] != "blocked")
    active_min = sum(1 for r in reactions.values() if r["class_min"] != "blocked")
    essential_rich = sum(1 for r in reactions.values() if "essential" in r["class_rich"])
    essential_min = sum(1 for r in reactions.values() if "essential" in r["class_min"])

    stats = {
        "total_reactions": len(reactions),
        "active_rich": active_rich,
        "active_min": active_min,
        "essential_rich": essential_rich,
        "essential_min": essential_min,
        "blocked_rich": len(reactions) - active_rich,
        "blocked_min": len(reactions) - active_min,
    }

    return {
        "user_genome": user_genome_id,
        "n_genomes": n_genomes,
        "reactions": reactions,
        "gene_index": gene_index,
        "stats": stats,
    }


def extract_summary_stats(db_path, user_genome_id):
    """Extract summary statistics for summary_stats.json."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    summary = {}

    # ── Gene category counts ────────────────────────────────────────────
    core = conn.execute(
        "SELECT COUNT(*) FROM user_feature WHERE genome = ? AND pangenome_is_core = 1",
        (user_genome_id,)
    ).fetchone()[0]
    accessory = conn.execute(
        "SELECT COUNT(*) FROM user_feature WHERE genome = ? AND pangenome_is_core = 0",
        (user_genome_id,)
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM user_feature WHERE genome = ? AND type = 'gene'",
        (user_genome_id,)
    ).fetchone()[0]

    summary["gene_categories"] = {
        "total_genes": total,
        "core_genes": core,
        "accessory_genes": accessory,
        "unknown_genes": total - core - accessory,
    }

    # ── Growth phenotype summary (from genome_phenotype) ────────────────
    try:
        positive = conn.execute(
            "SELECT COUNT(*) FROM genome_phenotype WHERE genome_id = ? AND class = 'P'",
            (user_genome_id,)
        ).fetchone()[0]
        negative = conn.execute(
            "SELECT COUNT(*) FROM genome_phenotype WHERE genome_id = ? AND class = 'N'",
            (user_genome_id,)
        ).fetchone()[0]
        summary["growth_phenotypes"] = {
            "positive_growth": positive,
            "negative_growth": negative,
            "total_phenotypes": positive + negative,
        }
    except sqlite3.OperationalError:
        summary["growth_phenotypes"] = None

    # ── Genome comparison stats ─────────────────────────────────────────
    n_ref = conn.execute(
        "SELECT COUNT(DISTINCT genome) FROM pangenome_feature"
    ).fetchone()[0]

    closest_ani = None
    try:
        row = conn.execute("""
            SELECT MAX(ani) as max_ani FROM ani
            WHERE genome1 = ? OR genome2 = ?
        """, (user_genome_id, user_genome_id)).fetchone()
        if row and row["max_ani"]:
            closest_ani = round(row["max_ani"], 4)
    except sqlite3.OperationalError:
        pass

    summary["comparison"] = {
        "n_reference_genomes": n_ref,
        "closest_ani": closest_ani,
    }

    # ── Reaction stats ──────────────────────────────────────────────────
    try:
        n_reactions = conn.execute(
            "SELECT COUNT(*) FROM genome_reaction WHERE genome_id = ?",
            (user_genome_id,)
        ).fetchone()[0]
        n_gapfilled = conn.execute(
            "SELECT COUNT(*) FROM genome_reaction WHERE genome_id = ? AND gapfilling_status != 'none'",
            (user_genome_id,)
        ).fetchone()[0]
        summary["reactions"] = {
            "total_reactions": n_reactions,
            "gapfilled_reactions": n_gapfilled,
        }
    except sqlite3.OperationalError:
        summary["reactions"] = None

    # ── Phenotype Prediction Landscape ───────────────────────────────
    try:
        phenotype_landscape = {"genomes": [], "user_genome_id": user_genome_id}
        for row in conn.execute("""
            SELECT genome_id,
                   COUNT(CASE WHEN class = 'P' THEN 1 END) as positive,
                   COUNT(CASE WHEN class = 'N' THEN 1 END) as negative,
                   COUNT(*) as total,
                   AVG(gap_count) as avg_gaps,
                   SUM(CASE WHEN gap_count = 0 THEN 1 ELSE 0 END) as no_gap_count,
                   AVG(CASE WHEN observed_objective > 0 THEN 1.0 ELSE NULL END) as accuracy
            FROM genome_phenotype
            GROUP BY genome_id
            ORDER BY genome_id
        """):
            phenotype_landscape["genomes"].append({
                "id": row["genome_id"],
                "positive": row["positive"],
                "negative": row["negative"],
                "total": row["total"],
                "avg_gaps": round(row["avg_gaps"], 2) if row["avg_gaps"] else 0,
                "no_gap_pct": round(row["no_gap_count"] / row["total"], 4) if row["total"] else 0,
                "accuracy": round(row["accuracy"], 4) if row["accuracy"] else None
            })
        # --- Reference phenotype accuracy (Jaccard matching) ---
        ref_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "heatmap", "reference_phenotypes.json")
        if os.path.exists(ref_path):
            logger.info("  Loading reference phenotypes for Jaccard matching...")
            with open(ref_path) as f:
                ref_data = json.load(f)

            user_vector = build_user_pheno_vector(conn, user_genome_id, ref_data["phenotype_ids"])

            best_match = None
            best_similarity = -1
            for ref_genome in ref_data["genomes"]:
                sim = jaccard_similarity(user_vector, ref_genome["vector"])
                if sim > best_similarity:
                    best_similarity = sim
                    best_match = ref_genome

            if best_match:
                for g in phenotype_landscape["genomes"]:
                    if g["id"] == user_genome_id:
                        g["accuracy"] = best_match["accuracy"]
                        g["closest_experimental"] = best_match["id"]
                        g["jaccard_similarity"] = round(best_similarity, 4)
                logger.info(f"  Closest experimental: {best_match['id']} "
                            f"(Jaccard={best_similarity:.4f}, accuracy={best_match['accuracy']})")

            phenotype_landscape["reference_accuracies"] = [
                {"id": g["id"], "accuracy": g["accuracy"]}
                for g in ref_data["genomes"]
                if g["accuracy"] is not None
            ]
            phenotype_landscape["has_accuracy"] = True
            logger.info(f"  {len(phenotype_landscape['reference_accuracies'])} reference genomes with accuracy")
        else:
            has_accuracy = any(g["accuracy"] is not None for g in phenotype_landscape["genomes"])
            phenotype_landscape["has_accuracy"] = has_accuracy
            logger.info(f"  No reference_phenotypes.json found, using DB accuracy (available: {has_accuracy})")

        summary["phenotype_landscape"] = phenotype_landscape
        logger.info(f"  Phenotype landscape: {len(phenotype_landscape['genomes'])} genomes")
    except sqlite3.OperationalError:
        summary["phenotype_landscape"] = None
        logger.info("  (genome_phenotype table not found for landscape)")

    conn.close()
    return summary


def extract_ref_genomes_data(db_path):
    """Extract reference genome metadata for ref_genomes_data.json."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ref_genomes = []
    for row in conn.execute("SELECT * FROM genome ORDER BY genome"):
        genome = {
            "genome_id": row["genome"],
            "kind": safe_get(row, "kind", "unknown"),
            "gtdb_taxonomy": safe_get(row, "gtdb_taxonomy", ""),
            "ncbi_taxonomy": safe_get(row, "ncbi_taxonomy", ""),
            "size": safe_get(row, "size", 0),
            "checkm_completeness": safe_get(row, "checkm_completeness"),
            "checkm_contamination": safe_get(row, "checkm_contamination"),
        }
        ref_genomes.append(genome)

    conn.close()
    return ref_genomes


def extract_all(db_path, pangenome_id=""):
    """Extract all data files from a single SQLite database.

    Returns dict of {filename: data} ready to be written as JSON files.
    """
    logger.info(f"Extracting all data from {db_path}")

    user_genome_id = get_user_genome_id(db_path)
    logger.info(f"User genome: {user_genome_id}")

    genes_data = extract_genes_data(db_path, user_genome_id)
    logger.info(f"Extracted {len(genes_data)} genes")

    metadata = extract_metadata(db_path, user_genome_id, pangenome_id)
    logger.info(f"Organism: {metadata['organism']}")

    tree_data = extract_tree_data(db_path, user_genome_id)
    logger.info(f"Tree: {tree_data.get('stats', {}).get('n_genomes', 0)} genomes")

    reactions_data = extract_reactions_data(db_path, user_genome_id, genes_data)
    logger.info(f"Reactions: {reactions_data.get('stats', {}).get('total_reactions', 0)}")

    summary_stats = extract_summary_stats(db_path, user_genome_id)

    ref_genomes = extract_ref_genomes_data(db_path)

    return {
        "genes_data.json": genes_data,
        "metadata.json": metadata,
        "tree_data.json": tree_data,
        "reactions_data.json": reactions_data,
        "summary_stats.json": summary_stats,
        "ref_genomes_data.json": ref_genomes,
    }
