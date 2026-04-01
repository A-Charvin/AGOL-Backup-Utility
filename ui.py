"""
AGOL Backup Utility
A responsive, thread-safe Tkinter UI for scanning, backing up, and restoring ArcGIS Online items.
"""
import os
import sys
import json
import csv
import tempfile
import threading
import subprocess
import queue
import atexit
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path

# ==================== Constants ====================
CONFIG_PATH = Path(__file__).parent.resolve() / "config.json"
SCRIPT_DIR = Path(__file__).parent.resolve()
LOG_BUFFER_THROTTLE_MS = 100
MIN_WINDOW_WIDTH = 900
MIN_WINDOW_HEIGHT = 700
DEFAULT_WINDOW_GEOMETRY = "1000x920"
TREE_ROW_HEIGHT = 28
LOG_FONT_SIZE = 9
UI_FONT_SIZE = 10

# ==================== Config Helpers ====================
def load_config():
    """Load configuration from JSON file."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config_atomic(cfg):
    """Save configuration using atomic write (temp file + rename)."""
    try:
        config_dir = CONFIG_PATH.parent
        config_dir.mkdir(parents=True, exist_ok=True)
        
        with tempfile.NamedTemporaryFile(
            mode='w', dir=config_dir, delete=False, 
            encoding='utf-8', suffix='.tmp'
        ) as tmp:
            json.dump(cfg, tmp, indent=2)
            tmp_path = Path(tmp.name)
        
        tmp_path.replace(CONFIG_PATH)
    except Exception:
        pass

# ==================== Custom Accent Button ====================
class AccentButton(tk.Button):
    """Modern-styled accent button using tk.Button for color control."""
    
    def __init__(self, master=None, **kwargs):
        self.bg_color = kwargs.pop('accent_bg', '#0078d7')
        self.active_bg = kwargs.pop('accent_active_bg', '#005bb5')
        self.disabled_bg = kwargs.pop('accent_disabled_bg', '#a0c4e0')
        self.fg_color = kwargs.pop('accent_fg', '#ffffff')
        self.disabled_fg = kwargs.pop('accent_disabled_fg', '#d0d0d0')
        
        # Get scaling from parent window
        scaling = 1.0
        try:
            scaling = master.winfo_toplevel().scaling
        except AttributeError:
            pass
        
        # FIX: Remove disabledbackground (not supported on Windows tk.Button)
        super().__init__(
            master,
            bg=self.bg_color,
            fg=self.fg_color,
            activebackground=self.active_bg,
            activeforeground=self.fg_color,
            disabledforeground=self.disabled_fg,
            # disabledbackground removed - not supported on Windows
            relief='flat',
            cursor='hand2',
            font=('Segoe UI', max(9, int(UI_FONT_SIZE * scaling)), 'bold'),
            padx=16,
            pady=8,
            **kwargs
        )
        
        self._original_state = 'normal'
        self._current_bg = self.bg_color
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)
    
    def _on_enter(self, event):
        if self['state'] != 'disabled':
            self.configure(bg=self.active_bg)
            self._current_bg = self.active_bg
    
    def _on_leave(self, event):
        if self['state'] != 'disabled':
            self.configure(bg=self.bg_color)
            self._current_bg = self.bg_color
    
    def config(self, **kwargs):
        """Override config to handle state changes properly."""
        if 'state' in kwargs:
            state = kwargs['state']
            self._original_state = state
            if state == 'disabled':
                # FIX: Manually set background for disabled state
                super().config(bg=self.disabled_bg, fg=self.disabled_fg)
                self._current_bg = self.disabled_bg
            elif state == 'normal':
                super().config(bg=self.bg_color, fg=self.fg_color)
                self._current_bg = self.bg_color
        super().config(**kwargs)
    
    def configure(self, **kwargs):
        """Alias for config."""
        self.config(**kwargs)

# ==================== Subprocess Runner ====================
class ScriptRunner:
    """Thread-safe subprocess runner with parallel stderr handling."""
    
    def __init__(self, log_callback, done_callback):
        self.log_callback = log_callback
        self.done_callback = done_callback
        self.process = None
        self.thread = None
        self.stop_requested = False
        self._stderr_queue = queue.Queue()
    
    def run(self, cmd, cwd=None):
        """Start subprocess in background thread."""
        def target():
            success, code = False, -1
            try:
                self._log(f"[SUBPROCESS] Running: {' '.join(cmd)}\n")
                self._log(f"[SUBPROCESS] Working directory: {cwd}\n")
                
                self.process = subprocess.Popen(
                    cmd, cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                    encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                
                self._log("[SUBPROCESS] Process started, reading output...\n")
                
                # Start stderr drain thread
                stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
                stderr_thread.start()
                
                # Read stdout line by line
                while True:
                    if self.stop_requested:
                        self._terminate_process()
                        break
                    
                    line = self.process.stdout.readline()
                    if not line:
                        break
                    if line.strip():
                        self._log(line)
                
                # Wait for process and stderr thread
                self.process.wait()
                stderr_thread.join(timeout=2.0)
                
                # Drain any remaining stderr
                while not self._stderr_queue.empty():
                    err_line = self._stderr_queue.get()
                    if err_line.strip():
                        self._log(f"[STDERR] {err_line}")
                
                code = self.process.returncode
                success = (code == 0)
                self._log(f"[SUBPROCESS] Process finished with exit code: {code}\n")
                
            except Exception as e:
                self._log(f"[ERROR] Exception in subprocess: {e}\n")
                import traceback
                self._log(f"[TRACEBACK] {traceback.format_exc()}\n")
            finally:
                self.done_callback(success, code)
        
        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
    
    def _drain_stderr(self):
        """Drain stderr into queue to prevent pipe buffer deadlock."""
        try:
            for line in iter(self.process.stderr.readline, ''):
                if self.stop_requested:
                    break
                self._stderr_queue.put(line)
        except Exception:
            pass
        finally:
            try:
                self.process.stderr.close()
            except Exception:
                pass
    
    def _log(self, text):
        """Thread-safe log callback."""
        if self.log_callback:
            self.log_callback(text)
    
    def _terminate_process(self):
        """Terminate subprocess gracefully."""
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            except Exception:
                pass
    
    def stop(self):
        """Request graceful stop."""
        self.stop_requested = True
        self._terminate_process()

# ==================== Main Application ====================
class App(tk.Tk):
    """Main application window with responsive, thread-safe UI."""
    
    def __init__(self):
        super().__init__()
        
        self._setup_scaling()
        
        # FIX: Add icon path
        icon_path = os.path.join(SCRIPT_DIR, "fc.ico")
        try:
            self.iconbitmap(icon_path)
        except Exception:
            pass
        
        self.title("Frontenac AGOL Backup Utility")
        self.geometry(DEFAULT_WINDOW_GEOMETRY)
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        
        self.cfg = load_config()
        self.runner = None
        self.temp_csv_path = None
        self.backup_items = []
        self.backup_mode = tk.StringVar(value=self.cfg.get("backup_mode", "standard"))
        self.sort_states = {"Title": False, "Type": False}
        
        self._log_buffer = []
        self._log_update_pending = False
        
        self._setup_styles()
        self._build_ui()
        self._bind_shortcuts()
        
        atexit.register(self._cleanup_temp_files)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _setup_scaling(self):
        """Detect and store DPI scaling factor for font sizing."""
        try:
            default_font = tkfont.nametofont("TkDefaultFont")
            base_size = 10
            actual_size = default_font.actual("size")
            self.scaling = max(0.8, min(2.0, actual_size / base_size))
        except Exception:
            self.scaling = 1.0
    
    def _setup_styles(self):
        """Configure ttk styles - keep vista theme for modern look."""
        style = ttk.Style(self)
        
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        elif 'xpnative' in style.theme_names():
            style.theme_use('xpnative')
        else:
            style.theme_use('clam')
        
        font_base = max(9, int(UI_FONT_SIZE * self.scaling))
        font_small = max(8, int((UI_FONT_SIZE - 1) * self.scaling))
        font_bold = max(9, int(UI_FONT_SIZE * self.scaling))
        
        style.configure('TFrame', background='#f0f0f0')
        style.configure('TLabel', background='#f0f0f0', font=('Segoe UI', font_base))
        style.configure('TLabelFrame', background='#f0f0f0', font=('Segoe UI', font_bold, 'bold'), padding=5)
        style.configure('TEntry', padding=[5, 5], font=('Segoe UI', font_base))
        style.configure('TButton', font=('Segoe UI', font_base, 'bold'), padding=8)
        style.configure("Treeview.Heading", font=('Segoe UI', font_bold, 'bold'))
        style.configure("Treeview", rowheight=TREE_ROW_HEIGHT, font=('Segoe UI', font_small))
        style.configure('TNotebook.Tab', padding=[20, 10], font=('Segoe UI', font_base, 'bold'))
        style.configure('TNotebook', padding=0)
        style.configure('TProgressbar', thickness=20)
    
    def _build_ui(self):
        """Build the complete UI layout with responsive grid configuration."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=1)
        
        # === Path Configuration Frame ===
        path_frame = ttk.Frame(self, padding=(20, 10))
        path_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=10)
        path_frame.columnconfigure(1, weight=1)
        
        self.csv_var = tk.StringVar(value=self.cfg.get("csv_path", str(SCRIPT_DIR / "output" / "AuthInventory.csv")))
        ttk.Label(path_frame, text="Layers CSV:", width=12).grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(path_frame, textvariable=self.csv_var, width=80).grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        ttk.Button(path_frame, text="...", command=self._choose_csv, width=3).grid(row=0, column=2, padx=2, pady=2)
        
        self.backup_dir_var = tk.StringVar(value=self.cfg.get("backup_dir", str(SCRIPT_DIR / "backups")))
        ttk.Label(path_frame, text="Backup Dir:", width=12).grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(path_frame, textvariable=self.backup_dir_var, width=80).grid(row=1, column=1, padx=5, pady=2, sticky="ew")
        ttk.Button(path_frame, text="...", command=self._choose_backup_dir, width=3).grid(row=1, column=2, padx=2, pady=2)
        
        # === Notebook with Tabs ===
        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 10))
        
        scan_tab = ttk.Frame(notebook, padding=20)
        backup_tab = ttk.Frame(notebook, padding=20)
        restore_tab = ttk.Frame(notebook, padding=20)
        
        notebook.add(scan_tab, text="1. Scan Layers")
        notebook.add(backup_tab, text="2. Backup Items")
        notebook.add(restore_tab, text="3. Restore Items")
        
        self._build_scan_tab(scan_tab)
        self._build_backup_tab(backup_tab)
        self._build_restore_tab(restore_tab)
        
        # === Log Container (Expandable) ===
        log_container = ttk.Frame(self, padding=(20, 10))
        log_container.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        log_container.columnconfigure(0, weight=1)
        log_container.rowconfigure(2, weight=1)
        
        ttk.Label(log_container, text="Log and Progress:", font=('Segoe UI', int(UI_FONT_SIZE * self.scaling), 'bold')).pack(fill="x", pady=(0, 5), anchor="w")
        
        controls_frame = ttk.Frame(log_container)
        controls_frame.pack(fill="x", pady=(0, 5))
        self.stop_btn = AccentButton(controls_frame, text="Stop", command=self._stop_running, state="disabled")
        self.stop_btn.pack(side="right", padx=5)
        
        self.progress = ttk.Progressbar(log_container, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 5))
        
        text_frame = ttk.Frame(log_container)
        text_frame.pack(fill="both", expand=True, pady=(5, 0))
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        
        log_scroll_y = ttk.Scrollbar(text_frame, orient="vertical")
        log_scroll_y.grid(row=0, column=1, sticky="ns")
        
        self.log = tk.Text(
            text_frame, wrap="word", yscrollcommand=log_scroll_y.set,
            font=('Consolas', max(8, int(LOG_FONT_SIZE * self.scaling))),
            bg="#fafafa", fg="#000000", bd=1, relief="flat", state='disabled'
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll_y.config(command=self.log.yview)
        
        self._log_msg("Ready.\n")
    
    def _bind_shortcuts(self):
        """Bind keyboard shortcuts for power users."""
        self.bind('<Control-q>', lambda e: self._on_close())
        self.bind('<Control-w>', lambda e: self._on_close())
        self.bind('<Escape>', lambda e: self._stop_running() if self.runner else None)
        self.bind('<Control-r>', lambda e: self._run_scan() if not self.runner else None)
        self.bind('<Control-b>', lambda e: self._run_backup() if not self.runner and self.backup_items else None)
        self.bind('<F5>', lambda e: self._refresh_scan_status())
    
    # ==================== Scan Tab ====================
    def _build_scan_tab(self, parent):
        ttk.Label(parent, text="Use an existing CSV or run a new layer scan.", 
                  font=('Segoe UI', int(UI_FONT_SIZE * self.scaling), 'bold')).pack(anchor="w", pady=(0, 10))
        
        btn_row = ttk.Frame(parent)
        btn_row.pack(anchor="w", pady=(5, 10))
        ttk.Button(btn_row, text="Choose Existing CSV", command=self._choose_existing_csv, width=22).pack(side="left", padx=10)
        self.scan_btn = AccentButton(btn_row, text="Run Layer Scan", command=self._run_scan)
        self.scan_btn.pack(side="left", padx=10)
        
        self.scan_status = ttk.Label(parent, text=f"Current CSV: {self.csv_var.get() or '(none)'}", 
                                     foreground="#666666", font=('Segoe UI', int((UI_FONT_SIZE-1) * self.scaling), 'italic'))
        self.scan_status.pack(anchor="w", pady=5)
        
        ttk.Label(parent, text="Note: Running a scan can take time. You can proceed to Backup with any valid CSV.", 
                  foreground="#888888", wraplength=600).pack(anchor="w", pady=(10, 0))
    
    def _choose_existing_csv(self):
        path = filedialog.askopenfilename(
            title="Select Existing Inventory CSV",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if path:
            self.csv_var.set(path)
            self._update_scan_status()
            self._log_msg(f"Using existing CSV: {path}\n")
    
    def _run_scan(self):
        csv_path = self.csv_var.get().strip()
        if not csv_path:
            messagebox.showerror("Error", "Please set a CSV output path.")
            return
        
        csv_path = os.path.normpath(csv_path)
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        index_path = os.path.normpath(os.path.join(os.path.dirname(csv_path), "index.csv"))
        scan_script = os.path.normpath(SCRIPT_DIR / "scan.py")
        
        if not os.path.exists(scan_script):
            messagebox.showerror("Error", f"scan.py not found at {scan_script}")
            return
        
        cmd = [sys.executable, scan_script, "--out", csv_path, "--index", index_path, "--skip-graph"]
        self._start_run(cmd, cwd=str(SCRIPT_DIR))
    
    def _update_scan_status(self):
        path = self.csv_var.get().strip()
        if path and os.path.exists(path):
            try:
                ts = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
                self.scan_status.config(text=f"Current CSV: {Path(path).name} (modified {ts})")
            except Exception:
                self.scan_status.config(text=f"Current CSV: {Path(path).name}")
        else:
            self.scan_status.config(text=f"Current CSV: {path or '(none)'}")
    
    def _refresh_scan_status(self):
        self._update_scan_status()
        self._log_msg("Scan status refreshed.\n")
    
    # ==================== Backup Tab ====================
    def _build_backup_tab(self, parent):
        mode_frame = ttk.LabelFrame(parent, text="Backup Mode", padding=(15, 15))
        mode_frame.pack(fill="x", pady=(0, 15))
        
        mode_info = {
            "standard": "Per-item .zip files (traditional)",
            "ocm_per_item": "Per-item .contentexport files (OCM)",
            "ocm_batch": "Single .contentexport file (OCM, with dependencies)"
        }
        
        for mode, description in mode_info.items():
            frame = ttk.Frame(mode_frame)
            frame.pack(anchor="w", pady=3)
            ttk.Radiobutton(frame, text=mode.upper().replace("_", " "), variable=self.backup_mode, value=mode).pack(side="left", padx=5)
            ttk.Label(frame, text=description, foreground="#666666", font=('Segoe UI', int((UI_FONT_SIZE-1) * self.scaling))).pack(side="left", padx=20)
        
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 10))
        AccentButton(controls, text="Load Items from CSV", command=self._load_backup_csv, width=20).pack(side="left", padx=10)
        
        selection_frame = ttk.Frame(controls)
        selection_frame.pack(side="left", padx=20)
        ttk.Button(selection_frame, text="Select All", command=lambda: self._toggle_all_backup_selection(True)).pack(side="left", padx=5)
        ttk.Button(selection_frame, text="Deselect All", command=lambda: self._toggle_all_backup_selection(False)).pack(side="left", padx=5)
        
        self.backup_status_label = ttk.Label(controls, text="Load a CSV to see items.", foreground="#666666")
        self.backup_status_label.pack(side="left", padx=20)
        
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, pady=(0, 15))
        
        tree_scroll_y = ttk.Scrollbar(tree_frame, orient="vertical")
        tree_scroll_y.pack(side="right", fill="y")
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient="horizontal")
        tree_scroll_x.pack(side="bottom", fill="x")
        
        self.backup_tree = ttk.Treeview(
            tree_frame,
            columns=("Select", "Title", "ID", "Type", "URL"),
            show="headings",
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set,
            height=15,
            selectmode="none"
        )
        tree_scroll_y.config(command=self.backup_tree.yview)
        tree_scroll_x.config(command=self.backup_tree.xview)
        
        self.backup_tree.heading("Select", text="✓")
        self.backup_tree.column("Select", width=50, minwidth=50, anchor="center", stretch=False)
        
        self.backup_tree.heading("Title", text="Title")
        self.backup_tree.column("Title", width=200, minwidth=150, anchor="w", stretch=True)
        
        self.backup_tree.heading("ID", text="ID")
        self.backup_tree.column("ID", width=180, minwidth=120, anchor="w", stretch=True)
        
        self.backup_tree.heading("Type", text="Type")
        self.backup_tree.column("Type", width=120, minwidth=100, anchor="w", stretch=False)
        
        self.backup_tree.heading("URL", text="URL")
        self.backup_tree.column("URL", width=250, minwidth=200, anchor="w", stretch=True)
        
        self.backup_tree.pack(fill="both", expand=True)
        self.backup_tree.bind("<Button-1>", self._on_backup_tree_click)
        self.backup_tree.bind("<Double-1>", self._on_backup_tree_double_click)
        
        self.backup_btn = AccentButton(parent, text="Start Backup of Selected Items", 
                                       command=self._run_backup, state="disabled")
        self.backup_btn.pack(pady=15)
    
    def _load_backup_csv(self):
        path = self.csv_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Error", "CSV file not found.")
            return
        
        try:
            self.backup_items = []
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    messagebox.showerror("Error", "CSV appears to have no header.")
                    return
                
                header_map = {h.strip().lower(): h for h in reader.fieldnames}
                
                def get_val(row, keys):
                    for k in keys:
                        src = header_map.get(k)
                        if src and src in row:
                            return (row.get(src) or "").strip()
                    return ""
                
                for row in reader:
                    self.backup_items.append({
                        "title": get_val(row, ["title", "name", "item title"]),
                        "id": get_val(row, ["id", "itemid", "item id"]),
                        "type": get_val(row, ["type", "item type"]),
                        "url": get_val(row, ["itempageurl", "url", "item url", "link"]),
                        "selected": True
                    })
            
            self._populate_backup_tree()
            self.backup_btn.config(state="normal" if self.backup_items else "disabled")
            self.backup_status_label.config(text=f"Loaded {len(self.backup_items)} items from CSV")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load CSV: {e}")
    
    def _populate_backup_tree(self):
        for iid in self.backup_tree.get_children():
            self.backup_tree.delete(iid)
        
        for item in self.backup_items:
            checkbox = "[X]" if item.get("selected") else "[ ]"
            self.backup_tree.insert("", "end", values=(
                checkbox,
                item.get("title", ""),
                item.get("id", ""),
                item.get("type", ""),
                item.get("url", "")
            ))
    
    def _on_backup_tree_click(self, event):
        region = self.backup_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        
        col = self.backup_tree.identify_column(event.x)
        row_id = self.backup_tree.identify_row(event.y)
        if not row_id or col != "#1":
            return
        
        index = self.backup_tree.index(row_id)
        if 0 <= index < len(self.backup_items):
            self.backup_items[index]["selected"] = not self.backup_items[index]["selected"]
            
            values = list(self.backup_tree.item(row_id, "values"))
            values[0] = "[X]" if self.backup_items[index]["selected"] else "[ ]"
            self.backup_tree.item(row_id, values=values)
    
    def _on_backup_tree_double_click(self, event):
        region = self.backup_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        
        col = self.backup_tree.identify_column(event.x)
        row_id = self.backup_tree.identify_row(event.y)
        if col == "#5" and row_id:
            import webbrowser
            values = self.backup_tree.item(row_id, "values")
            url = values[4]
            if url and url.startswith("http"):
                webbrowser.open_new_tab(url)
    
    def _toggle_all_backup_selection(self, select_state):
        for item in self.backup_items:
            item["selected"] = select_state
        self._populate_backup_tree()
    
    def _run_backup(self):
        selected_ids = [item["id"] for item in self.backup_items if item.get("selected") and item.get("id")]
        if not selected_ids:
            messagebox.showwarning("Nothing to do", "No items are selected for backup.")
            return
        
        backup_dir = self.backup_dir_var.get().strip()
        if not backup_dir:
            messagebox.showerror("Error", "Please select backup directory.")
            return
        
        os.makedirs(backup_dir, exist_ok=True)
        
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False, newline="", encoding="utf-8", suffix=".csv") as tmp:
                writer = csv.writer(tmp)
                writer.writerow(["id"])
                for item_id in selected_ids:
                    writer.writerow([item_id])
                self.temp_csv_path = tmp.name
            
            self._log_msg(f"Backing up {len(selected_ids)} items to '{backup_dir}'\n")
            self._log_msg(f"Backup mode: {self.backup_mode.get().upper()}\n")
            
            backup_script = SCRIPT_DIR / "backup.py"
            cmd = [sys.executable, str(backup_script), "--csv", self.temp_csv_path, "--dest", backup_dir, "--mode", self.backup_mode.get()]
            self._start_run(cmd, cwd=str(SCRIPT_DIR))
            
        except Exception as e:
            messagebox.showerror("Error", f"Could not create temp CSV: {e}")
    
    # ==================== Restore Tab ====================
    def _build_restore_tab(self, parent):
        ttk.Label(parent, text="Restore items from backups.", 
                  font=('Segoe UI', int(UI_FONT_SIZE * self.scaling), 'bold')).pack(anchor="w", pady=(0, 15))
        
        info_frame = ttk.LabelFrame(parent, text="Backup Format Info", padding=(15, 15))
        info_frame.pack(fill="x", pady=(0, 15))
        ttk.Label(info_frame, text="Supported backup formats:", font=('Segoe UI', int(UI_FONT_SIZE * self.scaling), 'bold')).pack(anchor="w")
        ttk.Label(info_frame, text="• .zip files (standard format with metadata)", foreground="#666666").pack(anchor="w", padx=20, pady=2)
        ttk.Label(info_frame, text="• .contentexport files (OCM format, single or per-item)", foreground="#666666").pack(anchor="w", padx=20, pady=2)
        
        restore_frame = ttk.LabelFrame(parent, text="Restore Options", padding=(15, 15))
        restore_frame.pack(fill="x", pady=(0, 15))
        
        select_frame = ttk.Frame(restore_frame)
        select_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(select_frame, text="Backup File:", width=15).pack(side="left", padx=5)
        self.restore_path_var = tk.StringVar()
        self.restore_path_var.trace_add("write", self._on_restore_path_changed)
        ttk.Entry(select_frame, textvariable=self.restore_path_var, width=70, state="readonly").pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(select_frame, text="Browse...", command=self._select_restore_backup, width=10).pack(side="left", padx=5)
        
        options_frame = ttk.Frame(restore_frame)
        options_frame.pack(fill="x", pady=(0, 10))
        self.restore_overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Overwrite existing items (if they exist)", variable=self.restore_overwrite_var).pack(anchor="w", pady=5)
        self.restore_keep_metadata_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Preserve original metadata", variable=self.restore_keep_metadata_var).pack(anchor="w", pady=5)
        
        button_frame = ttk.Frame(restore_frame)
        button_frame.pack(fill="x")
        self.restore_btn = AccentButton(button_frame, text="Start Restore", 
                                        command=self._run_restore, state="disabled")
        self.restore_btn.pack(side="left", padx=5)
        self.restore_info_label = ttk.Label(button_frame, text="Select a backup file to restore.", 
                                            foreground="#666666", font=('Segoe UI', int((UI_FONT_SIZE-1) * self.scaling), 'italic'))
        self.restore_info_label.pack(side="left", padx=20)
    
    def _on_restore_path_changed(self, *args):
        path = self.restore_path_var.get().strip()
        if path and os.path.exists(path):
            self.restore_btn.config(state="normal")
            try:
                size_mb = os.path.getsize(path) / (1024 * 1024)
                mod_time = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
                file_type = "ContentExport" if path.endswith(".contentexport") else "ZIP"
                info_text = f"{Path(path).name} | {file_type} | {size_mb:.1f} MB | {mod_time}"
                self.restore_info_label.config(text=info_text)
            except Exception:
                pass
        else:
            self.restore_btn.config(state="disabled")
            self.restore_info_label.config(text="Select a backup file to restore.")
    
    def _select_restore_backup(self):
        path = filedialog.askopenfilename(
            title="Select Backup File",
            filetypes=[("All Backups", "*.zip *.contentexport"), 
                       ("ZIP Files", "*.zip"),
                       ("ContentExport Files", "*.contentexport"),
                       ("All Files", "*.*")]
        )
        if path:
            self.restore_path_var.set(path)
    
    def _run_restore(self):
        backup_path = self.restore_path_var.get().strip()
        if not backup_path or not os.path.exists(backup_path):
            messagebox.showerror("Error", "Please select a valid backup file.")
            return
        
        if self.restore_overwrite_var.get():
            if not messagebox.askyesno(
                "Confirm Overwrite", 
                "Overwrite mode is enabled. Existing items with matching IDs will be replaced.\n\nContinue?",
                icon="warning"
            ):
                return
        
        keep_metadata = self.restore_keep_metadata_var.get()
        self._log_msg(f"\nRestoring from: {backup_path}\n")
        self._log_msg(f"Overwrite: {self.restore_overwrite_var.get()} | Keep meta {keep_metadata}\n")
        
        restore_script = SCRIPT_DIR / "restore.py"
        cmd = [sys.executable, str(restore_script), "--backup", backup_path, "--connection", "home"]
        
        if self.restore_overwrite_var.get():
            cmd.append("--overwrite")
        if keep_metadata:
            cmd.append("--keep-metadata")
        
        self._start_run(cmd, cwd=str(SCRIPT_DIR))
    
    # ==================== Run/Stop Management ====================
    def _start_run(self, cmd, cwd=None):
        if self.runner is not None:
            messagebox.showwarning("Busy", "Another task is running.")
            return
        
        self._log_msg("\n" + "="*80 + "\n")
        self.progress.start(10)
        self._set_buttons(running=True)
        
        self._progress_window = tk.Toplevel(self)
        self._progress_window.title("Operation in Progress")
        self._progress_window.geometry("700x500")
        self._progress_window.minsize(500, 400)
        self._progress_window.transient(self)
        self._progress_window.grab_set()
        
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 350
        y = self.winfo_y() + (self.winfo_height() // 2) - 250
        self._progress_window.geometry(f"+{max(0, x)}+{max(0, y)}")
        
        ttk.Label(self._progress_window, text="Operation in Progress", font=('Segoe UI', int(UI_FONT_SIZE * self.scaling), 'bold')).pack(pady=(15, 5))
        
        self._progress_popup_bar = ttk.Progressbar(self._progress_window, mode="indeterminate")
        self._progress_popup_bar.pack(pady=5, padx=20, fill="x")
        self._progress_popup_bar.start(10)
        
        log_frame = ttk.Frame(self._progress_window)
        log_frame.pack(fill="both", expand=True, padx=15, pady=10)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        
        ttk.Label(log_frame, text="Activity Log:", font=('Segoe UI', int((UI_FONT_SIZE-1) * self.scaling), 'bold')).grid(row=0, column=0, sticky="w", pady=(0, 5))
        
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical")
        log_scroll.grid(row=1, column=1, sticky="ns")
        
        self._progress_log = tk.Text(
            log_frame, wrap="word", yscrollcommand=log_scroll.set,
            font=('Consolas', max(8, int(LOG_FONT_SIZE * self.scaling))),
            bg="#fafafa", fg="#000000", bd=1, relief="flat", state='disabled'
        )
        self._progress_log.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        log_scroll.config(command=self._progress_log.yview)
        
        btn_frame = ttk.Frame(self._progress_window)
        btn_frame.pack(fill="x", padx=15, pady=(5, 15))
        self._progress_cancel_btn = AccentButton(btn_frame, text="Stop Operation", command=self._stop_running)
        self._progress_cancel_btn.pack(side="left", padx=5)
        ttk.Label(btn_frame, text="Close this window to view full log in main window", 
                  foreground="#666666", font=('Segoe UI', int((UI_FONT_SIZE-2) * self.scaling), 'italic')).pack(side="left", padx=20)
        
        self._original_log_msg = self._log_msg
        self._log_msg = self._log_msg_with_progress
        
        self.runner = ScriptRunner(self._log_msg, self._on_done)
        self.runner.run(cmd, cwd=cwd)
    
    def _log_msg_with_progress(self, text):
        self._original_log_msg(text)
        
        if hasattr(self, '_progress_log') and self._progress_log.winfo_exists():
            self.after(0, lambda t=text: self._do_progress_log(t))
    
    def _do_progress_log(self, text):
        if not self._progress_log.winfo_exists():
            return
        self._progress_log.configure(state='normal')
        self._progress_log.insert("end", text)
        if self._progress_log.yview()[1] >= 0.99:
            self._progress_log.see("end")
        self._progress_log.configure(state='disabled')
    
    def _stop_running(self):
        if self.runner:
            self._log_msg("Stopping...\n")
            self.runner.stop()
    
    def _on_done(self, success, code):
        if hasattr(self, '_original_log_msg'):
            self._log_msg = self._original_log_msg
        
        self.progress.stop()
        self._set_buttons(running=False)
        self._log_msg("Completed successfully.\n" if success else f"Finished with errors (exit code: {code}).\n")
        
        if hasattr(self, '_progress_window') and self._progress_window.winfo_exists():
            if hasattr(self, '_progress_popup_bar'):
                self._progress_popup_bar.stop()
            self._progress_cancel_btn.config(text="Close", command=self._close_progress_window)
        
        self._cleanup_temp_files()
        
        self.runner = None
        self._update_scan_status()
    
    def _close_progress_window(self):
        if hasattr(self, '_progress_window') and self._progress_window.winfo_exists():
            self._progress_window.destroy()
    
    def _set_buttons(self, running):
        state_run = "disabled" if running else "normal"
        state_stop = "normal" if running else "disabled"
        
        try:
            self.scan_btn.config(state=state_run)
        except Exception:
            pass
        
        self.backup_btn.config(state=state_run if self.backup_items else "disabled")
        
        if not running and self.restore_path_var.get().strip() and os.path.exists(self.restore_path_var.get().strip()):
            self.restore_btn.config(state="normal")
        elif running:
            self.restore_btn.config(state="disabled")
        
        self.stop_btn.config(state=state_stop)
    
    # ==================== Logging (Thread-Safe + Throttled) ====================
    def _log_msg(self, text):
        self._log_buffer.append(text)
        
        if not self._log_update_pending:
            self._log_update_pending = True
            self.after(LOG_BUFFER_THROTTLE_MS, self._flush_log_buffer)
    
    def _flush_log_buffer(self):
        if self._log_buffer and self.log.winfo_exists():
            self.log.configure(state='normal')
            for msg in self._log_buffer:
                self.log.insert("end", msg)
            if self.log.yview()[1] >= 0.99:
                self.log.see("end")
            self.log.configure(state='disabled')
            self.log.update_idletasks()
            self._log_buffer.clear()
        
        self._log_update_pending = False
    
    # ==================== Utilities ====================
    def _choose_csv(self):
        path = filedialog.asksaveasfilename(
            title="Select or enter CSV path",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialdir=self.cfg.get("csv_path", str(SCRIPT_DIR / "output"))
        )
        if path:
            self.csv_var.set(path)
            self._update_scan_status()
    
    def _choose_backup_dir(self):
        path = filedialog.askdirectory(
            title="Select Backup Directory",
            initialdir=self.cfg.get("backup_dir", str(SCRIPT_DIR / "backups"))
        )
        if path:
            self.backup_dir_var.set(path)
    
    def _cleanup_temp_files(self):
        if self.temp_csv_path and os.path.exists(self.temp_csv_path):
            try:
                os.remove(self.temp_csv_path)
            except Exception:
                pass
            finally:
                self.temp_csv_path = None
    
    def _on_close(self):
        self.cfg["csv_path"] = self.csv_var.get()
        self.cfg["backup_dir"] = self.backup_dir_var.get()
        self.cfg["backup_mode"] = self.backup_mode.get()
        save_config_atomic(self.cfg)
        
        if self.runner and self.runner.process and self.runner.process.poll() is None:
            if not messagebox.askyesno("Confirm Exit", "A task is running. Stop and exit?", icon="warning"):
                return
            try:
                self.runner.stop()
            except Exception:
                pass
        
        self._cleanup_temp_files()
        self.destroy()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    
    app = App()
    app.mainloop()
