"""thumbnail-skill: deterministic thumbnail rendering execution Skill (not an AI agent).

It renders a typed, caller-supplied ThumbnailDocument (a canvas plus positioned image and text
elements, sourced from a still image or an explicit video frame) into a validated PNG/JPEG artifact
with provenance. It never decides what a thumbnail should show: no frame selection, no face
detection, no automatic layout, no title generation, no click-through prediction. Those decisions
belong to the caller (video-production-agent). Video frame decoding is delegated to ffmpeg-skill;
this skill never runs ffmpeg itself, never accepts a shell command, a filter string or raw argv."""

SKILL_ID = "thumbnail-skill"
PACKAGE_NAME = "thumbnail-skill"
VERSION = "0.1.0"

CONTRACT_SCHEMA_VERSION = 1
REQUEST_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
DOCTOR_SCHEMA_VERSION = 1

__version__ = VERSION
__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION", "__version__",
           "CONTRACT_SCHEMA_VERSION", "REQUEST_SCHEMA_VERSION", "RESPONSE_SCHEMA_VERSION", "DOCTOR_SCHEMA_VERSION"]
