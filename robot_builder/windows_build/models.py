"""Settings, choice tables, and folder derivation for the GDK5L Build Tool.

Everything the window edits lives in a `Settings` instance; everything the
pipeline needs is a derived property on it, so the folder layout is decided
in exactly one place:

    <workspace>\\GDK5L\\Runtime                     SVN checkout of the GDK runtime
    <workspace>\\GDK5L\\Build_<vs>[_modular]        CMake build tree (the .sln lives here)
    <workspace>\\GDK5L\\Binaries_<vs>[_modular]     install prefix
    <workspace>\\SampleGames\\<name>                one folder per game URL

Runtime, the build tree and the install prefix all sit inside a `GDK5L`
folder, and games sit in a sibling `SampleGames` folder, matching the layout
of the official 5L Windows build process (which the user provided; see
Settings.gdk5l_dir).
"""

import dataclasses
import json
import os
import re
from dataclasses import dataclass, field
from typing import List

DEFAULT_RUNTIME_URL = "https://svn.ali.global/nAble/Development/GDK5L/Runtime"
SAMPLE_GAMES_BASE = "https://svn.ali.global/nAble/GDK_Sample_Games/GDK5L/Trunk"
DEFAULT_GAME_URLS = [
    SAMPLE_GAMES_BASE + "/BuffaloStrike",
    SAMPLE_GAMES_BASE + "/FrankensteinGame",
    SAMPLE_GAMES_BASE + "/GrandStar_Flexiplay_NSW_QLD",
    SAMPLE_GAMES_BASE + "/JokerPoker",
]

CONFIGS = ["Debug", "Release", "Retail"]
VS_CHOICES = {
    "2019": "Visual Studio 16 2019",
    "2022": "Visual Studio 17 2022",
}
LAYOUTS = ["Aggregate", "Modular"]
ACTIONS = ["Configure only", "Build", "Build + Install", "Install only"]

SCRIPT_RELATIVE = os.path.join("etc", "scripts", "build", "create_workspace_gdk5L.ps1")

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".gdk5l_build_tool.json")

# Repository path segments that say nothing about which game this is, so the
# derived folder name should come from the segment before them instead.
GENERIC_SEGMENTS = {
    "trunk", "sandbox", "source", "src", "branches", "tags", "dev",
    "devlines", "main", "master", "head", "release", "game",
}

FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# Short help for every field, shown by the (i) markers in the UI.
FIELD_HELP = {
    "workspace": (
        "Root folder for everything this tool creates or updates: a GDK5L folder "
        "(Runtime checkout plus the Build_/Binaries_ output folders) and a "
        "sibling SampleGames folder. Pick an empty folder for a fresh setup, or "
        "an existing one to reuse it."
    ),
    "runtime_url": (
        "SVN URL of the GDK5L Runtime. It is checked out into <workspace>\\GDK5L\\Runtime, "
        "or updated if that folder is already a working copy of this URL. The "
        "workspace-creation script is taken from inside this checkout."
    ),
    "games": (
        "One SVN URL per game to include in the solution. Each is checked out into "
        "<workspace>\\SampleGames\\<folder>; the folder name is derived from the URL "
        "but can be edited, which matters for repositories that end in 'sandbox' or "
        "'source'. Every folder is passed to the script via -GameDirectories."
    ),
    "skip_svn": (
        "Do not run svn at all. Use this when you are offline or already have the "
        "sources exactly as you want them."
    ),
    "config": "Build configuration. The generated solution offers Debug, Release and Retail.",
    "vs": (
        "Which Visual Studio generator CMake uses. This decides the toolset (v142 or "
        "v143) and the output folder names, so 2019 and 2022 builds never share a "
        "build tree."
    ),
    "layout": (
        "Aggregate is the default single-solution layout. Modular splits the "
        "components into separate build steps and uses its own output folders."
    ),
    "action": (
        "Configure only stops after generating the solution. Build compiles the "
        "chosen configuration. Build + Install also copies the results into the "
        "Binaries folder. Install only skips compiling and reuses the last build."
    ),
    "force_regenerate": (
        "Run the workspace script even when a CMakeCache.txt already exists, and let "
        "it delete and recreate the Build and Binaries folders first. Slow, but it "
        "gives a clean tree."
    ),
    "extra_script_args": (
        "Anything typed here is appended verbatim to the create_workspace_gdk5L.ps1 "
        "call, for options the tool has no control for, e.g. -Market aus or "
        "-EnableTracyProfiler."
    ),
}


def folder_name_for_url(url: str) -> str:
    """Suggest a local folder name for a repository URL.

    Uses the last path segment, skipping generic tails such as `trunk`,
    `sandbox` or `source` so that e.g. `.../Game17_Family/sandbox` becomes
    `Game17_Family`.
    """
    segments = [s for s in url.strip().replace("\\", "/").split("/") if s]
    # Drop the scheme and host.
    if segments and segments[0].endswith(":"):
        segments = segments[2:] if len(segments) > 1 else []
    while segments and segments[-1].lower() in GENERIC_SEGMENTS:
        segments.pop()
    if not segments:
        return ""
    name = segments[-1]
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", name)


@dataclass(eq=True)
class GameSource:
    url: str = ""
    folder: str = ""

    @classmethod
    def from_url(cls, url: str) -> "GameSource":
        return cls(url=url.strip(), folder=folder_name_for_url(url))


@dataclass(eq=True)
class Settings:
    workspace: str = ""
    runtime_url: str = DEFAULT_RUNTIME_URL
    games: List[GameSource] = field(default_factory=list)
    config: str = "Debug"
    vs: str = "2022"
    layout: str = "Aggregate"
    action: str = "Build + Install"
    skip_svn: bool = False
    force_regenerate: bool = False
    extra_script_args: str = ""

    # ------------------------------------------------------------------
    # Derived paths
    # ------------------------------------------------------------------

    @property
    def gdk5l_dir(self) -> str:
        """Root of the official build layout's GDK5L folder: Runtime and the
        Build_/Binaries_ output folders all live inside it."""
        return os.path.join(self.workspace, "GDK5L")

    @property
    def runtime_dir(self) -> str:
        return os.path.join(self.gdk5l_dir, "Runtime")

    @property
    def games_dir(self) -> str:
        # A sibling of GDK5L, matching the official layout, and matching the
        # vendor script's own default (GamesRootDirectory = WorkspaceBase\..\SampleGames,
        # where WorkspaceBase is the GDK5L folder).
        return os.path.join(self.workspace, "SampleGames")

    def game_dir(self, game: GameSource) -> str:
        return os.path.join(self.games_dir, game.folder)

    @property
    def _suffix(self) -> str:
        return self.vs + ("_modular" if self.layout == "Modular" else "")

    @property
    def build_dir(self) -> str:
        return os.path.join(self.gdk5l_dir, "Build_" + self._suffix)

    @property
    def binaries_dir(self) -> str:
        return os.path.join(self.gdk5l_dir, "Binaries_" + self._suffix)

    @property
    def generator(self) -> str:
        return VS_CHOICES[self.vs]

    @property
    def script_path(self) -> str:
        return os.path.join(self.runtime_dir, SCRIPT_RELATIVE)

    @property
    def script_dir(self) -> str:
        return os.path.dirname(self.script_path)

    @property
    def solution_path(self) -> str:
        name = "GDK5L-modular-build.sln" if self.layout == "Modular" else "GDK5L-aggregate-build.sln"
        return os.path.join(self.build_dir, name)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        defaults = cls()
        kwargs = {}
        for f in dataclasses.fields(cls):
            if f.name in data:
                kwargs[f.name] = data[f.name]
        games = []
        for entry in kwargs.get("games") or []:
            if isinstance(entry, dict):
                games.append(GameSource(str(entry.get("url", "")), str(entry.get("folder", ""))))
            elif isinstance(entry, str):
                games.append(GameSource.from_url(entry))
        kwargs["games"] = games
        # Choice fields fall back to the default when the stored value is unknown.
        if kwargs.get("config") not in CONFIGS:
            kwargs["config"] = defaults.config
        if kwargs.get("vs") not in VS_CHOICES:
            kwargs["vs"] = defaults.vs
        if kwargs.get("layout") not in LAYOUTS:
            kwargs["layout"] = defaults.layout
        if kwargs.get("action") not in ACTIONS:
            kwargs["action"] = defaults.action
        for name in ("workspace", "runtime_url", "extra_script_args"):
            if name in kwargs:
                kwargs[name] = str(kwargs[name] or "")
        for name in ("skip_svn", "force_regenerate"):
            if name in kwargs:
                kwargs[name] = bool(kwargs[name])
        return cls(**kwargs)


def default_settings() -> Settings:
    """Settings for a first run: sample games prefilled, no workspace yet."""
    return Settings(games=[GameSource.from_url(u) for u in DEFAULT_GAME_URLS])


def save_settings(settings: Settings, path: str = None) -> None:
    # Resolve the module-level path at call time so tests can redirect it.
    with open(path or SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(settings.to_dict(), handle, indent=2)


def load_settings(path: str = None) -> Settings:
    """Load saved settings; a missing or unreadable file gives defaults."""
    try:
        with open(path or SETTINGS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return Settings()
        return Settings.from_dict(data)
    except (OSError, ValueError):
        return Settings()


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def validate(settings: Settings) -> List[str]:
    """Return human-readable problems that must be fixed before running."""
    problems = []
    if not settings.workspace.strip():
        problems.append("Choose a workspace folder.")
    elif not os.path.isabs(settings.workspace):
        problems.append("The workspace folder must be an absolute path.")

    if not settings.skip_svn and not settings.runtime_url.strip():
        problems.append("Enter the Runtime SVN URL, or tick Skip SVN.")

    seen = {}
    for game in settings.games:
        if not game.url.strip() and not game.folder.strip():
            continue
        if not game.folder.strip():
            problems.append("Game '{}' has no folder name.".format(game.url))
            continue
        if not FOLDER_NAME_RE.match(game.folder):
            problems.append(
                "Folder name '{}' may only contain letters, digits, '_', '-' and '.'.".format(game.folder)
            )
        key = game.folder.lower()
        if key in seen:
            problems.append("Two games use the folder name '{}'.".format(game.folder))
        seen[key] = game
        if not settings.skip_svn and not game.url.strip():
            problems.append("Game folder '{}' has no SVN URL.".format(game.folder))

    if settings.config not in CONFIGS:
        problems.append("Unknown configuration '{}'.".format(settings.config))
    if settings.vs not in VS_CHOICES:
        problems.append("Unknown Visual Studio version '{}'.".format(settings.vs))
    if settings.action not in ACTIONS:
        problems.append("Unknown action '{}'.".format(settings.action))
    return problems


# ----------------------------------------------------------------------
# Tool discovery
# ----------------------------------------------------------------------


def find_powershell() -> str:
    """Prefer PowerShell 7 (pwsh); fall back to Windows PowerShell."""
    import shutil

    for name in ("pwsh", "powershell"):
        if shutil.which(name):
            return name
    return "pwsh"


def find_tool(name: str) -> str:
    import shutil

    return shutil.which(name) or ""
