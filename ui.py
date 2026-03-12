import os, sys, threading, subprocess, json, csv, tempfile, zipfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

CONFIG_PATH = "config.json"
script_dir = os.path.dirname(os.path.abspath(__file__))

# ------------------- Config helpers -------------------
def LoadConfig():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def SaveConfig(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

# ------------------- Script Runner -------------------
class ScriptRunner:
    def __init__(self, LogCallback, DoneCallback):
        self.LogCallback = LogCallback
        self.DoneCallback = DoneCallback
        self.Process = None
        self.Thread = None
        self.StopRequested = False

    def Run(self, Cmd, Cwd=None):
        def Target():
            Success, Code = False, -1
            try:
                self.LogCallback(f"[SUBPROCESS] Running: {' '.join(Cmd)}\n")
                self.LogCallback(f"[SUBPROCESS] Working directory: {Cwd}\n")
                
                self.Process = subprocess.Popen(
                    Cmd, 
                    cwd=Cwd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    text=True, 
                    bufsize=1, 
                    encoding="utf-8", 
                    errors="replace"
                )
                
                self.LogCallback("[SUBPROCESS] Process started, reading output...\n")
                
                # Read stdout
                while True:
                    line = self.Process.stdout.readline()
                    if not line:
                        break
                    if line.strip():
                        self.LogCallback(line)
                    if self.StopRequested:
                        try:
                            self.Process.terminate()
                        except Exception:
                            pass
                        break
                
                # Get any remaining stderr
                stderr_output = self.Process.stderr.read()
                if stderr_output:
                    self.LogCallback(f"[STDERR] {stderr_output}\n")
                
                self.Process.wait()
                Code = self.Process.returncode
                Success = (Code == 0)
                self.LogCallback(f"[SUBPROCESS] Process finished with exit code: {Code}\n")
            except Exception as e:
                self.LogCallback(f"[ERROR] Exception in subprocess: {e}\n")
                import traceback
                self.LogCallback(f"[TRACEBACK] {traceback.format_exc()}\n")
            finally:
                self.DoneCallback(Success, Code)
        
        self.Thread = threading.Thread(target=Target, daemon=True)
        self.Thread.start()

    def Stop(self):
        self.StopRequested = True
        if self.Process and self.Process.poll() is None:
            try:
                self.Process.terminate()
            except Exception:
                pass

# ------------------- Main Application -------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        icon_path = os.path.join(script_dir, "fc.ico")
        try:
            self.iconbitmap(icon_path)
        except Exception:
            pass

        self.title("Frontenac AGOL Backup Utility")
        self.geometry("1100x900")
        self.Cfg = LoadConfig()
        self.Runner = None
        self.TempCsvPath = None
        self.BackupItems = []
        self.BackupMode = tk.StringVar(value=self.Cfg.get("backup_mode", "standard"))
        
        self.Style = ttk.Style(self)
        
        if 'xpnative' in self.Style.theme_names():
            self.Style.theme_use('xpnative')
        else:
            self.Style.theme_use('default')

        self.Style.configure('TFrame', background='#ececec')
        self.Style.configure('TLabel', background='#ececec', font=('Segoe UI', 10))
        self.Style.configure('TLabelFrame', background='#ececec', font=('Segoe UI', 11, 'bold'))
        self.Style.configure('TEntry', padding=[5, 5], font=('Segoe UI', 10))
        self.Style.configure('TButton', font=('Segoe UI', 10, 'bold'), padding=8)
        self.Style.map('TButton', background=[('active', '#e0e0e0'), ('disabled', '#f0f0f0')])
        self.Style.configure('Accent.TButton', background='white', foreground='#0078d7')
        self.Style.map('Accent.TButton', 
                       background=[('active', '#005bb5'), ('disabled', '#a0a0a0')],
                       foreground=[('active', 'white'), ('disabled', '#d0d0d0')])
        self.Style.configure("Treeview.Heading", font=('Segoe UI', 10, 'bold'))
        self.Style.configure("Treeview", rowheight=25, font=('Segoe UI', 10))
        self.Style.configure('TNotebook.Tab', padding=[20, 10], font=('Segoe UI', 10, 'bold'))
        self.Style.configure('TNotebook', padding=0)
        
        self._BuildUI()
        self.protocol("WM_DELETE_WINDOW", self._OnClose)

    # ------------------- UI Layout -------------------
    def _BuildUI(self):
        RootPadding = 20

        PathFrame = ttk.Frame(self, padding=(RootPadding, 10))
        PathFrame.pack(fill="x", padx=RootPadding, pady=10)

        self.CsvVar = tk.StringVar(value=self.Cfg.get("csv_path", os.path.join("output", "AuthInventory.csv")))
        ttk.Label(PathFrame, text="Layers CSV:", width=12).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ttk.Entry(PathFrame, textvariable=self.CsvVar, width=100).grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        ttk.Button(PathFrame, text="...", command=self._ChooseCsv, width=3).grid(row=0, column=2, padx=5, pady=5)

        self.BackupDirVar = tk.StringVar(value=self.Cfg.get("backup_dir", "backups"))
        ttk.Label(PathFrame, text="Backup Dir:", width=12).grid(row=1, column=0, sticky="w", padx=10, pady=5)
        ttk.Entry(PathFrame, textvariable=self.BackupDirVar, width=100).grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Button(PathFrame, text="...", command=self._ChooseBackupDir, width=3).grid(row=1, column=2, padx=5, pady=5)
        
        PathFrame.grid_columnconfigure(1, weight=1)

        Notebook = ttk.Notebook(self)
        Notebook.pack(fill="both", expand=True, padx=RootPadding, pady=10)
        
        ScanTab = ttk.Frame(Notebook, padding=(20, 20))
        BackupTab = ttk.Frame(Notebook, padding=(20, 20))
        RestoreTab = ttk.Frame(Notebook, padding=(20, 20))
        
        Notebook.add(ScanTab, text="1. Scan Layers")
        Notebook.add(BackupTab, text="2. Backup Items")
        Notebook.add(RestoreTab, text="3. Restore Items")

        self._BuildScanTab(ScanTab)
        self._BuildBackupTab(BackupTab)
        self._BuildRestoreTab(RestoreTab)

        LogContainer = ttk.Frame(self, padding=(RootPadding, 10))
        LogContainer.pack(fill="both", expand=True, padx=RootPadding, pady=(10, RootPadding))
        
        ttk.Label(LogContainer, text="Log and Progress:", font=('Segoe UI', 11, 'bold')).pack(fill="x", pady=(0, 5), anchor="w")
        
        ControlsFrame = ttk.Frame(LogContainer, padding=(0, 0))
        ControlsFrame.pack(fill="x", pady=(0, 5))
        
        self.StopBtn = ttk.Button(ControlsFrame, text="Stop", command=self._StopRunning, state="disabled", style='Accent.TButton')
        self.StopBtn.pack(side="right", padx=5)
        
        self.Progress = ttk.Progressbar(LogContainer, mode="indeterminate", length=400)
        self.Progress.pack(fill="x", pady=(0, 5))
        
        TextFrame = ttk.Frame(LogContainer)
        TextFrame.pack(fill="both", expand=True, pady=(5, 0))
        
        LogScrollY = ttk.Scrollbar(TextFrame, orient="vertical")
        LogScrollY.pack(side="right", fill="y")
        
        self.Log = tk.Text(TextFrame, height=12, wrap="word", 
                           yscrollcommand=LogScrollY.set, 
                           font=('Consolas', 9), 
                           bg="#f8f8f8", fg="#000000", bd=1, relief="flat", state='disabled')
        self.Log.pack(side="left", fill="both", expand=True)
        LogScrollY.config(command=self.Log.yview)
        
        self._LogMsg("Ready.\n")

    # ------------------- Scan Tab -------------------
    def _BuildScanTab(self, Parent):
        ttk.Label(Parent, text="Use an existing CSV or run a new layer scan.", 
                  font=('Segoe UI', 12, 'bold')).pack(anchor="w", pady=(0, 10))
        
        BtnRow = ttk.Frame(Parent)
        BtnRow.pack(anchor="w", pady=(5, 10))
        
        ttk.Button(BtnRow, text="Choose Existing CSV", command=self._ChooseExistingCsv, width=22).pack(side="left", padx=10)
        self.ScanBtn = ttk.Button(BtnRow, text="Run Layer Scan", command=self._RunScan, width=18, style='Accent.TButton')
        self.ScanBtn.pack(side="left", padx=10)
        
        self.ScanStatus = ttk.Label(Parent, text=f"Current CSV: {self.CsvVar.get() or '(none)'}", 
                                    foreground="#666666", font=('Segoe UI', 10, 'italic'))
        self.ScanStatus.pack(anchor="w", pady=5)
        
        ttk.Label(Parent, text="Note: Running a scan can take time. You can proceed to Backup with any valid CSV.", 
                  foreground="#888888").pack(anchor="w", pady=(10, 0))

    def _ChooseExistingCsv(self):
        Path = filedialog.askopenfilename(title="Select Existing Inventory CSV", filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if Path:
            self.CsvVar.set(Path)
            self._UpdateScanStatus()
            self._LogMsg(f"Using existing CSV: {Path}\n")

    def _RunScan(self):
        print("[DIRECT PRINT] _RunScan() called!")
        
        CsvPath = self.CsvVar.get().strip()
        if not CsvPath:
            messagebox.showerror("Error", "Please set a CSV output path.")
            return
        
        # Normalize path separators
        CsvPath = os.path.normpath(CsvPath)
        
        os.makedirs(os.path.dirname(CsvPath) or ".", exist_ok=True)
        IndexPath = os.path.normpath(os.path.join(os.path.dirname(CsvPath), "index.csv"))
        ScanScript = os.path.normpath(os.path.join(script_dir, "scan.py"))
        
        print(f"[DIRECT PRINT] Script dir: {script_dir}")
        print(f"[DIRECT PRINT] Scan script path: {ScanScript}")
        print(f"[DIRECT PRINT] Script exists: {os.path.exists(ScanScript)}")
        
        self._LogMsg(f"[DEBUG] Script dir: {script_dir}\n")
        self._LogMsg(f"[DEBUG] Scan script path: {ScanScript}\n")
        self._LogMsg(f"[DEBUG] Script exists: {os.path.exists(ScanScript)}\n")
        
        if not os.path.exists(ScanScript):
            messagebox.showerror("Error", f"scan.py not found at {ScanScript}")
            return
        
        self._LogMsg(f"[DEBUG] CSV path: {CsvPath}\n")
        self._LogMsg(f"[DEBUG] Index path: {IndexPath}\n")
        
        # Cmd = [sys.executable, ScanScript, "--out", CsvPath, "--index", IndexPath]
        # Without Skip this will also generate Dependancy Graph which can take hours to finish.
        Cmd = [sys.executable, ScanScript, "--out", CsvPath, "--index", IndexPath, "--skip-graph"]

        self._LogMsg(f"[DEBUG] Command: {' '.join(Cmd)}\n")
        
        print(f"[DIRECT PRINT] Starting run with command: {Cmd}")
        print(f"[DIRECT PRINT] Working directory: {script_dir}")
        
        # Run with the script directory as the working directory
        self._StartRun(Cmd, Cwd=script_dir)

    def _UpdateScanStatus(self):
        Path = self.CsvVar.get().strip()
        if Path and os.path.exists(Path):
            try:
                Ts = datetime.fromtimestamp(os.path.getmtime(Path)).strftime("%Y-%m-%d %H:%M")
                self.ScanStatus.config(text=f"Current CSV: {Path} (modified {Ts})")
            except Exception:
                self.ScanStatus.config(text=f"Current CSV: {Path}")
        else:
            self.ScanStatus.config(text=f"Current CSV: {Path or '(none)'}")

    # ------------------- Backup Tab -------------------
    def _BuildBackupTab(self, Parent):
        ModeFrame = ttk.LabelFrame(Parent, text="Backup Mode", padding=(15, 15))
        ModeFrame.pack(fill="x", pady=(0, 15))
        
        ModeInfo = {
            "standard": "Per-item .zip files (traditional, each item separately)",
            "ocm_per_item": "Per-item .contentexport files (OCM, each item separately)",
            "ocm_batch": "Single .contentexport file (OCM, all items together with dependencies)"
        }
        
        for Mode, Description in ModeInfo.items():
            Frame = ttk.Frame(ModeFrame)
            Frame.pack(anchor="w", pady=5)
            ttk.Radiobutton(
                Frame,
                text=Mode.upper().replace("_", " "),
                variable=self.BackupMode,
                value=Mode
            ).pack(side="left", padx=5)
            ttk.Label(Frame, text=Description, foreground="#666666", font=('Segoe UI', 9)).pack(side="left", padx=20)
        
        Controls = ttk.Frame(Parent)
        Controls.pack(fill="x", pady=(0, 10))

        ttk.Button(Controls, text="Load Items from CSV",
                   command=self._LoadBackupCsv, style='Accent.TButton', width=20).pack(side="left", padx=10)

        SelectionFrame = ttk.Frame(Controls)
        SelectionFrame.pack(side="left", padx=20)
        ttk.Button(SelectionFrame, text="Select All",
                   command=lambda: self._ToggleAllBackupSelection(True)).pack(side="left", padx=5)
        ttk.Button(SelectionFrame, text="Deselect All",
                   command=lambda: self._ToggleAllBackupSelection(False)).pack(side="left", padx=5)

        self.BackupStatusLabel = ttk.Label(
            Controls, text="Load a CSV to see items.", foreground="#666666")
        self.BackupStatusLabel.pack(side="left", padx=20)

        TreeFrame = ttk.Frame(Parent)
        TreeFrame.pack(fill="both", expand=True, pady=(0, 15))
        TreeScrollY = ttk.Scrollbar(TreeFrame, orient="vertical")
        TreeScrollY.pack(side="right", fill="y")

        self.BackupTree = ttk.Treeview(
            TreeFrame,
            columns=("Select", "Title", "ID", "Type", "URL"),
            show="headings",
            yscrollcommand=TreeScrollY.set,
            height=12
        )
        TreeScrollY.config(command=self.BackupTree.yview)

        for Col, W in zip(("Select", "Title", "ID", "Type", "URL"), (60, 280, 220, 160, 400)):
            self.BackupTree.heading(Col, text=Col, command=lambda c=Col: self._SortBackupTree(c))
            self.BackupTree.column(Col, width=W, anchor="w")
        self.BackupTree.column("Select", anchor="center")
        self.BackupTree.pack(fill="both", expand=True)

        self.BackupTree.bind("<Button-1>", self._OnBackupTreeClick)
        self.BackupTree.bind("<Double-1>", self._OnBackupTreeDoubleClick)

        self.BackupBtn = ttk.Button(Parent, text="Start Backup of Selected Items",
                                    command=self._RunBackup, state="disabled", style='Accent.TButton')
        self.BackupBtn.pack(pady=15)

        self.SortStates = {"Title": False, "Type": False}

    # ------------------- Restore Tab -------------------
    def _BuildRestoreTab(self, Parent):
        ttk.Label(Parent, text="Restore items from backups.", 
                  font=('Segoe UI', 12, 'bold')).pack(anchor="w", pady=(0, 15))
        
        InfoFrame = ttk.LabelFrame(Parent, text="Backup Format Info", padding=(15, 15))
        InfoFrame.pack(fill="x", pady=(0, 15))
        
        ttk.Label(InfoFrame, text="Supported backup formats:", font=('Segoe UI', 10, 'bold')).pack(anchor="w")
        ttk.Label(InfoFrame, text="• .zip files (standard format with metadata)", foreground="#666666").pack(anchor="w", padx=20, pady=2)
        ttk.Label(InfoFrame, text="• .contentexport files (OCM format, single or per-item)", foreground="#666666").pack(anchor="w", padx=20, pady=2)
        
        RestoreFrame = ttk.LabelFrame(Parent, text="Restore Options", padding=(15, 15))
        RestoreFrame.pack(fill="x", pady=(0, 15))
        
        SelectFrame = ttk.Frame(RestoreFrame)
        SelectFrame.pack(fill="x", pady=(0, 10))
        ttk.Label(SelectFrame, text="Backup File:", width=15).pack(side="left", padx=5)
        self.RestorePathVar = tk.StringVar()
        self.RestorePathVar.trace_add("write", self._OnRestorePathChanged)
        ttk.Entry(SelectFrame, textvariable=self.RestorePathVar, width=80, state="readonly").pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(SelectFrame, text="Browse...", command=self._SelectRestoreBackup, width=10).pack(side="left", padx=5)
        
        OptionsFrame = ttk.Frame(RestoreFrame)
        OptionsFrame.pack(fill="x", pady=(0, 10))
        
        self.RestoreOverwriteVar = tk.BooleanVar(value=False)
        ttk.Checkbutton(OptionsFrame, text="Overwrite existing items (if they exist)", variable=self.RestoreOverwriteVar).pack(anchor="w", pady=5)
        
        self.RestoreKeepMetadataVar = tk.BooleanVar(value=True)
        ttk.Checkbutton(OptionsFrame, text="Preserve original metadata", variable=self.RestoreKeepMetadataVar).pack(anchor="w", pady=5)
        
        ButtonFrame = ttk.Frame(RestoreFrame)
        ButtonFrame.pack(fill="x")
        
        self.RestoreBtn = ttk.Button(ButtonFrame, text="Start Restore", 
                                     command=self._RunRestore, state="disabled", style='Accent.TButton', width=20)
        self.RestoreBtn.pack(side="left", padx=5)
        
        self.RestoreInfoLabel = ttk.Label(ButtonFrame, text="Select a backup file to restore.", 
                                          foreground="#666666", font=('Segoe UI', 9, 'italic'))
        self.RestoreInfoLabel.pack(side="left", padx=20)

    def _OnRestorePathChanged(self, *args):
        """Called whenever RestorePathVar changes - updates button state"""
        Path = self.RestorePathVar.get().strip()
        if Path and os.path.exists(Path):
            self.RestoreBtn.config(state="normal")
        else:
            self.RestoreBtn.config(state="disabled")

    def _SelectRestoreBackup(self):
        Path = filedialog.askopenfilename(
            title="Select Backup File",
            filetypes=[("All Backups", "*.zip;*.contentexport"), 
                       ("ZIP Files", "*.zip"),
                       ("ContentExport Files", "*.contentexport"),
                       ("All Files", "*.*")]
        )
        if Path:
            self.RestorePathVar.set(Path)
            if os.path.exists(Path):
                SizeMb = os.path.getsize(Path) / (1024 * 1024)
                ModTime = datetime.fromtimestamp(os.path.getmtime(Path)).strftime("%Y-%m-%d %H:%M")
                FileType = "ContentExport" if Path.endswith(".contentexport") else "ZIP"
                InfoText = f"File: {os.path.basename(Path)} | Type: {FileType} | Size: {SizeMb:.2f} MB | Modified: {ModTime}"
                self.RestoreInfoLabel.config(text=InfoText)
    
    def _RunRestore(self):
        BackupPath = self.RestorePathVar.get().strip()
        if not BackupPath or not os.path.exists(BackupPath):
            messagebox.showerror("Error", "Please select a valid backup file.")
            return
        
        Overwrite = self.RestoreOverwriteVar.get()
        KeepMetadata = self.RestoreKeepMetadataVar.get()
        
        self._LogMsg(f"\nRestoring from: {BackupPath}\n")
        self._LogMsg(f"Overwrite: {Overwrite} | Keep metadata: {KeepMetadata}\n")
        
        # Create a restore script command that will be executed asynchronously
        RestoreScript = os.path.join(script_dir, "restore.py")
        
        Cmd = [
            sys.executable, 
            RestoreScript, 
            "--backup", BackupPath,
            "--connection", "home"
        ]
        
        if Overwrite:
            Cmd.append("--overwrite")
        if KeepMetadata:
            Cmd.append("--keep-metadata")
        
        self._StartRun(Cmd, Cwd=script_dir)

    # ------------------- Run/Stop -------------------
    def _StartRun(self, Cmd, Cwd=None):
        if self.Runner is not None:
            messagebox.showwarning("Busy", "Another task is running.")
            return
        
        self._LogMsg("\n" + "="*80 + "\n")
        self.Progress.start(10)
        self._SetButtons(Running=True)
        
        # Show progress popup with log viewer
        self._ProgressWindow = tk.Toplevel(self)
        self._ProgressWindow.title("Operation in Progress")
        self._ProgressWindow.geometry("600x400")
        self._ProgressWindow.resizable(True, True)
        self._ProgressWindow.protocol("WM_DELETE_WINDOW", self._OnProgressWindowClose)
        
        # Center the progress window on the main window
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 300
        y = self.winfo_y() + (self.winfo_height() // 2) - 200
        self._ProgressWindow.geometry(f"+{x}+{y}")
        
        # Progress window content
        ttk.Label(self._ProgressWindow, text="Operation in Progress", font=('Segoe UI', 12, 'bold')).pack(pady=(10, 5))
        
        # Add animated progress bar
        self._ProgressPopupBar = ttk.Progressbar(self._ProgressWindow, mode="indeterminate", length=500)
        self._ProgressPopupBar.pack(pady=5, padx=20, fill="x")
        self._ProgressPopupBar.start(10)
        
        # Add log text area (NEW)
        LogFrame = ttk.Frame(self._ProgressWindow)
        LogFrame.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Label(LogFrame, text="Activity Log:", font=('Segoe UI', 9, 'bold')).pack(anchor="w", pady=(0, 5))
        
        LogScrollY = ttk.Scrollbar(LogFrame, orient="vertical")
        LogScrollY.pack(side="right", fill="y")
        
        self._ProgressLog = tk.Text(
            LogFrame, 
            height=12, 
            width=70,
            wrap="word", 
            yscrollcommand=LogScrollY.set, 
            font=('Consolas', 8), 
            bg="#f8f8f8", 
            fg="#000000", 
            bd=1, 
            relief="flat", 
            state='disabled'
        )
        self._ProgressLog.pack(side="left", fill="both", expand=True)
        LogScrollY.config(command=self._ProgressLog.yview)
        
        # Control buttons frame
        BtnFrame = ttk.Frame(self._ProgressWindow)
        BtnFrame.pack(fill="x", padx=10, pady=(5, 10))
        
        self._ProgressCancelBtn = ttk.Button(BtnFrame, text="Stop Operation", 
                                            command=self._StopRunning, style='Accent.TButton')
        self._ProgressCancelBtn.pack(side="left", padx=5)
        
        ttk.Label(BtnFrame, text="Close this window to view full log in main window", 
                 foreground="#666666", font=('Segoe UI', 8, 'italic')).pack(side="left", padx=20)
        
        # Store original log callback and wrap it
        self._OriginalLogMsg = self._LogMsg
        self._LogMsg = self._LogMsgWithProgress
        
        self.Runner = ScriptRunner(self._LogMsg, self._OnDone)
        self.Runner.Run(Cmd, Cwd=Cwd)
    
    def _LogMsgWithProgress(self, Text: str):
        """Log to both main window and progress popup"""
        # Log to main window
        self._OriginalLogMsg(Text)
        
        # Also log to progress popup if it exists
        if hasattr(self, '_ProgressLog') and self._ProgressLog.winfo_exists():
            self._ProgressLog.configure(state='normal')
            self._ProgressLog.insert("end", Text)
            self._ProgressLog.see("end")
            self._ProgressLog.update()
            self._ProgressLog.configure(state='disabled')
    
    def _OnProgressWindowClose(self):
        """Handle progress window close button"""
        pass  # Don't allow closing via X button

    def _StopRunning(self):
        if self.Runner:
            self._LogMsg("Stopping...\n")
            self.Runner.Stop()

    def _OnDone(self, Success, Code):
        # Restore original log function
        if hasattr(self, '_OriginalLogMsg'):
            self._LogMsg = self._OriginalLogMsg
        
        self.Progress.stop()
        self._SetButtons(Running=False)
        self._LogMsg("Completed successfully.\n" if Success else f"Finished with errors (exit code: {Code}).\n")
        
        # Update progress popup (keep it open, don't close it)
        if hasattr(self, '_ProgressWindow') and self._ProgressWindow.winfo_exists():
            # Change button to "Close" instead of "Stop Operation"
            self._ProgressCancelBtn.config(text="Close", command=self._CloseProgressWindow)
            # Stop the progress bar
            if hasattr(self, '_ProgressPopupBar'):
                self._ProgressPopupBar.stop()
        
        if self.TempCsvPath and os.path.exists(self.TempCsvPath):
            try:
                os.remove(self.TempCsvPath)
            except Exception as e:
                self._LogMsg(f"Warning: could not delete temp file {self.TempCsvPath}: {e}\n")
            finally:
                self.TempCsvPath = None
        
        self.Runner = None
        self._UpdateScanStatus()
    
    def _CloseProgressWindow(self):
        """Close the progress window"""
        if hasattr(self, '_ProgressWindow') and self._ProgressWindow.winfo_exists():
            self._ProgressWindow.destroy()

    def _SetButtons(self, Running: bool):
        StateRun = "disabled" if Running else "normal"
        StateStop = "normal" if Running else "disabled"
        try:
            self.ScanBtn.config(state=StateRun)
        except Exception:
            pass
        self.BackupBtn.config(state=StateRun if self.BackupItems else "disabled")
        # Restore button state is now controlled by _OnRestorePathChanged
        if not Running and self.RestorePathVar.get().strip() and os.path.exists(self.RestorePathVar.get().strip()):
            self.RestoreBtn.config(state="normal")
        elif Running:
            self.RestoreBtn.config(state="disabled")
        self.StopBtn.config(state=StateStop)

    # ------------------- Backup logic -------------------
    def _LoadBackupCsv(self):
        Path = self.CsvVar.get().strip()
        if not Path or not os.path.exists(Path):
            messagebox.showerror("Error", "CSV file not found.")
            return
        try:
            self.BackupItems = []
            with open(Path, "r", encoding="utf-8-sig", newline="") as f:
                Reader = csv.DictReader(f)
                if not Reader.fieldnames:
                    messagebox.showerror("Error", "CSV appears to have no header.")
                    return
                HeaderMap = {h.strip().lower(): h for h in Reader.fieldnames}
                
                def GetVal(Row, Keys):
                    for K in Keys:
                        Src = HeaderMap.get(K)
                        if Src and Src in Row:
                            return (Row.get(Src) or "").strip()
                    return ""
                    
                for Row in Reader:
                    self.BackupItems.append({
                        "title": GetVal(Row, ["title", "name", "item title"]),
                        "id": GetVal(Row, ["id", "itemid", "item id"]),
                        "type": GetVal(Row, ["type", "item type"]),
                        "url": GetVal(Row, ["itempageurl", "url", "item url", "link"]),
                        "selected": True
                    })
            
            self._PopulateBackupTree()
            self.BackupBtn.config(state="normal" if self.BackupItems else "disabled")
            self.BackupStatusLabel.config(text=f"Loaded {len(self.BackupItems)} items from CSV")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load CSV: {e}")

    def _PopulateBackupTree(self):
        for Iid in self.BackupTree.get_children():
            self.BackupTree.delete(Iid)
        for Item in self.BackupItems:
            Checkbox = "[X]" if Item.get("selected") else "[ ]"
            self.BackupTree.insert("", "end",
                                   values=(Checkbox,
                                           Item.get("title", ""),
                                           Item.get("id", ""),
                                           Item.get("type", ""),
                                           Item.get("url", "")))

    def _OnBackupTreeClick(self, Event):
        Region = self.BackupTree.identify_region(Event.x, Event.y)
        if Region != "cell":
            return
        Col = self.BackupTree.identify_column(Event.x)
        RowId = self.BackupTree.identify_row(Event.y)
        if not RowId:
            return

        if Col == "#1":
            Idx = self.BackupTree.index(RowId)
            self.BackupItems[Idx]["selected"] = not self.BackupItems[Idx]["selected"]
            self._PopulateBackupTree()

    def _OnBackupTreeDoubleClick(self, Event):
        Region = self.BackupTree.identify_region(Event.x, Event.y)
        if Region != "cell":
            return
        Col = self.BackupTree.identify_column(Event.x)
        RowId = self.BackupTree.identify_row(Event.y)
        if Col == "#5" and RowId:
            import webbrowser
            Values = self.BackupTree.item(RowId, "values")
            Url = Values[4]
            if Url.startswith("http"):
                webbrowser.open_new_tab(Url)

    def _SortBackupTree(self, Col):
        if Col not in ("Title", "Type"):
            return
        Reverse = self.SortStates[Col]
        self.SortStates[Col] = not Reverse
        KeyFunc = lambda Item: Item[Col.lower()] or ""
        self.BackupItems.sort(key=KeyFunc, reverse=Reverse)
        self._PopulateBackupTree()

    def _ToggleAllBackupSelection(self, SelectState: bool):
        for Item in self.BackupItems:
            Item["selected"] = SelectState
        self._PopulateBackupTree()

    def _RunBackup(self):
        SelectedIds = [Item["id"] for Item in self.BackupItems if Item.get("selected") and Item.get("id")]
        if not SelectedIds:
            messagebox.showwarning("Nothing to do", "No items are selected for backup.")
            return
        
        BackupDir = self.BackupDirVar.get().strip()
        if not BackupDir:
            messagebox.showerror("Error", "Please select backup directory.")
            return
        
        os.makedirs(BackupDir, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False, newline="", encoding="utf-8") as Tmp:
                Writer = csv.writer(Tmp)
                Writer.writerow(["id"])
                for ItemId in SelectedIds:
                    Writer.writerow([ItemId])
                self.TempCsvPath = Tmp.name
            
            self._LogMsg(f"Backing up {len(SelectedIds)} items to '{BackupDir}'\n")
            self._LogMsg(f"Backup mode: {self.BackupMode.get().upper()}\n")
            
            BackupScript = os.path.join(script_dir, "backup.py")
            Cmd = [sys.executable, BackupScript, "--csv", self.TempCsvPath, "--dest", BackupDir, "--mode", self.BackupMode.get()]
            self._StartRun(Cmd)
        except Exception as e:
            messagebox.showerror("Error", f"Could not create temp CSV: {e}")

    # ------------------- Utilities -------------------
    def _ChooseCsv(self):
        Path = filedialog.asksaveasfilename(
            title="Select or enter CSV path",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if Path:
            self.CsvVar.set(Path)
            self._UpdateScanStatus()

    def _ChooseBackupDir(self):
        Path = filedialog.askdirectory(title="Select Backup Directory")
        if Path:
            self.BackupDirVar.set(Path)

    def _LogMsg(self, Text: str):
        self.Log.configure(state='normal')
        self.Log.insert("end", Text)
        self.Log.see("end")
        self.Log.update()  # Force UI update
        self.Log.configure(state='disabled')

    def _OnClose(self):
        self.Cfg["csv_path"] = self.CsvVar.get()
        self.Cfg["backup_dir"] = self.BackupDirVar.get()
        self.Cfg["backup_mode"] = self.BackupMode.get()
        SaveConfig(self.Cfg)
        
        if self.Runner and self.Runner.Process and self.Runner.Process.poll() is None:
            if messagebox.askyesno("Confirm", "A task is running. Stop and exit?"):
                try:
                    self.Runner.Stop()
                except Exception:
                    pass
            else:
                return
        
        self.destroy()


if __name__ == "__main__":
    App().mainloop()

