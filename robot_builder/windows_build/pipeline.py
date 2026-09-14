"""Turn a `Settings` into the ordered list of commands to run.

Pure planning: nothing here starts a process. Filesystem questions go
through an injected probe object with an `exists(path)` method so the plan
can be tested against an imaginary workspace.
"""

import os
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from .models import Settings


class PlanError(Exception):
    """The requested action cannot be planned with the current workspace."""


@dataclass
class Stage:
    kind: str                 # "svn" | "configure" | "build" | "install"
    title: str                # one-line heading for the log
    argv: List[str]
    cwd: Optional[str] = None
    # SVN update pre-flight: the working copy at check_dir must report
    # expected_url, otherwise the run stops before touching it.
    check_dir: Optional[str] = None
    expected_url: Optional[str] = None
    notes: List[str] = field(default_factory=list)


class RealFS:
    @staticmethod
    def exists(path: str) -> bool:
        return os.path.exists(path)


# ----------------------------------------------------------------------
# Quoting helpers
# ----------------------------------------------------------------------


def ps_quote(text: str) -> str:
    """Single-quote a value for PowerShell (only ' needs escaping, by doubling)."""
    return "'" + text.replace("'", "''") + "'"


def shell_quote(arg: str) -> str:
    """Quote for display in the command preview (cmd-style double quotes)."""
    if not arg:
        return '""'
    if any(ch in arg for ch in " \t&|<>^()\"'"):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


# ----------------------------------------------------------------------
# Stage builders
# ----------------------------------------------------------------------


def _svn_stage(url: str, directory: str, label: str, fs) -> Stage:
    if fs.exists(os.path.join(directory, ".svn")):
        return Stage(
            kind="svn",
            title="SVN update {}".format(label),
            argv=["svn", "update", directory],
            check_dir=directory,
            expected_url=url,
        )
    return Stage(
        kind="svn",
        title="SVN checkout {}".format(label),
        argv=["svn", "checkout", url, directory],
    )


def svn_stages(settings: Settings, fs=RealFS) -> List[Stage]:
    if settings.skip_svn:
        return []
    stages = []
    if settings.runtime_url.strip():
        stages.append(_svn_stage(settings.runtime_url.strip(), settings.runtime_dir, "Runtime", fs))
    for game in settings.games:
        if not game.url.strip() or not game.folder.strip():
            continue
        stages.append(_svn_stage(game.url.strip(), settings.game_dir(game), game.folder, fs))
    return stages


def configure_command(settings: Settings) -> str:
    """The PowerShell one-liner that invokes create_workspace_gdk5L.ps1."""
    parts = [
        "& " + ps_quote(settings.script_path),
        "-Generator " + ps_quote(settings.generator),
        "-BuildOutputPath " + ps_quote(settings.build_dir),
        "-BinariesOutputPath " + ps_quote(settings.binaries_dir),
        "-NoDefaultGamesRootDirectory",
    ]
    game_dirs = [settings.game_dir(g) for g in settings.games if g.folder.strip()]
    if game_dirs:
        parts.append("-GameDirectories @(" + ",".join(ps_quote(d) for d in game_dirs) + ")")
    if settings.layout == "Modular":
        parts.append("-UseModularBuild")
    if not settings.force_regenerate:
        # Otherwise the script wipes both output folders before configuring.
        parts.append("-SuppressDeletionOfBuildOutputPath")
        parts.append("-SuppressDeletionOfBinariesOutputPath")
    extra = settings.extra_script_args.strip()
    if extra:
        parts.append(extra)
    # The script's own catch{} swallows errors into warnings, so at least make
    # sure a non-zero exit from the last cmake call reaches us.
    return " ".join(parts) + "; exit $LASTEXITCODE"


def configure_stage(settings: Settings, powershell: str = "pwsh") -> Stage:
    return Stage(
        kind="configure",
        title="Configure workspace ({}, {})".format(settings.generator, settings.layout),
        argv=[powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", configure_command(settings)],
        cwd=settings.script_dir,
    )


def build_stage(settings: Settings, cmake: str = "cmake") -> Stage:
    # Mirrors BuildWorkspace() in the script (`-- -r` restores NuGet packages
    # for the C# ConfigTool) and adds -m so MSBuild builds projects in parallel.
    return Stage(
        kind="build",
        title="Build {} ({})".format(settings.config, os.path.basename(settings.build_dir)),
        argv=[cmake, "--build", settings.build_dir, "--config", settings.config, "--", "-r", "-m"],
    )


def install_stage(settings: Settings, cmake: str = "cmake") -> Stage:
    return Stage(
        kind="install",
        title="Install {} -> {}".format(settings.config, settings.binaries_dir),
        argv=[cmake, "--install", settings.build_dir, "--config", settings.config],
    )


# ----------------------------------------------------------------------
# The plan
# ----------------------------------------------------------------------


def cache_exists(settings: Settings, fs=RealFS) -> bool:
    return fs.exists(os.path.join(settings.build_dir, "CMakeCache.txt"))


def plan(settings: Settings, fs=RealFS, powershell: str = "pwsh", cmake: str = "cmake") -> List[Stage]:
    """Ordered stages for the chosen action.

    Configure runs when there is no CMakeCache.txt in the build folder, when
    Force regenerate is on, or when the action is Configure only. Install only
    needs an existing cache and raises PlanError otherwise.
    """
    stages = svn_stages(settings, fs)
    action = settings.action
    has_cache = cache_exists(settings, fs)

    if action == "Install only":
        if not has_cache:
            raise PlanError(
                "Install only needs an existing build in {} (no CMakeCache.txt found). "
                "Choose Build + Install instead.".format(settings.build_dir)
            )
        stages.append(install_stage(settings, cmake))
        return stages

    if action == "Configure only" or settings.force_regenerate or not has_cache:
        stage = configure_stage(settings, powershell)
        if has_cache and settings.force_regenerate:
            stage.notes.append("Force regenerate: the script will delete and recreate the Build and Binaries folders.")
        stages.append(stage)
    if action in ("Build", "Build + Install"):
        stages.append(build_stage(settings, cmake))
    if action == "Build + Install":
        stages.append(install_stage(settings, cmake))
    return stages


def preview(stages: List[Stage]) -> str:
    """Copy-pastable rendering of the plan, one stage per block."""
    lines = []
    for index, stage in enumerate(stages, 1):
        lines.append("# {}. {}".format(index, stage.title))
        if stage.cwd:
            lines.append("#    cwd: {}".format(stage.cwd))
        for note in stage.notes:
            lines.append("#    {}".format(note))
        lines.append(" ".join(shell_quote(a) for a in stage.argv))
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


# ----------------------------------------------------------------------
# SVN pre-flight (the one thing here that runs a process, on demand)
# ----------------------------------------------------------------------


def working_copy_url(directory: str, timeout: float = 30.0) -> Optional[str]:
    """URL of the working copy at `directory`, or None if svn cannot tell."""
    creation = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        proc = subprocess.run(
            ["svn", "info", "--show-item", "url", directory],
            capture_output=True, text=True, timeout=timeout, **creation,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def urls_match(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()
