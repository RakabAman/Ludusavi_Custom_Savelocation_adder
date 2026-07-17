#!/usr/bin/env python3
"""
Ludusavi Custom Save Adder - GUI (Live Settings, Unified Rescan, Sample Count)
- All settings take effect immediately.
- Rescan Selected re-evaluates both name and path.
- Sample Files count adjustable.
- Toggle file exclusions on/off inside their respective frames.
- Match types (exact/parent/child) are configurable.
- Matched path is shown in a separate column.
- Custom config is included in name resolution and coverage.
- Horizontal scrollbar for the results tree.
- Startup warnings for missing ludusavi.exe / manifest.yaml.
- Popup autocomplete dialog for editing game names.
- Descriptive labels for match types.
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
    r"%USERPROFILE%\Saved Games",
    r"%PUBLIC%\Documents",
    r"%APPDATA%",
    r"%LOCALAPPDATA%",
    r"%LOCALAPPDATA%\Low",
    r"%USERPROFILE%\Documents",
]

DEFAULT_EXCLUDE = [
    "*.ini", "*.log", "*.cache", "*.dll", "*.exe", "*.txt",
    "*.jpg", "*.png", "*.bmp", "*.cfg", "*.json", "*.bak", "*.old"
]

DEFAULT_EXCLUDED_FOLDERS = [
    "steam", "uplay", "epic", "origin", "gog", "socialclub", "3dmgame",
    "reloaded", "skidrow", "codex", "rune", "empress", "cpy", "goldberg",
    "KoeiTecmo", "My Games", "FLT", "GSE Saves", "Guerrilla Games",
    "Insomniac Games", "IO Interactive", "player", "RenPy", "TangoGameworks",
    "Respawn", "MachineGames", "profiles", "id Software", "CD Projekt Red"
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
        (f"C:\\Users\\{username}", "<home>"),   # catches Saved Games, Desktop, etc.
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

def get_files_preview_and_valid(folder_path: Path, exclude_patterns, max_count=3, max_depth=2, exclude_enabled=True):
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
                    if exclude_enabled:
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
        self.root.geometry("1250x920")
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

        # Check essential files after UI is fully loaded
        self.root.after(500, self.check_essential_files)

        if len(sys.argv) > 1:
            folder = sys.argv[1]
            log(f"Command-line auto-scan: {folder}")
            self.root.after(1000, lambda: self.scan_custom_folder(folder))

    # ------------------------------------------------------------------
    # Essential files check
    # ------------------------------------------------------------------
    def check_essential_files(self):
        """Warn the user if ludusavi.exe or manifest.yaml are missing."""
        missing = []
        if not self.ludusavi_path.exists():
            missing.append("ludusavi.exe")
        if not self.manifest_path.exists():
            missing.append("manifest.yaml")
        if missing:
            msg = (f"The following essential file(s) were not found:\n\n"
                   f"{', '.join(missing)}\n\n"
                   f"Please set the correct paths in the Settings tab and click 'Reload Manifest'.\n"
                   f"The program will still work, but some features may be limited.")
            messagebox.showwarning("Missing Files", msg)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------
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
            # Normalize paths in custom config
            if "customGames" in self.custom_config:
                for entry in self.custom_config["customGames"]:
                    if "files" in entry:
                        entry["files"] = [normalize_path(Path(p)) for p in entry["files"]]
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

    def _find_best_fuzzy_match(self, candidate: str, keys: list, cutoff=0.65) -> str:
        if not candidate or not keys:
            return None
        normalized_candidate = self._normalize_for_fuzzy(candidate)
        if not normalized_candidate:
            return None
        best_match = None
        best_score = 0.0
        for key in keys:
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
    # Excluded folder names management
    # ------------------------------------------------------------------
    def add_excluded_folder(self):
        name = self.excl_folder_entry.get().strip()
        if name and name not in self.excluded_folder_names:
            self.excluded_folder_names.append(name)
            self.refresh_excl_folder_list()
            self.excl_folder_entry.delete(0, tk.END)
            log(f"Added excluded folder: {name}")

    def remove_excluded_folder(self):
        sel = self.excl_folder_listbox.curselection()
        if sel:
            removed = self.excluded_folder_names.pop(sel[0])
            self.refresh_excl_folder_list()
            log(f"Removed excluded folder: {removed}")

    def refresh_excl_folder_list(self):
        self.excl_folder_listbox.delete(0, tk.END)
        for name in self.excluded_folder_names:
            self.excl_folder_listbox.insert(tk.END, name)

    # ------------------------------------------------------------------
    # Predefined locations management
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
                    # Normalize paths in cached manifest
                    self._normalize_manifest_paths()
                    elapsed = (time.perf_counter() - start) * 1000
                    log(f"Loaded cached manifest: {len(self.manifest_data)} games in {elapsed:.0f} ms")
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
                # Normalize paths
                self._normalize_manifest_paths()
                with open(MANIFEST_CACHE, "wb") as f:
                    pickle.dump(self.manifest_data, f)
                elapsed = (time.perf_counter() - start) * 1000
                log(f"Parsed YAML: {len(self.manifest_data)} games in {elapsed:.0f} ms. Cached to {MANIFEST_CACHE}")
                self.update_status(f"Loaded manifest ({len(self.manifest_data)} games)")
            except Exception as e:
                log(f"YAML load error: {e}")
                self.update_status(f"Error loading manifest: {e}")
                self.manifest_data = {}
            self.manifest_loaded = True
            log("Manifest loading thread finished")

        self.manifest_loading_thread = threading.Thread(target=load_worker, daemon=True)
        self.manifest_loading_thread.start()

    def _normalize_manifest_paths(self):
        """Normalize all file paths in the manifest to use placeholders."""
        if not self.manifest_data:
            return
        for game_name, game_data in self.manifest_data.items():
            if "files" in game_data:
                # files is a dict: key = path, value = pattern
                new_files = {}
                for path, pattern in game_data["files"].items():
                    norm_path = normalize_path(Path(path))
                    new_files[norm_path] = pattern
                game_data["files"] = new_files

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
        """Resolve name from manifest first, then custom config."""
        if not self.manifest_loaded:
            self.ensure_manifest_ready()
        # 1. Check manifest
        if self.manifest_data:
            if candidate in self.manifest_data:
                log(f"  Exact match in manifest: {candidate}")
                return candidate
            lower_candidate = candidate.lower()
            for name in self.manifest_data:
                if name.lower() == lower_candidate:
                    log(f"  Case-insensitive match in manifest: {name}")
                    return name
            if candidate.isdigit():
                sid = int(candidate)
                for name, data in self.manifest_data.items():
                    if data.get("steam", {}).get("id") == sid:
                        log(f"  Steam ID match in manifest: {name}")
                        return name
        # 2. Check custom config
        if self.custom_config:
            for entry in self.custom_config.get("customGames", []):
                entry_name = entry.get("name")
                if not entry_name:
                    continue
                if entry_name == candidate:
                    log(f"  Exact match in custom config: {entry_name}")
                    return entry_name
                if entry_name.lower() == lower_candidate:
                    log(f"  Case-insensitive match in custom config: {entry_name}")
                    return entry_name
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

    # New helper to find all matches for a path against a list of known paths
    def _find_matches_for_path(self, normalized_path: str, known_paths: list):
        """Return list of (matched_path, match_type) for exact/parent/child matches."""
        if not known_paths:
            return []
        # Normalize separators and convert to lowercase for case‑insensitive matching
        norm_path = normalized_path.replace("\\", "/").rstrip("/") + "/"
        norm_path_lower = norm_path.lower()
        matches = []
        for known in known_paths:
            known_norm = known.replace("\\", "/").rstrip("/") + "/"
            known_norm_lower = known_norm.lower()
            if norm_path_lower == known_norm_lower:
                matches.append((known, "exact"))
            elif norm_path_lower.startswith(known_norm_lower):
                matches.append((known, "parent"))
            elif known_norm_lower.startswith(norm_path_lower):
                matches.append((known, "child"))
        return matches

    def is_path_covered_by_manifest(self, game_name, normalized_path):
        """Return list of (matched_path, match_type) from manifest."""
        if not self.manifest_loaded or not self.manifest_data:
            return []
        game_entry = self.manifest_data.get(game_name)
        if not game_entry:
            return []
        files_obj = game_entry.get("files", {})
        known_paths = list(files_obj.keys())
        return self._find_matches_for_path(normalized_path, known_paths)

    def is_path_covered_by_custom(self, normalized_path, game_name=None):
        """Return list of (matched_path, match_type) from custom config.
           If game_name is provided, only consider entries with that name.
        """
        if not self.custom_config:
            return []
        known_paths = []
        for entry in self.custom_config.get("customGames", []):
            if game_name and entry.get("name") != game_name:
                continue
            known_paths.extend(entry.get("files", []))
        return self._find_matches_for_path(normalized_path, known_paths)

    def get_coverage_status(self, game_name, normalized_path):
        """
        Combine matches from manifest and custom, filter by enabled types,
        pick the longest matched path, return (icon, status, matched_path).
        """
        all_matches = []
        # Get manifest matches if game_name provided and exists
        if game_name:
            manifest_matches = self.is_path_covered_by_manifest(game_name, normalized_path)
            all_matches.extend(manifest_matches)
        # Get custom matches (filtered by game_name)
        custom_matches = self.is_path_covered_by_custom(normalized_path, game_name)
        all_matches.extend(custom_matches)

        if not all_matches:
            return "❌", "New location", ""

        # Filter by enabled match types
        allowed_types = []
        if self.match_exact_enabled.get():
            allowed_types.append("exact")
        if self.match_parent_enabled.get():
            allowed_types.append("parent")
        if self.match_child_enabled.get():
            allowed_types.append("child")
        filtered = [(p, t) for p, t in all_matches if t in allowed_types]
        if not filtered:
            return "❌", "New location (types disabled)", ""

        # Priority: exact > parent > child. Within same type, pick longest path.
        def match_priority(match_tuple):
            path, mtype = match_tuple
            priority = {"exact": 0, "parent": 1, "child": 2}[mtype]
            return (priority, -len(path))  # longest path first within same priority

        best = min(filtered, key=match_priority)
        matched_path, match_type = best

        # Determine icon
        if match_type == "exact":
            icon = "✅"
            status = "Covered (exact match)"
        else:  # parent or child
            icon = "🟢"
            status = f"Covered ({match_type} match: {matched_path})"

        return icon, status, matched_path

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
    # Settings tab (live binding, checkboxes inside frames)
    # ------------------------------------------------------------------
    def build_settings_tab(self):
        main_frame = ttk.Frame(self.settings_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        paths_frame = ttk.LabelFrame(main_frame, text="Paths & Scan Settings")
        paths_frame.pack(fill=tk.X, pady=5)
        paths_frame.columnconfigure(1, weight=1)

        # Row 0: ludusavi.exe
        ttk.Label(paths_frame, text="ludusavi.exe:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.ludusavi_var = tk.StringVar(value=str(self.ludusavi_path))
        ttk.Entry(paths_frame, textvariable=self.ludusavi_var).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(paths_frame, text="Browse", command=self.browse_ludusavi).grid(row=0, column=2, padx=5)

        # Row 1: config.yaml
        ttk.Label(paths_frame, text="config.yaml:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.config_var = tk.StringVar(value=str(self.config_path))
        ttk.Entry(paths_frame, textvariable=self.config_var).grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(paths_frame, text="Browse", command=self.browse_config).grid(row=1, column=2, padx=5)

        # Row 2: manifest.yaml
        ttk.Label(paths_frame, text="manifest.yaml:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.manifest_var = tk.StringVar(value=str(self.manifest_path))
        ttk.Entry(paths_frame, textvariable=self.manifest_var).grid(row=2, column=1, sticky="ew", padx=5)
        ttk.Button(paths_frame, text="Browse", command=self.browse_manifest).grid(row=2, column=2, padx=5)

        # Row 3: Spinboxes (live)
        depth_frame = ttk.Frame(paths_frame)
        depth_frame.grid(row=3, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)

        ttk.Label(depth_frame, text="Standard Depth:").pack(side=tk.LEFT, padx=(0,5))
        ttk.Spinbox(depth_frame, from_=1, to=5, textvariable=self.scan_depth, width=4).pack(side=tk.LEFT, padx=(0,15))

        ttk.Label(depth_frame, text="Game Folder Depth:").pack(side=tk.LEFT, padx=(0,5))
        ttk.Spinbox(depth_frame, from_=1, to=5, textvariable=self.game_folder_depth, width=4).pack(side=tk.LEFT, padx=(0,15))

        ttk.Label(depth_frame, text="Save Folder Depth:").pack(side=tk.LEFT, padx=(0,5))
        ttk.Spinbox(depth_frame, from_=1, to=5, textvariable=self.save_folder_depth, width=4).pack(side=tk.LEFT)

        ttk.Label(depth_frame, text="Sample Files:").pack(side=tk.LEFT, padx=(10,5))
        ttk.Spinbox(depth_frame, from_=1, to=10, textvariable=self.sample_count, width=4).pack(side=tk.LEFT)

        # Row 4: Checkboxes (live)
        check_frame = ttk.Frame(paths_frame)
        check_frame.grid(row=4, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)

        ttk.Checkbutton(check_frame, text="Deep Scan (Recursively Through Folder)", variable=self.deep_scan_enabled).pack(side=tk.LEFT, padx=(0,10))

        ttk.Checkbutton(check_frame, text="Skip system folders (Windows, Program Files)", variable=self.skip_system_folders).pack(side=tk.LEFT, padx=(0,10))

        ttk.Checkbutton(check_frame, text="Stop Recursing at First Game Folder", variable=self.stop_at_game_folder).pack(side=tk.LEFT)

        # Row 5: Match type checkboxes (in one row)
        match_frame = ttk.Frame(paths_frame)
        match_frame.grid(row=5, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)

        ttk.Label(match_frame, text="Match types:").pack(side=tk.LEFT, padx=(0,5))
        ttk.Checkbutton(match_frame, text="Exact", variable=self.match_exact_enabled).pack(side=tk.LEFT, padx=(0,10))
        ttk.Checkbutton(match_frame, text="Parent", variable=self.match_parent_enabled).pack(side=tk.LEFT, padx=(0,10))
        ttk.Checkbutton(match_frame, text="Child", variable=self.match_child_enabled).pack(side=tk.LEFT)
        # NEW checkbox
        ttk.Checkbutton(check_frame, text="Resolve subfolder names in deep scan", variable=self.deep_subfolder_resolution_enabled).pack(side=tk.LEFT)

        # Row 6: Description label (wrapped)
        desc_frame = ttk.Frame(paths_frame)
        desc_frame.grid(row=6, column=0, columnspan=3, sticky=tk.W, padx=5, pady=2)
        desc_text = (
            "Exact: path matches known save root exactly.  "
            "Parent: scanned folder is inside a known save root.  "
            "Child: scanned folder contains a known save root (uncommon)."
        )
        ttk.Label(desc_frame, text=desc_text, wraplength=700, justify=tk.LEFT).pack(anchor=tk.W)

        # Row 7: Save & Reload buttons
        # Row 7: Save, Reload, Export, Import buttons
        btn_frame = ttk.Frame(paths_frame)
        btn_frame.grid(row=7, column=1, pady=5)
        ttk.Button(btn_frame, text="Save Settings", command=self.save_settings).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Reload Manifest", command=self.reload_manifest).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Export Custom Entries", command=self.export_custom_entries).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Import Custom Entries", command=self.import_custom_entries).pack(side=tk.LEFT, padx=2)


        # Rest of settings: Paned window with predefined locations, exclude patterns, excluded folders
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=5)

        # Left: Predefined Scan Locations
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

        # Middle: Exclude File Patterns (with enable checkbox inside frame)
        exclude_frame = ttk.LabelFrame(paned, text="Exclude Save File Patterns (wildcards)")
        paned.add(exclude_frame, weight=1)

        ttk.Checkbutton(exclude_frame, text="Enable file exclusions", variable=self.exclude_patterns_enabled).pack(anchor=tk.W, padx=5, pady=2)

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
        ttk.Button(pattern_frame, text="Add", command=self.add_exclude_pattern).pack(side=tk.LEFT, padx=2)
        ttk.Button(pattern_frame, text="Remove", command=self.remove_exclude_pattern).pack(side=tk.LEFT, padx=2)

        # Right: Excluded Folder Names (with enable checkbox inside frame)
        excl_folder_frame = ttk.LabelFrame(paned, text="Exclude Folder Names (Skip detection, scan recursively)")
        paned.add(excl_folder_frame, weight=1)

        ttk.Checkbutton(excl_folder_frame, text="Enable folder exclusions", variable=self.exclude_folders_enabled).pack(anchor=tk.W, padx=5, pady=2)

        excl_folder_list_frame = ttk.Frame(excl_folder_frame)
        excl_folder_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.excl_folder_listbox = tk.Listbox(excl_folder_list_frame, height=8)
        excl_folder_scroll = ttk.Scrollbar(excl_folder_list_frame, orient=tk.VERTICAL, command=self.excl_folder_listbox.yview)
        self.excl_folder_listbox.config(yscrollcommand=excl_folder_scroll.set)
        self.excl_folder_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        excl_folder_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        excl_btn_frame = ttk.Frame(excl_folder_frame)
        excl_btn_frame.pack(fill=tk.X, pady=5)
        self.excl_folder_entry = ttk.Entry(excl_btn_frame, width=30)
        self.excl_folder_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(excl_btn_frame, text="Add", command=self.add_excluded_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(excl_btn_frame, text="Remove", command=self.remove_excluded_folder).pack(side=tk.LEFT, padx=2)

        self.refresh_predef_listbox()
        self.refresh_exclude_list()
        self.refresh_excl_folder_list()

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
        settings = {
            "ludusavi": self.ludusavi_var.get(),
            "config": self.config_var.get(),
            "manifest": self.manifest_var.get(),
            "predefined_locations": self.predefined_locations,
            "exclude_patterns": self.exclude_patterns,
            "excluded_folder_names": self.excluded_folder_names,
            "exclude_folders_enabled": self.exclude_folders_enabled.get(),
            "exclude_patterns_enabled": self.exclude_patterns_enabled.get(),
            "scan_depth": self.scan_depth.get(),
            "deep_scan_enabled": self.deep_scan_enabled.get(),
            "game_folder_depth": self.game_folder_depth.get(),
            "save_folder_depth": self.save_folder_depth.get(),
            "sample_count": self.sample_count.get(),
            "skip_system_folders": self.skip_system_folders.get(),
            "stop_at_game_folder": self.stop_at_game_folder.get(),
            "deep_subfolder_resolution_enabled": self.deep_subfolder_resolution_enabled.get(),
            # New match type settings
            "match_exact_enabled": self.match_exact_enabled.get(),
            "match_parent_enabled": self.match_parent_enabled.get(),
            "match_child_enabled": self.match_child_enabled.get()
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        log(f"Settings saved to {SETTINGS_FILE}")
        self.ludusavi_path = Path(self.ludusavi_var.get())
        self.config_path = Path(self.config_var.get())
        self.manifest_path = Path(self.manifest_var.get())
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

    def export_custom_entries(self):
        """Export customGames entries to a portable YAML file."""
        if not self.custom_config or not self.custom_config.get("customGames"):
            messagebox.showinfo("No Entries", "There are no custom entries to export.")
            return

        file_path = filedialog.asksaveasfilename(
            title="Export Custom Entries",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")]
        )
        if not file_path:
            return

        export_data = {"customGames": self.custom_config["customGames"]}
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(export_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            messagebox.showinfo("Export Successful", f"Custom entries exported to:\n{file_path}")
            log(f"Exported {len(export_data['customGames'])} custom entries to {file_path}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export:\n{e}")
            log(f"Export error: {e}")

    def import_custom_entries(self):
        """Import custom entries from a YAML file and merge into current config."""
        file_path = filedialog.askopenfilename(
            title="Import Custom Entries",
            filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")]
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                import_data = yaml.safe_load(f) or {}
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to read file:\n{e}")
            log(f"Import read error: {e}")
            return

        imported_games = import_data.get("customGames")
        if not imported_games:
            messagebox.showinfo("No Entries", "The selected file contains no customGames entries.")
            return

        # Load current config
        if not self.config_path.exists():
            current_config = {"customGames": []}
        else:
            with open(self.config_path, "r", encoding="utf-8") as f:
                current_config = yaml.safe_load(f) or {}

        current_games = current_config.setdefault("customGames", [])

        def find_game(name):
            for entry in current_games:
                if entry.get("name") == name:
                    return entry
            return None

        added_count = 0
        merged_count = 0

        for imported_entry in imported_games:
            game_name = imported_entry.get("name")
            if not game_name:
                continue

            existing = find_game(game_name)
            if existing:
                existing_files = existing.setdefault("files", [])
                new_files = imported_entry.get("files", [])
                for path in new_files:
                    if path not in existing_files:
                        existing_files.append(path)
                merged_count += 1
            else:
                new_entry = {
                    "name": game_name,
                    "integration": imported_entry.get("integration", "override"),
                    "files": imported_entry.get("files", []),
                    "registry": imported_entry.get("registry", []),
                    "installDir": imported_entry.get("installDir", []),
                    "winePrefix": imported_entry.get("winePrefix", [])
                }
                current_games.append(new_entry)
                added_count += 1

        try:
            save_config(current_config, self.config_path)
            self.load_custom_config()
            messagebox.showinfo(
                "Import Successful",
                f"Imported {added_count} new game(s) and merged {merged_count} existing game(s)."
            )
            log(f"Imported {added_count} new, merged {merged_count} existing entries from {file_path}")
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to save config:\n{e}")
            log(f"Import save error: {e}") 

 # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
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
        # ----- Toolbar (packed at top) -----
        toolbar = ttk.Frame(self.scan_frame)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        ttk.Button(toolbar, text="Scan Predefined", command=self.scan_predefined).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Browse & Scan", command=self.scan_custom_folder_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Stop", command=self.stop_scanning).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Clear", command=self.clear_results).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Launch Ludusavi", command=self.launch_ludusavi).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=2)
        ttk.Button(toolbar, text="Rescan Selected", command=self.rescan_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Add Selected to Ludusavi", command=self.add_selected).pack(side=tk.LEFT, padx=2)

        # ----- Container for tree + scrollbars (packed below toolbar) -----
        tree_container = ttk.Frame(self.scan_frame)
        tree_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ----- Treeview (parent = tree_container) -----
        columns = ("check", "cov", "valid", "path", "name", "suggest", "files", "matched")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=12)
        self.tree.heading("check", text="✓")
        self.tree.heading("cov", text="Covered")
        self.tree.heading("valid", text="Valid")
        self.tree.heading("path", text="Folder Path")
        self.tree.heading("name", text="Game Name (editable)")
        self.tree.heading("suggest", text="Suggested")
        self.tree.heading("files", text="Sample Files")
        self.tree.heading("matched", text="Matched Path")

        # All columns stretch=False so resizing one doesn't squeeze others,
        # and horizontal scrollbar appears when total width exceeds visible area.
        self.tree.column("check", width=40, anchor=tk.CENTER, minwidth=30, stretch=False)
        self.tree.column("cov", width=60, anchor=tk.CENTER, minwidth=35, stretch=False)
        self.tree.column("valid", width=45, anchor=tk.CENTER, minwidth=35, stretch=False)
        self.tree.column("path", width=400, minwidth=200, stretch=False)
        self.tree.column("name", width=130, minwidth=120, stretch=False)
        self.tree.column("suggest", width=130, minwidth=120, stretch=False)
        self.tree.column("files", width=350, minwidth=150, stretch=False)
        self.tree.column("matched", width=250, minwidth=100, stretch=False)

        # ----- Scrollbars -----
        vsb = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.tree.xview)

        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Grid layout inside tree_container
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        # Drag‑and‑drop on scan_frame
        self.scan_frame.drop_target_register(DND_FILES)
        self.scan_frame.dnd_bind('<<Drop>>', self.on_drop)

        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Button-3>", self.show_context_menu)      
        
    
    # ------------------------------------------------------------------
    # Treeview interactions
    # ------------------------------------------------------------------
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
        if column == "#4":   # Path column – editable
            self.edit_path_cell(item, idx)
        elif column == "#6":   # Suggest column
            sugg = row.get("suggested_name", "")
            if sugg:
                self.tree.set(item, "#5", sugg)
                row["selected_name"] = sugg
                log(f"Copied suggestion '{sugg}' to Name")
                self.rescan_selected(ids=[item])   # <-- added
        elif column == "#5":   # Name column (editable)
            self.edit_name_with_popup(item, idx)

    def edit_path_cell(self, item, idx):
        col = "#4"
        x, y, width, height = self.tree.bbox(item, column=col)
        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        current = self.tree.set(item, col)
        entry.insert(0, current)
        entry.focus()
        def save_edit(event=None):
            new_val = entry.get().strip()
            if new_val:
                # Update treeview cell
                self.tree.set(item, col, new_val)
                # Update the row data
                self.scan_results[idx]["path"] = new_val
                # Update tag so _update_tree_row can find this row
                self.tree.item(item, tags=(new_val,))
                if os.path.exists(new_val):
                    self.scan_results[idx]["normalized_path"] = normalize_path(Path(new_val))
                log(f"Edited path to: {new_val}")
                # Auto‑rescan this row
                self.rescan_selected(ids=[item])
            entry.destroy()
        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)

    def edit_name_with_popup(self, item, idx):
        """Open a popup dialog with searchable list of known game names."""
        # Collect all known names from manifest and custom config
        known_names = set()
        if self.manifest_data:
            known_names.update(self.manifest_data.keys())
        if self.custom_config:
            for entry in self.custom_config.get("customGames", []):
                name = entry.get("name")
                if name:
                    known_names.add(name)
        known_names = sorted(known_names)  # sort alphabetically

        if not known_names:
            # Fallback to simple edit if no names available
            self.edit_name_cell(item, idx)
            return

        current_name = self.tree.set(item, "#5")
        row = self.scan_results[idx]

        # Create popup
        popup = tk.Toplevel(self.root)
        popup.title("Select or Enter Game Name")
        popup.geometry("400x350")
        popup.transient(self.root)
        popup.grab_set()

        # Search entry
        ttk.Label(popup, text="Type to filter:").pack(padx=5, pady=(5,0), anchor=tk.W)
        search_var = tk.StringVar()
        search_entry = ttk.Entry(popup, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=5, pady=5)
        search_entry.focus_set()

        # Listbox with scrollbar
        list_frame = ttk.Frame(popup)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        listbox = tk.Listbox(list_frame, selectmode=tk.SINGLE)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Populate initial list
        def update_list(filter_text=""):
            listbox.delete(0, tk.END)
            filter_lower = filter_text.lower()
            for name in known_names:
                if filter_text == "" or filter_lower in name.lower():
                    listbox.insert(tk.END, name)
            if listbox.size() > 0:
                listbox.selection_set(0)

        update_list()

        # Bind search
        def on_search_change(*args):
            update_list(search_var.get())
        search_var.trace_add('write', on_search_change)

        # Buttons
        btn_frame = ttk.Frame(popup)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        def on_select():
            selection = listbox.curselection()
            if selection:
                chosen = listbox.get(selection[0])
                self.tree.set(item, "#5", chosen)
                row["selected_name"] = chosen
                log(f"Selected name from popup: {chosen}")
                self.rescan_selected(ids=[item])   # <-- added
                popup.destroy()
            else:
                typed = search_var.get().strip()
                if typed:
                    self.tree.set(item, "#5", typed)
                    row["selected_name"] = typed
                    log(f"Entered custom name: {typed}")
                    self.rescan_selected(ids=[item])   # <-- added
                popup.destroy()

        def on_cancel():
            popup.destroy()

        ttk.Button(btn_frame, text="OK", command=on_select).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side=tk.LEFT, padx=2)
        # Double-click to select
        listbox.bind("<Double-1>", lambda e: on_select())
        # Enter key on search also selects
        search_entry.bind("<Return>", lambda e: on_select())

        # Pre‑populate search with current name (if any) to filter
        if current_name:
            search_var.set(current_name)
            # Also put it in the listbox selection if it matches exactly
            update_list(current_name)
            # Try to select the exact match
            for i, name in enumerate(known_names):
                if name == current_name:
                    listbox.selection_clear(0, tk.END)
                    listbox.selection_set(i)
                    listbox.see(i)
                    break

        # Wait for popup to close
        popup.wait_window()

    def edit_name_cell(self, item, idx):
        col = "#5"
        x, y, width, height = self.tree.bbox(item, column=col)
        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        current = self.tree.set(item, col)
        entry.insert(0, current)
        entry.focus()
        def save_edit(event=None):
            new_val = entry.get()
            self.tree.set(item, col, new_val)
            self.scan_results[idx]["selected_name"] = new_val
            log(f"Edited name to: {new_val}")
            self.rescan_selected(ids=[item])   # <-- added
            entry.destroy()
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
        menu.add_command(label="Open Folder", command=lambda: self.open_folder(selected_iids))
        menu.add_separator()
        menu.add_command(label="Rescan Selected", command=lambda: self.rescan_selected(ids=selected_iids))
        menu.add_command(label="Copy Suggestion to Name", command=lambda: self.copy_suggested_to_selected(ids=selected_iids))
        menu.add_command(label="Add Selected to Ludusavi", command=lambda: self.add_selected(ids=selected_iids))
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
        if self.stop_scan:
            return []
        results = []
        try:
            for item in root_path.iterdir():
                if self.stop_scan:
                    break
                if not item.is_dir():
                    continue
                if skip_system and self._is_system_folder(item.name):
                    continue

                if self.exclude_folders_enabled.get() and self.excluded_folder_names and item.name.lower() in [x.lower() for x in self.excluded_folder_names]:
                    log(f"  Excluded folder (skip game detection): {item.name}")
                    if current_depth < max_depth:
                        results.extend(self._collect_game_folders(item, max_depth, current_depth + 1, skip_system))
                    continue

                if current_depth >= 1:
                    resolved_name, match_type, _ = self.resolve_game_name_extended(item.name, item)
                else:
                    resolved_name = None
                    match_type = None
                if resolved_name and match_type in ("exact", "fuzzy", "path-ancestor"):
                    results.append((item, resolved_name, match_type))
                    if self.stop_at_game_folder.get():
                        continue
                if current_depth < max_depth:
                    results.extend(self._collect_game_folders(item, max_depth, current_depth + 1, skip_system))
        except PermissionError:
            pass
        return results

    def _collect_save_folders(self, game_folder: Path, max_depth: int, current_depth: int, exclude_patterns, exclude_enabled):
        if current_depth > max_depth:
            return []
        if self.stop_scan:
            return []
        results = []
        try:
            if self._has_valid_files_directly(game_folder, exclude_patterns, exclude_enabled):
                preview, valid_count = get_files_preview_and_valid(game_folder, exclude_patterns,
                                                                   max_count=self.sample_count.get(),
                                                                   max_depth=2,
                                                                   exclude_enabled=exclude_enabled)
                results.append((game_folder, valid_count, preview))
            for item in game_folder.iterdir():
                if self.stop_scan:
                    break
                if item.is_dir():
                    results.extend(self._collect_save_folders(item, max_depth, current_depth + 1, exclude_patterns, exclude_enabled))
        except PermissionError:
            pass
        return results


    def _has_valid_files_directly(self, folder_path: Path, exclude_patterns, exclude_enabled) -> bool:
        try:
            for item in folder_path.iterdir():
                if item.is_file():
                    if exclude_enabled:
                        excluded = False
                        for pattern in exclude_patterns:
                            if fnmatch.fnmatch(item.name, pattern):
                                excluded = True
                                break
                        if not excluded:
                            return True
                    else:
                        return True
        except PermissionError:
            pass
        return False

    # ------------------------------------------------------------------
    # Scanning
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
        if self.deep_scan_enabled.get():
            log(f"Starting deep scan worker for {len(root_folders)} root(s), game_depth={self.game_folder_depth.get()}, save_depth={self.save_folder_depth.get()}, stop_at_game={self.stop_at_game_folder.get()}")
        else:
            log(f"Starting standard scan worker for {len(root_folders)} root(s), depth={self.scan_depth.get()}")
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

            exclude_enabled = self.exclude_patterns_enabled.get()
           

            if self.deep_scan_enabled.get():
                # ---- DEEP SCAN ----
                log("  Using deep scan")
                game_folders = self._collect_game_folders(root_path, self.game_folder_depth.get(), 1, self.skip_system_folders.get())
                log(f"  Found {len(game_folders)} game folder candidates")
                for game_folder, game_name, match_type in game_folders:
                    if self.stop_scan:
                        log("Scan stopped by user")
                        break
                    log(f"    Scanning saves in: {game_folder} (game: {game_name})")

                    # --- Collect subfolders and resolve names (if option enabled) ---
                    subfolder_map = {}
                    if self.deep_subfolder_resolution_enabled.get():
                        try:
                            for sub in game_folder.iterdir():
                                if sub.is_dir():
                                    # Skip if the subfolder is in the exclusion list
                                    if self.exclude_folders_enabled.get() and sub.name.lower() in [x.lower() for x in self.excluded_folder_names]:
                                        continue
                                    sub_resolved, sub_match, _ = self.resolve_game_name_extended(sub.name, sub)
                                    if sub_resolved and sub_resolved != game_name:
                                        subfolder_map[sub] = sub_resolved
                                        log(f"      Subfolder '{sub.name}' resolved to '{sub_resolved}'")
                        except PermissionError:
                            pass

                    # Collect save folders under this game_folder
                    save_folders = self._collect_save_folders(game_folder, self.save_folder_depth.get(), 1, self.exclude_patterns, exclude_enabled)
                    if not save_folders:
                        preview, valid_count = get_files_preview_and_valid(game_folder, self.exclude_patterns,
                                                                           max_count=self.sample_count.get(),
                                                                           max_depth=2,
                                                                           exclude_enabled=exclude_enabled)
                        if valid_count > 0:
                            save_folders = [(game_folder, valid_count, preview)]
                        else:
                            log(f"    No save folders with valid files found in {game_folder}")
                            continue

                    for save_folder, valid_count, preview in save_folders:
                        if self.stop_scan:
                            log("Scan stopped by user")
                            break
                        norm_path = normalize_path(save_folder)

                        # Determine which subfolder (if any) this save_folder belongs to
                        parent = save_folder.parent
                        sub_resolved = subfolder_map.get(parent) if parent in subfolder_map else None

                        if sub_resolved:
                            # Check coverage under the subfolder's resolved name
                            cov_icon_sub, _, _ = self.get_coverage_status(sub_resolved, norm_path)
                            if cov_icon_sub == "✅" or cov_icon_sub == "🟢":
                                # Covered → use sub_resolved as selected, suggest original game_name
                                selected_name = sub_resolved
                                suggested_name = game_name
                            else:
                                # Not covered → keep game_name, suggest sub_resolved
                                selected_name = game_name
                                suggested_name = sub_resolved
                        else:
                            # No subfolder resolution: use the original game_name
                            selected_name = game_name
                            suggested_name = ""

                        # Now get coverage for the selected_name
                        cov_icon, cov_status, matched_path = self.get_coverage_status(selected_name, norm_path)
                        preview_str = ", ".join(preview) if preview else "(no files)"
                        valid_icon = "✔️" if valid_count > 0 else "❌"
                        self.scan_results.append({
                            "path": str(save_folder),
                            "normalized_path": norm_path,
                            "original_name": game_folder.name,
                            "selected_name": selected_name,
                            "suggested_name": suggested_name,
                            "files_preview": preview_str,
                            "checked": None,
                            "coverage_icon": cov_icon,
                            "valid_icon": valid_icon,
                            "valid_count": valid_count,
                            "game_name_for_coverage": selected_name,
                            "match_type": match_type,
                            "matched_path": matched_path
                        })
                        status_msg = f"  Found save: {save_folder} -> {selected_name} {cov_icon} {valid_icon}"
                        log(f"    {status_msg}")
                        self.scan_queue.put(("log", status_msg))
                progress = (r_idx + 1) / total_roots * 100
                self.scan_queue.put(("progress", progress))
            else:
                # ---- STANDARD SCAN ----
                candidates = self._collect_dirs_at_depth(root_path, self.scan_depth.get())
                log(f"  Found {len(candidates)} directories at depth {self.scan_depth.get()}")
                for sub in candidates:
                    if self.stop_scan:
                        log("Scan stopped by user")
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
                    # Get coverage using new method
                    cov_icon, cov_status, matched_path = self.get_coverage_status(game_for_coverage, norm_path)
                    preview, valid_count = get_files_preview_and_valid(sub, self.exclude_patterns,
                                                                       max_count=self.sample_count.get(),
                                                                       max_depth=2,
                                                                       exclude_enabled=exclude_enabled)
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
                        "match_type": match_type,
                        "matched_path": matched_path
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
                item["files_preview"],
                item.get("matched_path", "")
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
    # Unified Rescan Selected – re-evaluates both name and path
    # ------------------------------------------------------------------
    def rescan_selected(self, ids=None):
        if ids is None:
            ids = [child for child in self.tree.get_children() if self.tree.set(child, "check") == "☑"]
        else:
            ids = [iid for iid in ids]
        if not ids:
            messagebox.showinfo("No Selection", "No rows selected.")
            return
        log(f"Rescanning {len(ids)} selected rows")
        def worker():
            for iid in ids:
                if self.stop_scan:
                    break
                idx = self.tree.index(iid)
                row = self.scan_results[idx]
                log(f"  Rescanning {row['original_name']} (path: {row['path']})")

                # 1. Current live values
                current_name = row["selected_name"].strip()
                current_path = Path(row["path"])
                folder_name = row["original_name"]

                # 2. Normalise path (in case user edited it)
                row["normalized_path"] = normalize_path(current_path)

                # 3. Determine if the folder name is excluded
                excluded = (self.exclude_folders_enabled.get() and
                            any(folder_name.lower() == x.lower() for x in self.excluded_folder_names))

                # 4. Find a suggested name – never modifies selected_name
                suggested = None

                if excluded:
                    log(f"    Folder '{folder_name}' is excluded – trying parent folders for suggestion")
                    parent = current_path.parent
                    levels_up = 0
                    while parent != parent.parent and levels_up < 5:
                        parent_name = parent.name
                        if not parent_name:
                            break
                        parent_excluded = (self.exclude_folders_enabled.get() and
                                           any(parent_name.lower() == x.lower() for x in self.excluded_folder_names))
                        if not parent_excluded:
                            resolved = self.resolve_game_name(parent_name)
                            if resolved:
                                suggested = resolved
                                log(f"    Resolved parent '{parent_name}' to '{resolved}'")
                                break
                            else:
                                fuzzy = self.suggest_similar_name(parent_name)
                                if fuzzy:
                                    suggested = fuzzy
                                    log(f"    Fuzzy matched parent '{parent_name}' to '{fuzzy}'")
                                    break
                        parent = parent.parent
                        levels_up += 1

                    if not suggested:
                        # Fallback to path‑ancestor
                        game, matched = self.find_game_by_path_ancestor(row["normalized_path"])
                        if game:
                            suggested = game
                            log(f"    Path‑ancestor suggested: {game}")

                else:
                    # Not excluded – normal resolution
                    # 1) exact/case‑insens/Steam ID on folder_name
                    resolved = self.resolve_game_name(folder_name)
                    if resolved:
                        suggested = resolved
                        log(f"    Exact/case‑insens match: {resolved}")
                    else:
                        # 2) fuzzy
                        fuzzy = self.suggest_similar_name(folder_name)
                        if fuzzy:
                            suggested = fuzzy
                            log(f"    Fuzzy match: {fuzzy}")
                        else:
                            # 3) path‑ancestor
                            game, matched = self.find_game_by_path_ancestor(row["normalized_path"])
                            if game:
                                suggested = game
                                log(f"    Path‑ancestor match: {game}")

                # Only set suggested if it's different from current_name
                if suggested and suggested != current_name:
                    row["suggested_name"] = suggested
                else:
                    row["suggested_name"] = ""

                # 5. Update coverage using current selected_name and normalised path
                game_for_coverage = current_name if current_name else (suggested if suggested else folder_name)
                cov_icon, cov_status, matched_path = self.get_coverage_status(game_for_coverage, row["normalized_path"])
                row["coverage_icon"] = cov_icon
                row["matched_path"] = matched_path
                row["game_name_for_coverage"] = game_for_coverage

                # 6. Refresh file preview with current exclusion settings
                preview, valid_count = get_files_preview_and_valid(current_path, self.exclude_patterns,
                                                                   max_count=self.sample_count.get(),
                                                                   max_depth=2,
                                                                   exclude_enabled=self.exclude_patterns_enabled.get())
                row["valid_icon"] = "✔️" if valid_count > 0 else "❌"
                row["valid_count"] = valid_count
                row["files_preview"] = ", ".join(preview) if preview else "(no files)"

                # 7. Update the UI row
                self.root.after(0, lambda i=idx: self._update_tree_row(i))
                self.update_status(f"Resolved: {row['original_name']} -> {current_name or suggested or '?'} {cov_icon} {row['valid_icon']}")
        self.update_status("Rescanning selected rows...")
        threading.Thread(target=worker).start()
    
    # ------------------------------------------------------------------
    # Add selected rows to Ludusavi
    # ------------------------------------------------------------------
    def add_selected(self, ids=None):
        if ids is None:
            ids = [child for child in self.tree.get_children() if self.tree.set(child, "check") == "☑"]
        else:
            ids = [iid for iid in ids]
        indices = [self.tree.index(iid) for iid in ids]
        if not indices:
            messagebox.showinfo("No Selection", "No rows selected.")
            return
        to_add = []
        skipped_covered = 0
        skipped_invalid = 0
        for idx in indices:
            row = self.scan_results[idx]
            if row["coverage_icon"] == "✅" or row["coverage_icon"] == "🟢":
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

    # ------------------------------------------------------------------
    # Copy suggested name to selected name
    # ------------------------------------------------------------------
    def copy_suggested_to_selected(self, ids=None):
        if ids is None:
            ids = [child for child in self.tree.get_children() if self.tree.set(child, "check") == "☑"]
        else:
            ids = [iid for iid in ids]
        indices = [self.tree.index(iid) for iid in ids]
        if not indices:
            messagebox.showinfo("No Selection", "No rows selected.")
            return
        for idx in indices:
            row = self.scan_results[idx]
            if row["suggested_name"]:
                row["selected_name"] = row["suggested_name"]
                self._update_tree_row(idx)
        log(f"Copied suggestions to {len(indices)} rows")
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
                self.tree.set(child, "#8", row.get("matched_path", ""))
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
            self.excluded_folder_names = data.get("excluded_folder_names", DEFAULT_EXCLUDED_FOLDERS)
            # Tkinter variables
            self.exclude_folders_enabled = tk.BooleanVar(value=data.get("exclude_folders_enabled", True))
            self.exclude_patterns_enabled = tk.BooleanVar(value=data.get("exclude_patterns_enabled", True))
            self.scan_depth = tk.IntVar(value=data.get("scan_depth", 1))
            self.deep_scan_enabled = tk.BooleanVar(value=data.get("deep_scan_enabled", True))
            self.game_folder_depth = tk.IntVar(value=data.get("game_folder_depth", 3))
            self.save_folder_depth = tk.IntVar(value=data.get("save_folder_depth", 5))
            self.sample_count = tk.IntVar(value=data.get("sample_count", 3))
            self.skip_system_folders = tk.BooleanVar(value=data.get("skip_system_folders", True))
            self.stop_at_game_folder = tk.BooleanVar(value=data.get("stop_at_game_folder", True))
            # ... existing variables ...
            self.deep_subfolder_resolution_enabled = tk.BooleanVar(value=data.get("deep_subfolder_resolution_enabled", True))
            # Match type checkboxes (default: exact ON, parent ON, child OFF)
            self.match_exact_enabled = tk.BooleanVar(value=data.get("match_exact_enabled", True))
            self.match_parent_enabled = tk.BooleanVar(value=data.get("match_parent_enabled", True))
            self.match_child_enabled = tk.BooleanVar(value=data.get("match_child_enabled", False))
            log(f"Loaded settings from {SETTINGS_FILE}")
        else:
            self.ludusavi_path = DEFAULT_LUDUSAVI
            self.config_path = DEFAULT_CONFIG
            self.manifest_path = DEFAULT_MANIFEST
            self.predefined_locations = DEFAULT_PREDEFINED.copy()
            self.exclude_patterns = DEFAULT_EXCLUDE.copy()
            self.excluded_folder_names = DEFAULT_EXCLUDED_FOLDERS.copy()
            self.exclude_folders_enabled = tk.BooleanVar(value=True)
            self.exclude_patterns_enabled = tk.BooleanVar(value=True)
            self.scan_depth = tk.IntVar(value=1)
            self.deep_scan_enabled = tk.BooleanVar(value=True)
            self.game_folder_depth = tk.IntVar(value=3)
            self.save_folder_depth = tk.IntVar(value=5)
            self.sample_count = tk.IntVar(value=3)
            self.skip_system_folders = tk.BooleanVar(value=True)
            self.stop_at_game_folder = tk.BooleanVar(value=True)
            self.match_exact_enabled = tk.BooleanVar(value=True)
            self.match_parent_enabled = tk.BooleanVar(value=True)
            self.match_child_enabled = tk.BooleanVar(value=False)
            # ... existing variables ...
            self.deep_subfolder_resolution_enabled = tk.BooleanVar(value=True)
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