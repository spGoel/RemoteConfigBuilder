---
name: robot-config-builder
description: Windows GUI tool for creating Aristocrat Configurable Robot XML files — built 2026-08-05
metadata: 
  node_type: memory
  type: project
  originSessionId: 3b6dc701-318d-4d49-a6ee-95136003b9be
  modified: 2026-08-11T08:01:33.293Z
---

Built a Python + tkinter desktop tool at `C:\Users\SG108049\Downloads\claudeSessionData\robot_config_builder\`.

**Why:** Aristocrat's Configurable Robot requires XML files to script automated player simulation on gaming machines (slot machines). Hand-editing XML was slow, error-prone, and required memorising a 17-event-type schema plus platform-specific screen coordinates.

**How to apply:** When asked to extend or modify this tool, the 7-file layout is:
- `models.py` — `RobotNode` dataclass, all constants (22 meters, event types, tag colors, attr order)
- `xml_io.py` — XML parse / generate / validate, 4 template factories, `TEMPLATES` dict
- `tree_panel.py` — left pane `TreePanel`, treeview, context menu, add/remove/reorder
- `properties_panel.py` — center pane `PropertiesPanel`, 16 type-specific form renderers, scrollable canvas; touch event nodes have "Pick from Screen" button per point row
- `main.py` — `App(tk.Tk)`, 3-pane layout, menus, toolbar, persistent Game Machine bar, file ops, undo (20 levels), XML preview
- `snapshot_manager.py` — SSH/SCP logic (paramiko); `load_settings()`, `save_settings()`, `take_screenshot()`, `take_screenshot_async()`; machine settings persisted to `~/.robot_config_builder_machine.json`; screenshots cached per-IP in `%TEMP%\rcb_screenshots\`
- `coordinate_picker.py` — `ProgressDialog` (indeterminate modal) + `CoordinatePicker` Toplevel; scales 4K image with `tk.PhotoImage.subsample()` to fit screen; click → real pixel coordinates via scale factor; Esc to cancel
- `run.bat` — launcher that finds Python 3.14 even though default PATH has Python 2.7

**Key facts:**
- Source samples: `C:\Users\SG108049\Downloads\IDEA_ConfiguableRobot\` (18 XML files + PDF Confluence export)
- Python on this machine: `C:\Users\SG108049\AppData\Local\Programs\Python\Python314\python.exe` (Python 3.14.6). Default `python` on PATH = Python 2.7 from GDK tools — always use Python 3.14 path or `run.bat`
- All 18 sample files round-trip cleanly (parse → generate → valid XML, 0 warnings) — verified with test script
- 4 built-in templates: Helix Standard Play, Helix XT (4K Portrait), Game Switching with Gamble, Grandstar Auto Bank
- Launch: double-click `run.bat` from the folder
- XML preview uses dark theme with basic regex syntax highlighting
- Undo stack = 20 levels of `copy.deepcopy(root_node)` snapshots
- **Extra dependency:** `paramiko` — install with `C:\...\Python314\python.exe -m pip install paramiko`. Tool launches without it but "Pick from Screen" shows a helpful install-command error if missing.

**Visual Coordinate Picker (implemented 2026-08-11):**
- Game Machine bar in main toolbar: IP text field + Portrait/Landscape dropdown + Refresh Screenshot button + cache status — session-level, always visible
- Portrait orientation → `import -window root` (no crop, full display)
- Landscape orientation → `import -window root -crop '3840x2160+0+2160'` (2nd 4K monitor)
- Credentials always hardcoded as mk7/mk7 — never prompted
- "Pick from Screen" button on every touch point row: uses cached PNG if present, otherwise captures fresh
- CoordinatePicker scales image to fit screen (integer subsample factor), shows real coordinates on hover, click closes dialog and populates X/Y fields

**XML schema supported:**
- Root is always `<event type="Sequence">`
- 17 event types: Sequence, Random, Touch-Screen, Touch-Area, Button, Wait, Insert-Credit, Clear-Jackpot, Door, Switch, Random-Credit, Swipe-Screen, Condition, Scheduled, Simultaneous, meter-list, output
- 22 meter names for `<meter-list>`
- Platform coordinate systems: Helix (~1900×1100), Helix XT 4K (~3800×2200, doubled), Mars, Grandstar (~1400×800)

**Confluence page:** Created as TechnoAI-26 artifact — see [[project-confluence-page]]

**Why:** User at Aristocrat building internal test tools. Tool was also submitted as a TechnoAI-26 (Tech Fair 2026) entry.
