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

# ─────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# Shared: connect
# ─────────────────────────────────────────────

def connect_to_gis():
    gis = GIS("home")
    log(f"Connected to: {gis.url} ({gis.properties.portalName})")
    return gis


# ─────────────────────────────────────────────
# Shared: single broad content fetch
# ─────────────────────────────────────────────

def fetch_all_items(gis, max_items):
    log("Fetching all portal content (single pass)...")
    items = gis.content.search(query="", max_items=max_items, outside_org=False)
    log(f"Fetched {len(items)} total items from portal.")
    return items


# ─────────────────────────────────────────────
# Pipeline A: Authoritative Inventory → CSV
# ─────────────────────────────────────────────

VALID_STATUSES = ['org_authoritative', 'public_authoritative']

def get_item_details(gis, item):
    return {
        "Title":         item.title,
        "Id":            item.id,
        "Type":          item.type,
        "Owner":         item.owner,
        "Created":       pd.to_datetime(item.created, unit="ms"),
        "Modified":      pd.to_datetime(item.modified, unit="ms"),
        "RestUrl":       getattr(item, "url", ""),
        "ItemPageUrl":   f"{gis.url}/home/item.html?id={item.id}",
        "Tags":          ", ".join(item.tags or []),
        "ContentStatus": getattr(item, "content_status", "")
    }

def load_index(index_file):
    """Load delta-tracking index: item_id → last_modified timestamp."""
    if not os.path.exists(index_file):
        return {}
    with open(index_file, 'r', encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return {row['id']: int(row['mod']) for row in reader}

def save_index(index_file, index):
    with open(index_file, 'w', newline='', encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'mod'])
        writer.writeheader()
        for k, v in index.items():
            writer.writerow({'id': k, 'mod': v})

def run_inventory_pipeline(gis, all_items, out_file, index_file):
    log("--- Pipeline A: Authoritative Inventory ---")

    index = load_index(index_file)

    new_records = []
    skipped_not_auth = 0
    skipped_no_change = 0

    for item in all_items:
        # Strict status check
        actual_status = getattr(item, "content_status", "")
        if actual_status not in VALID_STATUSES:
            skipped_not_auth += 1
            continue

        # Delta check — skip if already captured at this version
        if item.id in index and index[item.id] >= item.modified:
            skipped_no_change += 1
            continue

        new_records.append(get_item_details(gis, item))
        index[item.id] = item.modified

    log(f"Filtered out {skipped_not_auth} non-authoritative items.")
    log(f"Skipped {skipped_no_change} unchanged items (delta check).")

    if new_records:
        df = pd.DataFrame(new_records)
        header = not os.path.exists(out_file)
        os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
        df.to_csv(out_file, mode='a', index=False, header=header, encoding="utf-8-sig")
        save_index(index_file, index)
        log(f"CSV updated: {len(new_records)} new/changed authoritative items → {out_file}")
    else:
        log("CSV inventory already up to date. No changes written.")

    return len(new_records)


# ─────────────────────────────────────────────
# Pipeline B: Dependency Graph → JSON + GML
# ─────────────────────────────────────────────

def run_graph_pipeline(gis, all_items, json_file, gml_file):
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
            outside_org=True,
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

    log(f"Internal relationships for JSON/viz: {len(filtered_edges)} (skipped {skipped_external} external refs — preserved in GML).")

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
    log(f"JSON saved → {json_file}")

    try:
        itemgraph.write_to_file(gml_file)
        log(f"GML saved → {gml_file}")
    except Exception as e:
        warn(f"Could not save GML: {e}")

    return graph_data["summary"]


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

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

        # Single broad fetch — shared by both pipelines
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
        log(f"  CSV inventory  → {args.out}")
        log(f"  JSON graph     → {args.json}")
        log(f"  GML graph      → {args.gml}")
        log("=" * 60)

    except Exception as e:
        err(f"CRITICAL ERROR: {e}")
        raise


if __name__ == "__main__":
    main()
