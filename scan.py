import pandas as pd
from arcgis.gis import GIS
import urllib3
import time
import csv
import os
import argparse

# Suppress HTTPS warnings for environments with SSL inspection
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def PrintWithTime(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

def GetItemDetails(gis, item):
    """
    Extracts core metadata only. 
    Using getattr to safely handle potential missing attributes.
    """
    return {
        "Title": item.title,
        "Id": item.id,
        "Type": item.type,
        "Owner": item.owner,
        "Created": pd.to_datetime(item.created, unit="ms"),
        "Modified": pd.to_datetime(item.modified, unit="ms"),
        "RestUrl": getattr(item, "url", ""),
        "ItemPageUrl": f"{gis.url}/home/item.html?id={item.id}",
        "Tags": ", ".join(item.tags or []),
        "ContentStatus": getattr(item, "content_status", "")
    }

def GenerateInventory(gis, out_file, index_file, max_items):
    # STRICT filter list to prevent 'fuzzy' search results from entering CSV
    VALID_STATUSES = ['org_authoritative', 'public_authoritative']
    
    # Server-side query to narrow down the initial list
    query = 'contentstatus:org_authoritative OR contentstatus:public_authoritative'
    
    # Load Index (item_id -> modified_timestamp)
    # This prevents re-processing items that haven't changed
    index = {}
    if os.path.exists(index_file):
        with open(index_file, 'r', encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            index = {row['id']: int(row['mod']) for row in reader}

    PrintWithTime("Querying server for potential authoritative items...")
    raw_items = gis.content.search(query=query, max_items=max_items, outside_org=False)
    PrintWithTime(f"Server returned {len(raw_items)} matches. Starting strict validation...")

    new_records = []
    skipped_not_auth = 0
    skipped_no_change = 0

    for item in raw_items:
        # --- STEP 1: Strict Status Validation ---
        # Ensures items with 'authoritative' in tags/description are excluded
        actual_status = getattr(item, "content_status", "")
        if actual_status not in VALID_STATUSES:
            skipped_not_auth += 1
            continue

        # --- STEP 2: Delta Change Check ---
        # Skip if we already have this version of the item in our CSV
        if item.id in index and index[item.id] >= item.modified:
            skipped_no_change += 1
            continue
        
        # --- STEP 3: Extraction ---
        new_records.append(GetItemDetails(gis, item))
        index[item.id] = item.modified

    PrintWithTime(f"Filtered out {skipped_not_auth} non-authoritative items.")
    PrintWithTime(f"Skipped {skipped_no_change} items with no new updates.")

    if new_records:
        df = pd.DataFrame(new_records)
        # Append to CSV (creates file if it doesn't exist)
        header = not os.path.exists(out_file)
        os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
        df.to_csv(out_file, mode='a', index=False, header=header, encoding="utf-8-sig")
        
        # Update the Index file for the next run
        with open(index_file, 'w', newline='', encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=['id', 'mod'])
            writer.writeheader()
            for k, v in index.items():
                writer.writerow({'id': k, 'mod': v})
        
        PrintWithTime(f"SUCCESS: Added/Updated {len(new_records)} items in {out_file}.")
    else:
        PrintWithTime("Inventory is already 100% up to date.")

def main():
    parser = argparse.ArgumentParser(description="Strict Authoritative Layer Scanner")
    parser.add_argument("--out", default="AuthInventory.csv", help="The final report CSV")
    parser.add_argument("--index", default="scan_index.csv", help="The tracking file for speed")
    parser.add_argument("--max", type=int, default=10000, help="Max items to scan")
    args = parser.parse_args()

    try:
        # Connect using the active ArcGIS Pro/Python profile
        gis = GIS("home")
        PrintWithTime(f"Connected to {gis.url}")
        
        GenerateInventory(gis, args.out, args.index, args.max)
    except Exception as e:
        PrintWithTime(f"CRITICAL ERROR: {e}")

if __name__ == "__main__":
    main()