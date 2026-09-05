"""Test fixtures generated at test time (nothing binary is committed).

  bg.jpg / bg.png   640x360 solid-colour still images (JPEG has no alpha, PNG does)
  logo.png          200x200 RGBA image, used as an overlay
  tall.png          100x400 image (aspect differs from its target box, exercises fit modes)
  video.mp4         3s 320x180 H.264 video with a visibly different colour per second, generated
                    with ffmpeg only if `ffmpeg`/`ffprobe` are on PATH (see `available()`)
  not_image.txt     not media, used for "invalid image" cases"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict

from PIL import Image

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def build_images(directory: Path) -> Dict[str, Path]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    f = {k: d / v for k, v in {"bg_jpg": "bg.jpg", "bg_png": "bg.png", "logo": "logo.png", "tall": "tall.png", "not_image": "not_image.txt"}.items()}
    Image.new("RGB", (640, 360), (30, 60, 90)).save(f["bg_jpg"], quality=90)
    Image.new("RGBA", (640, 360), (200, 220, 240, 255)).save(f["bg_png"])
    Image.new("RGBA", (200, 200), (220, 30, 30, 255)).save(f["logo"])
    Image.new("RGBA", (100, 400), (30, 200, 60, 255)).save(f["tall"])
    f["not_image"].write_text("not media\n", encoding="utf-8")
    return f


def build_video(directory: Path, name: str = "video.mp4", source: str = "testsrc2=size=320x180:rate=10") -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    subprocess.run(FF + ["-f", "lavfi", "-i", source, "-t", "3",
                         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return path


def build_all(directory: Path) -> Dict[str, Path]:
    files = build_images(directory)
    if ffmpeg_available():
        files["video"] = build_video(directory)
        # a second, content-different video (a plain colour source, not the moving test pattern) to
        # prove asset identity is keyed by content, not just by which asset_id/path was used
        files["video2"] = build_video(directory, name="video2.mp4", source="color=c=blue:size=320x180:rate=10")
    return files
