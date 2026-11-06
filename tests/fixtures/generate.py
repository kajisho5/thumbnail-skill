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


SESSIONS = [
    # (title, speaker, session); row 4 has a long title that only fits once shrunk
    ("Opening Keynote", "Ada Lovelace", "K1"),
    ("Scaling Live Captions", "Grace Hopper", "A2"),
    ("Colour Pipelines That Hold Up", "Hedy Lamarr", "A3"),
    ("What Twelve Years of Conference Recordings Taught Us About Audio Levels", "Alan Turing", "B1"),
    ("Edge Encoding", "Katherine Johnson", "B2"),
    ("Thumbnails at Scale", "Claude Shannon", "B3"),
    ("Accessible Video", "Margaret Hamilton", "C1"),
    ("Panel: The Next Five Years", "Tim Berners-Lee", "C2"),
    ("Q&A", "Barbara Liskov", "C3"),
    ("Closing Remarks", "Donald Knuth", "K2"),
]


def build_batch_fixture(directory: Path) -> Dict[str, Path]:
    """sessions.csv (title, speaker, photo, session; 10 rows, UTF-8) and one 400x400 speaker photo per row,
    each a different solid colour so a mix-up between rows would show in the output."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    lines = ["title,speaker,photo,session"]
    for i, (title, speaker, session) in enumerate(SESSIONS):
        photo = d / f"speaker_{i + 1:02d}.png"
        Image.new("RGB", (400, 400), (40 + 20 * i, 200 - 15 * i, 90 + 10 * i)).save(photo)
        lines.append(",".join([f'"{title}"', f'"{speaker}"', str(photo).replace("\\", "/"), session]))
    csv_path = d / "sessions.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": csv_path, "dir": d}


def face_detections(frames, frame_size=(1280, 720), fingerprint="sha256:" + "0" * 64):
    """A detection-skill/detections@1 face document in the shape `detection faces --attributes` writes (its
    docs/detections.md and schemas/detections.schema.json): one entry per sampled frame, `[x, y, w, h]` boxes in
    source pixels, five landmarks, and per-face attributes (size, yaw/roll, frontal, sharpness, eyes_open).
    `frames` is [(t, [(box, eyes_open, sharpness), ...]), ...]; nothing here runs a detector."""
    w, h = frame_size
    out = []
    for i, (t, faces) in enumerate(frames):
        dets = []
        for box, eyes, sharp in faces:
            x, y, bw, bh = box
            dets.append({"t": t, "kind": "face", "box": [float(x), float(y), float(bw), float(bh)],
                         "box_norm": [round(x / w, 5), round(y / h, 5), round(bw / w, 5), round(bh / h, 5)], "score": 0.93, "label": None,
                         "landmarks": [[x + 0.3 * bw, y + 0.4 * bh], [x + 0.7 * bw, y + 0.4 * bh], [x + 0.5 * bw, y + 0.6 * bh],
                                       [x + 0.35 * bw, y + 0.8 * bh], [x + 0.65 * bw, y + 0.8 * bh]],
                         "attributes": {"size": round(bh / h, 4), "yaw_deg": 4.0, "roll_deg": 1.0, "frontal": 0.998, "sharpness": sharp,
                                        "mouth_contrast": 0.3, "eyes_open": eyes},
                         "engine": "yunet", "model": "face_detection_yunet_2023mar.onnx", "model_sha256": "1" * 64})
        out.append({"index": i, "t": t, "detections": dets})
    return {"schema": "detection-skill/detections@1", "kind": "face",
            "source": {"filename": "talk.mp4", "fingerprint": fingerprint, "size_bytes": 1, "media_type": "video", "duration": 10.0,
                       "width": w, "height": h, "fps": 25.0},
            "frame_size": {"width": w, "height": h},
            "sampling": {"method": "at", "every": None, "times": [f[0] for f in frames], "scene_points": [], "frames_tool": "ffmpeg-skill/look"},
            "engine": {"id": "yunet", "kind": "face"}, "parameters": {"min_score": 0.6}, "frames": out,
            "counts": {"frames": len(out), "frames_with_detections": sum(1 for f in out if f["detections"]), "detections": sum(len(f["detections"]) for f in out)},
            "skill": "detection-skill", "skill_version": "0.3.0", "tool": "detection/faces", "created_at": "2026-11-06T00:00:00Z",
            "processing_seconds": 0.1, "warnings": []}


def build_all(directory: Path) -> Dict[str, Path]:
    files = build_images(directory)
    if ffmpeg_available():
        files["video"] = build_video(directory)
        # a second, content-different video (a plain colour source, not the moving test pattern) to
        # prove asset identity is keyed by content, not just by which asset_id/path was used
        files["video2"] = build_video(directory, name="video2.mp4", source="color=c=blue:size=320x180:rate=10")
    return files
