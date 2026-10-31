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
    """The package version, from one place: pyproject.toml (the only file the release workflow bumps -- ADR-11).

    From a source checkout (including an editable install, whose recorded metadata goes stale on the next release
    bump) it is read straight from this checkout's pyproject.toml; from an installed wheel, from the distribution
    metadata (importlib.metadata) that was built from that same pyproject.toml. If neither is found it reports
    0.0.0+unknown rather than a plausible but wrong number."""
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if re.search(r'^name\s*=\s*"thumbnail-skill"\s*$', text, re.M):
            found = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, re.M)
            if found:
                return found.group(1)
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python < 3.8 is not supported
        return "0.0.0+unknown"
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.0.0+unknown"


VERSION = _version()

CONTRACT_SCHEMA_VERSION = 1
REQUEST_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
DOCTOR_SCHEMA_VERSION = 1

__version__ = VERSION
__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION", "__version__",
           "CONTRACT_SCHEMA_VERSION", "REQUEST_SCHEMA_VERSION", "RESPONSE_SCHEMA_VERSION", "DOCTOR_SCHEMA_VERSION"]
