"""Machine-readable Skill / Capability / Tool contract (`thumbnail skill --json`, alias `contract --json`).
Derived from the same tables the code runs on (model.py, fonts.py, errors.py); nothing here is
hand-maintained beside the implementation it describes."""
from __future__ import annotations

from typing import Any, Dict, List

from . import CONTRACT_SCHEMA_VERSION, DOCTOR_SCHEMA_VERSION, PACKAGE_NAME, REQUEST_SCHEMA_VERSION, RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import SUPPORTED_CONTRACT_VERSION, TOOLS_USED
from .errors import ERROR_CODES, ERROR_TABLE, EXIT_CODES
from .executor import ENGINE_NAME, RESPONSE_SCHEMA_ID
from .fonts import FONT_REGISTRY, font_ids
from .model import (ASSET_KINDS, ELEMENT_TYPES, FIT_MODES, FORBIDDEN_KEYS, H_ALIGN, ID_RE, MAX_ASSETS, MAX_CANVAS, MAX_ELEMENTS, MAX_TEXT_LENGTH, MAX_TEXT_LINES,
                    MIN_CANVAS, OUTPUT_FORMATS, REQUEST_SCHEMA_ID, ROTATIONS, V_ALIGN)

CONTRACT_SCHEMA_ID = f"{SKILL_ID}/contract@{CONTRACT_SCHEMA_VERSION}"

TOOLS: List[Dict[str, Any]] = [
    {"tool_id": "thumbnail/validate", "role": "validation",
     "description": "Validate a ThumbnailDocument (canvas + assets + elements) structurally. Touches no file system, no font, no ffmpeg-skill; renders nothing.",
     "input": {"document": "ThumbnailDocument"}, "output": {"ok": "bool", "document_id": "str", "canvas": "ThumbnailCanvas", "assets": "int", "elements": "int"},
     "produces_output": False, "writes_media": False, "deterministic": True, "mutates_input": False, "side_effects": []},
    {"tool_id": "thumbnail/render", "role": "execution",
     "description": "Render a typed ThumbnailDocument (still image and/or an explicit video frame, positioned image and text elements) into a validated PNG/JPEG artifact with provenance. Never decides what to show: frame timestamps, layout, text and fonts are exactly what the caller specified.",
     "input": {"document": "ThumbnailDocument", "output": "OutputSpec", "options": "RenderOptions"},
     "output": {"output": "path", "format": "png|jpeg", "width": "int", "height": "int", "size": "int", "sha256": "str", "reused": "bool", "provenance": "object"},
     "produces_output": True, "writes_media": True, "deterministic": True, "mutates_input": False,
     "idempotency_hint": "same document + same asset/font content + same skill/engine version reuses the cached artifact",
     "delegates_to": ["ffmpeg-skill/probe", "ffmpeg-skill/look"], "side_effects": ["writes the output file", "writes a reuse cache entry under the workspace"]},
    {"tool_id": "thumbnail/extract_frame", "role": "execution",
     "description": "Extract exactly the video frame at a caller-given timestamp as a PNG/JPEG. No scene detection, no scoring, no 'best frame' search: one timestamp in, one frame out.",
     "input": {"source": {"path": "file", "timestamp": "seconds >= 0"}, "output": "OutputSpec", "options": "RenderOptions"},
     "output": {"output": "path", "format": "png|jpeg", "width": "int", "height": "int", "size": "int", "sha256": "str", "reused": "bool", "provenance": "object"},
     "produces_output": True, "writes_media": True, "deterministic": True, "mutates_input": False,
     "idempotency_hint": "same source content + same timestamp + same skill/engine version reuses the cached artifact",
     "delegates_to": ["ffmpeg-skill/probe", "ffmpeg-skill/look"], "side_effects": ["writes the output file", "writes a reuse cache entry under the workspace"]},
]


def element_specs() -> Dict[str, Any]:
    return {
        "image": {"description": "A still image or an extracted video frame, placed and sized on the canvas.",
                  "fields": {"asset_id": "ref to a declared asset", "position": {"x": "number", "y": "number"}, "size": {"width": "int", "height": "int"},
                             "fit": list(FIT_MODES), "crop": {"x": "int", "y": "int", "width": "int", "height": "int (optional; source-pixel space)"},
                             "opacity": "0..1", "rotation": list(ROTATIONS)}},
        "text": {"description": "Literal text (explicit '\\n' for line breaks; no automatic word-wrap or layout decisions).",
                  "fields": {"text": f"string, max {MAX_TEXT_LENGTH} chars, max {MAX_TEXT_LINES} lines", "font_id": f"one of {font_ids()}", "font_size": "6..400",
                             "color": "#RRGGBB(AA)", "position": {"x": "number", "y": "number"},
                             "align": {"horizontal": list(H_ALIGN), "vertical": list(V_ALIGN)}, "line_spacing": "0.5..5.0", "opacity": "0..1",
                             "background": {"color": "#RRGGBB(AA)", "padding": "0..200 (optional)"},
                             "stroke": {"color": "#RRGGBB(AA)", "width": "0..40 (optional)"},
                             "shadow": {"color": "#RRGGBB(AA)", "offset_x": "-200..200", "offset_y": "-200..200 (optional)"}}},
    }


def skill_contract() -> Dict[str, Any]:
    return {
        "schema": CONTRACT_SCHEMA_ID, "skill_id": SKILL_ID, "id": SKILL_ID, "name": PACKAGE_NAME, "package": PACKAGE_NAME, "version": VERSION,
        "kind": "execution", "role": "thumbnail rendering (deterministic execution); not decision, not selection, not design",
        "description": "Renders a typed ThumbnailDocument (canvas + still-image/video-frame assets + positioned image/text elements) into a "
                       "validated PNG/JPEG with provenance. Never decides what a thumbnail should show: no best-frame search, no face detection, "
                       "no OCR, no automatic layout or title generation, no click-through prediction, no A/B or publish decisions. Not an AI agent.",
        "repository": "https://github.com/kajisho5/thumbnail-skill",
        "not_provided": ["AI reasoning", "decisions", "production plans", "automatic frame selection", "face/object detection", "OCR",
                         "automatic title or layout generation", "click-through-rate prediction", "A/B testing", "publish decisions",
                         "video editing", "color grading", "subtitle rendering", "arbitrary ffmpeg filters", "shell execution", "network access"],
        "tools": [dict(t) for t in TOOLS],
        "document": {"schema": REQUEST_SCHEMA_ID, "id_pattern": ID_RE.pattern, "forbidden_fields": sorted(FORBIDDEN_KEYS),
                     "canvas": {"width": f"{MIN_CANVAS}..{MAX_CANVAS}", "height": f"{MIN_CANVAS}..{MAX_CANVAS}", "background": "#RRGGBB(AA), default #000000"},
                     "assets": {"kinds": list(ASSET_KINDS), "max_assets": MAX_ASSETS,
                                "video_frame": "explicit timestamp only; this skill never searches for a 'best frame'"},
                     "elements": {"types": list(ELEMENT_TYPES), "max_elements": MAX_ELEMENTS, "stacking": "z_index, ties broken by document order", **element_specs()}},
        "output_formats": {f: {"extensions": list(s["extensions"]), "lossless": s["lossless"]} for f, s in OUTPUT_FORMATS.items()},
        "fonts": {"font_ids": font_ids(), "resolution": "font_id -> the first existing file among this skill's registered per-platform candidate paths "
                  "(fonts.py); never an arbitrary caller-supplied path; a font_id with no resolvable file fails MISSING_INPUT, never a silent substitution",
                  "registry": {fid: {"display_name": e["display_name"], "role": e["role"]} for fid, e in FONT_REGISTRY.items()}},
        "rendering": {"engine": ENGINE_NAME, "video_frame_decoding": "delegated to ffmpeg-skill (probe, look --at --no-timecode); this skill never runs ffmpeg itself",
                      "compositing": "Pillow raster operations only (paste/crop/resize/draw); no filter string, no shell, no arbitrary code path"},
        "execution": {"mode": "local", "canonical_invocation": ["thumbnail", "run", "-", "--json"], "stdin": "{\"tool\": <tool_id>, \"params\": {...}}",
                      "stdout": f"exactly one {RESPONSE_SCHEMA_ID} document (through run -) or {{'ok', 'tool', 'result'}}",
                      "shell": False, "arbitrary_executables": False, "arbitrary_filters": False, "network": False, "input_mutation": False, "ai": False,
                      "forbidden_request_fields": sorted(FORBIDDEN_KEYS)},
        "ffmpeg_skill": {"contract_version": SUPPORTED_CONTRACT_VERSION, "tools_used": list(TOOLS_USED)},
        "schema_versions": {"contract": str(CONTRACT_SCHEMA_VERSION), "request": str(REQUEST_SCHEMA_VERSION), "response": str(RESPONSE_SCHEMA_VERSION), "doctor": str(DOCTOR_SCHEMA_VERSION)},
        "errors": {"codes": list(ERROR_CODES), "exit_codes": dict(EXIT_CODES), "retryable": {c: ERROR_TABLE[c][1] for c in ERROR_CODES}, "success_exit_code": 0},
        "provenance": {"per_output": ["skill", "skill_version", "operation", "engine", "engine_version", "identity", "reused", "assets (sha256, timestamp for video_frame)",
                                      "fonts (font_id, path, sha256)", "output_hash"],
                       "identity": "sha256 over canonical JSON of {document, resolved asset content identity, resolved font content identity, skill/engine version}; "
                                   "a video_frame's identity is its source video's sha256 + timestamp, not the decoded frame's bytes"},
        "deterministic": True,
    }


__all__ = ["CONTRACT_SCHEMA_ID", "TOOLS", "skill_contract", "element_specs"]
