import os
import datetime
import zipfile
import shutil
import urllib3
import json
import csv
import argparse
from typing import List, Tuple, Dict, Optional
from arcgis.gis import GIS
from concurrent.futures import ThreadPoolExecutor, as_completed

# Suppress HTTPS warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------
# Logging
# ---------------------------
def log(msg: str):
    # Remove Unicode characters for Windows compatibility
    safe_msg = msg.replace("✓", "[OK]").replace("✗", "[FAIL]").replace("→", "->")
    try:
        print(safe_msg, flush=True)
    except UnicodeEncodeError:
        # Fallback for Windows console encoding issues
        print(safe_msg.encode('ascii', 'ignore').decode('ascii'), flush=True)

# ---------------------------
# GIS Connection
# ---------------------------
def connect_to_gis(connection_string: str = "home") -> GIS:
    try:
        gis = GIS(connection_string)
        uname = gis.users.me.username if gis.users.me else "anonymous"
        portal = getattr(gis.properties, "portalName", "ArcGIS")
        log(f"[OK] Connected to: {portal} as {uname}")
        return gis
    except Exception as e:
        log(f"[ERR] Error connecting to GIS: {e}")
        raise

# ---------------------------
# FS Utilities
# ---------------------------
def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def make_backup_dir(dest_root: str, item_title: str) -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in (item_title or "untitled") if c.isalnum() or c in (" ", "_")).rstrip()
    safe_title = safe_title[:100] or "untitled"
    backup_dir = os.path.join(dest_root, f"{safe_title}_{timestamp}")
    ensure_dir(backup_dir)
    return backup_dir

def file_exists_and_nonempty(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:
        return False

def any_file_in_dir_nonempty(path: str) -> bool:
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fpath = os.path.join(root, f)
                if os.path.getsize(fpath) > 0:
                    return True
        return False
    except Exception:
        return False

def compress_backup(backup_dir: str, delete_uncompressed: bool = True) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        zip_path = f"{backup_dir}.zip"
        base_dir = os.path.dirname(backup_dir)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(backup_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, base_dir)
                    zipf.write(file_path, arcname)
        if not file_exists_and_nonempty(zip_path):
            return False, None, "Zip file is missing or empty."
        log(f"[ZIP] Compressed backup to: {zip_path}")
        if delete_uncompressed:
            shutil.rmtree(backup_dir, ignore_errors=True)
            log(f"[CLEAN] Deleted uncompressed folder: {backup_dir}")
        return True, zip_path, None
    except Exception as e:
        return False, None, f"Compression failed: {e}"

def append_log_line(backup_dir: str, line: str):
    try:
        with open(os.path.join(backup_dir, "backup_log.txt"), "a", encoding="utf-8") as logf:
            logf.write(line.rstrip() + "\n")
    except Exception:
        pass

# ---------------------------
# Artifact helpers
# ---------------------------
def save_metadata_only(item, backup_dir: str):
    try:
        data = {
            "title": item.title,
            "id": item.id,
            "type": item.type,
            "url": getattr(item, "url", None),
            "owner": getattr(item, "owner", None),
            "tags": getattr(item, "tags", []),
            "description": getattr(item, "description", ""),
            "created": getattr(item, "created", None),
            "modified": getattr(item, "modified", None),
        }
        path = os.path.join(backup_dir, f"{item.title}_metadata.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        append_log_line(backup_dir, f"METADATA: {item.title}")
    except Exception as e:
        log(f"[WARN] Could not save minimal metadata for {getattr(item, 'title', 'unknown')}: {e}")

def backup_json_metadata(item, backup_dir: str):
    try:
        metadata = getattr(item, "_json", None)
        if metadata:
            path = os.path.join(backup_dir, f"{item.title}_metadata_full.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)
            append_log_line(backup_dir, f"JSON_METADATA: {item.title}")
    except Exception as e:
        log(f"[WARN] Could not save JSON metadata for {getattr(item, 'title', 'unknown')}: {e}")

def backup_thumbnail(item, backup_dir: str):
    try:
        item.download_thumbnail(save_folder=backup_dir)
        append_log_line(backup_dir, f"THUMBNAIL: {item.title}")
    except Exception as e:
        log(f"[WARN] Thumbnail not downloaded for {getattr(item, 'title', 'unknown')}: {e}")

# ---------------------------
# Resource and Data helpers
# ---------------------------
def backup_item_resources(item, backup_dir: str) -> Tuple[bool, Optional[str]]:
    try:
        resources = getattr(item, "resources", None)
        if not resources:
            return True, "No resources"
        res_zip_path = os.path.join(backup_dir, "resources.zip")
        item.resources.export(save_path=backup_dir, file_name="resources.zip")
        if os.path.isfile(res_zip_path) and os.path.getsize(res_zip_path) > 0:
            append_log_line(backup_dir, f"RESOURCES: {item.title}")
            log(f"[OK] Exported all resources for {item.title} -> {res_zip_path}")
            return True, None
        else:
            return False, "Resource ZIP missing or empty."
    except Exception as e:
        log(f"[WARN] Failed to export resources for {item.title}: {e}")
        return False, str(e)

def backup_item_data_json(item, backup_dir: str) -> Tuple[bool, Optional[str]]:
    try:
        data = item.get_data()
        path = os.path.join(backup_dir, f"{item.title}_data.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data if data is not None else {}, f, indent=2, ensure_ascii=False)
        append_log_line(backup_dir, f"DATA_JSON: {item.title}")
        return True, None
    except Exception as e:
        return False, f"get_data failed: {e}"

# ---------------------------
# Download/Export handlers
# ---------------------------
def download_item(item, backup_dir: str) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        log(f"[TASK] Downloading {item.title}...")
        path = item.download(save_path=backup_dir)
        if isinstance(path, str) and file_exists_and_nonempty(path):
            append_log_line(backup_dir, f"DOWNLOAD: {item.title}")
            log(f"[OK] Downloaded: {path}")
            return True, path, None
        if path and os.path.isdir(path) and any_file_in_dir_nonempty(path):
            append_log_line(backup_dir, f"DOWNLOAD_DIR: {item.title}")
            log(f"[OK] Downloaded to folder: {path}")
            return True, path, None
        return False, None, "Download returned no file or empty content."
    except Exception as e:
        return False, None, f"Download failed: {e}"

def export_item(item, export_format: str, backup_dir: str, label: str, keep_exports: bool = False) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        log(f"[TASK] Exporting {label} {item.title} as {export_format}...")
        export = item.export(f"{item.title}_export", export_format=export_format, wait=True)
        try:
            path = export.download(backup_dir)
            if isinstance(path, str) and os.path.isfile(path) and os.path.getsize(path) > 0:
                append_log_line(backup_dir, f"EXPORT_{export_format.upper().replace(' ', '_')}: {item.title}")
                log(f"[OK] Exported to: {path}")
                return True, path, None
            if path and os.path.isdir(path) and any_file_in_dir_nonempty(path):
                append_log_line(backup_dir, f"EXPORTDIR_{export_format.upper().replace(' ', '_')}: {item.title}")
                log(f"[OK] Exported to folder: {path}")
                return True, path, None
            return False, None, "Export produced no file or empty content."
        finally:
            if not keep_exports:
                try:
                    export.delete()
                    log(f"[CLEAN] Deleted temporary export item: {export.id}")
                except Exception as de:
                    log(f"[WARN] Could not delete temporary export item: {de}")
            else:
                log(f"[INFO] Keeping temporary export item: {export.id}")
    except Exception as e:
        return False, None, f"Export failed: {e}"

def try_create_replica(item, backup_dir: str) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        if not getattr(item, "url", None):
            return False, None, "No service URL; replica not applicable."
        layers = getattr(item, "layers", None)
        if not layers:
            return False, None, "Item has no layers; replica not applicable."
        layer_ids = []
        for lyr in layers:
            try:
                lid = lyr.properties.id
                layer_ids.append(str(lid))
            except Exception:
                continue
        if not layer_ids:
            return False, None, "No layer IDs available for replica."
        params = {
            "f": "json",
            "replicaName": f"{item.title}_replica",
            "layers": ",".join(layer_ids),
            "returnAttachments": True,
            "attachmentsSyncDirection": "none",
            "syncModel": "none",
            "dataFormat": "filegdb",
            "async": True,
            "transportType": "esriTransportTypeUrl"
        }
        url = item.url.rstrip("/") + "/createReplica"
        resp = item._con.post(url, params)
        if not resp or "resultUrl" not in resp:
            return False, None, f"Replica response invalid: {resp}"
        import requests
        r = requests.get(resp["resultUrl"], stream=True, timeout=600)
        r.raise_for_status()
        out = os.path.join(backup_dir, f"{item.title}_replica.gdb.zip")
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        if file_exists_and_nonempty(out):
            append_log_line(backup_dir, f"REPLICA_FGDB: {item.title}")
            log(f"[OK] Replica downloaded: {out}")
            return True, out, None
        return False, None, "Replica file empty."
    except Exception as e:
        return False, None, f"Replica failed: {e}"

# ---------------------------
# Backup logic per item
# ---------------------------
def backup_item(
    item,
    dest_root: str,
    keep_uncompressed: bool,
    include_thumbnails: bool,
    try_export_fgdb: bool,
    keep_exports: bool = False,
) -> Tuple[bool, Optional[str], str]:
    log(f"\n=== Backing up: {item.title} ({item.type}) ===")
    backup_dir = make_backup_dir(dest_root, item.title)
    item_type = (item.type or "").lower()
    type_keywords = [k.lower() for k in getattr(item, "typeKeywords", []) or []]

    try:
        save_metadata_only(item, backup_dir)
        backup_json_metadata(item, backup_dir)
        backup_item_data_json(item, backup_dir)
        if include_thumbnails:
            backup_thumbnail(item, backup_dir)
        backup_item_resources(item, backup_dir)
        try:
            rel = {
                "forward": [ri.id for ri in (item.related_items("forward") or [])],
                "reverse": [ri.id for ri in (item.related_items("reverse") or [])]
            }
            with open(os.path.join(backup_dir, f"{item.title}_relationships.json"), "w", encoding="utf-8") as f:
                json.dump(rel, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    except Exception as pree:
        log(f"[WARN] Pre-backup metadata/resources capture issue: {pree}")

    data_ok = False
    data_reason = "No data captured."

    try:
        if ("feature layer" in item_type) or ("feature service" in item_type) or ("table" in item_type):
            if try_export_fgdb:
                ok, _, err = export_item(item, "File Geodatabase", backup_dir, "Feature", keep_exports=keep_exports)
                if ok:
                    data_ok, data_reason = True, "Exported as File Geodatabase."
                else:
                    log(f"[WARN] FGDB export failed: {err}")
            if not data_ok:
                ok, _, err = try_create_replica(item, backup_dir)
                if ok:
                    data_ok, data_reason = True, "Created replica as File Geodatabase."
                else:
                    log(f"[WARN] Replica fallback failed: {err}")
            if not data_ok:
                ok, _, err = download_item(item, backup_dir)
                if ok:
                    data_ok, data_reason = True, "Downloaded item package."
                else:
                    data_reason = f"No reliable data export. Download failed: {err}"

        elif ("survey123" in type_keywords) or (item.type and item.type.lower() == "form"):
            dj_ok, _ = backup_item_data_json(item, backup_dir)
            res_ok, _ = backup_item_resources(item, backup_dir)
            related_ok = False
            try:
                related_items = item.related_items("forward", "Survey2Data") or []
                for ri in related_items:
                    ok, _, err = export_item(ri, "File Geodatabase", backup_dir, "Survey Data", keep_exports=keep_exports)
                    if ok:
                        related_ok = True
                        break
                if not related_ok:
                    for ri in item.related_items("forward") or []:
                        if "feature" in (ri.type or "").lower():
                            ok, _, err = export_item(ri, "File Geodatabase", backup_dir, "Survey Data", keep_exports=keep_exports)
                            if ok:
                                related_ok = True
                                break
            except Exception as se:
                log(f"[WARN] Survey related data export attempt failed: {se}")
            if dj_ok and (related_ok or res_ok):
                data_ok = True
                data_reason = "Survey form JSON/resources saved; survey data exported if available."

        else:
            data_json_ok, _ = backup_item_data_json(item, backup_dir)
            res_ok, _ = backup_item_resources(item, backup_dir)
            if data_json_ok:
                data_ok = True
                data_reason = "Saved JSON definition and resources."
            else:
                export_type = "Web Map" if "web map" in item_type else "Web Mapping Application"
                ok, _, err = export_item(item, export_type, backup_dir, "Item", keep_exports=keep_exports)
                if ok:
                    data_ok, data_reason = True, f"Exported as {export_type}."
                else:
                    ok, _, err = download_item(item, backup_dir)
                    if ok:
                        data_ok, data_reason = True, "Downloaded item content."
                    else:
                        data_reason = f"No reliable export or download: {err}"

        if not data_ok:
            message = f"FAILED: {item.title} ({item.id}) — {data_reason}. Metadata/resources saved for diagnostics."
            log("[ERR] " + message)
            append_log_line(backup_dir, message)
            return False, None, message

        success_zip, zip_path, zip_err = compress_backup(backup_dir, delete_uncompressed=not keep_uncompressed)
        if not success_zip:
            message = f"FAILED: {item.title} ({item.id}) — {zip_err}"
            log("[ERR] " + message)
            append_log_line(backup_dir, message)
            return False, None, message

        message = f"SUCCESS: {item.title} ({item.id}) — {data_reason}. Zip: {zip_path}"
        log("[OK] " + message)
        append_log_line(os.path.dirname(zip_path), message)
        return True, zip_path, message

    except Exception as e:
        message = f"FAILED: {item.title} ({item.id}) — Unexpected error: {e}"
        log("[ERR] " + message)
        append_log_line(backup_dir, message)
        return False, None, message

# ---------------------------
# Batch OCM Backup
# ---------------------------
def backup_batch_with_ocm(
    item_ids: List[str],
    gis: GIS,
    dest_root: str,
    try_export_fgdb: bool = True,
) -> Tuple[bool, Optional[str], str]:
    """
    Backup a batch of items as a single OCM export (more efficient).
    Returns: (success, contentexport_path, message)
    
    Use this for backing up multiple related items together.
    The single .contentexport includes all items + all their dependencies.
    """
    try:
        # Validate API version
        if not hasattr(gis.content, "offline"):
            return False, None, "OfflineContentManager not available. Requires ArcGIS API for Python >= 2.4.1"
        
        if not item_ids:
            return False, None, "No item IDs provided for batch OCM backup."
        
        log(f"[OCM] Preparing batch export for {len(item_ids)} item(s)...")
        
        # Fetch all Item objects
        items = []
        failed_ids = []
        for item_id in item_ids:
            try:
                item = gis.content.get(item_id)
                if item:
                    items.append(item)
                else:
                    failed_ids.append(item_id)
            except Exception as e:
                log(f"[WARN] Could not fetch item {item_id}: {e}")
                failed_ids.append(item_id)
        
        if not items:
            return False, None, f"No valid items found. Failed IDs: {failed_ids}"
        
        if failed_ids:
            log(f"[WARN] Skipping {len(failed_ids)} items that could not be fetched: {failed_ids}")
        
        log(f"[OCM] Exporting {len(items)} item(s) + dependencies as single package...")
        
        # Create batch name (Windows-safe)
        item_titles = [item.title for item in items]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Remove problematic characters for Windows filenames
        safe_names = "-".join([
            "".join(c for c in t if c.isalnum() or c in ("_", "-"))[:15] 
            for t in item_titles[:5]
        ])
        safe_names = safe_names.replace("--", "-").strip("-") or "batch"
        package_name = f"batch_{safe_names}_{timestamp}"
        
        service_format = "File Geodatabase" if try_export_fgdb else "Shapefile"
        
        # Single OCM call for entire batch
        ocm = gis.content.offline
        backup_path = ocm.export_items(
            items=items,
            output_folder=dest_root,
            package_name=package_name,
            service_format=service_format,
        )
        
        if not backup_path or not os.path.isfile(backup_path):
            return False, None, "OCM export returned no file or invalid path."
        
        if not file_exists_and_nonempty(backup_path):
            return False, None, "OCM package file is empty."
        
        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        log(f"[OCM] Batch export complete: {backup_path} ({size_mb:.2f} MB)")
        msg = f"Batch OCM export: {len(items)} items + dependencies ({size_mb:.2f} MB)"
        return True, backup_path, msg
        
    except Exception as e:
        msg = f"Batch OCM export failed: {e}"
        log(f"[ERR] {msg}")
        return False, None, msg

# ---------------------------
# Single item wrapper
# ---------------------------
def backup_by_id(
    item_id: str,
    gis: GIS,
    dest_root: str,
    keep_uncompressed: bool,
    include_thumbnails: bool,
    try_export_fgdb: bool,
    keep_exports: bool = False,
    use_ocm_per_item: bool = False,
) -> Tuple[str, bool, Optional[str], str]:
    try:
        item = gis.content.get(item_id)
        if not item:
            msg = f"FAILED: No item found with ID: {item_id}"
            log("[ERR] " + msg)
            return item_id, False, None, msg
        
        # OCM per-item mode: one .contentexport per item
        if use_ocm_per_item:
            if not hasattr(gis.content, "offline"):
                log(f"[WARN] OCM not available for {item.title}, falling back to standard backup")
                use_ocm_per_item = False
            else:
                log(f"[OCM] Exporting {item.title} as .contentexport...")
                try:
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    safe_title = "".join(c for c in item.title if c.isalnum() or c in ("_", "-"))[:50]
                    safe_title = safe_title.replace("--", "-").strip("-") or "item"
                    package_name = f"{safe_title}_{timestamp}"
                    
                    ocm = gis.content.offline
                    backup_path = ocm.export_items(
                        items=[item],
                        output_folder=dest_root,
                        package_name=package_name,
                        service_format="File Geodatabase",
                    )
                    
                    if backup_path and os.path.isfile(backup_path) and os.path.getsize(backup_path) > 0:
                        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
                        msg = f"SUCCESS: {item.title} ({item.id}) - OCM export ({size_mb:.2f} MB). Path: {backup_path}"
                        log(f"[OK] {msg}")
                        return item_id, True, backup_path, msg
                    else:
                        log(f"[WARN] OCM export returned empty or invalid path, falling back to standard")
                        use_ocm_per_item = False
                except Exception as e:
                    log(f"[WARN] OCM per-item export failed: {e}, falling back to standard")
                    use_ocm_per_item = False
        
        # Standard per-item backup (default or fallback from OCM)
        if not use_ocm_per_item:
            success, zip_path, message = backup_item(
                item, dest_root, keep_uncompressed, include_thumbnails, try_export_fgdb, keep_exports=keep_exports
            )
            return item_id, success, zip_path, message
            
    except Exception as e:
        msg = f"FAILED: {item_id} — {e}"
        log("[ERR] " + msg)
        return item_id, False, None, msg

# ---------------------------
# CSV reader
# ---------------------------
def read_ids_from_csv(csv_path: str) -> List[str]:
    with open(csv_path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        if not reader.fieldnames:
            raise ValueError("CSV has no headers.")
        headers = [h.strip().lower() for h in reader.fieldnames]
        try:
            id_idx = headers.index("id")
        except ValueError:
            id_idx = 0
        ids: List[str] = []
        for row in reader:
            values = list(row.values())
            if not values:
                continue
            val = values[id_idx]
            if val:
                ids.append(val.strip())
        return ids

# ---------------------------
# Batch runner
# ---------------------------
def backup_from_csv(
    csv_path: str,
    dest_root: str,
    connection: str = "home",
    max_workers: int = 4,
    keep_uncompressed: bool = False,
    include_thumbnails: bool = True,
    try_export_fgdb: bool = True,
    keep_exports: bool = False,
    backup_mode: str = "standard",
):
    """
    backup_mode options:
    - "standard": Per-item .zip files (old method)
    - "ocm_per_item": Per-item .contentexport files (OCM, one per item)
    - "ocm_batch": Single .contentexport for all items (OCM, batched)
    """
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    ensure_dir(dest_root)

    gis = connect_to_gis(connection)
    item_ids = read_ids_from_csv(csv_path)
    if not item_ids:
        log("[WARN] No item IDs found in CSV.")
        return

    log(f"Starting backup of {len(item_ids)} item(s) to: {dest_root}")
    log(f"Backup mode: {backup_mode.upper()}")
    log(f"Workers: {max_workers} | Keep uncompressed: {keep_uncompressed} | Thumbnails: {include_thumbnails} | Export FGDB: {try_export_fgdb} | Keep AGOL exports: {keep_exports}")

    results: Dict[str, Tuple[bool, Optional[str], str]] = {}
    success_count = 0
    fail_count = 0

    # OCM batch mode: single .contentexport for all items
    if backup_mode == "ocm_batch":
        log("\n[OCM] Running batch export (single .contentexport for all items + dependencies)...")
        ocm_success, ocm_path, ocm_msg = backup_batch_with_ocm(item_ids, gis, dest_root, try_export_fgdb)
        if ocm_success:
            log(f"[OCM] Batch export successful: {ocm_path}")
            for iid in item_ids:
                results[iid] = (True, ocm_path, "Included in batch OCM export")
            success_count = len(item_ids)
        else:
            log(f"[OCM] Batch export failed: {ocm_msg}")
            log("[INFO] Falling back to standard per-item backup...")
            backup_mode = "standard"

    # Standard or OCM per-item: use threading
    if backup_mode in ["standard", "ocm_per_item"]:
        use_ocm = (backup_mode == "ocm_per_item")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {
                executor.submit(
                    backup_by_id,
                    item_id,
                    gis,
                    dest_root,
                    keep_uncompressed,
                    include_thumbnails,
                    try_export_fgdb,
                    keep_exports,
                    use_ocm,
                ): item_id for item_id in item_ids
            }

            for future in as_completed(future_to_id):
                item_id = future_to_id[future]
                try:
                    _id, success, zip_path, message = future.result()
                    results[item_id] = (success, zip_path, message)
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    msg = f"FAILED: {item_id} — {e}"
                    results[item_id] = (False, None, msg)
                    fail_count += 1
                    log("[ERR] " + msg)

    # Summary
    log("\n" + "=" * 72)
    log("Backup Summary")
    log("=" * 72)
    log(f"Total: {len(item_ids)} | Success: {success_count} | Failed: {fail_count}\n")

    if success_count:
        log("Successful backups:")
        for iid, (ok, zpath, msg) in results.items():
            if ok:
                log(f"- {iid} | {zpath}")
    if fail_count:
        log("\nFailed backups:")
        for iid, (ok, zpath, msg) in results.items():
            if not ok:
                log(f"- {iid} | {msg}")

# ---------------------------
# CLI
# ---------------------------
def parse_args(argv: Optional[List[str]] = None):
    p = argparse.ArgumentParser(description="Back up ArcGIS Online/Portal items by IDs from a CSV.")
    p.add_argument("--csv", required=True, help="Path to CSV containing an 'id' column or IDs in first column.")
    p.add_argument("--dest", required=True, help="Destination folder for backups.")
    p.add_argument("--connection", default="home", help="ArcGIS connection string (default: home).")
    p.add_argument("--workers", type=int, default=4, help="Max concurrent backups.")
    p.add_argument("--keep-uncompressed", action="store_true", help="Keep the folder after zipping.")
    p.add_argument("--no-thumbnails", action="store_true", help="Do not download thumbnails.")
    p.add_argument("--no-fgdb", action="store_true", help="Do not try to export Feature Layers/Services to File Geodatabase.")
    p.add_argument("--keep-exports", action="store_true", help="Keep temporary export items in ArcGIS after download.")
    p.add_argument("--mode", choices=["standard", "ocm_per_item", "ocm_batch"], default="standard", 
                   help="Backup mode: standard (per-item .zip), ocm_per_item (per-item .contentexport), ocm_batch (single .contentexport).")
    return p.parse_args(argv)

def main(argv: Optional[List[str]] = None):
    args = parse_args(argv)
    backup_from_csv(
        csv_path=args.csv,
        dest_root=args.dest,
        connection=args.connection,
        max_workers=args.workers,
        keep_uncompressed=args.keep_uncompressed,
        include_thumbnails=not args.no_thumbnails,
        try_export_fgdb=not args.no_fgdb,
        keep_exports=args.keep_exports,
        backup_mode=args.mode,
    )

if __name__ == "__main__":
    main()