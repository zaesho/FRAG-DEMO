"""Tests for the VideoEncoder."""

from __future__ import annotations

from pathlib import Path

import pytest

from frag_demo.encoder.ffmpeg import VideoEncoder


class TestEncodeSequence:
    def test_non_positive_framerate_rejected(self, tmp_path: Path) -> None:
        input_dir = tmp_path / "clip"
        input_dir.mkdir()
        (input_dir / "00000.tga").touch()

        encoder = VideoEncoder()

        with pytest.raises(ValueError, match="framerate"):
            encoder.encode_sequence(
                str(input_dir),
                str(tmp_path / "clip.mp4"),
                framerate=0,
            )

    def test_missing_audio_adds_silent_track(self, tmp_path: Path) -> None:
        input_dir = tmp_path / "clip"
        input_dir.mkdir()
        (input_dir / "00000.tga").touch()
        (input_dir / "00001.tga").touch()

        captured_args: list[str] = []
        encoder = VideoEncoder()
        encoder._run_ffmpeg = lambda args: captured_args.extend(args)  # type: ignore[method-assign]

        encoder.encode_sequence(str(input_dir), str(tmp_path / "clip.mp4"))

        assert captured_args[:4] == [
            "ffmpeg",
            "-y",
            "-start_number",
            "0",
        ]
        assert captured_args[captured_args.index("-framerate") + 1] == "60"
        silent_audio_args = [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
        silent_start = captured_args.index("-f")
        assert captured_args[silent_start : silent_start + 4] == silent_audio_args
        assert "-c:a" in captured_args
        assert "-shortest" in captured_args

    def test_detects_primary_frame_sequence_when_other_tgas_exist(
        self, tmp_path: Path
    ) -> None:
        input_dir = tmp_path / "clip"
        input_dir.mkdir()
        for name in ("shot0002.tga", "shot0001.tga", "shot0003.tga", "thumb0001.tga"):
            (input_dir / name).touch()

        captured_args: list[str] = []
        encoder = VideoEncoder()
        encoder._run_ffmpeg = lambda args: captured_args.extend(args)  # type: ignore[method-assign]

        encoder.encode_sequence(str(input_dir), str(tmp_path / "clip.mp4"), has_audio=False)

        assert captured_args[2:5] == ["-start_number", "1", "-framerate"]
        assert str(input_dir / "shot%04d.tga") in captured_args


class TestConcatenate:
    def test_concat_file_escapes_apostrophes_with_posix_paths(
        self, tmp_path: Path
    ) -> None:
        first = tmp_path / "player's clip.mp4"
        second = tmp_path / "second clip.mp4"
        first.touch()
        second.touch()

        captured_concat = ""

        def fake_run(args: list[str]) -> None:
            nonlocal captured_concat
            concat_path = Path(args[args.index("-i") + 1])
            captured_concat = concat_path.read_text(encoding="utf-8")

        encoder = VideoEncoder()
        encoder._run_ffmpeg = fake_run  # type: ignore[method-assign]

        encoder.concatenate([str(first), str(second)], str(tmp_path / "all.mp4"))

        escaped_first = first.resolve().as_posix().replace("'", r"'\''")
        escaped_second = second.resolve().as_posix()

        assert f"file '{escaped_first}'" in captured_concat
        assert f"file '{escaped_second}'" in captured_concat
