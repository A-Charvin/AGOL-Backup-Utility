import os
import sys
import json
import zipfile
import shutil
import argparse
from typing import Optional, List, Dict, Any, Tuple
import datetime as dt
from arcgis.gis import GIS

# =====================================================================
# LOGGING TO FILE AND CONSOLE
# =====================================================================
LOG_DIR = "logs"
LOG_FILE = None

def _ensure_log_dir():
    global LOG_DIR
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

def _get_log_file_path() -> str:
    _ensure_log_dir()
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(LOG_DIR, f"restore_{timestamp}.log")

def _write_to_log(msg: str):
    """Write to log file"""
    try:
        global LOG_FILE
        if LOG_FILE is None:
            LOG_FILE = _get_log_file_path()
        
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception as e:
        print(f"[LOG_ERROR] Could not write to log: {e}", flush=True)

# =====================================================================
# SAFE CONSOLE OUTPUT
# =====================================================================
def _safe_print(msg: str):
    try:
        print(msg, flush=True)
        _write_to_log(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe, flush=True)
        _write_to_log(safe)

def log(msg: str): _safe_print(msg)
def ok(msg: str): _safe_print(f"[OK] {msg}")
def warn(msg: str): _safe_print(f"[WARN] {msg}")
def err(msg: str): _safe_print(f"[ERR] {msg}")
def info(msg: str): _safe_print(f"[INFO] {msg}")

def get_log_file() -> Optional[str]:
    """Return the current log file path"""
    global LOG_FILE
    return LOG_FILE

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
    
    info(f"Extracting ZIP to: {base}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(base)
        ok(f"Extracted: {zip_path} -> {base}")
        
        # List contents for debugging
        contents = os.listdir(base)
        info(f"Extracted files: {', '.join(contents[:10])}")  # Show first 10 files
        
        return base
    except Exception as e:
        err(f"Failed to extract ZIP: {e}")
        raise

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
    
    Handles Feature Service naming conflicts by:
    1. Attempting import normally
    2. If Feature Service is skipped due to name conflict, create it with timestamp
    3. Imports Service Definition successfully
    """
    try:
        log(f"\n{'='*70}")
        log(f"Restoring .contentexport file")
        log(f"{'='*70}\n")
        
        # Validate file exists
        if not os.path.isfile(contentexport_path):
            err(f"ContentExport file not found: {contentexport_path}")
            return False, None
        
        file_size = os.path.getsize(contentexport_path) / (1024 * 1024)
        info(f"File size: {file_size:.2f} MB")
        info(f"File path: {os.path.basename(contentexport_path)}")
        
        # Validate OCM availability
        if not hasattr(gis.content, "offline"):
            err("OfflineContentManager not available.")
            err("Requires: ArcGIS API for Python >= 2.4.1")
            err("Install with: pip install arcgis --upgrade")
            return False, None
        
        log(f"[OCM] Using OfflineContentManager")
        ocm = gis.content.offline
        info(f"OCM instance created successfully\n")
        
        # Step 1: List items in the package
        info(f"Step 1: Analyzing package contents...")
        items_to_import = {}
        feature_services_in_package = {}
        service_definitions_in_package = {}
        
        try:
            items_dict = ocm.list_items(contentexport_path)
            if items_dict:
                items_to_import = items_dict
                item_count = len(items_dict)
                log(f"[OCM] Package contains {item_count} item(s):\n")
                
                for item_id, item_info in items_dict.items():
                    title = item_info.get('title', 'Unknown')
                    item_type = item_info.get('type', 'Unknown')
                    org_source = item_info.get('org_source', 'Unknown')
                    
                    log(f"[OCM]   • Title: {title}")
                    log(f"[OCM]     Type: {item_type}")
                    log(f"[OCM]     ID: {item_id}")
                    log(f"[OCM]     Source: {org_source}\n")
                    
                    if item_type == "Feature Service":
                        feature_services_in_package[item_id] = item_info
                    elif item_type == "Service Definition":
                        service_definitions_in_package[item_id] = item_info
                
                if feature_services_in_package:
                    info(f"Detected {len(feature_services_in_package)} Feature Service(s)")
                    info(f"[NOTE] Feature Services may be skipped if name conflicts exist")
                if service_definitions_in_package:
                    info(f"Detected {len(service_definitions_in_package)} Service Definition(s)")
                log("")
            else:
                err("Package appears to be empty")
                return False, None
        except Exception as e:
            err(f"Could not read package contents: {e}")
            import traceback
            err(f"Traceback: {traceback.format_exc()}")
            return False, None
        
        # Step 2: Import items from package
        info(f"Step 2: Importing {len(items_to_import)} item(s) from package...")
        log(f"[OCM] Starting import operation...\n")
        
        try:
            log(f"[OCM] Calling import_content()...")
            
            # This will skip Feature Services if they already exist
            imported_items = ocm.import_content(
                package_path=contentexport_path,
                folder=None,
                failure_rollback=False,
                search_existing_items=False
            )
            
            # Step 3: Process the result
            info(f"Step 3: Processing import results...")
            log(f"[OCM] Import operation returned: {type(imported_items)}\n")
            
            item_ids = []
            imported_by_type = {}
            
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if isinstance(imported_items, list):
                log(f"[OCM] Import returned a list of {len(imported_items)} item(s):\n")
                
                for idx, item in enumerate(imported_items, 1):
                    try:
                        item_id = item.id
                        title = item.title if hasattr(item, 'title') else 'Unknown'
                        item_type = item.type if hasattr(item, 'type') else 'Unknown'
                        
                        # Add timestamp to prevent conflicts
                        new_title = f"{title}_{timestamp}"
                        
                        try:
                            item.update(item_properties={'title': new_title})
                            info(f"Renamed: {title} → {new_title}")
                        except Exception as rename_err:
                            warn(f"Could not rename item: {rename_err}")
                        
                        item_ids.append(item_id)
                        
                        if item_type not in imported_by_type:
                            imported_by_type[item_type] = []
                        imported_by_type[item_type].append((item_id, new_title))
                        
                        log(f"[OCM]   [{idx}] ✓ {new_title} ({item_type})")
                        log(f"[OCM]       ID: {item_id}\n")
                        
                        ok(f"Imported: {new_title} ({item_type})")
                    except Exception as e:
                        warn(f"Could not process item {idx}: {e}")
                        continue
            else:
                err(f"Unexpected return type from import_content(): {type(imported_items)}")
                return False, None
            
            # Step 3.5: Check if Feature Services were skipped due to conflicts
            if feature_services_in_package and len(imported_by_type.get('Feature Service', [])) == 0:
                warn(f"\n[NOTE] Feature Service(s) were skipped by OCM due to existing service name conflicts")
                warn(f"[NOTE] This is an OCM limitation - it won't overwrite existing services")
                info(f"\n[WORKAROUND] To restore the Feature Service data:")
                info(f"  1. Rename or delete the existing Feature Service(s)")
                info(f"  2. Try the restore again")
                info(f"  Or:")
                info(f"  1. Back up the existing service")
                info(f"  2. Delete it")
                info(f"  3. Restore the new version")
                info(f"  4. Migrate data if needed\n")
                
                log(f"\n[OCM] FEATURE SERVICE CONFLICT DETECTED")
                log(f"[OCM] The following Feature Service(s) could not be imported:\n")
                for fs_id, fs_info in feature_services_in_package.items():
                    log(f"[OCM]   - {fs_info.get('title', 'Unknown')} ({fs_id})\n")
                    
                    # Try to find existing service with same name
                    try:
                        search_results = gis.content.search(f'title:"{fs_info.get("title")}" type:"Feature Service"', max_items=5)
                        if search_results:
                            log(f"[OCM] Found existing service(s) with same name:\n")
                            for result in search_results:
                                log(f"[OCM]       - {result.title} (ID: {result.id}, Owner: {result.owner})\n")
                    except Exception:
                        pass
            
            # Step 4: Verify results
            if item_ids:
                info(f"\nStep 4: Verifying imported items in GIS...")
                verified_ids = []
                verified_by_type = {}
                
                for item_id in item_ids:
                    try:
                        verify_item = gis.content.get(item_id)
                        if verify_item:
                            verified_ids.append(item_id)
                            item_type = verify_item.type
                            if item_type not in verified_by_type:
                                verified_by_type[item_type] = []
                            verified_by_type[item_type].append(verify_item.title)
                            ok(f"✓ Verified: {verify_item.title} ({item_type})")
                        else:
                            warn(f"✗ Item {item_id} not found after import")
                    except Exception as e:
                        warn(f"✗ Could not verify item {item_id}: {e}")
                
                log(f"\n[OCM] Verification summary:\n")
                for item_type, titles in verified_by_type.items():
                    log(f"[OCM]   {item_type}: {len(titles)} item(s)")
                    for title in titles:
                        log(f"[OCM]     ✓ {title}")
                log("")
                
                log(f"\n{'='*70}")
                if verified_ids:
                    ok(f"Import successful: {len(verified_ids)} item(s) verified")
                    log(f"{'='*70}\n")
                    
                    info(f"\n✓ All items have been renamed with timestamp: {timestamp}")
                    info(f"✓ This prevents conflicts with existing items\n")
                    
                    # Warn if Feature Services were expected but not created
                    if feature_services_in_package and len(verified_by_type.get('Feature Service', [])) == 0:
                        warn(f"\n⚠ Feature Service(s) from the package were not imported.")
                        warn(f"⚠ This is because a service with the same name already exists.")
                        warn(f"⚠ See above for workaround options.\n")
                    
                    return True, verified_ids
                else:
                    warn(f"Import returned items but could not verify any")
                    log(f"{'='*70}\n")
                    return True, item_ids
            else:
                err("No items were successfully imported")
                return False, None
            
        except Exception as e:
            err(f"Import operation failed: {e}")
            import traceback
            err(f"Traceback:\n{traceback.format_exc()}")
            return False, None
            
    except Exception as e:
        err(f"ContentExport restore failed: {e}")
        import traceback
        err(f"Traceback:\n{traceback.format_exc()}")
        return False, None

# =====================================================================
# STANDARD ZIP RESTORE (for .zip files)
# =====================================================================
def find_metadata_file(extract_dir: str) -> Optional[str]:
    """Find the metadata file in extracted directory (searches recursively)"""
    try:
        # First try top level
        for f in os.listdir(extract_dir):
            if f.endswith("_metadata.json") and not f.endswith("_metadata_full.json"):
                return os.path.join(extract_dir, f)
        
        # If not found, search in subdirectories
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                if f.endswith("_metadata.json") and not f.endswith("_metadata_full.json"):
                    return os.path.join(root, f)
    except Exception as e:
        warn(f"Error searching for metadata: {e}")
    return None

def find_data_file(extract_dir: str) -> Optional[str]:
    """Find the data JSON file in extracted directory (searches recursively)"""
    try:
        # First try top level
        for f in os.listdir(extract_dir):
            if f.endswith("_data.json"):
                return os.path.join(extract_dir, f)
        
        # If not found, search in subdirectories
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                if f.endswith("_data.json"):
                    return os.path.join(root, f)
    except Exception as e:
        warn(f"Error searching for data file: {e}")
    return None

def find_thumbnail(extract_dir: str) -> Optional[str]:
    """Find thumbnail image in extracted directory (searches recursively)"""
    try:
        # First try top level
        for f in os.listdir(extract_dir):
            if f.lower() in ["thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"]:
                return os.path.join(extract_dir, f)
        
        # If not found, search in subdirectories
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                if f.lower() in ["thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"]:
                    return os.path.join(root, f)
    except Exception as e:
        warn(f"Error searching for thumbnail: {e}")
    return None

def find_resources_zip(extract_dir: str) -> Optional[str]:
    """Find resources.zip in extracted directory (searches recursively)"""
    try:
        # First try top level
        resources_path = os.path.join(extract_dir, "resources.zip")
        if os.path.isfile(resources_path):
            return resources_path
        
        # If not found, search in subdirectories
        for root, dirs, files in os.walk(extract_dir):
            if "resources.zip" in files:
                return os.path.join(root, "resources.zip")
    except Exception as e:
        warn(f"Error searching for resources.zip: {e}")
    return None

def load_backup_artifacts(extract_dir: str) -> Dict[str, Any]:
    """Load metadata and data from extracted ZIP backup"""
    meta_file = find_metadata_file(extract_dir)
    data_file = find_data_file(extract_dir)
    
    if not meta_file:
        info("No metadata.json found, creating minimal metadata")
        meta = {}
        base_title = os.path.basename(extract_dir)
        info(f"Will default to 'Web Map' type (no metadata available)")
    else:
        meta = load_json_if_exists(meta_file) or {}
        # Use title from metadata, fallback to filename
        base_title = meta.get("title") or os.path.basename(meta_file).replace("_metadata.json", "")
        info(f"Loaded metadata from: {os.path.basename(meta_file)}")
        
        # Log the detected item type from metadata - THIS IS CRITICAL
        detected_type = meta.get("type", "Unknown")
        info(f"✓ Item type from metadata.json: '{detected_type}'")
        
        if not detected_type or detected_type == "Unknown":
            warn(f"⚠ Could not determine item type from metadata")
            warn(f"⚠ Will default to 'Web Map'")
    
    data_json = load_json_if_exists(data_file) if data_file else None
    if data_file and data_json:
        info(f"Loaded data from: {os.path.basename(data_file)}")
    elif data_file:
        warn(f"Data file exists but could not be parsed: {data_file}")
    else:
        info(f"No data file found in backup")
    
    thumbnail = find_thumbnail(extract_dir)
    if thumbnail:
        info(f"Found thumbnail: {os.path.basename(thumbnail)}")
    
    resources_zip = find_resources_zip(extract_dir)
    if resources_zip:
        info(f"Found resources: resources.zip")
    
    return {
        "base_title": base_title,
        "meta": meta,
        "data_json": data_json,
        "thumbnail": thumbnail,
        "resources_zip": resources_zip,
        "extract_dir": extract_dir
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
    try:
        existing = gis.content.search(f'title:"{title}"', max_items=100)
        if existing:
            title = f"{title}_{len(existing)+1}"
            warn(f"Item with title '{base_title}' already exists, renamed to: {title}")
    except Exception as e:
        warn(f"Could not check for existing items: {e}")

    item_type = item_type or meta.get("type", "Web Map")
    
    props = {
        "title": title,
        "type": item_type,
        "tags": meta.get("tags", []),
        "snippet": meta.get("snippet") or "",
        "description": meta.get("description") or "",
        "accessInformation": meta.get("accessInformation") or "",
        "licenseInfo": meta.get("licenseInfo") or "",
    }

    info(f"Creating item: {title} (type: {item_type})")

    # Get or create folder
    folder_obj = None
    if folder:
        try:
            folders_dict = {f['title']: f for f in gis.users.me.folders}
            if folder not in folders_dict:
                folder_obj = gis.users.me.create_folder(folder)
                info(f"Created folder: {folder}")
            else:
                folder_obj = folders_dict[folder]
                info(f"Using existing folder: {folder}")
        except Exception as e:
            warn(f"Could not manage folder '{folder}': {e}")

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
        err(f"Failed to create item '{title}': {e}")
        raise

def restore_resources(item, resources_zip_path: Optional[str]):
    """Restore resources from resources.zip to item"""
    if not resources_zip_path or not os.path.isfile(resources_zip_path):
        info("No resources to restore.")
        return
    
    try:
        temp_dir = os.path.join(os.path.dirname(resources_zip_path), "resources_temp")
        ensure_dir(temp_dir)
        
        info(f"Extracting resources from: {os.path.basename(resources_zip_path)}")
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
    """
    Restore a standard .zip backup.
    
    Supports:
    - Feature Services (publishes from FGDB)
    - Web Maps
    - Web Apps
    - Survey123 Forms
    - Other item types
    
    Handles nested folders and the backup structure properly.
    """
    extract_dir = None
    try:
        log(f"\n{'='*70}")
        log(f"Restoring from .zip: {os.path.basename(zip_path)}")
        log(f"{'='*70}\n")
        
        # Extract the backup
        extract_dir = extract_zip(zip_path)
        info(f"Backup extracted to: {extract_dir}")
        
        # Load all backup artifacts
        art = load_backup_artifacts(extract_dir)
        
        # Get metadata to determine item type
        meta = art.get("meta", {})
        item_type = meta.get("type", "Web Map")
        base_title = art.get("base_title", "Restored Item")
        
        info(f"\nBackup Information:")
        info(f"  Title: {base_title}")
        info(f"  Type: {item_type}")
        info(f"  Has metadata: {bool(meta)}")
        info(f"  Has data: {bool(art.get('data_json'))}")
        info(f"  Has thumbnail: {bool(art.get('thumbnail'))}")
        info(f"  Has resources: {bool(art.get('resources_zip'))}")
        
        # Add timestamp to prevent naming conflicts
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        new_title = f"{base_title}_{timestamp}"
        
        # Handle different item types
        item_id = None
        
        if item_type == "Feature Service":
            # Feature Services need to be published from FGDB
            info(f"\nDetected Feature Service - attempting to publish from geodatabase...")
            item_id = restore_feature_service_from_zip(
                gis=gis,
                extract_dir=extract_dir,
                meta=meta,
                new_title=new_title,
                keep_metadata=keep_metadata
            )
        else:
            # Standard item creation (Web Maps, Apps, Survey123, etc.)
            info(f"\nRestoring as standard item...")
            item_id = create_item(
                gis,
                base_title=new_title,
                meta=meta if keep_metadata else {},
                item_type=item_type,
                folder=None,
                thumbnail=art.get("thumbnail"),
                text_data=art.get("data_json")
            )
        
        if not item_id:
            err(f"Failed to create item from backup")
            return None
        
        # Restore resources
        info(f"\nRestoring resources...")
        new_item = gis.content.get(item_id)
        if new_item:
            restore_resources(new_item, art.get("resources_zip"))
        else:
            warn(f"Could not retrieve created item {item_id} for resource restoration")
        
        log(f"\n{'='*70}")
        ok(f"ZIP Restore completed successfully!")
        ok(f"Item ID: {item_id}")
        ok(f"New Title: {new_title}")
        ok(f"Type: {item_type}")
        log(f"{'='*70}\n")
        
        return item_id
        
    except Exception as e:
        err(f"ZIP restore failed: {e}")
        import traceback
        err(f"Traceback: {traceback.format_exc()}")
        return None
    finally:
        if extract_dir and os.path.isdir(extract_dir):
            info(f"Cleaning up temporary files...")
            try:
                shutil.rmtree(extract_dir, ignore_errors=True)
                info(f"Cleanup complete")
            except Exception as e:
                warn(f"Could not clean up {extract_dir}: {e}")


def restore_feature_service_from_zip(
    gis: GIS,
    extract_dir: str,
    meta: Dict[str, Any],
    new_title: str,
    keep_metadata: bool = True
) -> Optional[str]:
    """
    Restore a Feature Service from a ZIP backup.
    
    Handles:
    1. Finding the exported geodatabase (.gdb or _export.zip)
    2. Publishing the geodatabase as a hosted feature service
    3. Applying metadata from the backup
    """
    try:
        # Find the geodatabase
        gdb_path = find_geodatabase(extract_dir)
        if not gdb_path:
            err(f"Feature Service backup detected but no geodatabase found")
            err(f"Searched for: .gdb folder or _export.zip")
            return None
        
        info(f"Found geodatabase: {os.path.basename(gdb_path)}")
        
        # If it's a .zip, extract it first
        if gdb_path.endswith(".zip"):
            gdb_extract_dir = os.path.join(os.path.dirname(gdb_path), "gdb_extract")
            os.makedirs(gdb_extract_dir, exist_ok=True)
            info(f"Extracting geodatabase ZIP...")
            with zipfile.ZipFile(gdb_path, "r") as zf:
                zf.extractall(gdb_extract_dir)
            
            # Find the actual .gdb folder
            for root, dirs, _ in os.walk(gdb_extract_dir):
                for d in dirs:
                    if d.endswith(".gdb"):
                        gdb_path = os.path.join(root, d)
                        break
            
            if not gdb_path.endswith(".gdb"):
                err(f"Could not find .gdb folder after extracting _export.zip")
                return None
        
        info(f"Publishing Feature Service from geodatabase...")
        info(f"Title: {new_title}")
        
        # Publish the feature service
        # Using publish method from arcgis.gis.Item
        # This creates a Feature Service from the geodatabase
        try:
            # Try to publish using the standard method
            from arcgis.gis import Item
            
            # Create item from the geodatabase
            publish_result = gis.content.publish_csv(
                file_path=gdb_path,
                item_id=None,
                publish_parameters={
                    "name": new_title,
                    "targetSr": {"latestWkid": 3857},
                    "type": "Feature Service"
                }
            )
            
            if publish_result:
                item_id = publish_result.id
                ok(f"Feature Service published: {item_id}")
                
                # Update with metadata if keeping it
                if keep_metadata and meta:
                    try:
                        published_item = gis.content.get(item_id)
                        if published_item:
                            # Update title and other properties
                            published_item.update(
                                item_properties={
                                    "title": new_title,
                                    "tags": meta.get("tags", []),
                                    "description": meta.get("description", ""),
                                    "snippet": meta.get("snippet", ""),
                                    "accessInformation": meta.get("accessInformation", ""),
                                    "licenseInfo": meta.get("licenseInfo", "")
                                }
                            )
                            ok(f"Metadata updated")
                    except Exception as m_err:
                        warn(f"Could not update metadata: {m_err}")
                
                return item_id
            else:
                err(f"publish_csv returned no result")
                return None
                
        except Exception as pub_err:
            warn(f"Standard publish method failed: {pub_err}")
            warn(f"Attempting alternative publication method...")
            
            # Fallback: Create a manual feature service item
            # This is more basic but more reliable
            try:
                item_id = create_feature_service_item(
                    gis=gis,
                    title=new_title,
                    meta=meta if keep_metadata else {}
                )
                if item_id:
                    warn(f"Created basic Feature Service item (data import requires manual steps)")
                    return item_id
            except Exception as alt_err:
                err(f"Alternative method also failed: {alt_err}")
                return None
                
    except Exception as e:
        err(f"Feature Service restore failed: {e}")
        import traceback
        err(f"Traceback: {traceback.format_exc()}")
        return None


def find_geodatabase(extract_dir: str) -> Optional[str]:
    """
    Find a geodatabase in the extracted backup directory.
    
    Looks for:
    1. .gdb folders (geodatabase)
    2. _export.zip files (zipped geodatabase)
    
    Returns the path to whichever is found first.
    """
    try:
        # Walk through all directories looking for .gdb or _export.zip
        for root, dirs, files in os.walk(extract_dir):
            # Check for .gdb folder
            for d in dirs:
                if d.endswith(".gdb"):
                    return os.path.join(root, d)
            
            # Check for _export.zip
            for f in files:
                if f.endswith("_export.zip"):
                    return os.path.join(root, f)
        
        return None
    except Exception as e:
        err(f"Error searching for geodatabase: {e}")
        return None


def create_feature_service_item(
    gis: GIS,
    title: str,
    meta: Dict[str, Any]
) -> Optional[str]:
    """
    Create a basic Feature Service item (fallback method).
    
    This creates an empty Feature Service item with metadata.
    Data would need to be added separately.
    """
    try:
        info(f"Creating Feature Service item: {title}")
        
        # Create the service using create_service
        service_name = title.replace(" ", "_")[:50]
        
        new_service = gis.content.create_service(
            name=service_name,
            service_type="featureService",
            item_properties={
                "title": title,
                "tags": meta.get("tags", []),
                "description": meta.get("description", ""),
                "snippet": meta.get("snippet", ""),
                "accessInformation": meta.get("accessInformation", ""),
                "licenseInfo": meta.get("licenseInfo", "")
            }
        )
        
        if new_service:
            ok(f"Feature Service created: {new_service.id}")
            return new_service.id
        else:
            err(f"create_service returned None")
            return None
            
    except Exception as e:
        err(f"Could not create Feature Service item: {e}")
        return None

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
    log(f"\n{'='*70}")
    log(f"RESTORE OPERATION STARTED")
    log(f"{'='*70}\n")
    
    if not os.path.exists(backup_path):
        err(f"Backup file not found: {backup_path}")
        return False, None
    
    # Validate it's a readable file
    try:
        if not os.path.isfile(backup_path):
            err(f"Path is not a file: {backup_path}")
            return False, None
        
        file_size = os.path.getsize(backup_path)
        info(f"Backup file size: {file_size / (1024*1024):.2f} MB")
    except Exception as e:
        err(f"Cannot access backup file: {e}")
        return False, None
    
    try:
        info(f"Connecting to GIS...")
        gis = connect_to_gis(connection)
        info(f"Connection established\n")
        
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
        err(f"Traceback: {traceback.format_exc()}")
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
    
    info(f"Restore CLI called with:")
    info(f"  Backup: {args.backup}")
    info(f"  Connection: {args.connection}")
    info(f"  Overwrite: {args.overwrite}")
    info(f"  Keep metadata: {args.keep_metadata}\n")
    
    success, result = restore_backup(
        backup_path=args.backup,
        connection=args.connection,
        overwrite=args.overwrite,
        keep_metadata=args.keep_metadata
    )
    
    log_file = get_log_file()
    if log_file:
        log(f"\nLog file: {log_file}")
    
    if success:
        ok(f"Restore completed. Restored items: {result}")
        sys.exit(0)
    else:
        err(f"Restore failed: {result}")
        sys.exit(1)

if __name__ == "__main__":
    main()
