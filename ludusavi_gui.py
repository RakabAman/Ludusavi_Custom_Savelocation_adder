#!/usr/bin/env python3
"""
Ludusavi Custom Save Adder - GUI (Deep Scan + Stop‑at‑Game option)
- Deep scan: find game folders, then save folders inside them.
- Optional: stop recursing once a game folder is found.
- All existing features preserved.
"""

import os
import sys
import json
import threading
import queue
import time
import difflib
import fnmatch
import pickle
import subprocess
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import DND_FILES, TkinterDnD
from pathlib import Path
import yaml

# ----------------------------------------------------------------------
# Detect if running as frozen exe
# ----------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
DEFAULT_LUDUSAVI = BASE_DIR / "ludusavi.exe"
DEFAULT_CONFIG = BASE_DIR / "config.yaml"
DEFAULT_MANIFEST = BASE_DIR / "manifest.yaml"
SETTINGS_FILE = BASE_DIR / "ludusavi_gui_settings.json"
MANIFEST_CACHE = BASE_DIR / "manifest_cache.pkl"

DEFAULT_PREDEFINED = [
    r"C:\Users\Public\Documents\Steam\CODEX",
    r"C:\Users\Public\Documents\uPlay\CODEX\Saves",
    r"%APPDATA%\Goldberg SteamEmu Saves",
    r"%APPDATA%\Steam\CODEX",
    r"%APPDATA%\SKIDROW",
    r"%LOCALAPPDATA%\SKIDROW",
    r"%PROGRAMDATA%\RELOADED\RLD!",
    r"%PROGRAMDATA%\Socialclub\RLD!",
    r"%PROGRAMDATA%\Steam\RLD!",
    r"%PROGRAMDATA%\3DMGAME",
    r"%USERPROFILE%\Documents\CPY_SAVES",
    r"%USERPROFILE%\Saved Games"
]

DEFAULT_EXCLUDE = [
    "*.ini", "*.log", "*.cache", "*.dll", "*.exe", "*.txt",
    "*.jpg", "*.png", "*.bmp", "*.cfg", "*.json", "*.bak", "*.old"
]

# System folders to skip when skip_system_folders is True
SYSTEM_FOLDERS = {
    "Windows", "Program Files", "Program Files (x86)", "System32", "System",
    "SysWOW64", "Boot", "Recovery", "$Recycle.Bin", "System Volume Information",
    "ProgramData", "PerfLogs", "Python", "Microsoft", "MSBuild", "Common Files",
    "WindowsApps", "ModifiableWindowsApps", "Temp", "tmp"
}

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
def log(msg):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

# ----------------------------------------------------------------------
# Core Functions
# ----------------------------------------------------------------------
def normalize_path(path_obj: Path) -> str:
    path_str = str(path_obj)
    username = os.getlogin()
    mappings = [
        (f"C:\\Users\\{username}\\Documents", "<winDocuments>"),
        (f"C:\\Users\\{username}\\OneDrive\\Documents", "<winDocuments>"),
        (f"C:\\Users\\{username}\\AppData\\Roaming", "<winAppData>"),
        (f"C:\\Users\\{username}\\AppData\\Local", "<winLocalAppData>"),
        (f"C:\\Users\\{username}\\AppData\\LocalLow", "<winLocalAppDataLow>"),
        (f"C:\\Users\\{username}\\Saved Games", "<winDocuments>\\Saved Games"),
        (f"C:\\Users\\{username}", "<home>"),
        ("C:\\ProgramData", "<winProgramData>"),
        ("C:\\Users\\Public", "<winPublic>"),
    ]
    for prefix, placeholder in mappings:
        if path_str.startswith(prefix):
            remainder = path_str[len(prefix):]
            if remainder.startswith("\\"):
                remainder = remainder[1:]
            return f"{placeholder}\\{remainder}" if remainder else placeholder
    return path_str

def save_config(config, config_path):
    if "customGames" in config:
        ordered_games = []
        for entry in config["customGames"]:
            ordered_entry = {
                "name": entry.get("name", ""),
                "integration": entry.get("integration", "override"),
                "files": entry.get("files", []),
                "registry": entry.get("registry", []),
                "installDir": entry.get("installDir", []),
                "winePrefix": entry.get("winePrefix", [])
            }
            ordered_games.append(ordered_entry)
        config["customGames"] = ordered_games
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

def add_custom_game(config_path, game_name, normalized_path, merge=True):
    log(f"Adding custom entry: {game_name} -> {normalized_path}")
    if not config_path.exists():
        config = {"customGames": []}
        log("  Config file did not exist, created new.")
    else:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    custom = config.get("customGames", [])
    existing = None
    for entry in custom:
        if entry.get("name") == game_name:
            existing = entry
            break
    if existing:
        if merge:
            files = existing.get("files", [])
            if normalized_path not in files:
                files.append(normalized_path)
                existing["files"] = files
                log(f"  Merged path into existing game '{game_name}'")
            else:
                log(f"  Path already exists for '{game_name}'")
    else:
        custom.append({
            "name": game_name,
            "integration": "override",
            "files": [normalized_path],
            "registry": [],
            "installDir": [],
            "winePrefix": []
        })
        log(f"  Created new game '{game_name}'")
    config["customGames"] = custom
    save_config(config, config_path)
    log(f"  Saved to {config_path}")
    return True

def get_files_preview_and_valid(folder_path: Path, exclude_patterns, max_count=3, max_depth=2):
    preview = []
    valid_count = 0
    base_depth = len(folder_path.parts)
    try:
        for item in folder_path.rglob("*"):
            if item.is_file():
                rel = item.relative_to(folder_path)
                depth = len(rel.parts)
                if depth <= max_depth:
                    rel_str = str(rel)
                    excluded = False
                    for pattern in exclude_patterns:
                        if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(item.name, pattern):
                            excluded = True
                            break
                    if not excluded:
                        valid_count += 1
                        if len(preview) < max_count:
                            preview.append(rel_str)
                    elif len(preview) < max_count:
                        preview.append(rel_str)
    except PermissionError:
        pass
    return preview, valid_count

# ----------------------------------------------------------------------
# GUI Application
# ----------------------------------------------------------------------
class LudusaviGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Ludusavi Save Adder")
        self.root.geometry("1250x900")  # increased height for new checkbox
        self.scan_queue = queue.Queue()
        self.scan_thread = None
        self.stop_scan = False
        self.scan_results = []
        self.manifest_data = None
        self.manifest_loaded = False
        self.manifest_loading_thread = None
        self.custom_config = None
        self.load_settings()
        self.load_custom_config()

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.scan_frame = ttk.Frame(self.notebook)
        self.settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.scan_frame, text="Scan")
        self.notebook.add(self.settings_frame, text="Settings")

        self.build_status_bar(root)
        self.build_settings_tab()
        self.build_scan_tab()

        self.start_background_manifest_load()

        if len(sys.argv) > 1:
            folder = sys.argv[1]
            log(f"Command-line auto-scan: {folder}")
            self.root.after(500, lambda: self.scan_custom_folder(folder))

    def build_status_bar(self, parent):
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=2)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(status_frame, variable=self.progress_var, maximum=100, mode='determinate')
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,5))
        self.status_label = ttk.Label(status_frame, text="Ready", width=50, anchor=tk.W, relief=tk.SUNKEN)
        self.status_label.pack(side=tk.RIGHT, fill=tk.X, expand=True)

    def load_custom_config(self):
        log("Loading custom config from " + str(self.config_path))
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.custom_config = yaml.safe_load(f) or {}
            log(f"  Loaded {len(self.custom_config.get('customGames', []))} custom entries")
        else:
            self.custom_config = {"customGames": []}
            log("  No existing config.yaml, will create on first save")

    # ------------------------------------------------------------------
    # Advanced matching methods
    # ------------------------------------------------------------------
    def _normalize_for_fuzzy(self, name: str) -> str:
        if not name:
            return ""
        name = re.sub(r'[^\w\s]', ' ', name)
        stopwords = {'the', 'of', 'and', 'for', 'with', 'on', 'at', 'from', 'by', 'in', 'a', 'to',
                     'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x'}
        words = name.lower().split()
        words = [w for w in words if w not in stopwords]
        return ' '.join(words)

    def _find_best_fuzzy_match(self, candidate: str, manifest_keys: list, cutoff=0.65) -> str:
        if not candidate or not manifest_keys:
            return None
        normalized_candidate = self._normalize_for_fuzzy(candidate)
        if not normalized_candidate:
            return None
        best_match = None
        best_score = 0.0
        for key in manifest_keys:
            norm_key = self._normalize_for_fuzzy(key)
            if not norm_key:
                continue
            score = difflib.SequenceMatcher(None, normalized_candidate, norm_key).ratio()
            if score > best_score and score >= cutoff:
                best_score = score
                best_match = key
        if best_match:
            log(f"  Fuzzy match: '{candidate}' -> '{best_match}' (score={best_score:.2f})")
        return best_match

    def find_game_by_path_ancestor(self, normalized_path: str):
        if not self.manifest_data:
            return None, None
        norm_path = normalized_path.lower().replace("\\", "/")
        for game_name, game_data in self.manifest_data.items():
            files_obj = game_data.get("files", {})
            for manifest_path in files_obj.keys():
                norm_manifest = manifest_path.lower().replace("\\", "/")
                if norm_path.startswith(norm_manifest) or norm_manifest.startswith(norm_path):
                    log(f"  Path-ancestor match: {game_name} (manifest: {manifest_path})")
                    return game_name, manifest_path
        return None, None

    def resolve_game_name_extended(self, folder_name: str, full_path: Path):
        if not self.manifest_data:
            return None, None, None
        exact = self.resolve_game_name(folder_name)
        if exact:
            return exact, "exact", None
        fuzzy = self._find_best_fuzzy_match(folder_name, list(self.manifest_data.keys()))
        if fuzzy:
            return fuzzy, "fuzzy", None
        norm_path = normalize_path(full_path)
        game, matched = self.find_game_by_path_ancestor(norm_path)
        if game:
            return game, "path-ancestor", matched
        path_parts = full_path.parts
        for i in range(1, min(3, len(path_parts))):
            parent_idx = -i-1
            if abs(parent_idx) <= len(path_parts):
                parent_folder = path_parts[parent_idx]
                if parent_folder:
                    fuzzy_parent = self._find_best_fuzzy_match(parent_folder, list(self.manifest_data.keys()))
                    if fuzzy_parent:
                        return fuzzy_parent, f"parent-{i}", None
        generic_names = {"saved", "savegames", "saves", "save", "config", "settings", "data", "files", "profiles"}
        if folder_name.lower() in generic_names and len(path_parts) >= 2:
            parent_folder = path_parts[-2]
            fuzzy_parent = self._find_best_fuzzy_match(parent_folder, list(self.manifest_data.keys()))
            if fuzzy_parent:
                return fuzzy_parent, "parent-generic", None
        return None, None, None

    def resolve_edited_name(self, edited_name: str) -> str:
        """Try to resolve an edited name: exact/case-insens, then fuzzy."""
        if not self.manifest_loaded:
            self.ensure_manifest_ready()
        if not self.manifest_data:
            return None
        exact = self.resolve_game_name(edited_name)
        if exact:
            return exact
        fuzzy = self._find_best_fuzzy_match(edited_name, list(self.manifest_data.keys()))
        return fuzzy

    # ------------------------------------------------------------------
    # Exclude patterns management
    # ------------------------------------------------------------------
    def add_exclude_pattern(self):
        pattern = self.exclude_entry.get().strip()
        if pattern and pattern not in self.exclude_patterns:
            self.exclude_patterns.append(pattern)
            self.refresh_exclude_list()
            self.exclude_entry.delete(0, tk.END)
            log(f"Added exclude pattern: {pattern}")
        elif pattern in self.exclude_patterns:
            log(f"Pattern {pattern} already exists")

    def remove_exclude_pattern(self):
        sel = self.exclude_listbox.curselection()
        if sel:
            removed = self.exclude_patterns.pop(sel[0])
            self.refresh_exclude_list()
            log(f"Removed exclude pattern: {removed}")

    def refresh_exclude_list(self):
        self.exclude_listbox.delete(0, tk.END)
        for p in self.exclude_patterns:
            self.exclude_listbox.insert(tk.END, p)

    # ------------------------------------------------------------------
    # Background manifest loading with pickle cache
    # ------------------------------------------------------------------
    def start_background_manifest_load(self):
        def load_worker():
            log("Manifest loading thread started")
            self.update_status("Loading manifest...")
            start = time.perf_counter()
            if MANIFEST_CACHE.exists():
                log(f"Cache file found: {MANIFEST_CACHE}")
                try:
                    with open(MANIFEST_CACHE, "rb") as f:
                        self.manifest_data = pickle.load(f)
                    elapsed = (time.perf_counter() - start) * 1000
                    log(f"Loaded cached manifest: {len(self.manifest_data)} games in {elapsed:.0f} ms")
                    if "Stardew Valley" in self.manifest_data:
                        log("  Stardew Valley found in cache")
                    else:
                        log("  Stardew Valley NOT in cache")
                    self.update_status(f"Loaded cached manifest ({len(self.manifest_data)} games)")
                    self.manifest_loaded = True
                    return
                except Exception as e:
                    log(f"Cache load error: {e}")
                    self.update_status("Cache error, parsing YAML...")
            else:
                log("No cache file found, will parse YAML")
            if not self.manifest_path.exists():
                log(f"Manifest file not found: {self.manifest_path}")
                self.update_status("manifest.yaml not found")
                self.manifest_data = {}
                self.manifest_loaded = True
                return
            log(f"Parsing YAML manifest: {self.manifest_path}")
            try:
                try:
                    from yaml import CLoader as Loader
                    log("Using CLoader for YAML")
                except ImportError:
                    from yaml import SafeLoader as Loader
                    log("Using SafeLoader (slower), install libyaml for faster parsing")
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self.manifest_data = yaml.load(f, Loader=Loader)
                with open(MANIFEST_CACHE, "wb") as f:
                    pickle.dump(self.manifest_data, f)
                elapsed = (time.perf_counter() - start) * 1000
                log(f"Parsed YAML: {len(self.manifest_data)} games in {elapsed:.0f} ms. Cached to {MANIFEST_CACHE}")
                if "Stardew Valley" in self.manifest_data:
                    log("  Stardew Valley found in parsed manifest")
                else:
                    log("  Stardew Valley NOT in parsed manifest")
                self.update_status(f"Loaded manifest ({len(self.manifest_data)} games)")
            except Exception as e:
                log(f"YAML load error: {e}")
                self.update_status(f"Error loading manifest: {e}")
                self.manifest_data = {}
            self.manifest_loaded = True
            log("Manifest loading thread finished")

        self.manifest_loading_thread = threading.Thread(target=load_worker, daemon=True)
        self.manifest_loading_thread.start()

    def ensure_manifest_ready(self):
        if self.manifest_loaded:
            return
        log("Waiting for manifest to finish loading...")
        self.update_status("Waiting for manifest...")
        while not self.manifest_loaded and self.manifest_loading_thread and self.manifest_loading_thread.is_alive():
            self.root.update_idletasks()
            time.sleep(0.1)
        log("Manifest ready")
        self.update_status("Manifest ready")

    def resolve_game_name(self, candidate):
        if not self.manifest_loaded:
            self.ensure_manifest_ready()
        if not self.manifest_data:
            return None
        if candidate in self.manifest_data:
            log(f"  Exact match: {candidate}")
            return candidate
        lower_candidate = candidate.lower()
        for name in self.manifest_data:
            if name.lower() == lower_candidate:
                log(f"  Case-insensitive match: {name}")
                return name
        if candidate.isdigit():
            sid = int(candidate)
            for name, data in self.manifest_data.items():
                if data.get("steam", {}).get("id") == sid:
                    log(f"  Steam ID match: {name}")
                    return name
        return None

    def suggest_similar_name(self, candidate):
        if not self.manifest_loaded:
            self.ensure_manifest_ready()
        if not self.manifest_data:
            return None
        if candidate in self.manifest_data:
            return candidate
        match = self._find_best_fuzzy_match(candidate, list(self.manifest_data.keys()))
        return match

    def is_path_covered_by_manifest(self, game_name, normalized_path):
        if not self.manifest_loaded or not self.manifest_data:
            return False
        game_entry = self.manifest_data.get(game_name)
        if not game_entry:
            return False
        files_obj = game_entry.get("files", {})
        norm_lower = normalized_path.lower().replace("\\", "/")
        for manifest_path in files_obj.keys():
            if manifest_path.lower().replace("\\", "/") == norm_lower:
                return True
        return False

    def is_path_covered_by_custom(self, normalized_path):
        if not self.custom_config:
            return False
        for entry in self.custom_config.get("customGames", []):
            for path in entry.get("files", []):
                if path == normalized_path:
                    return True
        return False

    def get_coverage_status(self, game_name, normalized_path, match_type=None):
        if match_type == "path-ancestor":
            return "✅", "Covered by manifest (parent)"
        if game_name and self.is_path_covered_by_manifest(game_name, normalized_path):
            return "✅", "Covered by manifest"
        if self.is_path_covered_by_custom(normalized_path):
            return "✅", "Covered by custom entry"
        return "❌", "New location"

    def update_status(self, msg):
        self.root.after(0, lambda: self.status_label.config(text=msg[:120]))

    # ------------------------------------------------------------------
    # Launch Ludusavi
    # ------------------------------------------------------------------
    def launch_ludusavi(self):
        if self.ludusavi_path.exists():
            log(f"Launching Ludusavi: {self.ludusavi_path}")
            try:
                subprocess.Popen([str(self.ludusavi_path)], shell=True)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to launch Ludusavi:\n{e}")
        else:
            messagebox.showerror("Error", f"ludusavi.exe not found at:\n{self.ludusavi_path}")

    # ------------------------------------------------------------------
    # Custom dialogs for predefined locations
    # ------------------------------------------------------------------
    def add_predef_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Predefined Location")
        dialog.geometry("550x120")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Enter path (can use %APPDATA%, %USERPROFILE%, etc.):").pack(padx=5, pady=5)
        entry = ttk.Entry(dialog, width=70)
        entry.pack(padx=5, pady=5)
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=5)
        def browse():
            folder = filedialog.askdirectory(title="Select folder")
            if folder:
                converted = self.to_env_path(folder)
                entry.delete(0, tk.END)
                entry.insert(0, converted)
        ttk.Button(btn_frame, text="Browse", command=browse).pack(side=tk.LEFT, padx=5)
        def add():
            path = entry.get().strip()
            if path:
                self.predefined_locations.append(path)
                self.refresh_predef_listbox()
                log(f"Added predefined location: {path}")
                dialog.destroy()
        ttk.Button(btn_frame, text="Add", command=add).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def edit_predef_dialog(self):
        sel = self.predef_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        old_path = self.predefined_locations[idx]
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Predefined Location")
        dialog.geometry("550x120")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Edit path (can use %APPDATA%, %USERPROFILE%, etc.):").pack(padx=5, pady=5)
        entry = ttk.Entry(dialog, width=70)
        entry.insert(0, old_path)
        entry.pack(padx=5, pady=5)
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=5)
        def browse():
            folder = filedialog.askdirectory(title="Select folder", initialdir=old_path)
            if folder:
                converted = self.to_env_path(folder)
                entry.delete(0, tk.END)
                entry.insert(0, converted)
        ttk.Button(btn_frame, text="Browse", command=browse).pack(side=tk.LEFT, padx=5)
        def save():
            new_path = entry.get().strip()
            if new_path:
                self.predefined_locations[idx] = new_path
                self.refresh_predef_listbox()
                log(f"Edited location: {old_path} -> {new_path}")
                dialog.destroy()
        ttk.Button(btn_frame, text="Save", command=save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    # ------------------------------------------------------------------
    # Settings tab (added stop-at-game checkbox)
    # ------------------------------------------------------------------
    def build_settings_tab(self):
        main_frame = ttk.Frame(self.settings_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        paths_frame = ttk.LabelFrame(main_frame, text="Paths & Scan Depth")
        paths_frame.pack(fill=tk.X, pady=5)
        paths_frame.columnconfigure(1, weight=1)

        ttk.Label(paths_frame, text="ludusavi.exe:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.ludusavi_var = tk.StringVar(value=str(self.ludusavi_path))
        ttk.Entry(paths_frame, textvariable=self.ludusavi_var).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(paths_frame, text="Browse", command=self.browse_ludusavi).grid(row=0, column=2, padx=5)

        ttk.Label(paths_frame, text="config.yaml:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.config_var = tk.StringVar(value=str(self.config_path))
        ttk.Entry(paths_frame, textvariable=self.config_var).grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(paths_frame, text="Browse", command=self.browse_config).grid(row=1, column=2, padx=5)

        ttk.Label(paths_frame, text="manifest.yaml:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.manifest_var = tk.StringVar(value=str(self.manifest_path))
        ttk.Entry(paths_frame, textvariable=self.manifest_var).grid(row=2, column=1, sticky="ew", padx=5)
        ttk.Button(paths_frame, text="Browse", command=self.browse_manifest).grid(row=2, column=2, padx=5)

        # Standard scan depth
        ttk.Label(paths_frame, text="Standard Scan Depth:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        self.depth_var = tk.IntVar(value=self.scan_depth)
        ttk.Spinbox(paths_frame, from_=1, to=5, textvariable=self.depth_var, width=5).grid(row=3, column=1, sticky=tk.W, padx=5)

        # Separator
        ttk.Separator(paths_frame, orient=tk.HORIZONTAL).grid(row=4, column=0, columnspan=3, sticky="ew", pady=5)

        # Deep scan controls
        self.deep_scan_var = tk.BooleanVar(value=self.deep_scan_enabled)
        ttk.Checkbutton(paths_frame, text="Enable Deep Scan (find game folders, then saves)",
                        variable=self.deep_scan_var).grid(row=5, column=0, columnspan=3, sticky=tk.W, padx=5, pady=2)

        ttk.Label(paths_frame, text="Game Folder Depth:").grid(row=6, column=0, sticky=tk.W, padx=5, pady=2)
        self.game_depth_var = tk.IntVar(value=self.game_folder_depth)
        ttk.Spinbox(paths_frame, from_=1, to=5, textvariable=self.game_depth_var, width=5).grid(row=6, column=1, sticky=tk.W, padx=5)

        ttk.Label(paths_frame, text="Save Folder Depth:").grid(row=7, column=0, sticky=tk.W, padx=5, pady=2)
        self.save_depth_var = tk.IntVar(value=self.save_folder_depth)
        ttk.Spinbox(paths_frame, from_=1, to=5, textvariable=self.save_depth_var, width=5).grid(row=7, column=1, sticky=tk.W, padx=5)

        self.skip_system_var = tk.BooleanVar(value=self.skip_system_folders)
        ttk.Checkbutton(paths_frame, text="Skip System Folders (Windows, Program Files, etc.)",
                        variable=self.skip_system_var).grid(row=8, column=0, columnspan=3, sticky=tk.W, padx=5, pady=2)

        # NEW: Stop-at-game checkbox
        self.stop_at_game_var = tk.BooleanVar(value=self.stop_at_game_folder)
        ttk.Checkbutton(paths_frame, text="Stop at first game folder (do not scan deeper)",
                        variable=self.stop_at_game_var).grid(row=9, column=0, columnspan=3, sticky=tk.W, padx=5, pady=2)

        btn_frame = ttk.Frame(paths_frame)
        btn_frame.grid(row=10, column=1, pady=10)
        ttk.Button(btn_frame, text="Save Settings", command=self.save_settings).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Reload Manifest", command=self.reload_manifest).pack(side=tk.LEFT, padx=2)

        # Rest of settings (predefined locations and exclude patterns) unchanged
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=5)

        predef_frame = ttk.LabelFrame(paned, text="Predefined Scan Locations")
        paned.add(predef_frame, weight=1)
        list_frame = ttk.Frame(predef_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.predef_listbox = tk.Listbox(list_frame, height=8)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.predef_listbox.yview)
        self.predef_listbox.config(yscrollcommand=scrollbar.set)
        self.predef_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        btn_frame2 = ttk.Frame(predef_frame)
        btn_frame2.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame2, text="Add", command=self.add_predef_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame2, text="Edit", command=self.edit_predef_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame2, text="Remove", command=self.remove_predef).pack(side=tk.LEFT, padx=2)

        exclude_frame = ttk.LabelFrame(paned, text="Exclude File Patterns (wildcards)")
        paned.add(exclude_frame, weight=1)
        excl_list_frame = ttk.Frame(exclude_frame)
        excl_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.exclude_listbox = tk.Listbox(excl_list_frame, height=8)
        excl_scroll = ttk.Scrollbar(excl_list_frame, orient=tk.VERTICAL, command=self.exclude_listbox.yview)
        self.exclude_listbox.config(yscrollcommand=excl_scroll.set)
        self.exclude_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        excl_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        pattern_frame = ttk.Frame(exclude_frame)
        pattern_frame.pack(fill=tk.X, pady=5)
        self.exclude_entry = ttk.Entry(pattern_frame, width=30)
        self.exclude_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(pattern_frame, text="Add Pattern", command=self.add_exclude_pattern).pack(side=tk.LEFT, padx=2)
        ttk.Button(pattern_frame, text="Remove Selected", command=self.remove_exclude_pattern).pack(side=tk.LEFT, padx=2)

        self.refresh_predef_listbox()
        self.refresh_exclude_list()

    def browse_ludusavi(self):
        p = filedialog.askopenfilename(title="Select ludusavi.exe", filetypes=[("Executable", "*.exe")])
        if p:
            self.ludusavi_var.set(p)
            log(f"Set ludusavi.exe to {p}")
    def browse_config(self):
        p = filedialog.askopenfilename(title="Select config.yaml", filetypes=[("YAML", "*.yaml")])
        if p:
            self.config_var.set(p)
            self.load_custom_config()
            log(f"Set config.yaml to {p}")
    def browse_manifest(self):
        p = filedialog.askopenfilename(title="Select manifest.yaml", filetypes=[("YAML", "*.yaml")])
        if p:
            self.manifest_var.set(p)
            self.manifest_path = Path(p)
            self.manifest_loaded = False
            self.manifest_data = None
            self.start_background_manifest_load()
            log(f"Set manifest.yaml to {p}")

    def save_settings(self):
        self.ludusavi_path = Path(self.ludusavi_var.get())
        self.config_path = Path(self.config_var.get())
        self.manifest_path = Path(self.manifest_var.get())
        self.scan_depth = self.depth_var.get()
        self.deep_scan_enabled = self.deep_scan_var.get()
        self.game_folder_depth = self.game_depth_var.get()
        self.save_folder_depth = self.save_depth_var.get()
        self.skip_system_folders = self.skip_system_var.get()
        self.stop_at_game_folder = self.stop_at_game_var.get()
        settings = {
            "ludusavi": str(self.ludusavi_path),
            "config": str(self.config_path),
            "manifest": str(self.manifest_path),
            "predefined_locations": self.predefined_locations,
            "exclude_patterns": self.exclude_patterns,
            "scan_depth": self.scan_depth,
            "deep_scan_enabled": self.deep_scan_enabled,
            "game_folder_depth": self.game_folder_depth,
            "save_folder_depth": self.save_folder_depth,
            "skip_system_folders": self.skip_system_folders,
            "stop_at_game_folder": self.stop_at_game_folder
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        log(f"Settings saved to {SETTINGS_FILE}")
        self.manifest_loaded = False
        self.manifest_data = None
        self.start_background_manifest_load()
        self.load_custom_config()
        messagebox.showinfo("Settings", "Settings saved.")

    def reload_manifest(self):
        log("Reload manifest requested")
        self.manifest_loaded = False
        self.manifest_data = None
        if MANIFEST_CACHE.exists():
            MANIFEST_CACHE.unlink()
            log(f"Deleted cache file: {MANIFEST_CACHE}")
        self.start_background_manifest_load()
        self.update_status("Reloading manifest...")

    def remove_predef(self):
        sel = self.predef_listbox.curselection()
        if sel:
            removed = self.predefined_locations.pop(sel[0])
            self.refresh_predef_listbox()
            log(f"Removed predefined location: {removed}")

    def refresh_predef_listbox(self):
        self.predef_listbox.delete(0, tk.END)
        for loc in self.predefined_locations:
            self.predef_listbox.insert(tk.END, loc)

    def to_env_path(self, abs_path: str) -> str:
        abs_path = os.path.normpath(abs_path)
        env_mappings = [
            (os.path.expandvars("%APPDATA%"), "%APPDATA%"),
            (os.path.expandvars("%LOCALAPPDATA%"), "%LOCALAPPDATA%"),
            (os.path.expandvars("%USERPROFILE%"), "%USERPROFILE%"),
            (os.path.expandvars("%PROGRAMDATA%"), "%PROGRAMDATA%"),
            (os.path.expandvars("%PUBLIC%"), "%PUBLIC%"),
        ]
        for expanded, var in env_mappings:
            if abs_path.startswith(expanded):
                rest = abs_path[len(expanded):].lstrip("\\")
                return f"{var}\\{rest}" if rest else var
        return abs_path

    # ------------------------------------------------------------------
    # Scan tab
    # ------------------------------------------------------------------
    def build_scan_tab(self):
        toolbar = ttk.Frame(self.scan_frame)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(toolbar, text="Scan Predefined", command=self.scan_predefined).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Browse & Scan", command=self.scan_custom_folder_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Stop", command=self.stop_scanning).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Clear", command=self.clear_results).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Launch Ludusavi", command=self.launch_ludusavi).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=2)
        ttk.Button(toolbar, text="Rescan Checked", command=self.rescan_checked).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Add Checked", command=self.add_checked).pack(side=tk.LEFT, padx=2)

        columns = ("check", "cov", "valid", "path", "name", "suggest", "files")
        self.tree = ttk.Treeview(self.scan_frame, columns=columns, show="headings", height=12)
        self.tree.heading("check", text="✓")
        self.tree.heading("cov", text="Covered")
        self.tree.heading("valid", text="Valid")
        self.tree.heading("path", text="Folder Path")
        self.tree.heading("name", text="Game Name (editable)")
        self.tree.heading("suggest", text="Suggested")
        self.tree.heading("files", text="Sample Files")
        
        # Set fixed widths for the first three columns (no stretching)
        self.tree.column("check", width=40, anchor=tk.CENTER, minwidth=30, stretch=False)
        self.tree.column("cov", width=45, anchor=tk.CENTER, minwidth=35, stretch=False)
        self.tree.column("valid", width=45, anchor=tk.CENTER, minwidth=35, stretch=False)
        # Other columns will stretch (default) and have reasonable minwidths
        self.tree.column("path", width=300, minwidth=200, stretch=True)
        self.tree.column("name", width=170, minwidth=120, stretch=False)
        self.tree.column("suggest", width=170, minwidth=120, stretch=False)
        self.tree.column("files", width=280, minwidth=150, stretch=True)

        tree_scroll = ttk.Scrollbar(self.scan_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.scan_frame.drop_target_register(DND_FILES)
        self.scan_frame.dnd_bind('<<Drop>>', self.on_drop)

        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Button-3>", self.show_context_menu)


    def on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return
        idx = self.tree.index(item)
        row = self.scan_results[idx]
        if column == "#4":   # Path column
            path = row["path"]
            if os.path.exists(path):
                os.startfile(path)
                log(f"Opened folder: {path}")
            else:
                messagebox.showerror("Error", f"Path not found:\n{path}")
        elif column == "#6":   # Suggest column
            sugg = row.get("suggested_name", "")
            if sugg:
                self.tree.set(item, "#5", sugg)
                row["selected_name"] = sugg
                log(f"Copied suggestion '{sugg}' to Name")
        elif column == "#5":   # Name column (editable)
            self.edit_name_cell(item, idx)

    def edit_name_cell(self, item, idx):
        x, y, width, height = self.tree.bbox(item, column="#5")
        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        current = self.tree.set(item, "#5")
        entry.insert(0, current)
        entry.focus()
        def save_edit(event=None):
            new_val = entry.get()
            self.tree.set(item, "#5", new_val)
            self.scan_results[idx]["selected_name"] = new_val
            entry.destroy()
            log(f"Edited name to: {new_val}")
        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        if item not in self.tree.selection():
            self.tree.selection_set(item)
        selected_iids = self.tree.selection()
        if not selected_iids:
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Rescan Selected", command=lambda: self.rescan_selected(ids=selected_iids))
        menu.add_command(label="Copy Suggestion to Name", command=lambda: self.copy_suggested_to_selected(ids=selected_iids))
        menu.add_command(label="Add Selected", command=lambda: self.add_selected(ids=selected_iids))
        menu.add_separator()
        menu.add_command(label="Open Folder", command=lambda: self.open_folder(selected_iids))
        menu.post(event.x_root, event.y_root)

    def on_drop(self, event):
        import re
        data = event.data
        paths = re.findall(r'\{.*?\}|\S+', data)
        for p in paths:
            p = p.strip('{}')
            if os.path.isdir(p):
                log(f"Drop detected: {p}")
                self.scan_custom_folder(p)
                return
        messagebox.showinfo("No Folder", "No valid folder detected in drop.")

    def open_folder(self, iids):
        for iid in iids:
            idx = self.tree.index(iid)
            path = self.scan_results[idx]["path"]
            if os.path.exists(path):
                os.startfile(path)
                log(f"Opened folder: {path}")

    # ------------------------------------------------------------------
    # Deep scan helper functions
    # ------------------------------------------------------------------
    def _is_system_folder(self, folder_name: str) -> bool:
        return folder_name in SYSTEM_FOLDERS

    def _collect_game_folders(self, root_path: Path, max_depth: int, current_depth: int, skip_system: bool):
        if current_depth > max_depth:
            return []
        results = []
        try:
            for item in root_path.iterdir():
                if not item.is_dir():
                    continue
                if skip_system and self._is_system_folder(item.name):
                    continue
                # Only resolve if we are at depth >= 1 (i.e., not the root)
                if current_depth >= 1:
                    resolved_name, match_type, _ = self.resolve_game_name_extended(item.name, item)
                else:
                    resolved_name = None
                    match_type = None
                # Only accept exact, fuzzy, or path-ancestor as game folder matches
                if resolved_name and match_type in ("exact", "fuzzy", "path-ancestor"):
                    results.append((item, resolved_name, match_type))
                    if self.stop_at_game_folder:
                        continue  # don't recurse into this folder
                # Always recurse deeper (unless max depth reached)
                if current_depth < max_depth:
                    results.extend(self._collect_game_folders(item, max_depth, current_depth + 1, skip_system))
        except PermissionError:
            pass
        return results

    def _collect_save_folders(self, game_folder: Path, max_depth: int, current_depth: int, exclude_patterns):
        if current_depth > max_depth:
            return []
        results = []
        try:
            # Only add this folder if it has valid files directly inside it
            if self._has_valid_files_directly(game_folder, exclude_patterns):
                preview, valid_count = get_files_preview_and_valid(game_folder, exclude_patterns, max_count=3, max_depth=2)
                results.append((game_folder, valid_count, preview))
            # Recurse into subfolders
            for item in game_folder.iterdir():
                if item.is_dir():
                    results.extend(self._collect_save_folders(item, max_depth, current_depth + 1, exclude_patterns))
        except PermissionError:
            pass
        return results

    def _has_valid_files_directly(self, folder_path: Path, exclude_patterns) -> bool:
        """Return True if folder contains at least one non‑excluded file directly inside it."""
        try:
            for item in folder_path.iterdir():
                if item.is_file():
                    excluded = False
                    for pattern in exclude_patterns:
                        if fnmatch.fnmatch(item.name, pattern):
                            excluded = True
                            break
                    if not excluded:
                        return True
        except PermissionError:
            pass
        return False

    # ------------------------------------------------------------------
    # Scanning with depth and extended matching (modified for deep scan)
    # ------------------------------------------------------------------
    def scan_predefined(self):
        if not self.predefined_locations:
            messagebox.showwarning("No Locations", "Add locations in Settings tab.")
            return
        log("Scanning predefined locations...")
        self.scan_locations(self.predefined_locations)

    def scan_custom_folder_dialog(self):
        folder = filedialog.askdirectory(title="Select folder to scan")
        if folder:
            self.scan_custom_folder(folder)

    def scan_custom_folder(self, folder):
        log(f"Scanning custom folder: {folder}")
        self.scan_locations([folder])

    def scan_locations(self, root_folders):
        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo("Scan in Progress", "Please stop the current scan first.")
            return
        self.stop_scan = False
        self.scan_results = []
        self.clear_results()
        self.progress_var.set(0)
        self.update_status("Preparing scan...")
        self.ensure_manifest_ready()
        self.load_custom_config()
        if self.deep_scan_enabled:
            log(f"Starting deep scan worker for {len(root_folders)} root(s), game_depth={self.game_folder_depth}, save_depth={self.save_folder_depth}, stop_at_game={self.stop_at_game_folder}")
        else:
            log(f"Starting standard scan worker for {len(root_folders)} root(s), depth={self.scan_depth}")
        self.scan_thread = threading.Thread(target=self._scan_worker, args=(root_folders,))
        self.scan_thread.start()
        self.root.after(100, self._poll_scan_queue)

    def _scan_worker(self, root_folders):
        total_roots = len(root_folders)
        log(f"Scan worker started, {total_roots} root folders")
        for r_idx, root in enumerate(root_folders):
            if self.stop_scan:
                log("Scan stopped by user")
                break
            expanded_root = os.path.expandvars(root)
            root_path = Path(expanded_root)
            log(f"Processing root {r_idx+1}/{total_roots}: {root} -> {expanded_root}")
            if not root_path.exists():
                log(f"  Root does not exist: {expanded_root}")
                self.update_status(f"Root missing: {root}")
                continue
            self.update_status(f"Scanning: {root_path}")

            if self.deep_scan_enabled:
                # ---- DEEP SCAN ----
                log("  Using deep scan")
                game_folders = self._collect_game_folders(root_path, self.game_folder_depth, 1, self.skip_system_folders)
                log(f"  Found {len(game_folders)} game folder candidates")
                for game_folder, game_name, match_type in game_folders:
                    if self.stop_scan:
                        break
                    log(f"    Scanning saves in: {game_folder} (game: {game_name})")
                    save_folders = self._collect_save_folders(game_folder, self.save_folder_depth, 1, self.exclude_patterns)
                    if not save_folders:
                        # No save folder found with valid files; optionally add the game folder itself
                        preview, valid_count = get_files_preview_and_valid(game_folder, self.exclude_patterns, max_count=3, max_depth=2)
                        if valid_count > 0:
                            save_folders = [(game_folder, valid_count, preview)]
                        else:
                            log(f"    No save folders with valid files found in {game_folder}")
                            continue
                    for save_folder, valid_count, preview in save_folders:
                        if self.stop_scan:
                            break
                        norm_path = normalize_path(save_folder)
                        cov_icon, _ = self.get_coverage_status(game_name, norm_path, match_type)
                        preview_str = ", ".join(preview) if preview else "(no files)"
                        valid_icon = "✔️" if valid_count > 0 else "❌"
                        self.scan_results.append({
                            "path": str(save_folder),
                            "normalized_path": norm_path,
                            "original_name": game_folder.name,
                            "selected_name": game_name,
                            "suggested_name": "",
                            "files_preview": preview_str,
                            "checked": None,
                            "coverage_icon": cov_icon,
                            "valid_icon": valid_icon,
                            "valid_count": valid_count,
                            "game_name_for_coverage": game_name,
                            "match_type": match_type
                        })
                        status_msg = f"  Found save: {save_folder} -> {game_name} {cov_icon} {valid_icon}"
                        log(f"    {status_msg}")
                        self.scan_queue.put(("log", status_msg))
                progress = (r_idx + 1) / total_roots * 100
                self.scan_queue.put(("progress", progress))
            else:
                # ---- STANDARD SCAN (old logic) ----
                candidates = self._collect_dirs_at_depth(root_path, self.scan_depth)
                log(f"  Found {len(candidates)} directories at depth {self.scan_depth}")
                for sub in candidates:
                    if self.stop_scan:
                        break
                    orig = sub.name
                    log(f"  Checking folder: {orig} (path: {sub})")
                    resolved_name, match_type, matched_path = self.resolve_game_name_extended(orig, sub)
                    exact = None
                    sugg = None
                    if resolved_name:
                        if match_type in ("exact", "fuzzy", "path-ancestor", "parent-1", "parent-2", "parent-generic"):
                            exact = resolved_name
                        else:
                            sugg = resolved_name
                    if not exact and not sugg:
                        sugg = self.suggest_similar_name(orig)
                    norm_path = normalize_path(sub)
                    game_for_coverage = exact if exact else (sugg if sugg else orig)
                    cov_icon, _ = self.get_coverage_status(game_for_coverage, norm_path, match_type)
                    preview, valid_count = get_files_preview_and_valid(sub, self.exclude_patterns, max_count=3, max_depth=2)
                    preview_str = ", ".join(preview) if preview else "(no files)"
                    valid_icon = "✔️" if valid_count > 0 else "❌"
                    self.scan_results.append({
                        "path": str(sub),
                        "normalized_path": norm_path,
                        "original_name": orig,
                        "selected_name": exact if exact else "",
                        "suggested_name": sugg if sugg else "",
                        "files_preview": preview_str,
                        "checked": None,
                        "coverage_icon": cov_icon,
                        "valid_icon": valid_icon,
                        "valid_count": valid_count,
                        "game_name_for_coverage": game_for_coverage,
                        "match_type": match_type
                    })
                    status_msg = f"Found: {orig} -> {exact or sugg or '?'} {cov_icon} {valid_icon} (match: {match_type or 'none'})"
                    log(f"    {status_msg}")
                    self.scan_queue.put(("log", status_msg))
                progress = (r_idx + 1) / total_roots * 100
                self.scan_queue.put(("progress", progress))

        self.scan_queue.put(("done", None))
        log("Scan worker finished")

    def _collect_dirs_at_depth(self, root_path: Path, target_depth: int):
        if target_depth == 0:
            return [root_path] if root_path.is_dir() else []
        result = []
        try:
            for item in root_path.iterdir():
                if item.is_dir():
                    if target_depth == 1:
                        result.append(item)
                    else:
                        result.extend(self._collect_dirs_at_depth(item, target_depth - 1))
        except PermissionError:
            pass
        return result

    def _poll_scan_queue(self):
        try:
            while True:
                msg, data = self.scan_queue.get_nowait()
                if msg == "progress":
                    self.progress_var.set(data)
                    self.update_status(f"Scanning... {data:.0f}%")
                elif msg == "log":
                    self.update_status(data)
                elif msg == "done":
                    self._populate_results()
                    self.update_status(f"Scan completed. {len(self.scan_results)} folders found.")
                    if self.scan_thread:
                        self.scan_thread.join()
                    log("Scan completed")
                    return
        except queue.Empty:
            pass
        if self.scan_thread and self.scan_thread.is_alive():
            self.root.after(100, self._poll_scan_queue)
        else:
            self._populate_results()
            self.update_status("Ready")

    def _populate_results(self):
        self.tree.delete(*self.tree.get_children())
        for item in self.scan_results:
            if item["checked"] is None:
                item["checked"] = tk.BooleanVar(value=False)
            chk = "☑" if item["checked"].get() else "☐"
            self.tree.insert("", tk.END, values=(
                chk,
                item["coverage_icon"],
                item["valid_icon"],
                item["path"],
                item["selected_name"],
                item["suggested_name"],
                item["files_preview"]
            ), tags=(item["path"],))
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)

    def on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            col = self.tree.identify_column(event.x)
            if col == "#1":
                item = self.tree.identify_row(event.y)
                if item:
                    idx = self.tree.index(item)
                    cur = self.scan_results[idx]["checked"].get()
                    self.scan_results[idx]["checked"].set(not cur)
                    self.tree.set(item, "#1", "☑" if not cur else "☐")

    def stop_scanning(self):
        self.stop_scan = True
        self.update_status("Stopping...")
        log("Stop requested")

    def clear_results(self):
        self.tree.delete(*self.tree.get_children())
        self.scan_results.clear()
        self.progress_var.set(0)
        self.update_status("Ready")
        log("Results cleared")

    # ------------------------------------------------------------------
    # Actions on checked rows (respects manual edits + fuzzy)
    # ------------------------------------------------------------------
    def get_checked_indices(self):
        return [i for i, item in enumerate(self.scan_results) if item["checked"].get()]

    def rescan_checked(self):
        indices = self.get_checked_indices()
        if not indices:
            messagebox.showinfo("No Selection", "No rows checked.")
            return
        log(f"Rescanning {len(indices)} checked rows")
        def worker():
            for idx in indices:
                if self.stop_scan:
                    break
                row = self.scan_results[idx]
                log(f"  Rescanning {row['original_name']} (path: {row['path']})")
                if row["selected_name"].strip():
                    name_to_resolve = row["selected_name"].strip()
                    official = self.resolve_edited_name(name_to_resolve)
                    if official:
                        row["selected_name"] = official
                        log(f"    User name resolved to official: {official}")
                    else:
                        log(f"    User name '{name_to_resolve}' not found in manifest, keeping as custom")
                    sugg = self.suggest_similar_name(row["original_name"])
                    row["suggested_name"] = sugg if sugg else ""
                else:
                    resolved_name, match_type, _ = self.resolve_game_name_extended(row["original_name"], Path(row["path"]))
                    if resolved_name:
                        if match_type in ("exact", "fuzzy", "path-ancestor", "parent-1", "parent-2", "parent-generic"):
                            row["selected_name"] = resolved_name
                            log(f"    Auto-resolved to: {resolved_name} (match: {match_type})")
                        else:
                            row["suggested_name"] = resolved_name
                            log(f"    Auto-suggested: {resolved_name}")
                    else:
                        sugg = self.suggest_similar_name(row["original_name"])
                        row["suggested_name"] = sugg if sugg else ""
                        log(f"    No auto-resolution, suggested: {row['suggested_name']}")
                game_for_coverage = row["selected_name"] if row["selected_name"] else (row["suggested_name"] if row["suggested_name"] else row["original_name"])
                cov_icon, _ = self.get_coverage_status(game_for_coverage, row["normalized_path"], match_type if 'match_type' in locals() else None)
                row["coverage_icon"] = cov_icon
                row["game_name_for_coverage"] = game_for_coverage
                preview, valid_count = get_files_preview_and_valid(Path(row["path"]), self.exclude_patterns, max_count=3, max_depth=2)
                row["valid_icon"] = "✔️" if valid_count > 0 else "❌"
                row["valid_count"] = valid_count
                row["files_preview"] = ", ".join(preview) if preview else "(no files)"
                self.root.after(0, lambda i=idx: self._update_tree_row(i))
                self.update_status(f"Resolved: {row['original_name']} -> {row['selected_name'] or row['suggested_name'] or '?'} {cov_icon} {row['valid_icon']}")
        self.update_status("Rescanning checked rows...")
        threading.Thread(target=worker).start()

    def add_checked(self):
        indices = self.get_checked_indices()
        if not indices:
            messagebox.showinfo("No Selection", "No rows checked.")
            return
        to_add = []
        skipped_covered = 0
        skipped_invalid = 0
        for idx in indices:
            row = self.scan_results[idx]
            if row["coverage_icon"] == "✅":
                if not messagebox.askyesno("Already Covered", f"Folder '{row['original_name']}' is already covered.\nAdd anyway?"):
                    skipped_covered += 1
                    continue
            if row["valid_icon"] == "❌":
                if not messagebox.askyesno("No Valid Files", f"Folder '{row['original_name']}' has no valid files (after exclusions).\nAdd anyway?"):
                    skipped_invalid += 1
                    continue
            name = row["selected_name"].strip()
            if not name:
                if not messagebox.askyesno("Missing Name", f"Folder '{row['original_name']}' has no name. Use folder name?"):
                    continue
                name = row["original_name"]
            to_add.append((name, row["normalized_path"]))
        if not to_add:
            msg = f"Skipped {skipped_covered} covered, {skipped_invalid} invalid."
            messagebox.showinfo("Nothing added", msg if (skipped_covered+skipped_invalid) else "No rows to add.")
            return
        success = 0
        for name, norm_path in to_add:
            if add_custom_game(self.config_path, name, norm_path):
                success += 1
        messagebox.showinfo("Done", f"Added {success} entries. Skipped: {skipped_covered} covered, {skipped_invalid} invalid.")
        self.update_status(f"Added {success} custom entries")
        self.load_custom_config()

    def copy_suggested_checked(self):
        indices = self.get_checked_indices()
        if not indices:
            messagebox.showinfo("No Selection", "No rows checked.")
            return
        for idx in indices:
            row = self.scan_results[idx]
            if row["suggested_name"]:
                row["selected_name"] = row["suggested_name"]
                self._update_tree_row(idx)
        log(f"Copied suggestions to {len(indices)} checked rows")
        self.update_status("Copied suggestions to checked rows")

    # ------------------------------------------------------------------
    # Context menu actions (on selected/highlighted rows)
    # ------------------------------------------------------------------
    def rescan_selected(self, ids=None):
        indices = [self.tree.index(iid) for iid in ids] if ids else []
        if not indices:
            messagebox.showinfo("No Selection", "No rows selected.")
            return
        log(f"Rescanning {len(indices)} selected rows")
        def worker():
            for idx in indices:
                if self.stop_scan:
                    break
                row = self.scan_results[idx]
                log(f"  Rescanning {row['original_name']} (path: {row['path']})")
                if row["selected_name"].strip():
                    name_to_resolve = row["selected_name"].strip()
                    official = self.resolve_edited_name(name_to_resolve)
                    if official:
                        row["selected_name"] = official
                        log(f"    User name resolved to official: {official}")
                    else:
                        log(f"    User name '{name_to_resolve}' not found in manifest, keeping as custom")
                    sugg = self.suggest_similar_name(row["original_name"])
                    row["suggested_name"] = sugg if sugg else ""
                else:
                    resolved_name, match_type, _ = self.resolve_game_name_extended(row["original_name"], Path(row["path"]))
                    if resolved_name:
                        if match_type in ("exact", "fuzzy", "path-ancestor", "parent-1", "parent-2", "parent-generic"):
                            row["selected_name"] = resolved_name
                            log(f"    Auto-resolved to: {resolved_name} (match: {match_type})")
                        else:
                            row["suggested_name"] = resolved_name
                            log(f"    Auto-suggested: {resolved_name}")
                    else:
                        sugg = self.suggest_similar_name(row["original_name"])
                        row["suggested_name"] = sugg if sugg else ""
                        log(f"    No auto-resolution, suggested: {row['suggested_name']}")
                game_for_coverage = row["selected_name"] if row["selected_name"] else (row["suggested_name"] if row["suggested_name"] else row["original_name"])
                cov_icon, _ = self.get_coverage_status(game_for_coverage, row["normalized_path"], match_type if 'match_type' in locals() else None)
                row["coverage_icon"] = cov_icon
                row["game_name_for_coverage"] = game_for_coverage
                preview, valid_count = get_files_preview_and_valid(Path(row["path"]), self.exclude_patterns, max_count=3, max_depth=2)
                row["valid_icon"] = "✔️" if valid_count > 0 else "❌"
                row["valid_count"] = valid_count
                row["files_preview"] = ", ".join(preview) if preview else "(no files)"
                self.root.after(0, lambda i=idx: self._update_tree_row(i))
                self.update_status(f"Resolved: {row['original_name']} -> {row['selected_name'] or row['suggested_name'] or '?'} {cov_icon} {row['valid_icon']}")
        self.update_status("Rescanning selected rows...")
        threading.Thread(target=worker).start()

    def add_selected(self, ids=None):
        indices = [self.tree.index(iid) for iid in ids] if ids else []
        if not indices:
            messagebox.showinfo("No Selection", "No rows selected.")
            return
        to_add = []
        skipped_covered = 0
        skipped_invalid = 0
        for idx in indices:
            row = self.scan_results[idx]
            if row["coverage_icon"] == "✅":
                if not messagebox.askyesno("Already Covered", f"Folder '{row['original_name']}' is already covered.\nAdd anyway?"):
                    skipped_covered += 1
                    continue
            if row["valid_icon"] == "❌":
                if not messagebox.askyesno("No Valid Files", f"Folder '{row['original_name']}' has no valid files (after exclusions).\nAdd anyway?"):
                    skipped_invalid += 1
                    continue
            name = row["selected_name"].strip()
            if not name:
                if not messagebox.askyesno("Missing Name", f"Folder '{row['original_name']}' has no name. Use folder name?"):
                    continue
                name = row["original_name"]
            to_add.append((name, row["normalized_path"]))
        if not to_add:
            msg = f"Skipped {skipped_covered} covered, {skipped_invalid} invalid."
            messagebox.showinfo("Nothing added", msg if (skipped_covered+skipped_invalid) else "No rows to add.")
            return
        success = 0
        for name, norm_path in to_add:
            if add_custom_game(self.config_path, name, norm_path):
                success += 1
        messagebox.showinfo("Done", f"Added {success} entries. Skipped: {skipped_covered} covered, {skipped_invalid} invalid.")
        self.update_status(f"Added {success} custom entries")
        self.load_custom_config()

    def copy_suggested_to_selected(self, ids=None):
        indices = [self.tree.index(iid) for iid in ids] if ids else []
        if not indices:
            messagebox.showinfo("No Selection", "No rows selected.")
            return
        for idx in indices:
            row = self.scan_results[idx]
            if row["suggested_name"]:
                row["selected_name"] = row["suggested_name"]
                self._update_tree_row(idx)
        log(f"Copied suggestions to {len(indices)} selected rows")
        self.update_status("Copied suggestions to selected rows")

    def _update_tree_row(self, idx):
        row = self.scan_results[idx]
        for child in self.tree.get_children():
            if self.tree.item(child, "tags")[0] == row["path"]:
                self.tree.set(child, "#2", row["coverage_icon"])
                self.tree.set(child, "#3", row["valid_icon"])
                self.tree.set(child, "#5", row["selected_name"])
                self.tree.set(child, "#6", row["suggested_name"])
                self.tree.set(child, "#7", row["files_preview"])
                break

    # ------------------------------------------------------------------
    # Settings persistence with defaults
    # ------------------------------------------------------------------
    def load_settings(self):
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.ludusavi_path = Path(data.get("ludusavi", DEFAULT_LUDUSAVI))
            self.config_path = Path(data.get("config", DEFAULT_CONFIG))
            self.manifest_path = Path(data.get("manifest", DEFAULT_MANIFEST))
            self.predefined_locations = data.get("predefined_locations", DEFAULT_PREDEFINED)
            self.exclude_patterns = data.get("exclude_patterns", DEFAULT_EXCLUDE)
            self.scan_depth = data.get("scan_depth", 1)
            self.deep_scan_enabled = data.get("deep_scan_enabled", True)
            self.game_folder_depth = data.get("game_folder_depth", 3)
            self.save_folder_depth = data.get("save_folder_depth", 5)
            self.skip_system_folders = data.get("skip_system_folders", True)
            self.stop_at_game_folder = data.get("stop_at_game_folder", True)
            log(f"Loaded settings from {SETTINGS_FILE}")
        else:
            self.ludusavi_path = DEFAULT_LUDUSAVI
            self.config_path = DEFAULT_CONFIG
            self.manifest_path = DEFAULT_MANIFEST
            self.predefined_locations = DEFAULT_PREDEFINED.copy()
            self.exclude_patterns = DEFAULT_EXCLUDE.copy()
            self.scan_depth = 1
            self.deep_scan_enabled = True
            self.game_folder_depth = 3
            self.save_folder_depth = 5
            self.skip_system_folders = True
            self.stop_at_game_folder = True
            log("No settings file, using defaults")
        if not self.ludusavi_path.exists():
            log(f"Warning: ludusavi.exe not found at {self.ludusavi_path}, using default")
            self.ludusavi_path = DEFAULT_LUDUSAVI
        if not self.config_path.exists():
            log(f"config.yaml not found at {self.config_path}, will create later")
        if not self.manifest_path.exists():
            log(f"manifest.yaml not found at {self.manifest_path}")
        else:
            log(f"manifest.yaml found at {self.manifest_path}")

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
if __name__ == "__main__":
    log("Starting Ludusavi Save Adder GUI")
    root = TkinterDnD.Tk()
    app = LudusaviGUI(root)
    root.mainloop()
    log("Application closed")