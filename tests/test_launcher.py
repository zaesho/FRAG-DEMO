"""Tests for the CS2Launcher plugin lifecycle and launch command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from frag_demo.launcher.cs2 import CS2Launcher, _GAMEINFO_CSDM_LINE, _GAMEINFO_CSGO_LINE


def _make_launcher(tmp_path: Path) -> tuple[CS2Launcher, Path]:
    """Return a launcher rooted at *tmp_path* with stub install assets."""
    cs2_exe = tmp_path / "game" / "bin" / "win64" / "cs2.exe"
    cs2_exe.parent.mkdir(parents=True, exist_ok=True)
    cs2_exe.touch()

    gameinfo_dir = tmp_path / "game" / "csgo"
    gameinfo_dir.mkdir(parents=True, exist_ok=True)
    gameinfo_path = gameinfo_dir / "gameinfo.gi"
    gameinfo_path.write_text(
        '"GameInfo"\n'
        "{\n"
        "\tFileSystem\n"
        "\t{\n"
        "\t\tSearchPaths\n"
        "\t\t{\n"
        "\t\t\t" + _GAMEINFO_CSGO_LINE + "\n"
        "\t\t}\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )

    hlae_path = tmp_path / "HLAE.exe"
    hlae_path.touch()

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    plugin_dll = plugin_dir / "server.dll"
    plugin_dll.write_bytes(b"\x00" * 16)

    launcher = CS2Launcher(
        hlae_path=str(hlae_path),
        cs2_path=str(cs2_exe),
    )
    return launcher, plugin_dll


class TestCS2Root:
    def test_root_derived_from_exe_path(self, tmp_path: Path) -> None:
        launcher, _ = _make_launcher(tmp_path)
        assert launcher._cs2_root() == tmp_path.resolve()

    def test_root_none_when_no_cs2_path(self) -> None:
        launcher = CS2Launcher.__new__(CS2Launcher)
        launcher.cs2_path = None
        assert launcher._cs2_root() is None

    def test_root_none_for_unexpected_manual_path(self, tmp_path: Path) -> None:
        launcher = CS2Launcher(cs2_path=str(tmp_path / "cs2.exe"))
        assert launcher._cs2_root() is None


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


class TestInstallPlugin:
    def test_dll_is_copied_to_csdm_bin(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            ok = launcher.install_plugin()

        assert ok is True
        assert (tmp_path / "game" / "csgo" / "csdm" / "bin" / "server.dll").exists()

    def test_dll_is_also_copied_to_csdm_bin_win64(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            ok = launcher.install_plugin()

        assert ok is True
        assert (tmp_path / "game" / "csgo" / "csdm" / "bin" / "win64" / "server.dll").exists()

    def test_gameinfo_gi_is_backed_up(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        assert (tmp_path / "game" / "csgo" / "gameinfo.gi.backup").exists()

    def test_gameinfo_gi_is_patched(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        gameinfo = (tmp_path / "game" / "csgo" / "gameinfo.gi").read_text(encoding="utf-8")
        assert _GAMEINFO_CSDM_LINE in gameinfo
        assert _GAMEINFO_CSGO_LINE in gameinfo

        stripped_lines = [line.strip() for line in gameinfo.splitlines()]
        csdm_idx = next(i for i, line in enumerate(stripped_lines) if line == _GAMEINFO_CSDM_LINE)
        csgo_idx = next(i for i, line in enumerate(stripped_lines) if line == _GAMEINFO_CSGO_LINE)
        assert csdm_idx < csgo_idx

    def test_no_duplicate_patch(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()
            launcher.install_plugin()

        gameinfo = (tmp_path / "game" / "csgo" / "gameinfo.gi").read_text(encoding="utf-8")
        assert gameinfo.count(_GAMEINFO_CSDM_LINE) == 1

    def test_second_install_keeps_original_backup(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        gameinfo_path = tmp_path / "game" / "csgo" / "gameinfo.gi"
        original_text = gameinfo_path.read_text(encoding="utf-8")

        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            assert launcher.install_plugin() is True
            assert launcher.install_plugin() is True

        backup = tmp_path / "game" / "csgo" / "gameinfo.gi.backup"
        assert backup.read_text(encoding="utf-8") == original_text

    def test_second_install_reconstructs_missing_backup(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        gameinfo_path = tmp_path / "game" / "csgo" / "gameinfo.gi"
        original_text = gameinfo_path.read_text(encoding="utf-8")

        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            assert launcher.install_plugin() is True

        backup = tmp_path / "game" / "csgo" / "gameinfo.gi.backup"
        backup.unlink()

        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            assert launcher.install_plugin() is True

        assert backup.read_text(encoding="utf-8") == original_text

    def test_missing_gameinfo_does_not_leave_partial_plugin_tree(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        (tmp_path / "game" / "csgo" / "gameinfo.gi").unlink()

        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            assert launcher.install_plugin() is False

        assert not (tmp_path / "game" / "csgo" / "csdm").exists()

    def test_returns_false_when_plugin_not_found(self, tmp_path: Path) -> None:
        launcher, _ = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=None):
            assert launcher.install_plugin() is False

    def test_returns_false_when_cs2_not_found(self) -> None:
        launcher = CS2Launcher.__new__(CS2Launcher)
        launcher.cs2_path = None
        launcher.hlae_path = None
        assert launcher.install_plugin() is False


class TestUninstallPlugin:
    def test_gameinfo_gi_is_restored(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        gameinfo_path = tmp_path / "game" / "csgo" / "gameinfo.gi"
        original_text = gameinfo_path.read_text(encoding="utf-8")

        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        launcher.uninstall_plugin()

        assert gameinfo_path.read_text(encoding="utf-8") == original_text

    def test_backup_file_removed_after_uninstall(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        launcher.uninstall_plugin()

        assert not (tmp_path / "game" / "csgo" / "gameinfo.gi.backup").exists()

    def test_csdm_directory_removed(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        launcher.uninstall_plugin()

        assert not (tmp_path / "game" / "csgo" / "csdm").exists()

    def test_uninstall_without_prior_install_does_not_crash(self, tmp_path: Path) -> None:
        launcher, _ = _make_launcher(tmp_path)
        launcher.uninstall_plugin()

    def test_uninstall_preserves_unmanaged_files_in_csdm(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        with patch.object(launcher, "find_plugin_dll", return_value=plugin_dll):
            launcher.install_plugin()

        extra_file = tmp_path / "game" / "csgo" / "csdm" / "notes.txt"
        extra_file.write_text("keep me", encoding="utf-8")

        launcher.uninstall_plugin()

        assert extra_file.exists()
        assert not (tmp_path / "game" / "csgo" / "csdm" / "bin" / "server.dll").exists()


class TestLaunchCommand:
    def test_launch_builds_hlae_command(self, tmp_path: Path) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        demo = tmp_path / "match.dem"
        demo.touch()

        captured_cmd: list[str] = []

        def fake_popen(cmd: list[str], **kwargs):  # type: ignore[override]
            captured_cmd.extend(cmd)
            process = MagicMock()
            process.pid = 99
            process.wait.return_value = 0
            return process

        with (
            patch.object(launcher, "find_plugin_dll", return_value=plugin_dll),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            launcher.launch(demo_path=str(demo))

        assert captured_cmd[0] == launcher.hlae_path
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

        assert not (tmp_path / "game" / "csgo" / "csdm").exists()

    def test_tasklist_unavailable_waits_for_hlae_before_cleanup(
        self, tmp_path: Path
    ) -> None:
        launcher, plugin_dll = _make_launcher(tmp_path)
        demo = tmp_path / "match.dem"
        demo.touch()
        process = MagicMock()
        process.pid = 99
        process.wait.return_value = 0

        with (
            patch.object(launcher, "find_plugin_dll", return_value=plugin_dll),
            patch.object(launcher, "_list_cs2_pids", return_value=None),
            patch("subprocess.Popen", return_value=process),
            patch("time.sleep", return_value=None),
        ):
            returned = launcher.launch(demo_path=str(demo))

        assert returned is process
        process.wait.assert_called_once_with(timeout=30)
        assert not (tmp_path / "game" / "csgo" / "csdm").exists()
