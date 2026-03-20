"""CS2 + HLAE launcher with CS Demo Manager server plugin support."""

from __future__ import annotations

import os
import shutil
import subprocess
import csv
from pathlib import Path


# Project-local HLAE install (tools/hlae alongside the repo root).
_PROJECT_HLAE = Path(__file__).resolve().parents[3] / "tools" / "hlae" / "HLAE.exe"

# Default locations to probe when auto-detecting HLAE.
_HLAE_CANDIDATE_PATHS: list[str] = [
    str(_PROJECT_HLAE),
    r"C:\Program Files\HLAE\HLAE.exe",
    r"C:\Program Files (x86)\HLAE\HLAE.exe",
    r"C:\HLAE\HLAE.exe",
    str(Path.home() / "HLAE" / "HLAE.exe"),
]

# Registry path used by Steam on Windows.
_STEAM_REGISTRY_PATH = r"SOFTWARE\WOW6432Node\Valve\Steam"
_CS2_APP_ID = "730"

# Locations to search for the CS Demo Manager server plugin DLL.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_CANDIDATE_PATHS: list[Path] = [
    _PROJECT_ROOT / "tools" / "plugin" / "server.dll",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "cs-demo-manager"
    / "resources"
    / "static"
    / "cs2"
    / "server.dll",
]

# The line in gameinfo.gi that we insert our plugin search path before.
_GAMEINFO_CSGO_LINE = "Game\tcsgo"
_GAMEINFO_CSDM_LINE = "Game\tcsgo/csdm"


class CS2Launcher:
    """Builds and executes the HLAE launch command for CS2 demo playback.

    HLAE (Half-Life Advanced Effects) is used to inject AfxHookSource2
    which enables the MIRV commands required for per-frame recording.

    The launcher also manages the CS Demo Manager server plugin lifecycle:
    - :meth:`install_plugin` copies ``server.dll`` and patches
      ``gameinfo.gi`` so that CS2 loads the plugin from ``csgo/csdm``.
    - :meth:`uninstall_plugin` restores ``gameinfo.gi`` from the backup
      and removes the ``csdm`` folder.
    """

    def __init__(
        self,
        hlae_path: str | None = None,
        cs2_path: str | None = None,
        width: int = 1920,
        height: int = 1080,
    ) -> None:
        self.hlae_path = hlae_path or self.find_hlae_path()
        self.cs2_path = cs2_path or self.find_cs2_path()
        self.width = width
        self.height = height

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def find_cs2_path(self) -> str | None:
        """Attempt to locate cs2.exe via the Steam registry on Windows.

        Returns the path to ``cs2.exe`` or ``None`` if it cannot be found.
        """
        try:
            import winreg  # type: ignore[import]

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _STEAM_REGISTRY_PATH)
            steam_path, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)

            # The library folders file lists all Steam library locations.
            libraryfolders = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
            cs2_candidates: list[Path] = []

            if libraryfolders.exists():
                text = libraryfolders.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith('"path"'):
                        parts = line.split('"')
                        if len(parts) >= 4:
                            lib_path = parts[3].replace("\\\\", "\\")
                            candidate = (
                                Path(lib_path)
                                / "steamapps"
                                / "common"
                                / "Counter-Strike Global Offensive"
                                / "game"
                                / "bin"
                                / "win64"
                                / "cs2.exe"
                            )
                            cs2_candidates.append(candidate)

            for candidate in cs2_candidates:
                if candidate.exists():
                    return str(candidate)

        except Exception:
            pass

        # Fallback: check a well-known default location
        default = Path(
            r"C:\Program Files (x86)\Steam\steamapps\common"
            r"\Counter-Strike Global Offensive\game\bin\win64\cs2.exe"
        )
        if default.exists():
            return str(default)

        return None

    def find_hlae_path(self) -> str | None:
        """Check common HLAE install locations and PATH.

        Returns the path to ``HLAE.exe`` or ``None`` if not found.
        """
        # Check PATH first
        found = shutil.which("HLAE")
        if found:
            return found

        for candidate in _HLAE_CANDIDATE_PATHS:
            if Path(candidate).exists():
                return candidate

        return None

    def find_plugin_dll(self) -> Path | None:
        """Search for the CS Demo Manager server plugin DLL.

        Checks the bundled project location first, then the CS Demo Manager
        install directory under ``%LOCALAPPDATA%``.

        Returns:
            Path to ``server.dll`` or ``None`` if not found.
        """
        for candidate in _PLUGIN_CANDIDATE_PATHS:
            if candidate.exists():
                return candidate
        return None

    def _cs2_root(self) -> Path | None:
        """Derive the CS2 root directory from the discovered CS2 exe path."""
        if not self.cs2_path:
            return None
        # cs2.exe lives at <root>/game/bin/win64/cs2.exe
        cs2_exe = Path(self.cs2_path).resolve()
        if len(cs2_exe.parents) < 4:
            return None
        if cs2_exe.name.lower() != "cs2.exe":
            return None
        if cs2_exe.parent.name.lower() != "win64":
            return None
        if cs2_exe.parents[1].name.lower() != "bin":
            return None
        if cs2_exe.parents[2].name.lower() != "game":
            return None
        return cs2_exe.parents[3]

    # ------------------------------------------------------------------
    # Plugin lifecycle
    # ------------------------------------------------------------------

    def install_plugin(self) -> bool:
        """Install the CS Demo Manager server plugin into CS2.

        Steps:
        1. Copy ``server.dll`` from the bundled/installed CS Demo Manager
           location to ``{cs2_root}/game/csgo/csdm/bin/server.dll``.
        2. Back up ``gameinfo.gi`` to ``gameinfo.gi.backup``.
        3. Prepend ``Game\tcsgo/csdm`` before the ``Game\tcsgo`` line so
           that CS2 discovers and loads the plugin.

        Returns:
            ``True`` on success, ``False`` if a prerequisite is missing.
        """
        cs2_root = self._cs2_root()
        if cs2_root is None:
            print("[frag-demo] ERROR: Cannot install plugin — CS2 not found.")
            return False

        plugin_src = self.find_plugin_dll()
        if plugin_src is None:
            print(
                "[frag-demo] ERROR: Cannot install plugin — server.dll not found.\n"
                "  Searched:\n"
                + "\n".join(f"    {p}" for p in _PLUGIN_CANDIDATE_PATHS)
            )
            return False

        gameinfo_path = cs2_root / "game" / "csgo" / "gameinfo.gi"
        if not gameinfo_path.exists():
            print(f"[frag-demo] ERROR: gameinfo.gi not found at {gameinfo_path}")
            return False

        backup_path = gameinfo_path.with_suffix(".gi.backup")
        original_text = gameinfo_path.read_text(encoding="utf-8", errors="replace")
        already_patched = _GAMEINFO_CSDM_LINE in original_text
        patched_text = original_text
        reverted_text = original_text.replace(
            _GAMEINFO_CSDM_LINE + "\n\t\t\t" + _GAMEINFO_CSGO_LINE,
            _GAMEINFO_CSGO_LINE,
            1,
        )

        if not already_patched:
            patched_text = original_text.replace(
                _GAMEINFO_CSGO_LINE,
                _GAMEINFO_CSDM_LINE + "\n\t\t\t" + _GAMEINFO_CSGO_LINE,
                1,
            )
            if patched_text == original_text:
                print(
                    "[frag-demo] WARNING: Could not find the expected 'Game\\tcsgo' line "
                    "in gameinfo.gi. The file may have an unexpected format."
                )
                return False

        # --- Copy DLL ---
        dest_dir = cs2_root / "game" / "csgo" / "csdm" / "bin"
        dest_dll = dest_dir / "server.dll"
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Also copy to bin/win64/ to match CS2's real server.dll search path.
        dest_win64_dir = dest_dir / "win64"
        dest_win64_dll = dest_win64_dir / "server.dll"
        dest_win64_dir.mkdir(parents=True, exist_ok=True)

        try:
            for dst in (dest_dll, dest_win64_dll):
                try:
                    print(f"[frag-demo] Copying plugin: {plugin_src} -> {dst}")
                    shutil.copy2(str(plugin_src), str(dst))
                except PermissionError:
                    if dst.exists():
                        print(
                            f"[frag-demo] DLL locked but already in place: {dst} (skipping)"
                        )
                    else:
                        raise RuntimeError(f"Cannot copy DLL to {dst} (locked)")

            if already_patched:
                if not backup_path.exists():
                    if reverted_text != original_text:
                        backup_path.write_text(reverted_text, encoding="utf-8")
                        print(
                            "[frag-demo] Reconstructed missing gameinfo.gi.backup "
                            "from the already-patched file."
                        )
                    else:
                        print(
                            "[frag-demo] WARNING: gameinfo.gi is already patched but "
                            "its original contents could not be reconstructed."
                        )
                print(
                    "[frag-demo] gameinfo.gi already contains csdm entry — skipping patch."
                )
                return True

            if not backup_path.exists():
                print(f"[frag-demo] Backing up gameinfo.gi -> {backup_path.name}")
                shutil.copy2(str(gameinfo_path), str(backup_path))

            gameinfo_path.write_text(patched_text, encoding="utf-8")
            print("[frag-demo] gameinfo.gi patched successfully.")
            return True
        except Exception as exc:
            print(f"[frag-demo] ERROR: Plugin installation failed: {exc}")
            self._remove_plugin_files(cs2_root)
            return False

    def uninstall_plugin(self) -> None:
        """Remove the CS Demo Manager server plugin from CS2.

        Steps:
        1. Restore ``gameinfo.gi`` from ``gameinfo.gi.backup`` (if present).
        2. Remove the ``{cs2_root}/game/csgo/csdm`` directory tree.
        """
        cs2_root = self._cs2_root()
        if cs2_root is None:
            print("[frag-demo] WARNING: Cannot uninstall plugin — CS2 root not found.")
            return

        # --- Restore gameinfo.gi ---
        gameinfo_path = cs2_root / "game" / "csgo" / "gameinfo.gi"
        backup_path = gameinfo_path.with_suffix(".gi.backup")
        if backup_path.exists():
            print(f"[frag-demo] Restoring gameinfo.gi from {backup_path.name}")
            shutil.copy2(str(backup_path), str(gameinfo_path))
            backup_path.unlink()
        else:
            print("[frag-demo] WARNING: No gameinfo.gi.backup found — skipping restore.")

        # --- Remove frag-demo managed plugin files ---
        self._remove_plugin_files(cs2_root)

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------

    def launch(
        self,
        demo_path: str,
        actions_json_path: str | None = None,
        width: int | None = None,
        height: int | None = None,
        install_plugin: bool = True,
    ) -> subprocess.Popen | None:  # type: ignore[type-arg]
        """Install the plugin, launch CS2 via HLAE, then uninstall the plugin.

        The command uses HLAE's ``-customLoader`` mechanism to inject
        ``AfxHookSource2.dll`` and passes ``+playdemo`` so that CS2 begins
        demo playback immediately.  The sequences JSON placed next to the
        ``.dem`` file is discovered by the CS Demo Manager plugin at
        runtime.

        Args:
            demo_path: Path to the .dem file.
            actions_json_path: Reserved for future use; ignored (the plugin
                reads ``{demo_path}.json`` automatically).
            width: Override the resolution width.
            height: Override the resolution height.
            install_plugin: When ``True`` (default), install the server
                plugin before launching and uninstall it afterwards.

        Returns:
            A :class:`subprocess.Popen` instance if HLAE and CS2 were
            found; ``None`` otherwise (command is still printed).
        """
        w = width or self.width
        h = height or self.height
        demo = Path(demo_path).resolve()

        if not (self.hlae_path and self.cs2_path):
            print(
                "[frag-demo] WARNING: HLAE and/or CS2 not found. "
                "Command cannot be executed."
            )
            self._print_missing_paths()
            return None

        hlae_dir = Path(self.hlae_path).parent
        hook_dll = hlae_dir / "x64" / "AfxHookSource2.dll"

        # Build the CS2 -cmdLine string (must be a single quoted argument to HLAE).
        cs2_cmdline = (
            f'-insecure -novid -sw -width {w} -height {h} +playdemo "{demo}"'
        )

        cmd: list[str] = [
            self.hlae_path,
            "-noGui",
            "-autoStart",
            "-noConfig",
            "-afxDisableSteamStorage",
            "-customLoader",
            "-hookDllPath", str(hook_dll),
            "-programPath", self.cs2_path,
            "-cmdLine", cs2_cmdline,
        ]

        print("[frag-demo] Launch command:")
        print(" ".join(f'"{c}"' if " " in c else c for c in cmd))

        # Install the plugin before launching.
        if install_plugin:
            print("\n[frag-demo] Installing CS Demo Manager server plugin...")
            ok = self.install_plugin()
            if not ok:
                print(
                    "[frag-demo] ERROR: Plugin installation failed. "
                    "Aborting launch to avoid a broken CS2 state."
                )
                return None

        proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        try:
            print("\n[frag-demo] Starting CS2 via HLAE...")
            existing_cs2_pids = self._list_cs2_pids()
            proc = subprocess.Popen(cmd)
            print(f"[frag-demo] HLAE started (PID {proc.pid}).")

            # HLAE exits after launching CS2 as a child process, so we
            # need to poll for cs2.exe separately and wait for it to close.
            print("[frag-demo] Waiting for CS2 to start...")
            import time

            if existing_cs2_pids is None:
                print(
                    "[frag-demo] WARNING: 'tasklist' is unavailable; "
                    "waiting on HLAE only before cleanup."
                )
                try:
                    proc.wait(timeout=30)
                except Exception:
                    pass
                time.sleep(5)
                return proc

            cs2_started = False
            launched_cs2_pid: int | None = None
            for _ in range(120):  # up to 2 minutes for CS2 to appear
                time.sleep(1)
                try:
                    current_pids = self._list_cs2_pids()
                    if current_pids is None:
                        break
                    new_pids = current_pids - existing_cs2_pids
                    if new_pids:
                        cs2_started = True
                        launched_cs2_pid = max(new_pids)
                        print("[frag-demo] CS2 is running. Waiting for demo playback to finish...")
                        break
                except Exception:
                    pass

            if not cs2_started:
                print("[frag-demo] WARNING: CS2 did not start within 2 minutes.")
            else:
                # Poll until cs2.exe exits
                while True:
                    time.sleep(2)
                    try:
                        current_pids = self._list_cs2_pids()
                        if current_pids is None:
                            break
                        if launched_cs2_pid is None or launched_cs2_pid not in current_pids:
                            break
                    except Exception:
                        break

            print("[frag-demo] CS2 exited.")
        except FileNotFoundError as exc:
            print(f"[frag-demo] ERROR: Could not start process — {exc}")
        finally:
            if install_plugin:
                print("\n[frag-demo] Uninstalling CS Demo Manager server plugin...")
                self.uninstall_plugin()

        return proc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _print_missing_paths(self) -> None:
        """Print diagnostic information about missing HLAE / CS2 paths."""
        if not self.hlae_path:
            print("[frag-demo]   HLAE not found. Searched:")
            for p in _HLAE_CANDIDATE_PATHS:
                print(f"[frag-demo]     {p}")
        if not self.cs2_path:
            print(
                "[frag-demo]   CS2 not found. "
                r"Expected at: C:\Program Files (x86)\Steam\steamapps\common"
                r"\Counter-Strike Global Offensive\game\bin\win64\cs2.exe"
            )

    @staticmethod
    def _remove_plugin_files(cs2_root: Path) -> None:
        """Remove only the plugin files and empty directories created by frag-demo."""
        csdm_dir = cs2_root / "game" / "csgo" / "csdm"
        managed_paths = [
            csdm_dir / "bin" / "win64" / "server.dll",
            csdm_dir / "bin" / "server.dll",
        ]

        for path in managed_paths:
            if path.exists():
                print(f"[frag-demo] Removing plugin file: {path}")
                path.unlink(missing_ok=True)

        for directory in (csdm_dir / "bin" / "win64", csdm_dir / "bin", csdm_dir):
            if directory.exists():
                try:
                    directory.rmdir()
                except OSError:
                    pass

    @staticmethod
    def _list_cs2_pids() -> set[int] | None:
        """Return the currently running cs2.exe PIDs on Windows."""
        tasklist_path = shutil.which("tasklist")
        if tasklist_path is None:
            return None

        try:
            result = subprocess.run(
                [tasklist_path, "/fi", "imagename eq cs2.exe", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return set()

        pids: set[int] = set()
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) >= 2 and row[0].lower() == "cs2.exe":
                try:
                    pids.add(int(row[1]))
                except ValueError:
                    continue
        return pids
