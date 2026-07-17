# Ludusavi Save Adder
A graphical tool to help you quickly add custom save game locations to Ludusavi, especially for cracked/pirated games that use non‑standard save paths.

## 🎯 What it does
Scans folders (predefined or custom) for save game directories.

Automatically resolves game names from folder names using Ludusavi’s manifest (exact, fuzzy, Steam ID, path‑matching).

Detects if a save path is already covered by the official manifest or your custom entries.

Adds selected folders as custom entries to Ludusavi’s config.yaml with proper path normalisation (e.g., <winDocuments>).

Saves you from manually editing YAML files.

Why this is useful – many cracked/pirated games save to non‑standard locations (e.g., under %APPDATA%, %LOCALAPPDATA%, or inside the game folder). This tool finds those hidden save folders and registers them with Ludusavi in one click.

## ✨ Features
🔍 Deep Scan – finds game folders even when nested several levels deep.

🧠 Intelligent matching – exact, fuzzy, Steam ID, and path‑ancestor matching.

🛑 Stop‑at‑Game – optionally stop recursing once a game folder is found (avoids false positives).

🖱️ Drag‑and‑drop – drop a folder onto the app to scan it instantly.

✏️ Editable game names – double‑click any name to correct it manually.

🔄 Rescan selected rows – re‑evaluate after editing.

✅ Coverage & validity indicators – see at a glance which paths are already covered and which have actual save files.

🚫 Exclude patterns – ignore common non‑save files (.ini, .log, .cache, etc.).

💾 Portable – all settings are stored alongside the executable.

## 📦 Requirements
Python 3.7+ (or use the pre‑built EXE)

Ludusavi (portable mode)

manifest.yaml (downloaded by Ludusavi)

Python dependencies (if running from source)
bash
pip install pyyaml tkinterdnd2
## 🚀 First‑time Setup (Summary)
Download Ludusavi (portable version) and extract to a folder.

Enable portable mode – create an empty file named ludusavi.portable in that folder.

Run Ludusavi once – it will create config.yaml and download manifest.yaml. Close it after download finishes.

Place the GUI (ludusavi_gui.py or LudusaviSaveAdder.exe) in the same folder.

Run the GUI – it will automatically detect the files.

## 🖥️ How to use the app
### 1. Configure settings (optional)
Predefined Scan Locations – add folders you often scan (e.g., C:\Users\Public\Documents\Steam\CODEX).

Exclude File Patterns – add wildcards to filter out non‑save files (defaults are provided).

Deep Scan options – enable for deeper folder structures.

Click Save Settings to store your preferences.

### 2. Scan
Click Scan Predefined to scan all your predefined locations.

Click Browse & Scan to pick a single folder.

Or drag and drop a folder onto the app window.

### 3. Review results
The table shows:

✓ – tick to select a row for adding.

Cov – ✅ if the path is already covered by manifest or exists in config as custom entry, ❌ if new.

Val – ✔️ if the folder contains valid save files (after exclusions), ❌ if not.

Folder Path – the full path.

Game Name (editable) – the resolved game name (double‑click to edit).

Suggested – a close match if the folder name didn't resolve exactly. 

Sample Files – a preview of files inside the folder.

### 4. Resolve names
Double‑click the Suggested column to copy it to the editable name.

Or manually type the correct game name.

Select rows and click Rescan Checked to re‑resolve names (useful after manual edits).

### 5. Add to Ludusavi
Tick the folders you want to add.

Click Add Checked.

The app will write the custom entries to config.yaml using portable placeholders (e.g., <winDocuments>).

## ⚙️ Settings explained
Setting	Description
ludusavi.exe	Path to the Ludusavi executable.
config.yaml	Path to Ludusavi’s config file (where custom entries are stored).
manifest.yaml	Path to the Ludusavi manifest (game database).
Standard Scan Depth	How many subfolder levels to scan when Deep Scan is off.
Enable Deep Scan	Turns on two‑stage scanning: find game folders, then save folders inside them.
Game Folder Depth	How deep to search for game folders (when Deep Scan is on).
Save Folder Depth	How deep to search for save folders inside each game folder.
Skip System Folders	Ignore Windows, Program Files, etc.
Stop at first game folder	Stop recursing into a folder once it matches a game name.
Predefined Scan Locations	Folders scanned when clicking “Scan Predefined”.
Exclude File Patterns	Wildcard patterns to ignore non‑save files (e.g., *.ini, *.log).
## 🛠️ Building a standalone EXE
Create a build_exe.bat:

batch
@echo off
pip install pyinstaller --quiet
pyinstaller --noconsole --onefile --name "LudusaviSaveAdder" --hidden-import tkinterdnd2 ludusavi_gui.py
The EXE will be in the dist folder. Copy it to the same folder as ludusavi.exe and run.

## 📝 License
MIT License – see LICENSE file.

## 🙏 Credits
Ludusavi by mtkennerly

PyYAML

tkinterdnd2

