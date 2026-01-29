import os
import sys
import json
import zipfile
import shutil
import argparse
from typing import Optional, List, Dict, Any, Tuple
from arcgis.gis import GIS

# =====================================================================
# SAFE CONSOLE OUTPUT
# =====================================================================
def _safe_print(msg: str):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe, flush=True)

def log(msg: str): _safe_print(msg)
def ok(msg: str): _safe_print(f"[OK] {msg}")
def warn(msg: str): _safe_print(f"[WARN] {msg}")
def err(msg: str): _safe_print(f"[ERR] {msg}")

# =====================================================================
# GIS CONNECTION
# =====================================================================
def connect_to_gis(connection: str = "home") -> GIS:
    try:
        gis = GIS(connection)
        user_me = gis.users.me
        uname = user_me.username if user_me else "anonymous"
        portal = getattr(gis.properties, "portalName", "ArcGIS")
        ok(f"Connected to: {portal} as {uname}")
        return gis
    except Exception as e:
        err(f"Error connecting to GIS: {e}")
        raise

# =====================================================================
# FILE UTILITIES
# =====================================================================
def ensure_dir(path: str): 
    os.makedirs(path, exist_ok=True)

def is_contentexport(file_path: str) -> bool:
    """Check if file is a .contentexport by extension"""
    return file_path.lower().endswith(".contentexport")

def extract_zip(zip_path: str, work_dir: Optional[str] = None) -> str:
    """Extract standard ZIP backup"""
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"Backup ZIP not found: {zip_path}")
    base = os.path.abspath(work_dir or os.path.splitext(zip_path)[0])
    ensure_dir(base)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(base)
    ok(f"Extracted: {zip_path} -> {base}")
    return base

def load_json_if_exists(path: str) -> Optional[Dict[str, Any]]:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        warn(f"Could not load JSON {path}: {e}")
    return None

# =====================================================================
# OCM RESTORE (for .contentexport files)
# =====================================================================
def restore_contentexport(
    contentexport_path: str,
    gis: GIS,
    overwrite: bool = False
) -> Tuple[bool, Optional[List[str]]]:
    """
    Restore items from a .contentexport file using OfflineContentManager.
    Returns: (success, list_of_item_ids)
    """
    try:
        # Validate OCM availability
        if not hasattr(gis.content, "offline"):
            err("OfflineContentManager not available. Requires ArcGIS API for Python >= 2.4.1")
            return False, None
        
        if not os.path.isfile(contentexport_path):
            err(f"ContentExport file not found: {contentexport_path}")
            return False, None
        
        log(f"[OCM] Restoring from .contentexport: {contentexport_path}")
        ocm = gis.content.offline
        
        # List items in the package first
        try:
            items_dict = ocm.list_items(contentexport_path)
            log(f"[OCM] Package contains {len(items_dict)} item(s)")
            for item_id, item_info in items_dict.items():
                title = item_info.get('title', 'Unknown')
                item_type = item_info.get('type', 'Unknown')
                log(f"[OCM]   - {title} ({item_type})")
        except Exception as e:
            warn(f"Could not list package contents: {e}")
        
        # Import items from package
        log(f"[OCM] Importing items...")
        imported_items = ocm.import_content(
            contentexport_path,
            folder=None,
            fail_fast=False,  # Continue on errors
            failure_rollback=False  # Don't rollback all on single failure
        )
        
        # Extract item IDs from result
        if imported_items and isinstance(imported_items, dict):
            item_ids = list(imported_items.keys())
            ok(f"Successfully imported {len(item_ids)} item(s)")
            return True, item_ids
        elif imported_items and isinstance(imported_items, list):
            # If it returns a list of items, get their IDs
            item_ids = [item.id if hasattr(item, 'id') else str(item) for item in imported_items]
            ok(f"Successfully imported {len(item_ids)} item(s)")
            return True, item_ids
        else:
            warn("Import returned unexpected format")
            return False, None
            
    except Exception as e:
        err(f"ContentExport restore failed: {e}")
        import traceback
        traceback.print_exc()
        return False, None

# =====================================================================
# STANDARD ZIP RESTORE (for .zip files)
# =====================================================================
def load_backup_artifacts(extract_dir: str) -> Dict[str, Any]:
    """Load metadata and data from extracted ZIP backup"""
    meta_file = None
    data_file = None

    # Find metadata and data JSON files
    for f in os.listdir(extract_dir):
        if f.endswith("_metadata.json") and not f.endswith("_metadata_full.json"):
            meta_file = os.path.join(extract_dir, f)
        elif f.endswith("_data.json"):
            data_file = os.path.join(extract_dir, f)

    if not meta_file:
        raise RuntimeError("metadata.json not found in backup (expected *_metadata.json).")

    meta = load_json_if_exists(meta_file) or {}
    base_title = os.path.basename(meta_file).replace("_metadata.json", "")
    data_json = load_json_if_exists(data_file)
    
    # Find thumbnail
    thumbnail = None
    for f in os.listdir(extract_dir):
        if f.lower() in ["thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"]:
            thumbnail = os.path.join(extract_dir, f)
            break

    # Find resources
    resources_zip = None
    for f in os.listdir(extract_dir):
        if f == "resources.zip" and os.path.isfile(os.path.join(extract_dir, f)):
            resources_zip = os.path.join(extract_dir, f)
            break

    return {
        "base_title": base_title,
        "meta": meta,
        "data_json": data_json,
        "thumbnail": thumbnail,
        "resources_zip": resources_zip
    }

def create_item(
    gis: GIS,
    base_title: str,
    meta: Dict[str, Any],
    item_type: Optional[str] = None,
    folder: Optional[str] = None,
    thumbnail: Optional[str] = None,
    text_data: Optional[Dict[str, Any]] = None
) -> str:
    """Create an item in GIS from backup metadata"""
    title = meta.get("title", base_title)
    
    # Check for existing items and avoid duplicates
    existing = gis.content.search(f'title:"{title}"', max_items=100)
    if existing:
        title = f"{title}_{len(existing)+1}"

    props = {
        "title": title,
        "type": item_type or meta.get("type", "Web Map"),
        "tags": meta.get("tags", []),
        "snippet": meta.get("snippet") or "",
        "description": meta.get("description") or "",
        "accessInformation": meta.get("accessInformation") or "",
        "licenseInfo": meta.get("licenseInfo") or "",
    }

    log(f"Creating item: {title} ({props['type']})")

    # Get or create folder
    folder_obj = None
    if folder:
        folders_dict = {f['title']: f for f in gis.users.me.folders}
        if folder not in folders_dict:
            folder_obj = gis.users.me.create_folder(folder)
        else:
            folder_obj = folders_dict[folder]

    # Prepare data
    data_to_add = json.dumps(text_data) if text_data else None

    # Add item
    try:
        if folder_obj:
            new_item = folder_obj.add(item_properties=props, file=None, text=data_to_add, thumbnail=thumbnail)
        else:
            new_item = gis.content.add(item_properties=props, file=None, text=data_to_add, thumbnail=thumbnail)
        
        ok(f"Created item: {new_item.title} ({new_item.id})")
        return new_item.id
    except Exception as e:
        err(f"Failed to create item: {e}")
        raise

def restore_resources(item, resources_zip_path: Optional[str]):
    """Restore resources from resources.zip to item"""
    if not resources_zip_path or not os.path.isfile(resources_zip_path):
        log("No resources to restore.")
        return
    
    try:
        temp_dir = os.path.join(os.path.dirname(resources_zip_path), "resources_temp")
        ensure_dir(temp_dir)
        
        with zipfile.ZipFile(resources_zip_path, "r") as zf:
            zf.extractall(temp_dir)
        
        rm = item.resources
        count = 0
        for root, _, files in os.walk(temp_dir):
            for f in files:
                file_path = os.path.join(root, f)
                rel_path = os.path.relpath(file_path, temp_dir).replace("\\", "/")
                try:
                    rm.add(file=file_path, file_name=rel_path)
                    count += 1
                except Exception as e:
                    warn(f"Failed to add resource {rel_path}: {e}")
        
        shutil.rmtree(temp_dir, ignore_errors=True)
        ok(f"Restored {count} resource(s)")
    except Exception as e:
        warn(f"Failed to restore resources: {e}")

def restore_zip(
    zip_path: str,
    gis: GIS,
    keep_metadata: bool = True
) -> Optional[str]:
    """Restore a standard .zip backup"""
    extract_dir = None
    try:
        log(f"Restoring from .zip: {zip_path}")
        extract_dir = extract_zip(zip_path)
        art = load_backup_artifacts(extract_dir)
        
        item_id = create_item(
            gis,
            base_title=art["base_title"],
            meta=art["meta"],
            item_type=art["meta"].get("type"),
            folder=None,
            thumbnail=art["thumbnail"],
            text_data=art["data_json"]
        )
        
        # Get the created item and restore resources
        new_item = gis.content.get(item_id)
        restore_resources(new_item, art["resources_zip"])
        
        ok(f"Successfully restored item: {item_id}")
        return item_id
        
    except Exception as e:
        err(f"ZIP restore failed: {e}")
        return None
    finally:
        if extract_dir and os.path.isdir(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)

# =====================================================================
# MAIN RESTORE DISPATCHER
# =====================================================================
def restore_backup(
    backup_path: str,
    connection: str = "home",
    overwrite: bool = False,
    keep_metadata: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Restore a backup file (.contentexport or .zip).
    Returns: (success, item_ids_or_message)
    """
    if not os.path.exists(backup_path):
        err(f"Backup file not found: {backup_path}")
        return False, None
    
    try:
        gis = connect_to_gis(connection)
        
        # Determine format and restore accordingly
        if is_contentexport(backup_path):
            log(f"Detected .contentexport format")
            success, item_ids = restore_contentexport(backup_path, gis, overwrite)
            if success and item_ids:
                return True, ",".join(item_ids)
            else:
                return False, "ContentExport import failed"
        else:
            log(f"Detected .zip format")
            item_id = restore_zip(backup_path, gis, keep_metadata)
            if item_id:
                return True, item_id
            else:
                return False, "ZIP restore failed"
    
    except Exception as e:
        err(f"Restore failed: {e}")
        import traceback
        traceback.print_exc()
        return False, None

# =====================================================================
# CLI
# =====================================================================
def parse_args(argv: Optional[List[str]] = None):
    p = argparse.ArgumentParser(description="Restore ArcGIS items from backups (.zip or .contentexport).")
    p.add_argument("--backup", required=True, help="Path to backup file (.zip or .contentexport).")
    p.add_argument("--connection", default="home", help="ArcGIS connection string (default: home).")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing items (for .contentexport).")
    p.add_argument("--keep-metadata", action="store_true", default=True, help="Preserve original metadata.")
    return p.parse_args(argv)

def main(argv: Optional[List[str]] = None):
    args = parse_args(argv)
    success, result = restore_backup(
        backup_path=args.backup,
        connection=args.connection,
        overwrite=args.overwrite,
        keep_metadata=args.keep_metadata
    )
    
    if success:
        ok(f"Restore completed. Restored items: {result}")
        sys.exit(0)
    else:
        err(f"Restore failed: {result}")
        sys.exit(1)

if __name__ == "__main__":
    main()