import pandas as pd
from arcgis.gis import GIS
from arcgis.apps.itemgraph import create_dependency_graph
import json
import urllib3
import logging
import time
import csv
import os
import argparse
import sys
from datetime import datetime
from functools import wraps

# Force UTF-8 output on Windows to prevent cp1252 encoding errors
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Suppress HTTPS warnings for environments with SSL inspection
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================================================================================
# INTERNAL CONFIGURATION (Does not affect CLI interface)
# ================================================================================
_CHUNK_SIZE = 500
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2
_VALID_STATUSES = ['org_authoritative', 'public_authoritative']
_CSV_FIELDS = [
    "Title", "Id", "Type", "Owner", "Created", "Modified",
    "RestUrl", "ItemPageUrl", "Tags", "ContentStatus"
]

# ================================================================================
# Logging helpers (unchanged interface)
# ================================================================================

def log(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)
    logger.info(msg)

def warn(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] WARN: {msg}", flush=True)
    logger.warning(msg)

def err(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] ERROR: {msg}", flush=True)
    logger.error(msg)


# ================================================================================
# Shared: connect (unchanged)
# ================================================================================

def connect_to_gis():
    gis = GIS("home")
    log(f"Connected to: {gis.url} ({gis.properties.portalName})")
    return gis


# ================================================================================
# INTERNAL: Retry decorator for resilience
# ================================================================================

def _retry_on_failure(max_attempts=_RETRY_ATTEMPTS, base_delay=_RETRY_DELAY):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt == max_attempts - 1:
                        raise
                    delay = base_delay * (attempt + 1)
                    time.sleep(delay)
            return None
        return wrapper
    return decorator


# ================================================================================
# INTERNAL: Safe property access to avoid lazy-loading traps
# ================================================================================

def _safe_get(obj, attr, default=None):
    try:
        val = getattr(obj, attr, default)
        return val if val is not None else default
    except Exception:
        return default


# ================================================================================
# INTERNAL: Progress indicator (optional, non-intrusive)
# ================================================================================

def _print_progress(iteration, total, prefix='', suffix='', decimals=1, length=40):
    percent = ("{0:." + str(decimals) + "f}").format(100 * iteration / float(total))
    filled = int(length * iteration // total)
    bar = '#' * filled + '-' * (length - filled)
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end='', flush=True)
    if iteration == total:
        print()


# ================================================================================
# Shared: single broad content fetch (OPTIMIZED internally)
# ================================================================================

def fetch_all_items(gis, max_items):
    """
    Fetch items using optimized search pattern.
    Returns list for backward compatibility with existing callers.
    Note: For very large portals, consider migrating to generator pattern.
    """
    log("Fetching all portal content (single pass)...")
    start_time = time.time()
    
    # CORRECT pattern: only query + max_items, no start/page params
    items = gis.content.search(query="", max_items=max_items, outside_org=False)
    
    elapsed = time.time() - start_time
    log(f"Fetched {len(items)} total items from portal in {elapsed:.2f}s.")
    return items


# ================================================================================
# Pipeline A: Authoritative Inventory -> CSV (OPTIMIZED internally)
# ================================================================================

def _get_item_details(gis, item):
    """Extract fields using direct property access - avoids extra API calls"""
    return {
        "Title":         _safe_get(item, 'title', '')[:2000],
        "Id":            _safe_get(item, 'id', ''),
        "Type":          _safe_get(item, 'type', ''),
        "Owner":         _safe_get(item, 'owner', ''),
        "Created":       pd.to_datetime(item.created, unit="ms") if item.created else None,
        "Modified":      pd.to_datetime(item.modified, unit="ms") if item.modified else None,
        "RestUrl":       _safe_get(item, "url", ""),
        "ItemPageUrl":   f"{gis.url}/home/item.html?id={_safe_get(item, 'id', '')}",
        "Tags":          ", ".join(_safe_get(item, 'tags', []) or []),
        "ContentStatus": _safe_get(item, "content_status", "")
    }

def _load_index(index_file):
    """Load delta-tracking index: item_id -> last_modified timestamp."""
    if not os.path.exists(index_file):
        return {}
    with open(index_file, 'r', encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return {row['id']: int(row['mod']) for row in reader}

def _save_index_atomic(index_file, index):
    """Write to temp file first, then rename (prevents corruption on interrupt)"""
    temp_file = index_file + ".tmp"
    with open(temp_file, 'w', newline='', encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'mod'])
        writer.writeheader()
        for k, v in index.items():
            writer.writerow({'id': k, 'mod': v})
    os.replace(temp_file, index_file)

@_retry_on_failure(max_attempts=_RETRY_ATTEMPTS)
def _write_csv_batch(csv_file, rows, header_written):
    """Append batch of rows to CSV with retry logic"""
    mode = 'a' if os.path.exists(csv_file) and header_written else 'w'
    header = not header_written
    with open(csv_file, mode, newline='', encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction='ignore')
        if header:
            writer.writeheader()
        writer.writerows(rows)
    return True

def run_inventory_pipeline(gis, all_items, out_file, index_file):
    """
    Pipeline A: Authoritative Inventory -> CSV
    Internal optimization: batched writes, safe attribute access, atomic index updates
    External interface: unchanged
    """
    log("--- Pipeline A: Authoritative Inventory ---")

    index = _load_index(index_file)

    new_records = []
    skipped_not_auth = 0
    skipped_no_change = 0
    total = len(all_items)
    
    start_time = time.time()

    for count, item in enumerate(all_items, 1):
        # Strict status check
        actual_status = _safe_get(item, "content_status", "")
        if actual_status not in _VALID_STATUSES:
            skipped_not_auth += 1
            continue

        # Delta check - skip if already captured at this version
        item_modified = _safe_get(item, 'modified', 0)
        if item.id in index and index[item.id] >= item_modified:
            skipped_no_change += 1
            continue

        new_records.append(_get_item_details(gis, item))
        index[item.id] = item_modified
        
        # Progress indicator every 100 items
        if count % 100 == 0:
            _print_progress(count, total, prefix='[PROCESS]', suffix='items')
        
        # Batch write to reduce I/O overhead
        if len(new_records) >= _CHUNK_SIZE:
            header = not os.path.exists(out_file)
            os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
            _write_csv_batch(out_file, new_records, header)
            new_records = []

    # Write remaining records
    if new_records:
        header = not os.path.exists(out_file)
        os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
        _write_csv_batch(out_file, new_records, header)
        _save_index_atomic(index_file, index)
    
    elapsed = time.time() - start_time
    log(f"Filtered out {skipped_not_auth} non-authoritative items.")
    log(f"Skipped {skipped_no_change} unchanged items (delta check).")
    log(f"CSV updated: {len(new_records) if new_records else 0} new/changed authoritative items -> {out_file}")
    log(f"Pipeline A completed in {elapsed:.2f}s")

    return len(new_records) if new_records else 0


# ================================================================================
# Pipeline B: Dependency Graph -> JSON + GML (unchanged interface)
# ================================================================================

def run_graph_pipeline(gis, all_items, json_file, gml_file):
    """
    Pipeline B: Dependency Graph -> JSON + GML
    External interface unchanged. Internal logging enhanced.
    """
    log("--- Pipeline B: Dependency Graph ---")

    # Register all items as nodes
    nodes = {}
    for item in all_items:
        nodes[item.id] = {
            "id":           item.id,
            "label":        item.title,
            "type":         item.type,
            "owner":        item.owner,
            "views":        item.numViews,
            "access":       item.access,
            "url":          item.homepage,
            "modified":     item.modified,
            "is_abandoned": True
        }

    log(f"Registered {len(nodes)} nodes.")
    log("Building dependency graph via ItemGraph (may take several minutes)...")

    try:
        itemgraph = create_dependency_graph(
            gis,
            all_items,
            outside_org=False,
            include_reverse=True
        )
        log(f"ItemGraph built: {len(itemgraph.all_items())} total nodes.")
    except Exception as e:
        err(f"ItemGraph failed: {e}")
        raise

    # Extract edges
    edges = []
    for source_id, target_id in itemgraph.edges():
        edges.append({"source": source_id, "target": target_id, "type": "dependency"})
    log(f"Extracted {len(edges)} raw relationships.")

    # Mark connected nodes
    connected_ids = set()
    for edge in edges:
        connected_ids.add(edge["source"])
        connected_ids.add(edge["target"])

    abandoned_count = 0
    for item_id in nodes:
        if item_id in connected_ids:
            nodes[item_id]["is_abandoned"] = False
        else:
            abandoned_count += 1

    log(f"Orphaned/abandoned items (no relationships): {abandoned_count}")

    # Per-item dependency stats
    log("Analysing per-item dependency structure...")
    item_dependency_stats = {}

    for item_id in nodes:
        try:
            node = itemgraph.get_node(item_id)
            if node:
                stats = {
                    "contains_count":          len(node.contains()),
                    "contained_by_count":      len(node.contained_by()),
                    "total_requires":          len(node.requires()),
                    "total_required_by":       len(node.required_by())
                }
                item_dependency_stats[item_id] = stats
                nodes[item_id]["dependency_info"] = {
                    "immediate_dependencies":          stats["contains_count"],
                    "immediate_dependents":            stats["contained_by_count"],
                    "total_recursive_dependencies":    stats["total_requires"],
                    "total_recursive_dependents":      stats["total_required_by"]
                }
        except Exception:
            pass

    # Filter edges to internal-only (prevents D3/viz errors on external refs)
    filtered_edges = []
    skipped_external = 0
    for edge in edges:
        if edge["source"] in nodes and edge["target"] in nodes:
            filtered_edges.append(edge)
        else:
            skipped_external += 1

    log(f"Internal relationships for JSON/viz: {len(filtered_edges)} (skipped {skipped_external} external refs -- preserved in GML).")

    # Top critical items (most dependents)
    critical_items = sorted(
        [
            {
                "id":              item_id,
                "title":           nodes[item_id]["label"],
                "type":            nodes[item_id]["type"],
                "dependents_count": stats["total_required_by"]
            }
            for item_id, stats in item_dependency_stats.items()
            if stats["total_required_by"] > 0
        ],
        key=lambda x: x["dependents_count"],
        reverse=True
    )[:50]

    # Build final JSON
    graph_data = {
        "summary": {
            "total_items":          len(all_items),
            "abandoned_count":      abandoned_count,
            "connected_count":      len(connected_ids),
            "total_relationships":  len(filtered_edges),
            "analysis_date":        datetime.now().isoformat(),
            "graph_method":         "ItemGraph (arcgis.apps.itemgraph.create_dependency_graph)"
        },
        "high_risk_items": critical_items,
        "nodes":           list(nodes.values()),
        "edges":           filtered_edges
    }

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(graph_data, f, indent=2)
    log(f"JSON saved -> {json_file}")

    try:
        itemgraph.write_to_file(gml_file)
        log(f"GML saved -> {gml_file}")
    except Exception as e:
        warn(f"Could not save GML: {e}")

    return graph_data["summary"]


# ================================================================================
# Main (unchanged CLI interface)
# ================================================================================

def main():
    parser = argparse.ArgumentParser(description="ArcGIS Combined Scanner: Authoritative Inventory + Dependency Graph")
    # Pipeline A args
    parser.add_argument("--out",   default="AuthInventory.csv",       help="Authoritative items CSV output")
    parser.add_argument("--index", default="scan_index.csv",           help="Delta-tracking index file")
    # Pipeline B args
    parser.add_argument("--json",  default="content_audit_graph.json", help="Dependency graph JSON output")
    parser.add_argument("--gml",   default="content_audit_graph.gml",  help="Dependency graph GML output")
    # Shared
    parser.add_argument("--max",   type=int, default=10000,            help="Max items to fetch from portal")
    # Optional: skip a pipeline
    parser.add_argument("--skip-inventory", action="store_true", help="Skip Pipeline A (CSV inventory)")
    parser.add_argument("--skip-graph",     action="store_true", help="Skip Pipeline B (dependency graph)")
    args = parser.parse_args()

    try:
        gis = connect_to_gis()

        # Single broad fetch -- shared by both pipelines
        all_items = fetch_all_items(gis, args.max)

        if not args.skip_inventory:
            run_inventory_pipeline(gis, all_items, args.out, args.index)

        if not args.skip_graph:
            summary = run_graph_pipeline(gis, all_items, args.json, args.gml)

            # Print graph summary
            log("=" * 60)
            log("DEPENDENCY GRAPH SUMMARY")
            log("=" * 60)
            log(f"  Total items scanned   : {summary['total_items']}")
            log(f"  Items with relations  : {summary['connected_count']}")
            log(f"  Orphaned items        : {summary['abandoned_count']}")
            log(f"  Total relationships   : {summary['total_relationships']}")

        log("=" * 60)
        log("ALL PIPELINES COMPLETE")
        log(f"  CSV inventory  -> {args.out}")
        log(f"  JSON graph     -> {args.json}")
        log(f"  GML graph      -> {args.gml}")
        log("=" * 60)

    except Exception as e:
        err(f"CRITICAL ERROR: {e}")
        raise


if __name__ == "__main__":
    main()
