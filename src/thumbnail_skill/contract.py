"""Machine-readable Skill / Capability / Tool contract (`thumbnail skill --json`, alias `contract --json`).
Derived from the same tables the code runs on (model.py, fonts.py, errors.py); nothing here is
hand-maintained beside the implementation it describes."""
from __future__ import annotations

from typing import Any, Dict, List

from . import CONTRACT_SCHEMA_VERSION, DOCTOR_SCHEMA_VERSION, PACKAGE_NAME, REQUEST_SCHEMA_VERSION, RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .background import MAX_PROMPT_LENGTH, MAX_SEED, MAX_STEPS, PROVIDERS, SIZE_MULTIPLE, provider_ids
from .background import MAX_SIZE as BG_MAX_SIZE, MIN_SIZE as BG_MIN_SIZE
from .adapter import FLAGS_USED, OPTIONAL_FLAGS, SUPPORTED_CONTRACT_VERSION, TOOLS_USED
from .errors import ERROR_CODES, ERROR_TABLE, EXIT_CODES
from .executor import ENGINE_NAME, RESPONSE_SCHEMA_ID
from .fonts import FONT_REGISTRY, font_ids
from .model import (ASSET_KINDS, ELEMENT_TYPES, FIT_MODES, FORBIDDEN_KEYS, H_ALIGN, ID_RE, MAX_ASSETS, MAX_CANVAS, MAX_ELEMENTS, MAX_TEXT_LENGTH, MAX_TEXT_LINES,
                    MIN_CANVAS, OUTPUT_FORMATS, PLATFORM_PRESETS, REQUEST_SCHEMA_ID, ROTATIONS, V_ALIGN, _ratio_text)

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
    {"tool_id": "thumbnail/generate_background", "role": "execution",
     "description": "Optional (ADR-12): generate one background image from a caller-written prompt and an explicit seed with a named, locally "
                    "installed AI provider (FLUX.2 [klein] 4B), as a validated PNG/JPEG with provenance (engine, model, licence). Never decides what "
                    "to show and never composes: the result is an ordinary image asset for thumbnail/render. Refused (TOOL_ERROR, "
                    "reason provider_unavailable) when the provider is not configured or cannot run.",
     "input": {"background": {"provider": "provider id", "prompt": "string", "width": "int", "height": "int", "seed": "int",
                              "steps": "int (optional)", "guidance_scale": "number (optional)"},
               "output": "OutputSpec", "options": "RenderOptions"},
     "output": {"output": "path", "format": "png|jpeg", "width": "int", "height": "int", "size": "int", "sha256": "str", "reused": "bool (always false)",
                "provenance": "object (generated, provider, engine, model, licence, prompt, seed, steps, guidance_scale)"},
     "produces_output": True, "writes_media": True, "deterministic": False, "mutates_input": False, "ai": True, "optional": True,
     "requires": "a configured background provider (see background_providers); never downloads a model, opens no connection",
     "side_effects": ["writes the output file"]},
]

# Cross-repository Capability ids (kajisho5/AI-video-production-OS docs/SPEC.md
# `CapabilityContract.provides`), matching the ids proposed for this Skill in that project's
# own docs/CAPABILITY_MATRIX.md (as of this writing, on its unmerged `claude/ai-video-
# production-os-arch-fck6fy` branch, not yet on that repo's main -- hence lifecycle
# EXPERIMENTAL here too, not a claim of a finalized cross-repo contract). Only tools with
# produces_output True get one:
# a Capability id names something that produces an Artifact, and `thumbnail/validate`
# (produces_output False, a structural pre-flight check) does not.
CAPABILITY_IDS: Dict[str, str] = {
    "thumbnail/render": "thumbnail.render",
    "thumbnail/extract_frame": "thumbnail.extract_frame",
    "thumbnail/generate_background": "thumbnail.generate_background",
}


def capability_provides() -> List[Dict[str, str]]:
    return [{"id": CAPABILITY_IDS[t["tool_id"]], "lifecycle": "EXPERIMENTAL", "tool_id": t["tool_id"]}
            for t in TOOLS if t["tool_id"] in CAPABILITY_IDS]


# The AI Video Production OS registry's `dependencies` (registry/capability_contract.schema.json): ffmpeg-skill, as a
# range of its contract_version, in the shape audio-production-skill, color-grading-skill and motion-graphics-skill use.
# A lower bound only, derived from adapter.SUPPORTED_CONTRACT_VERSION -- the same constant `ffmpeg_skill.contract_version`
# below reports -- so the two can never say different things. Never compared at run time: compatibility is the flags the
# located checkout exposes (ADR-8). Not required: only a video_frame asset (render) or extract_frame needs it (ADR-11).
FFMPEG_SKILL_CONTRACT_RANGE = f">={SUPPORTED_CONTRACT_VERSION}"


def dependencies() -> List[Dict[str, Any]]:
    return [{"skill_id": "ffmpeg-skill", "version_range": FFMPEG_SKILL_CONTRACT_RANGE, "range_of": "contract_version", "required": False,
             "for": [t["tool_id"] for t in TOOLS if t.get("delegates_to")], "tools": [f"ffmpeg-skill/{t}" for t in TOOLS_USED]}]


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


def platform_presets() -> Dict[str, Any]:
    """`output.preset`, declared beside `output_formats` and derived from the same table the check runs on
    (model.PLATFORM_PRESETS). A new top-level key: nothing a consumer already pins changes shape."""
    return {
        "field": "output.preset", "tools": ["thumbnail/render", "thumbnail/extract_frame"],
        "precedence": "explicit document.canvas width+height win over the preset's size; with a preset both may be omitted (the preset's size is used); "
                      "giving only one of the two is INVALID_REQUEST. The preset's limits are checked either way.",
        "check": "after rendering, the written artifact's real dimensions (min/max width/height and aspect-ratio range), format and byte size are checked "
                 "against the preset before it is cached or placed at the output path; a cache hit is checked the same way",
        "failure": {"code": "VALIDATION_ERROR", "reason": "preset_limit_exceeded", "details": ["preset", "violations [{check, limit?, expected, got}]"]},
        "response": "on success, `preset` {name, platform, checked, ok} in the response and `preset` (name) in provenance; absent without a preset",
        "bytes": "max_bytes in bytes, decimal megabytes (1 MB = 1,000,000 bytes)",
        "presets": {name: {"platform": p["platform"], "surface": p["surface"], "width": p["width"], "height": p["height"],
                           "aspect_ratio": {"min": _ratio_text(p["aspect_ratio"]["min"]), "max": _ratio_text(p["aspect_ratio"]["max"])},
                           "min_width": p["min_width"], "min_height": p["min_height"], "max_width": p["max_width"], "max_height": p["max_height"],
                           "formats": list(p["formats"]), "max_bytes": p["max_bytes"], "unverified": list(p["unverified"]), "sources": list(p["sources"])}
                    for name, p in PLATFORM_PRESETS.items()},
    }


def background_providers() -> Dict[str, Any]:
    """The optional AI background providers (ADR-12): a new top-level key beside the core sections, which keep describing
    the deterministic render path (`execution.ai` false, `deterministic` true) exactly as consumers pin them."""
    return {
        "tool": "thumbnail/generate_background", "ai": True, "optional": True, "deterministic": False,
        "core_tools_use_ai": False,
        "core_tools": ["thumbnail/validate", "thumbnail/render", "thumbnail/extract_frame"],
        "without_a_provider": "render, extract_frame and validate are unchanged; doctor reports the provider not_configured without changing its status; "
                              "generate_background is refused (TOOL_ERROR, reason provider_unavailable, not retryable)",
        "configured_but_unavailable": "doctor status degraded (a warning names what is missing); generate_background refused the same way",
        "network": False, "downloads_models": False,
        "request_limits": {"prompt_chars": MAX_PROMPT_LENGTH, "width": f"{BG_MIN_SIZE}..{BG_MAX_SIZE}, a multiple of {SIZE_MULTIPLE}",
                           "height": f"{BG_MIN_SIZE}..{BG_MAX_SIZE}, a multiple of {SIZE_MULTIPLE}", "seed": f"0..{MAX_SEED}", "steps": f"1..{MAX_STEPS}",
                           "guidance_scale": "0..20"},
        "providers": {pid: PROVIDERS[pid]().describe() for pid in provider_ids()},
        "provenance": ["generated", "provider", "engine", "engine_version", "model", "licence", "licence_url", "model_card", "prompt", "prompt_sha256",
                       "seed", "steps", "guidance_scale", "width", "height", "output_hash"],
        "decision": "docs/decisions.md ADR-12",
    }


def skill_contract() -> Dict[str, Any]:
    return {
        "schema": CONTRACT_SCHEMA_ID, "skill_id": SKILL_ID, "id": SKILL_ID, "name": PACKAGE_NAME, "package": PACKAGE_NAME, "version": VERSION,
        "contract_version": str(CONTRACT_SCHEMA_VERSION),
        "kind": "execution", "role": "thumbnail rendering (deterministic execution); not decision, not selection, not design",
        "description": "Renders a typed ThumbnailDocument (canvas + still-image/video-frame assets + positioned image/text elements) into a "
                       "validated PNG/JPEG with provenance. Never decides what a thumbnail should show: no best-frame search, no face detection, "
                       "no OCR, no automatic layout or title generation, no click-through prediction, no A/B or publish decisions. Not an AI agent.",
        "repository": "https://github.com/kajisho5/thumbnail-skill",
        "not_provided": ["AI reasoning", "decisions", "production plans", "automatic frame selection", "face/object detection", "OCR",
                         "automatic title or layout generation", "click-through-rate prediction", "A/B testing", "publish decisions",
                         "video editing", "color grading", "subtitle rendering", "arbitrary ffmpeg filters", "shell execution", "network access"],
        "tools": [dict(t) for t in TOOLS],
        "provides": capability_provides(),
        "dependencies": dependencies(),
        "document": {"schema": REQUEST_SCHEMA_ID, "id_pattern": ID_RE.pattern, "forbidden_fields": sorted(FORBIDDEN_KEYS),
                     "canvas": {"width": f"{MIN_CANVAS}..{MAX_CANVAS}", "height": f"{MIN_CANVAS}..{MAX_CANVAS}", "background": "#RRGGBB(AA), default #000000"},
                     "assets": {"kinds": list(ASSET_KINDS), "max_assets": MAX_ASSETS,
                                "video_frame": "explicit timestamp only; this skill never searches for a 'best frame'"},
                     "elements": {"types": list(ELEMENT_TYPES), "max_elements": MAX_ELEMENTS, "stacking": "z_index, ties broken by document order", **element_specs()}},
        "output_formats": {f: {"extensions": list(s["extensions"]), "lossless": s["lossless"]} for f, s in OUTPUT_FORMATS.items()},
        "platform_presets": platform_presets(),
        "background_providers": background_providers(),
        "fonts": {"font_ids": font_ids(), "resolution": "font_id -> the first existing file among this skill's registered per-platform candidate paths "
                  "(fonts.py); never an arbitrary caller-supplied path; a font_id with no resolvable file fails MISSING_INPUT, never a silent substitution",
                  "registry": {fid: {"display_name": e["display_name"], "role": e["role"]} for fid, e in FONT_REGISTRY.items()}},
        "rendering": {"engine": ENGINE_NAME, "video_frame_decoding": "delegated to ffmpeg-skill (probe, look --at --no-timecode); this skill never runs ffmpeg itself",
                      "compositing": "Pillow raster operations only (paste/crop/resize/draw); no filter string, no shell, no arbitrary code path"},
        "execution": {"mode": "local", "canonical_invocation": ["thumbnail", "run", "-", "--json"], "stdin": "{\"tool\": <tool_id>, \"params\": {...}}",
                      "stdout": f"exactly one {RESPONSE_SCHEMA_ID} document (through run -) or {{'ok', 'tool', 'result'}}",
                      "shell": False, "arbitrary_executables": False, "arbitrary_filters": False, "network": False, "input_mutation": False, "ai": False,
                      "forbidden_request_fields": sorted(FORBIDDEN_KEYS)},
        "ffmpeg_skill": {"contract_version": SUPPORTED_CONTRACT_VERSION, "tools_used": list(TOOLS_USED),
                         "compatibility": "any version whose probe/look expose flags_required (detected from its contract or --help; no version window)",
                         "flags_required": {t: list(f) for t, f in FLAGS_USED.items()},
                         "flags_optional": {t: list(f) for t, f in OPTIONAL_FLAGS.items()}},
        "schema_versions": {"contract": str(CONTRACT_SCHEMA_VERSION), "request": str(REQUEST_SCHEMA_VERSION), "response": str(RESPONSE_SCHEMA_VERSION), "doctor": str(DOCTOR_SCHEMA_VERSION)},
        "errors": {"codes": list(ERROR_CODES), "exit_codes": dict(EXIT_CODES), "retryable": {c: ERROR_TABLE[c][1] for c in ERROR_CODES}, "success_exit_code": 0},
        "provenance": {"per_output": ["skill", "skill_version", "operation", "engine", "engine_version", "identity", "reused", "assets (sha256, timestamp for video_frame)",
                                      "fonts (font_id, path, sha256)", "output_hash"],
                       "identity": "sha256 over canonical JSON of {document, resolved asset content identity, resolved font content identity, skill/engine version}; "
                                   "a video_frame's identity is its source video's sha256 + timestamp, not the decoded frame's bytes"},
        "deterministic": True,
    }


__all__ = ["CONTRACT_SCHEMA_ID", "TOOLS", "FFMPEG_SKILL_CONTRACT_RANGE", "skill_contract", "element_specs", "capability_provides", "dependencies", "platform_presets",
           "background_providers"]
