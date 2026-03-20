"""FFmpeg video encoding utilities."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class VideoEncoder:
    """Encodes TGA frame sequences (+ optional WAV audio) into video files
    and concatenates multiple clips into a single output.

    HLAE records frames as numbered TGA images and a WAV file in a
    per-clip directory.  This class wraps ffmpeg to convert those assets
    into a polished MP4 (or other container) file.
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        crf: int = 18,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
        container: str = "mp4",
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.crf = crf
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.container = container

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode_sequence(
        self,
        input_dir: str,
        output_path: str,
        framerate: int = 60,
        has_audio: bool = True,
    ) -> None:
        """Encode a directory of TGA frames (and optional WAV) to video.

        HLAE writes frames as ``%05d.tga`` (zero-padded, 1-based) and a
        ``audio.wav`` file in the same directory.

        Args:
            input_dir: Directory containing TGA frames and optionally an
                ``audio.wav`` file.
            output_path: Destination video file path.
            framerate: Frame rate matching the MIRV recording setting.
            has_audio: When ``True`` look for ``audio.wav`` alongside the
                frames and mux it into the output.
        """
        in_dir = Path(input_dir)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        wav_path = in_dir / "audio.wav"

        # Auto-detect TGA frame naming pattern.
        # MIRV uses: 00000.tga, 00001.tga, ...
        # startmovie may use: 00000.tga or other patterns
        tga_files = sorted(in_dir.glob("*.tga"))
        if not tga_files:
            raise FileNotFoundError(f"No TGA frames found in {in_dir}")

        first_name = tga_files[0].stem  # e.g. "00000" or "frame00000"
        # Extract the numeric part to determine the pattern
        import re
        m = re.match(r"^(.*?)(\d+)$", first_name)
        if m:
            prefix = m.group(1)
            digits = len(m.group(2))
            start_number = int(m.group(2))
            frame_pattern = str(in_dir / f"{prefix}%0{digits}d.tga")
        else:
            # Fallback: assume sequential 5-digit numbering
            frame_pattern = str(in_dir / "%05d.tga")
            start_number = 0

        args: list[str] = [
            self.ffmpeg_path,
            "-y",
            "-start_number", str(start_number),
            "-framerate", str(framerate),
            "-i", frame_pattern,
        ]

        if has_audio and wav_path.exists():
            args += ["-i", str(wav_path)]

        args += [
            "-c:v", self.video_codec,
            "-crf", str(self.crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]

        if has_audio and wav_path.exists():
            args += ["-c:a", self.audio_codec]
        else:
            args += ["-an"]

        args.append(str(out))
        self._run_ffmpeg(args)

    def concatenate(self, video_paths: list[str], output_path: str) -> None:
        """Concatenate multiple video files into a single output.

        Uses the ffmpeg concat demuxer which supports lossless
        concatenation when all input files share the same encoding
        parameters.

        Args:
            video_paths: Ordered list of video file paths to concatenate.
            output_path: Destination file path for the combined video.
        """
        if not video_paths:
            raise ValueError("video_paths must not be empty")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Write a temporary concat list file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            concat_list_path = fh.name
            for vp in video_paths:
                # ffmpeg concat list uses forward slashes and single-quoted paths
                escaped = str(Path(vp).resolve()).replace("'", r"'")
                fh.write("file '" + escaped + "'\n")

        try:
            args: list[str] = [
                self.ffmpeg_path,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                str(out),
            ]
            self._run_ffmpeg(args)
        finally:
            Path(concat_list_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_ffmpeg(self, args: list[str]) -> None:
        """Execute ffmpeg with the given argument list.

        Args:
            args: Full command including the ffmpeg binary as the first
                element.

        Raises:
            subprocess.CalledProcessError: If ffmpeg exits with a
                non-zero status.
            FileNotFoundError: If the ffmpeg binary cannot be found.
        """
        print(f"[frag-demo/ffmpeg] Running: {' '.join(args)}")
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                args,
                output=result.stdout,
            )
