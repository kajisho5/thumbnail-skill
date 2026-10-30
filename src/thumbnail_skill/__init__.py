"""thumbnail-skill: deterministic thumbnail rendering execution Skill (not an AI agent).

It renders a typed, caller-supplied ThumbnailDocument (a canvas plus positioned image and text
elements, sourced from a still image or an explicit video frame) into a validated PNG/JPEG artifact
with provenance. It never decides what a thumbnail should show: no frame selection, no face
detection, no automatic layout, no title generation, no click-through prediction. Those decisions
belong to the caller (video-production-agent). Video frame decoding is delegated to ffmpeg-skill;
this skill never runs ffmpeg itself, never accepts a shell command, a filter string or raw argv."""

SKILL_ID = "thumbnail-skill"
PACKAGE_NAME = "thumbnail-skill"
def _version() -> str:
    """The package version, from one place: this checkout's pyproject.toml when running from source,
    otherwise the installed distribution's metadata. Releases bump only pyproject.toml."""
    import re as _re
    from pathlib import Path as _Path
    pyproject = _Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if _re.search(r'^name\s*=\s*"thumbnail-skill"', text, _re.M):
            found = _re.search(r'^version\s*=\s*"([^"]+)"', text, _re.M)
            if found:
                return found.group(1)
    import sys as _sys
    for base in _sys.path:
        for meta in sorted(_Path(base or ".").glob("thumbnail_skill-*.dist-info/METADATA")):
            found = _re.search(r"^Version: *(\S+)", meta.read_text(encoding="utf-8", errors="replace"), _re.M)
            if found:
                return found.group(1)
    return "0.0.0+unknown"


VERSION = _version()

CONTRACT_SCHEMA_VERSION = 1
REQUEST_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
DOCTOR_SCHEMA_VERSION = 1

__version__ = VERSION
__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION", "__version__",
           "CONTRACT_SCHEMA_VERSION", "REQUEST_SCHEMA_VERSION", "RESPONSE_SCHEMA_VERSION", "DOCTOR_SCHEMA_VERSION"]
