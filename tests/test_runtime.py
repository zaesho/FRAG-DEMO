"""Tests for Flask-free runtime helpers used by the Python worker."""

from __future__ import annotations

import json
from pathlib import Path

from frag_demo.runtime import _clean_old_clips, _clips_payload, _expected_clip_dirs_from_json


def test_clean_old_clips_removes_demo_outputs(tmp_path: Path) -> None:
    demo_path = tmp_path / "match.dem"
    demo_path.touch()

    (tmp_path / "match_001").mkdir()
    (tmp_path / "match_001" / "00000.tga").touch()
    (tmp_path / "match_002.mp4").touch()
    (tmp_path / "other_001.mp4").touch()

    removed = _clean_old_clips(str(demo_path))

    assert removed == 2
    assert not (tmp_path / "match_001").exists()
    assert not (tmp_path / "match_002.mp4").exists()
    assert (tmp_path / "other_001.mp4").exists()


def test_expected_clip_dirs_from_json_dedupes_record_commands(tmp_path: Path) -> None:
    clip_dir = tmp_path / "match_001"
    json_path = tmp_path / "match.dem.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "actions": [
                        {"tick": 10, "cmd": f'mirv_streams record name "{clip_dir}"'},
                        {"tick": 20, "cmd": f'mirv_streams record name "{clip_dir}"'},
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )

    assert _expected_clip_dirs_from_json(json_path) == [clip_dir]


def test_clips_payload_lists_encoded_mp4s_for_demo(tmp_path: Path) -> None:
    demo_path = tmp_path / "match.dem"
    demo_path.touch()
    combined = tmp_path / "match_all.mp4"
    combined.write_bytes(b"x" * 1024)
    clip = tmp_path / "match_001.mp4"
    clip.write_bytes(b"x" * 2048)
    (tmp_path / "other.mp4").write_bytes(b"x")

    payload = _clips_payload(str(demo_path))

    assert [item["name"] for item in payload] == ["match_001.mp4", "match_all.mp4"]
    assert payload[0]["is_combined"] is False
    assert payload[1]["is_combined"] is True
