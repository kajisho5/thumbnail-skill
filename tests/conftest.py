from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

import pytest

from thumbnail_skill.fonts import font_status

from .fixtures.generate import build_all, ffmpeg_available


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> Dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    return build_all(d)


@pytest.fixture()
def workspace(tmp_path) -> Path:
    w = tmp_path / "workspace"
    w.mkdir()
    return w


@pytest.fixture(scope="session")
def ffmpeg_skill_dir() -> str:
    """Where a real ffmpeg-skill checkout is expected for video_frame tests. Resolution order matches
    the adapter's own: an explicit env var, then a `vendor/ffmpeg-skill` checkout next to this repo
    (as CI creates), then a sibling checkout. Tests that need it are skipped if none resolves."""
    for candidate in (
        os.environ.get("THUMBNAIL_SKILL_FFMPEG_SKILL_DIR"),
        os.environ.get("VIDEO_AGENT_FFMPEG_SKILL_DIR"),
        str(Path(__file__).resolve().parents[1] / "vendor" / "ffmpeg-skill"),
        str(Path(__file__).resolve().parents[2] / "ffmpeg-skill"),
        str(Path.home() / ".claude" / "skills" / "ffmpeg-skill"),
    ):
        if candidate and (Path(candidate) / "scripts" / "_contract.py").is_file():
            return candidate
    pytest.skip("no ffmpeg-skill checkout found (set THUMBNAIL_SKILL_FFMPEG_SKILL_DIR)")


@pytest.fixture(scope="session")
def has_ffmpeg() -> bool:
    return ffmpeg_available()


@pytest.fixture(scope="session")
def has_cjk_font() -> bool:
    """Whether fonts.py's `cjk` font_id actually resolves on this machine. A bare CI runner may not
    have a CJK-capable font installed (see .github/workflows/tests.yml for the Linux package this
    repo installs); tests exercising CJK glyph rendering skip cleanly rather than failing on an
    environment gap that isn't a code defect."""
    return font_status("cjk")["status"] == "available"
