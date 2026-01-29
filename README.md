# AGOL Backup Utility

A comprehensive backup and restore solution for ArcGIS Online (AGOL) and ArcGIS Portal items. This utility provides three main operations: scanning and cataloging authoritative layers, creating flexible backups in multiple formats, and restoring items from those backups.

![License](https://img.shields.io/badge/license-CC%20BY-blue)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![ArcGIS](https://img.shields.io/badge/ArcGIS%20API-2.4.2-green)

---

## Table of Contents

- [Features](#features)
- [Disclaimer](#disclaimer)
- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [GUI Application](#gui-application)
  - [Command-Line Interface](#command-line-interface)
  - [Workflow Overview](#workflow-overview)
- [Components](#components)
  - [Application GUI (`ui.py`)](#application-gui-uipy)
  - [Layer Scanner (`scan.py`)](#layer-scanner-scanpy)
  - [Backup Engine (`backup.py`)](#backup-engine-backuppy)
  - [Restore Module (`restore.py`)](#restore-module-restorepy)
- [Backup Modes](#backup-modes)
- [File Structure](#file-structure)
- [Dependencies](#dependencies)
- [Troubleshooting](#troubleshooting)
- [Performance Considerations](#performance-considerations)
- [License](#license)
- [Contributing](#contributing)

---

## Features

### 🔍 Scanning & Inventory
- Scans ArcGIS Online/Portal for authoritative content
- Strict validation filters to ensure data integrity
- Incremental updates using change tracking (index-based)
- CSV-based inventory export with comprehensive metadata

### 💾 Flexible Backup Formats
- **Standard ZIP** - Per-item .zip files with full metadata and resources
- **OCM Per-Item** - Individual .contentexport files for each item (OCM format)
- **OCM Batch** - Single .contentexport file containing all items and dependencies (most efficient)

### 🔄 Advanced Restoration
- Supports both .zip and .contentexport formats
- Preserves original metadata and thumbnails
- Handles item-to-item relationships
- Feature layer data restoration with spatial data
- Survey123 form backup and recovery

### ⚙️ User Interface
- Professional GUI built with Tkinter
- Three-tab workflow: Scan → Backup → Restore
- Real-time progress monitoring and detailed logging
- Configurable backup settings with persistent preferences

---

## Disclaimer

**⚠️ IMPORTANT - PLEASE READ BEFORE USE**

This utility is provided "AS IS" without warranty of any kind, express or implied. By using this software, you acknowledge and accept the following:

1. **No Liability for Data Loss**: The authors and contributors of this project are **not liable** for any data loss, corruption, or damage that may occur during backup, restore, or any operations performed by this utility. Users are solely responsible for verifying backup integrity and testing restore procedures.

2. **Use at Your Own Risk**: This software is used entirely at your own risk. You assume all responsibility and risk for the use of this tool and its outputs. Before performing backup or restore operations on production systems, thoroughly test in a non-production environment first.

3. **Backup Verification**: Always verify that backups are complete and valid before relying on them. Test restore procedures in a development or staging environment to ensure data can be successfully recovered.

4. **Production Caution**: Exercise extreme caution when using this utility with production AGOL/Portal systems. Consider the following:
   - Back up your AGOL/Portal data using Esri's native backup tools as well
   - Test restore operations in a staging environment first
   - Monitor backup and restore operations closely
   - Keep detailed logs of all operations
   - Maintain regular snapshots or copies of your authoritative data

5. **No Technical Support Guarantee**: While support may be provided, there is no guarantee of response time or resolution of issues.

6. **Compliance and Regulations**: Ensure your use of this tool complies with all applicable laws, regulations, and your organization's data governance policies.

**We strongly recommend:**
- Creating multiple backup copies using different methods
- Regularly testing your ability to restore from backups
- Maintaining offline copies of critical data
- Reviewing all backup and restore logs for errors
- Having a documented disaster recovery plan

---

## System Requirements

### Software Requirements
- **Python**: 3.13 or higher
- **ArcGIS API for Python**: Version 2.4.2 (October 2025 release)
- **ArcGIS Pro**: 3.6 with cloned Python environment
- **Operating System**: Windows (with full support)

### Hardware Recommendations
- **RAM**: Minimum 8 GB (16 GB recommended for large item backups)
- **Disk Space**: Adequate space for backup destination (varies by item size)
- **Network**: Stable internet connection for ArcGIS Online/Portal access

### Environment Setup

The utility **requires a cloned Python environment** from ArcGIS Pro with the necessary packages pre-installed. Do not use a standalone Python installation.

1. **Create a cloned Python environment in ArcGIS Pro 3.6:**
   ```bash
   # In ArcGIS Pro Python Command Prompt
   proswap backup-env
   ```

2. **Verify required packages are available:**
   ```bash
   python -c "from arcgis.gis import GIS; print('ArcGIS installed')"
   pip show arcgis
   pip show pandas
   ```

The cloned environment from ArcGIS Pro includes `arcgis`, `pandas`, and `urllib3` by default. The application uses the ArcGIS Python API modules such as `gis.home()` which are only available in the ArcGIS Pro cloned environment and cannot be replicated in a standalone Python installation.

---

## Installation

### 1. Clone or Download the Repository

```bash
git clone https://github.com/A-Charvin/AGOL-Backup-Utility.git
cd AGOL-Backup-Utility
```

### 2. Configure ArcGIS Connection

The application uses `GIS("home")` to connect, which uses your default ArcGIS Pro profile. Ensure you're authenticated:

```python
# In Python or Pro Python Command Prompt
from arcgis.gis import GIS
gis = GIS("home")  # Should authenticate without prompting if configured
```

### 3. Verify Installation

Run the GUI to verify everything is working:

```bash
python ui.py
```

---

## Configuration

### Configuration File (`config.json`)

The application automatically creates and manages `config.json` with your preferences:

```json
{
  "csv_path": "output/AuthInventory.csv",
  "backup_dir": "backups",
  "backup_mode": "standard"
}
```

**Fields:**
- `csv_path`: Path to the inventory CSV (created/updated by scan)
- `backup_dir`: Default directory for backups
- `backup_mode`: Default backup mode (`standard`, `ocm_per_item`, or `ocm_batch`)

---

## Usage

### GUI Application

Run the main application:

```bash
python ui.py
```

The GUI provides three main tabs:

#### **Tab 1: Scan Layers**
1. Click "Run Layer Scan" or select an existing CSV
2. Application queries AGOL/Portal for authoritative items
3. Results are saved to the configured CSV path
4. An index file tracks changes for incremental updates

**Options:**
- Use an existing CSV if you've already scanned
- Run a new scan to get the latest inventory
- Specify CSV path and output location

#### **Tab 2: Backup Items**
1. Click "Load Items from CSV" to populate the item list
2. Select backup mode: Standard, OCM Per-Item, or OCM Batch
3. Use checkboxes or "Select All" to choose items
4. Click "Start Backup of Selected Items"
5. Monitor progress in the log panel

**Backup Modes:**
- **Standard**: Creates individual .zip files per item
- **OCM Per-Item**: Creates individual .contentexport files
- **OCM Batch**: Creates single .contentexport with all items and dependencies (recommended)

#### **Tab 3: Restore Items**
1. Click "Browse..." to select a backup file (.zip or .contentexport)
2. Configure restore options:
   - "Overwrite existing items" - Replace if item already exists
   - "Preserve original metadata" - Keep original metadata
3. Click "Start Restore"
4. Monitor restoration in the log

---

### Command-Line Interface

#### Scan Command

```bash
python scan.py --out output/AuthInventory.csv --index output/scan_index.csv --max 10000
```

**Arguments:**
- `--out`: Output CSV file path (default: `AuthInventory.csv`)
- `--index`: Index file for tracking changes (default: `scan_index.csv`)
- `--max`: Maximum items to scan (default: `10000`)

#### Backup Command

```bash
python backup.py --csv items.csv --dest backups/ --mode standard --workers 4
```

**Arguments:**
- `--csv` (required): CSV file with item IDs
- `--dest` (required): Destination directory for backups
- `--mode`: Backup mode - `standard`, `ocm_per_item`, or `ocm_batch` (default: `standard`)
- `--workers`: Number of parallel backup threads (default: `4`)
- `--connection`: ArcGIS connection string (default: `home`)
- `--keep-uncompressed`: Keep uncompressed folders after zipping
- `--no-thumbnails`: Skip downloading item thumbnails
- `--no-fgdb`: Don't export Feature Layers to File Geodatabase
- `--keep-exports`: Keep temporary export items in AGOL after backup

**Example - OCM Batch Mode:**
```bash
python backup.py --csv inventory.csv --dest ./backups --mode ocm_batch
```

#### Restore Command

```bash
python restore.py --backup backups/item_name_20250129_120000.zip --connection home --keep-metadata
```

**Arguments:**
- `--backup` (required): Path to .zip or .contentexport file
- `--connection`: ArcGIS connection string (default: `home`)
- `--overwrite`: Overwrite existing items (for .contentexport files)
- `--keep-metadata`: Preserve original metadata (default: `True`)

---

### Workflow Overview

```
1. SCAN
   ├─ Run scan.py
   ├─ Creates: AuthInventory.csv, scan_index.csv
   └─ Identifies authoritative items

2. BACKUP
   ├─ Select items from CSV
   ├─ Choose backup mode (standard/ocm_per_item/ocm_batch)
   ├─ Run backup.py
   └─ Creates: .zip or .contentexport files

3. RESTORE
   ├─ Select backup file
   ├─ Configure restore options
   ├─ Run restore.py
   └─ Items restored to AGOL/Portal
```

---

## Components

### Application GUI (`ui.py`)

**Purpose:** Multi-tab GUI application for managing backup workflow

**Key Classes:**
- `ScriptRunner`: Manages subprocess execution with real-time output capture
- `App`: Main Tkinter application with three-tab interface

**Features:**
- Configuration persistence
- Real-time logging with scroll-to-end behavior
- Progress bar for long-running operations
- Tab-based workflow navigation
- Configurable paths and options

**Key Methods:**
- `_BuildUI()`: Constructs the user interface
- `_RunScan()`: Initiates layer scanning
- `_RunBackup()`: Starts backup process with selected items
- `_RunRestore()`: Executes restore operation
- `_LogMsg()`: Appends timestamped messages to log panel

---

### Layer Scanner (`scan.py`)

**Purpose:** Query AGOL/Portal and identify authoritative, catalogable content

**Key Functions:**
- `GenerateInventory()`: Main scanning function
  - Queries AGOL for items with `content_status:org_authoritative OR content_status:public_authoritative`
  - Performs strict validation (filters false positives)
  - Implements delta change detection using index
  - Appends new/updated records to CSV

- `GetItemDetails()`: Extracts core metadata
  - Title, ID, Type, Owner
  - Creation/modification timestamps
  - Tags, description, access information
  - REST URL and item page URL

**Scanning Strategy:**
1. Server-side query filters by content status
2. Strict client-side validation (prevents fuzzy matches)
3. Delta detection skips unchanged items
4. Index-based tracking for incremental runs

**Output Files:**
- `AuthInventory.csv`: Complete item inventory with metadata
- Index file: Tracks item IDs and modification timestamps

---

### Backup Engine (`backup.py`)

**Purpose:** Create backups in multiple formats with flexible export options

**Key Functions:**

**Standard ZIP Backup:**
- `backup_item()`: Per-item backup workflow
  - Saves metadata (full JSON + minimal metadata)
  - Downloads thumbnails
  - Exports data (get_data, file, or replica)
  - Handles resources and relationships
  - Compresses to .zip

- `backup_item_data_json()`: Extracts item definition
- `backup_item_resources()`: Exports item resources
- `download_item()`: Downloads item package
- `export_item()`: Exports to Web Map, FGDB, or other formats
- `try_create_replica()`: Creates feature service replicas for offline use

**OCM Batch Backup:**
- `backup_batch_with_ocm()`: Single .contentexport for all items
  - More efficient for multiple related items
  - Automatically handles dependencies
  - Single file per batch vs. one per item

**Supporting Functions:**
- `backup_thumbnail()`: Downloads item thumbnail
- `backup_json_metadata()`: Exports full item JSON
- `compress_backup()`: Creates .zip archive
- `read_ids_from_csv()`: Parses CSV for item IDs
- `backup_from_csv()`: Batch processing orchestrator

**Backup Modes:**

| Mode | Format | Use Case |
|------|--------|----------|
| `standard` | .zip | Individual item backups with full flexibility |
| `ocm_per_item` | .contentexport | Per-item OCM format, compatible with AGOL |
| `ocm_batch` | .contentexport | Batch backup, most efficient for multiple items |

**Threading:**
- Multi-threaded backup using `ThreadPoolExecutor`
- Configurable worker count (default: 4)
- Real-time progress reporting

---

### Restore Module (`restore.py`)

**Purpose:** Restore items from .zip or .contentexport backups

**Key Functions:**

**Standard ZIP Restore:**
- `restore_zip()`: Restore from .zip file
  - Extracts backup archive
  - Loads metadata and data files
  - Creates new item with GIS.content.add()
  - Restores resources and relationships

- `load_backup_artifacts()`: Parses backup contents
- `create_item()`: Instantiates new AGOL/Portal item
- `restore_resources()`: Restores item resources
- `extract_zip()`: Extracts .zip backup safely

**OCM Restore:**
- `restore_contentexport()`: Restore from .contentexport
  - Uses OfflineContentManager API
  - Lists package contents
  - Imports items and dependencies
  - Handles batch restoration

**Supporting Functions:**
- `connect_to_gis()`: Establish AGOL/Portal connection
- `load_json_if_exists()`: Safe JSON loading
- `ensure_dir()`: Create directory structure
- `is_contentexport()`: File format detection

**Restore Options:**
- `overwrite`: Replace existing items (OCM mode)
- `keep_metadata`: Preserve original metadata and properties

---

## Backup Modes

### Standard ZIP Backup
- **Format:** Per-item .zip files
- **Contents:**
  - `*_metadata.json`: Minimal metadata
  - `*_metadata_full.json`: Complete item JSON
  - `*_data.json`: Item definition/data
  - `thumbnail.png/jpg`: Item thumbnail
  - `resources.zip`: Associated resources
  - `*_relationships.json`: Item relationships
  - `backup_log.txt`: Operation log

- **Advantages:**
  - Individual backup per item
  - Easy selective restore
  - Maximum flexibility
  - Compatible with all item types

- **Disadvantages:**
  - More files (one .zip per item)
  - No automatic dependency handling

**Example:**
```bash
python backup.py --csv items.csv --dest backups/ --mode standard
```

### OCM Per-Item Backup
- **Format:** Per-item .contentexport files
- **Contents:**
  - AGOL/Portal native OCM format
  - Includes item + immediate dependencies
  - Single binary file per item

- **Advantages:**
  - Native AGOL format
  - Includes automatic dependency resolution
  - Individual .contentexport per item

- **Disadvantages:**
  - Still one file per item
  - Requires ArcGIS API >= 2.4.1

**Example:**
```bash
python backup.py --csv items.csv --dest backups/ --mode ocm_per_item
```

### OCM Batch Backup (Recommended)
- **Format:** Single .contentexport file
- **Contents:**
  - All selected items in one package
  - Full dependency graph included
  - AGOL/Portal native format

- **Advantages:**
  - Most efficient (single file for all items)
  - Automatic dependency resolution
  - Preserves item relationships
  - Easy to share/transport

- **Disadvantages:**
  - All-or-nothing restoration
  - Larger single file

**Example:**
```bash
python backup.py --csv items.csv --dest backups/ --mode ocm_batch
```

---

## File Structure

```
agol-backup-utility/
├── ui.py                    # GUI application
├── scan.py                    # Layer scanner
├── backup.py                  # Backup engine
├── restore.py                 # Restore module
├── config.json               # User configuration (auto-generated)
├── fc.ico                    # Application icon
├── README.md                 # This file
└── backups/                  # Default backup directory
    ├── map1_20250129_120000.zip
    ├── map2_20250129_120500.zip
    └── batch_map1_map2_20250129_121000.contentexport
```

### Output CSV Format (AuthInventory.csv)

```csv
Title,Id,Type,Owner,Created,Modified,RestUrl,ItemPageUrl,Tags,ContentStatus
Layer Title,abc123def456,Feature Layer,admin@org.com,2024-01-15,2025-01-15,https://org.arcgis.com/...,https://org.arcgis.com/home/item.html?id=abc123,...,tag1|tag2,org_authoritative
```

### Backup Directory Structure

**Standard .zip format:**
```
item_title_20250129_120000/
├── item_title_metadata.json
├── item_title_metadata_full.json
├── item_title_data.json
├── item_title_relationships.json
├── thumbnail.png
├── resources.zip
└── backup_log.txt
```

**After compression:**
```
item_title_20250129_120000.zip
```

---

## Dependencies

### Core Dependencies

| Package | Version | Purpose | Link |
|---------|---------|---------|------|
| arcgis | 2.4.2 | ArcGIS API for Python | [PyPI](https://pypi.org/project/arcgis/) |
| pandas | ≥1.3.0 | Data manipulation and CSV handling | [PyPI](https://pypi.org/project/pandas/) |
| urllib3 | ≥1.26.0 | HTTP client (HTTPS warning suppression) | [PyPI](https://pypi.org/project/urllib3/) |

### Python Standard Library

| Module | Purpose |
|--------|---------|
| `tkinter` | GUI framework (built-in with Python) |
| `json` | Configuration and metadata serialization |
| `csv` | CSV file reading/writing |
| `os`, `sys` | File and system operations |
| `zipfile` | ZIP archive creation and extraction |
| `shutil` | File operations (copy, remove) |
| `tempfile` | Temporary file handling |
| `threading` | Multi-threaded backup operations |
| `subprocess` | Script execution (scan, backup, restore) |
| `datetime` | Timestamps and file naming |
| `argparse` | Command-line argument parsing |
| `concurrent.futures` | Thread pool management |

### System Dependencies

- **ArcGIS Pro 3.6** with Python environment
- **Python 3.13+**
- **Windows** (GUI fully supported)
- **macOS/Linux** (Not Supported / Not Tested)

### Optional Dependencies

For advanced features:
- **Requests**: Already installed with arcgis (for replica downloads)
- **SSL/TLS**: System-level (for AGOL connections)

---

## Troubleshooting

### Connection Issues

**Problem:** "Cannot connect to GIS" error
```
Solution:
1. Verify ArcGIS Pro authentication: gis = GIS("home") in Pro Python
2. Ensure stable internet connection
3. Check firewall/proxy settings
4. Try explicit connection: GIS("https://your-portal-url")
```

**Problem:** SSL/HTTPS certificate warnings
```
Solution:
The backup.py already suppresses these for environments with SSL inspection:
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
```

### CSV Issues

**Problem:** CSV file not found or not loading
```
Solution:
1. Verify CSV path is correct and file exists
2. Check file encoding: Should be UTF-8 or UTF-8-sig
3. Ensure CSV has 'id' column (case-insensitive)
4. Run scan.py to generate new CSV
```

**Problem:** No items loading from CSV
```
Solution:
1. Open CSV in text editor; verify it has headers and data rows
2. Check that item IDs are valid (40-character alphanumeric)
3. Ensure IDs correspond to items accessible to your user
4. Try: python scan.py --out new_inventory.csv
```

### Backup Issues

**Problem:** "No data captured" or incomplete backups
```
Solution:
1. Check item type support (Web Maps, Feature Layers, Services, etc.)
2. Verify user has read access to all item resources
3. Try standard mode (--mode standard) if OCM fails
4. Check network connectivity and AGOL service availability
5. Review log for specific error messages
```

**Problem:** OCM export not available
```
Solution:
1. Verify ArcGIS API version: pip show arcgis
2. Required: arcgis >= 2.4.1
3. Update: pip install --upgrade arcgis
4. Fall back to standard mode (--mode standard)
```

**Problem:** Backup stalling or timeout
```
Solution:
1. Reduce worker count: --workers 2
2. Check network speed and AGOL performance
3. For large items, increase timeout settings
4. Check available disk space
```

### Restore Issues

**Problem:** Restore fails with "Item already exists"
```
Solution:
1. Use --overwrite flag to replace existing items
2. Or manually delete existing item from AGOL/Portal
3. Or rename item in restore (change title in metadata)
```

**Problem:** Restored item missing data or resources
```
Solution:
1. Check backup was complete: inspect .zip or .contentexport
2. Verify restore log for specific errors
3. Check permissions: user needs create/write access
4. Try manual restore with --keep-metadata flag
```

**Problem:** ContentExport format not recognized
```
Solution:
1. Verify file has .contentexport extension
2. Check file is not corrupted: try opening with unzip
3. Ensure file is .contentexport, not .zip
4. Try standard .zip restore instead
```

### Performance Issues

**Problem:** Backup taking too long
```
Solution:
1. Reduce item count in initial CSV
2. Use parallel workers: --workers 8 (adjust for system)
3. Try OCM batch mode (--mode ocm_batch) for multiple items
4. Skip thumbnails: --no-thumbnails
5. Skip FGDB export: --no-fgdb
```

**Problem:** Memory usage too high
```
Solution:
1. Reduce worker count: --workers 2
2. Process items in smaller batches
3. Skip resource exports for large items
4. Ensure other applications are closed
```

### Windows-Specific Issues

**Problem:** "No module named 'arcgis'"
```
Solution:
1. Verify you're using ArcGIS Pro cloned environment
2. In Pro: Python Command Prompt > proswap backup-env
3. Install: pip install arcgis==2.4.2
4. Run from environment: ui.py
```

**Problem:** Icon file not found
```
Solution:
The application tries to load fc.ico but continues if missing.
If needed: Place fc.ico in same directory as ui.py
No impact on functionality if missing.
```

**Problem:** GUI not rendering properly
```
Solution:
1. Ensure Tkinter is available (standard with Python on Windows)
2. Try: python -m tkinter (should open test window)
3. Update Windows: may need Windows Display Driver updates
4. Try command-line scripts instead: scan.py, backup.py, restore.py
```

---

## Performance Considerations

### Scanning Performance
- Initial scan may take 5-30 minutes depending on AGOL size
- Subsequent scans are incremental (only new/changed items)
- Scan results cached in index file for speed

### Backup Performance
- **Standard mode**: ~1-2 minutes per item (varies by type/size)
- **OCM batch mode**: Often faster for multiple items (1 operation vs. N)
- **Threading**: Increase workers for parallel backups (monitor CPU/memory)

| Configuration | 10 Items | 50 Items | 100 Items |
|---|---|---|---|
| 1 worker (serial) | ~15 min | 1.2 hr | 2.4 hr |
| 4 workers (parallel) | ~4 min | 15 min | 30 min |
| 8 workers (parallel) | ~2 min | 8 min | 16 min |

### Optimization Tips
1. **Use OCM batch mode** for multiple related items
2. **Increase workers** if system has spare CPU capacity
3. **Skip thumbnails** if not needed: `--no-thumbnails`
4. **Skip FGDB exports** for non-spatial items: `--no-fgdb`
5. **Process in batches** rather than all items at once

---

## License

This project is licensed under CC BY SA. You may use, modify, and share the work if you provide attribution and release any derivatives under the same license.

---

## Contributing

For issues, feature requests, or bugs, just fork the repo and send a pull request.

### Development Guidelines
- Follow PEP 8 style guide
- Add docstrings to new functions
- Test with multiple item types
- Update README for new features
- Maintain backward compatibility

---

## Support

For technical support or questions:
- Check the [Troubleshooting](#troubleshooting) section
- Review script logs for error messages
- Verify ArcGIS API and Pro versions
- Test with smaller item sets first

---

## Changelog

### Version 1.0 (2026-01)
- Initial release with three backup modes
- GUI application with real-time logging
- Support for .zip and .contentexport formats
- Batch and incremental backup/restore operations

---

## Related Projects: Dependency Visualiser

As part of the development of the AGOL Backup Utility, the **[Dependency Visualiser](https://github.com/A-Charvin/fc/tree/main/OCluster)** project was created to help manage and understand item relationships within ArcGIS Online and Portal.

### Overview

The Dependency Visualiser is a companion tool that:
- **Maps item relationships**: Visualizes connections between Web Maps, Web Services, Feature Layers, and other AGOL/Portal items
- **Identifies dependencies**: Shows which items depend on others, helping to understand the impact of changes or deletions
- **Supports backup planning**: Helps determine which items should be backed up together to maintain data integrity

### Use Cases

- **Impact Analysis**: Before deleting or modifying an item, see what will be affected
- **Backup Strategy**: Group related items for efficient backup operations
- **Maintenance**: Understand the full scope of changes needed when updating shared resources
- **Documentation**: Create visual and textual documentation of your AGOL/Portal architecture

### Connection to This Utility

While the AGOL Backup Utility focuses on creating backups and restoring items, the Dependency Visualiser complements it by providing visibility into:
- Which items should be backed up together
- The order in which items should be restored to maintain relationships
- The potential impact of selective backups or restores

### Integration

The two projects work together:
1. Use **Dependency Visualiser** to understand your item relationships
2. Use **AGOL Backup Utility** to execute backups/restores based on those insights
3. Leverage insights from the Dependency Visualiser when planning OCM batch backups

---

## References

- [ArcGIS API for Python Documentation](https://developers.arcgis.com/python/)
- [ArcGIS Pro Python Environment](https://developers.arcgis.com/python/latest/guide/install-and-set-up/arcgis-pro/)
- [ContentManager](https://developers.arcgis.com/python/latest/api-reference/arcgis.gis.toc.html#arcgis.gis.ContentManager)
- [OfflineContentManager](https://developers.arcgis.com/python/latest/api-reference/arcgis.gis.toc.html#arcgis.gis.OfflineContentManager)
- [arcgis.apps.itemgraph module](https://developers.arcgis.com/python/latest/api-reference/arcgis.apps.itemgraph.html)
- [Pandas Documentation](https://pandas.pydata.org/docs/)
