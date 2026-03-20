"""Tests for the CS2Launcher plugin lifecycle and launch command."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frag_demo.launcher.cs2 import CS2Launcher, _GAMEINFO_CSGO_LINE, _GAMEINFO_CSDM_LINE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_launcher(tmp_path: Path) -> tuple[CS2Launcher, Path]:
    """Return a launcher whose CS2 root points at *tmp_path*.

    Also creates the minimal directory/file structure that install_plugin
    expects:
      - {tmp_path}/game/bin/win64/cs2.exe  (stub)
      - {tmp_path}/game/csgo/gameinfo.gi    (stub with the expected 'Game  csgo' line)
      - {plugin_src}/server.dll             (stub)
    """
    # cs2.exe stub
    cs2_exe = tmp_path / "game" / "bin" / "win64" / "cs2.exe"
    cs2_exe.parent.mkdir(parents=True, exist_ok=True)
    cs2_exe.touch()

    # gameinfo.gi stub (must contain "Game\tcsgo" to be patchable)
    gameinfo_dir = tmp_path / "game" / "csgo"
    gameinfo_dir.mkdir(parents=True, exist_ok=True)
    gameinfo_path = gameinfo_dir / "gameinfo.gi"
    gameinfo_content = (
        '"GameInfo"\n'
        "{\n"
        "\tFileSystem\n"
        "\t{\n"
        "\t\tSearchPaths\n"
        "\t\t{\n"
        "\t\t\t" + _GAMEINFO_CSGO_LINE + "\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )
    gameinfo_path.write_text(gameinfo_content, encoding="utf-8")

    # Fake HLAE stub
    hlae_path = tmp_path / "HLAE.exe"
    hlae_path.touch()

    # Plugin DLL stub (we will override find_plugin_dll via a separate temp dir)
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    plugin_dll = plugin_dir / "server.dll"
    plugin_dll.write_bytes(b"\x00" * 16)

    launcher = CS2Launcher(
        hlae_path=str(hlae_path),
        cs2_path=str(cs2_exe),
    )
    return launcher, plugin_dll


# ---------------------------------------------------------------------------
# _cs2_root
# ---------------------------------------------------------------------------

class TestCS2Root:
    def test_root_derived_from_exe_path(self, tmp_path: Path) -> None:
        cs2_exe = tmp_path / "game" / "bin" / "win64" / "cs2.exe"
        cs2_exe.parent.mkdir(parents=True, exist_ok=True)
        cs2_exe.touch()
        launcher = CS2Launcher(cs2_path=str(cs2_exe))
        assert launcher._cs2_root() == tmp_path.resolve()

    def test_root_none_when_no_cs2_path(self) -> None:
        launcher = CS2Launcher.__new__(CS2Launcher)
        launcher.cs2_path = None
        assert launcher._cs2_root() is None


# ---------------------------------------------------------------------------
# find_plugin_dll
# ---------------------------------------------------------------------------

class TestFindPluginDll:
    def test_returns_none_when_no_candidates_exist(self, tmp_path: Path) -> None:
        launcher = CS2Launcher.__new__(CS2Launcher)
        launcher.cs2_path = None
        launcher.hlae_path = None
        with patch("frag_demo.launcher.cs2._PLUGIN_CANDIDATE_PATHS", [tmp_path / "nope.dll"]):
            assert launcher.find_plugin_dll() is None

    def test_returns_first_existing_candidate(self, tmp_path: Path) -> None:
        dll = tmp_path / "server.dll"
        dll.touch()
        launcher = CS2Launcher.__new__(CS2Launcher)
        with patch("frag_demo.launcher.cs2._PLUGIN_CANDIDATE_PATHS", [tmp_path / "no.dll", dll]):
            assert launcher.find_plugin_dll() == dll


# ---------------------------------------------------------------------------
# install_plugin
# ---------------------------------------------------------------------------

class TestInstallPlugin:
    def test_dll_is_copied_to_csdm_bin(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            ok = launcher.install_plugin()

        assert ok is True
        dest = tmp_path / "game" / "csgo" / "csdm" / "bin" / "server.dll"
        assert dest.exists()

    def test_dll_is_also_copied_to_csdm_bin_win64(self, tmp_path: Path) -> None:
        """CS2 also searches bin/win64/ so the DLL must be present there too."""
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            ok = launcher.install_plugin()

        assert ok is True
        dest_win64 = tmp_path / "game" / "csgo" / "csdm" / "bin" / "win64" / "server.dll"
        assert dest_win64.exists()

    def test_gameinfo_gi_is_backed_up(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        backup = tmp_path / "game" / "csgo" / "gameinfo.gi.backup"
        assert backup.exists()

    def test_gameinfo_gi_is_patched(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        gameinfo = (tmp_path / "game" / "csgo" / "gameinfo.gi").read_text(encoding="utf-8")
        assert _GAMEINFO_CSDM_LINE in gameinfo
        assert _GAMEINFO_CSGO_LINE in gameinfo

        # Find line indices for exact matches (avoid substring confusion: "Game\tcsgo"
        # is a prefix of "Game\tcsgo/csdm").
        stripped_lines = [l.strip() for l in gameinfo.splitlines()]
        csdm_idx = next(
            i for i, l in enumerate(stripped_lines) if l == _GAMEINFO_CSDM_LINE
        )
        csgo_idx = next(
            i for i, l in enumerate(stripped_lines) if l == _GAMEINFO_CSGO_LINE
        )
        assert csdm_idx < csgo_idx, (
            f"Expected csdm line (line {csdm_idx}) before csgo line (line {csgo_idx})"
        )

    def test_no_duplicate_patch(self, tmp_path: Path) -> None:
        """Calling install_plugin twice should not add the csdm line twice."""
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()
            launcher.install_plugin()

        gameinfo = (tmp_path / "game" / "csgo" / "gameinfo.gi").read_text(encoding="utf-8")
        assert gameinfo.count(_GAMEINFO_CSDM_LINE) == 1

    def test_returns_false_when_plugin_not_found(self, tmp_path: Path) -> None:
        launcher, _ = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=None):
            ok = launcher.install_plugin()
        assert ok is False

    def test_returns_false_when_cs2_not_found(self, tmp_path: Path) -> None:
        launcher = CS2Launcher.__new__(CS2Launcher)
        launcher.cs2_path = None
        launcher.hlae_path = None
        assert launcher.install_plugin() is False


# ---------------------------------------------------------------------------
# uninstall_plugin
# ---------------------------------------------------------------------------

class TestUninstallPlugin:
    def test_gameinfo_gi_is_restored(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        gameinfo_path = tmp_path / "game" / "csgo" / "gameinfo.gi"
        original_text = gameinfo_path.read_text(encoding="utf-8")

        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        # Confirm it was patched
        assert _GAMEINFO_CSDM_LINE in gameinfo_path.read_text(encoding="utf-8")

        launcher.uninstall_plugin()

        restored_text = gameinfo_path.read_text(encoding="utf-8")
        assert restored_text == original_text

    def test_backup_file_removed_after_uninstall(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        launcher.uninstall_plugin()

        backup = tmp_path / "game" / "csgo" / "gameinfo.gi.backup"
        assert not backup.exists()

    def test_csdm_directory_removed(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        csdm_dir = tmp_path / "game" / "csgo" / "csdm"
        assert csdm_dir.exists()  # sanity

        launcher.uninstall_plugin()

        assert not csdm_dir.exists()

    def test_uninstall_without_prior_install_does_not_crash(self, tmp_path: Path) -> None:
        launcher, _ = _make_launcher(tmp_path)
        # No backup exists -- should just print a warning, not raise.
        launcher.uninstall_plugin()


# ---------------------------------------------------------------------------
# launch command construction
# ---------------------------------------------------------------------------

class TestLaunchCommand:
    def test_launch_builds_hlae_command(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        demo = tmp_path / "match.dem"
        demo.touch()

        captured_cmd: list[str] = []

        def fake_popen(cmd: list[str], **kwargs):  # type: ignore[override]
            captured_cmd.extend(cmd)
            m = MagicMock()
            m.pid = 99
            m.wait.return_value = 0
            return m

        with (
            patch.object(launcher, "find_plugin_dll", return_value=plugin_dll),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            launcher.launch(demo_path=str(demo))

        assert captured_cmd[0] == launcher.hlae_path
        assert "-noGui" in captured_cmd
        assert "-autoStart" in captured_cmd
        assert "-noConfig" in captured_cmd
        assert "-afxDisableSteamStorage" in captured_cmd
        assert "-customLoader" in captured_cmd
        assert "-insecure" in " ".join(captured_cmd)
        assert "+playdemo" in " ".join(captured_cmd)

    def test_launch_returns_none_when_hlae_missing(self, tmp_path: Path) -> None:
        launcher = CS2Launcher.__new__(CS2Launcher)
        launcher.hlae_path = None
        launcher.cs2_path = None
        launcher.width = 1920
        launcher.height = 1080
        assert launcher.launch(demo_path=str(tmp_path / "match.dem")) is None

    def test_plugin_uninstalled_even_on_popen_error(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        demo = tmp_path / "match.dem"
        demo.touch()

        with (
            patch.object(launcher, "find_plugin_dll", return_value=plugin_dll),
            patch("subprocess.Popen", side_effect=FileNotFoundError("no exe")),
        ):
            launcher.launch(demo_path=str(demo))

        # After the failed launch, the plugin should be cleaned up.
        assert not (tmp_path / "game" / "csgo" / "csdm").exists()
