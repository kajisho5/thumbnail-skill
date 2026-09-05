"""Environment diagnosis against the contract. Reports only what was detected on this machine, never
a guess: fonts and ffmpeg-skill are either found (with the exact path / version used) or reported
missing. `render`/`extract_frame` never silently substitute a different font or run without
ffmpeg-skill when a video_frame asset is used; `doctor` is how a caller finds out ahead of time."""
from __future__ import annotations

import platform
from typing import Any, Dict, List, Optional

import PIL

from . import DOCTOR_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FfmpegSkill
from .errors import ThumbnailError
from .fonts import FONT_REGISTRY, font_status
from .model import ELEMENT_TYPES, OUTPUT_FORMATS
from .security import PathPolicy

DOCTOR_SCHEMA_ID = f"{SKILL_ID}/doctor@{DOCTOR_SCHEMA_VERSION}"


def doctor_report(ffmpeg_skill_dir: Optional[str] = None, workspace: Optional[str] = None, allowed_input: Optional[List[str]] = None) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    problems: List[str] = []
    warnings: List[str] = []

    checks["python"] = {"status": "ok", "version": platform.python_version(), "implementation": platform.python_implementation(), "platform": platform.system()}
    checks["pillow"] = {"status": "ok", "version": str(getattr(PIL, "__version__", "unknown"))}

    fonts: Dict[str, Any] = {fid: font_status(fid) for fid in FONT_REGISTRY}
    checks["fonts"] = fonts
    missing_fonts = sorted(fid for fid, st in fonts.items() if st["status"] != "available")
    if missing_fonts:
        warnings.append(f"font_id(s) with no resolvable file on this machine: {missing_fonts} (text elements using them will fail MISSING_INPUT)")

    skill: Optional[FfmpegSkill] = None
    try:
        skill = FfmpegSkill.locate(ffmpeg_skill_dir)
        info = skill.info()
        checks["ffmpeg_skill"] = {"status": "ok" if info.supported else "fail", "directory": str(info.directory), "version": info.version,
                                  "contract_version": info.contract_version, "problems": info.problems,
                                  "role": "video_frame asset decoding only (ffmpeg-skill/probe, ffmpeg-skill/look); still-image and text rendering never need it"}
        problems += ["ffmpeg-skill: " + p for p in info.problems]
    except ThumbnailError as e:
        checks["ffmpeg_skill"] = {"status": "missing", "detail": e.message, "tried": e.details.get("tried"),
                                  "impact": "video_frame assets will fail TOOL_ERROR; still-image-only documents are unaffected"}
        warnings.append("ffmpeg-skill not found: video_frame assets are unavailable, still-image thumbnails are unaffected")

    checks["element_types"] = list(ELEMENT_TYPES)
    checks["output_formats"] = {f: {"status": "ok", "extensions": list(s["extensions"])} for f, s in OUTPUT_FORMATS.items()}

    try:
        policy = PathPolicy(workspace, allowed_input)
        checks["path_policy"] = policy.describe()
        checks["path_policy"]["status"] = "ok"
        checks["path_policy"]["cache_dir"] = str(policy.workspace / ".thumbnail-skill" / "cache")
    except ThumbnailError as e:
        checks["path_policy"] = {"status": "fail", "detail": e.message}
        problems.append("path policy: " + e.message)

    status = "fail" if problems else ("degraded" if warnings else "ok")
    return {"schema": DOCTOR_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "status": status, "ok": status != "fail",
            "checks": checks, "problems": problems, "warnings": warnings, "secrets_shown": False}


__all__ = ["DOCTOR_SCHEMA_ID", "doctor_report"]
