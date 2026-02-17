"""
Extract data from GenomeDataLakeTables SQLite database for heatmap viewer.

The GenomeDataLakeTables object contains multiple pangenomes (clades), each with
a SQLite database stored in Shock. This module downloads the SQLite file and
extracts data in the format expected by the genome-heatmap-viewer.
"""

import json
import sqlite3
import os
import tempfile


def extract_genes_data(db_path, user_genome_id):
    """
    Extract gene data from SQLite database.

    Expected output format for genes_data.json:
    [
        [id, fid, length, start, strand, conservation_frac, pan_category,
         function, n_ko, n_cog, n_pfam, n_go, localization, rast_cons,
         ko_cons, go_cons, ec_cons, avg_cons, bakta_cons, ec_avg_cons,
         specificity],
        ...
    ]

    Args:
        db_path: Path to SQLite database file
        user_genome_id: ID of the user genome (e.g., 'user_genome' or specific genome ID)

    Returns:
        List of gene arrays
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Query genome_features table for the user genome
    query = """
        SELECT *
        FROM genome_features
        WHERE genome_id = ?
        ORDER BY start
    """

    cursor = conn.execute(query, (user_genome_id,))
    genes = []

    for idx, row in enumerate(cursor.fetchall()):
        # Extract data in the order expected by genome-heatmap-viewer
        gene = [
            idx,  # id (index)
            row['feature_id'],  # fid
            row['protein_length'],  # length
            row['start'],  # start
            row['strand'],  # strand
            row.get('pangenome_conservation_fraction', 0.0),  # conservation_frac
            row.get('pangenome_category', 0),  # pan_category (0=unknown, 1=accessory, 2=core)
            row['rast_function'] or row.get('bakta_function', 'hypothetical protein'),  # function
            count_terms(row.get('ko', '')),  # n_ko
            count_terms(row.get('cog', '')),  # n_cog
            count_terms(row.get('pfam', '')),  # n_pfam
            count_terms(row.get('go', '')),  # n_go
            row.get('psortb_localization', 'Unknown'),  # localization
            row.get('rast_annotation_consistency', -1.0),  # rast_cons
            row.get('ko_annotation_consistency', -1.0),  # ko_cons
            row.get('go_annotation_consistency', -1.0),  # go_cons
            row.get('ec_annotation_consistency', -1.0),  # ec_cons
            row.get('avg_annotation_consistency', -1.0),  # avg_cons
            row.get('bakta_annotation_consistency', -1.0),  # bakta_cons
            row.get('ec_map_annotation_consistency', -1.0),  # ec_avg_cons
            row.get('annotation_specificity', 0.5),  # specificity
        ]
        genes.append(gene)

    conn.close()
    return genes


def extract_metadata(db_path, user_genome_id, pangenome_taxonomy):
    """
    Extract organism metadata.

    Args:
        db_path: Path to SQLite database file
        user_genome_id: ID of the user genome
        pangenome_taxonomy: Taxonomy string from PangenomeData

    Returns:
        Dictionary with organism metadata
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Query genome table for organism info
    query = "SELECT * FROM genome WHERE genome_id = ? LIMIT 1"
    cursor = conn.execute(query, (user_genome_id,))
    row = cursor.fetchone()

    if row:
        metadata = {
            "organism": row.get('organism_name', pangenome_taxonomy),
            "genome_id": user_genome_id,
            "taxonomy": pangenome_taxonomy,
            "ncbi_taxonomy": row.get('ncbi_taxonomy', ''),
            "n_contigs": row.get('n_contigs', 0),
            "n_features": row.get('n_features', 0),
        }
    else:
        # Fallback if genome table doesn't exist or has no data
        metadata = {
            "organism": pangenome_taxonomy,
            "genome_id": user_genome_id,
            "taxonomy": pangenome_taxonomy,
        }

    conn.close()
    return metadata


def extract_tree_data(db_path):
    """
    Extract phylogenetic tree data.

    Returns:
        Dictionary with tree structure (Newick format)
    """
    conn = sqlite3.connect(db_path)

    # Check if phylogenetic_tree table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='phylogenetic_tree'"
    )

    if cursor.fetchone():
        cursor = conn.execute("SELECT newick FROM phylogenetic_tree LIMIT 1")
        row = cursor.fetchone()
        if row:
            conn.close()
            return {"newick": row[0]}

    conn.close()
    return {}


def extract_reactions_data(db_path, user_genome_id):
    """
    Extract metabolic reactions.

    Returns:
        List of reaction dictionaries
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    reactions = []

    # Check if reactions table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='model_reactions'"
    )

    if cursor.fetchone():
        query = """
            SELECT *
            FROM model_reactions
            WHERE genome_id = ?
        """
        cursor = conn.execute(query, (user_genome_id,))

        for row in cursor.fetchall():
            reaction = {
                "reaction_id": row['reaction_id'],
                "name": row.get('name', ''),
                "equation": row.get('equation', ''),
                "genes": row.get('genes', '').split(';') if row.get('genes') else [],
                "flux_min": row.get('flux_min', 0.0),
                "flux_max": row.get('flux_max', 0.0),
                "is_essential": row.get('is_essential', 0),
                "is_gapfilled": row.get('is_gapfilled', 0),
            }
            reactions.append(reaction)

    conn.close()
    return reactions


def extract_summary_stats(db_path, user_genome_id):
    """
    Extract or compute summary statistics.

    Returns:
        Dictionary with precomputed statistics
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Count genes by pangenome category
    query = """
        SELECT
            pangenome_category,
            COUNT(*) as count
        FROM genome_features
        WHERE genome_id = ?
        GROUP BY pangenome_category
    """
    cursor = conn.execute(query, (user_genome_id,))

    stats = {
        "total_genes": 0,
        "core_genes": 0,
        "accessory_genes": 0,
        "unknown_genes": 0,
    }

    for row in cursor.fetchall():
        category = row['pangenome_category']
        count = row['count']
        stats["total_genes"] += count
        if category == 2:
            stats["core_genes"] = count
        elif category == 1:
            stats["accessory_genes"] = count
        else:
            stats["unknown_genes"] = count

    conn.close()
    return stats


def extract_ref_genomes_data(db_path):
    """
    Extract reference genomes metadata.

    Returns:
        List of reference genome dictionaries
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ref_genomes = []

    # Query genome table for all genomes
    query = "SELECT * FROM genome ORDER BY organism_name"
    cursor = conn.execute(query)

    for row in cursor.fetchall():
        genome = {
            "genome_id": row['genome_id'],
            "organism": row.get('organism_name', ''),
            "ncbi_taxonomy": row.get('ncbi_taxonomy', ''),
            "n_features": row.get('n_features', 0),
        }
        ref_genomes.append(genome)

    conn.close()
    return ref_genomes


def extract_cluster_data(db_path):
    """
    Extract pangenome cluster data for UMAP visualization.

    Returns:
        Dictionary with cluster embeddings and metadata
    """
    conn = sqlite3.connect(db_path)

    # Check if cluster_embeddings table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cluster_embeddings'"
    )

    if cursor.fetchone():
        cursor = conn.execute("SELECT * FROM cluster_embeddings")
        clusters = []
        for row in cursor.fetchall():
            cluster = {
                "cluster_id": row[0],
                "umap_x": row[1],
                "umap_y": row[2],
            }
            clusters.append(cluster)
        conn.close()
        return {"clusters": clusters}

    conn.close()
    return {}


# Helper functions

def count_terms(term_string):
    """
    Count semicolon-separated terms.

    Args:
        term_string: String like "K00001;K00002;K00003"

    Returns:
        Number of non-empty terms
    """
    if not term_string:
        return 0
    return len([t for t in term_string.split(';') if t.strip()])


def get_user_genome_id(db_path):
    """
    Determine the user genome ID from the database.

    The user genome is typically labeled as 'user_genome' or can be
    identified from the genome table.

    Args:
        db_path: Path to SQLite database file

    Returns:
        User genome ID string
    """
    conn = sqlite3.connect(db_path)

    # Try to find 'user_genome' first
    cursor = conn.execute(
        "SELECT genome_id FROM genome_features WHERE genome_id = 'user_genome' LIMIT 1"
    )
    row = cursor.fetchone()
    if row:
        conn.close()
        return 'user_genome'

    # Otherwise, look for genome with is_user_genome flag
    cursor = conn.execute(
        "SELECT genome_id FROM genome WHERE is_user_genome = 1 LIMIT 1"
    )
    row = cursor.fetchone()
    if row:
        conn.close()
        return row[0]

    # Fallback: get first genome from genome_features
    cursor = conn.execute(
        "SELECT DISTINCT genome_id FROM genome_features ORDER BY genome_id LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        return row[0]

    raise ValueError("Could not determine user genome ID from database")
