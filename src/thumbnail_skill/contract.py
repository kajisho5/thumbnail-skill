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
    {"tool_id": "thumbnail/render_batch", "role": "execution",
     "description": "Render one thumbnail per row of a table (a CSV file or inline rows) and per platform target, from one ThumbnailDocument "
                    "template per target with {{column}} placeholders. Named text elements are shrunk to fit a caller-given box, down to a stated "
                    "minimum font size, never cut or wrapped; a row that does not fit is refused. Every row is checked before anything is written; "
                    "each file then goes through thumbnail/render, with its output check and its output.preset check.",
     "input": {"csv": {"path": "file", "encoding": "utf-8 (default) | cp932", "delimiter": "',' (default) | ';' | tab"}, "rows": "[{column: string}] (instead of csv)",
               "targets": "[{preset, document: ThumbnailDocument template, text_fit?: {element_id: {max_width, max_height?, min_font_size}}}]",
               "output": {"directory": "path", "name": "template, default {{row}}-{{preset}}", "format": "png|jpeg", "overwrite": "bool", "jpeg_quality": "int (jpeg)"},
               "options": "RenderOptions"},
     "output": {"items": "[{row, preset, output, format, width, height, size, sha256, reused, preset_check, fitted}]", "count": "int", "rows": "int",
                "targets": "[preset]", "provenance": "object"},
     "produces_output": True, "writes_media": True, "deterministic": True, "mutates_input": False,
     "idempotency_hint": "each file is a thumbnail/render and is reused from its cache the same way",
     "delegates_to": ["ffmpeg-skill/probe", "ffmpeg-skill/look"], "side_effects": ["writes one output file per row and target", "writes reuse cache entries under the workspace"]},
    {"tool_id": "thumbnail/render_variants", "role": "execution",
     "description": "Write N documented variants of one thumbnail for a platform A/B test (CLI: thumbnail render --variants N). Variant k takes "
                    "entry (k-1) mod n of each axis: the text element's layout, its palette, and the frame one image element shows; entry 0 of "
                    "each axis is the document as given, so variant 1 is the original. Each variant is a thumbnail/render and is checked at "
                    "phone size: its text height at phone_width px wide (default 320) and its contrast through qc-skill's text legibility. A "
                    "variant below a minimum is reported (flagged), never dropped. Never picks a winner.",
     "input": {"document": "ThumbnailDocument", "output": "OutputSpec (variants are written as <stem>-v1..vN)", "options": "RenderOptions",
               "variants": {"count": "1..12", "text_element": "element_id (default: the first text element)", "layouts": "[{name, position, align?, font_size?}] (optional)",
                            "palettes": "[{name, color, background?, stroke?}] (optional)", "frame_element": "element_id", "frames": "[{name, asset}] (optional)",
                            "phone_width": "px (default 320)", "min_text_height_px": "px at phone_width (default 12)", "min_contrast_ratio": "default 4.5",
                            "qc_skill": "checkout directory (optional)"}},
     "output": {"variants": "[{variant, output, choices, description, small_size {status, ok, texts [{element_id, text_height_px_at_phone, contrast_ratio, findings}]}}]",
                "flagged": "[variant]", "axes": "object", "provenance": "object"},
     "produces_output": True, "writes_media": True, "deterministic": True, "mutates_input": False,
     "idempotency_hint": "each variant is a thumbnail/render and is reused from its cache the same way",
     "delegates_to": ["ffmpeg-skill/probe", "ffmpeg-skill/look", "qc-skill/run"],
     "side_effects": ["writes one output file per variant", "writes reuse cache entries under the workspace"]},
    {"tool_id": "thumbnail/choose_frame", "role": "ranking",
     "description": "Rank the sampled frames of a detection-skill/detections@1 face document (detection faces --attributes) by a stated, weighted "
                    "formula over measured facts: eyes open, frontal angle and face size from detection-skill, sharpness from the caller's frame "
                    "measures or else detection-skill's face sharpness, exposure from the caller's frame measures. Frames whose main face has its "
                    "eyes closed come after every frame where they are open. Every part and every missing part is listed. Detects nothing and "
                    "reads no pixels; the ranking suggests and the caller decides.",
     "input": {"detections": "detection-skill/detections@1 (kind face, with attributes)", "detections_path": "file (instead of detections)",
               "video": "file (optional; checked against detections.source.fingerprint)", "measures": "[{t, sharpness?, mean_luma?}] (optional)",
               "weights": "{eyes_open, sharpness, exposure, frontal, size} (optional)", "eyes_closed_below": "0..1 (optional, default 0.5)"},
     "output": {"ranking": "[{rank, index, t, score, parts, missing, sources, eyes_closed, faces, main_face, main_face_box}]", "chosen": "the first ranked frame",
                "asset": "{kind: video_frame, path, timestamp} when video is given", "method": "object"},
     "produces_output": False, "writes_media": False, "deterministic": True, "mutates_input": False, "side_effects": []},
    {"tool_id": "thumbnail/place_text", "role": "layout",
     "description": "Move one text element of a ThumbnailDocument to the first candidate position (caller-given, or eight edge and corner "
                    "positions) whose painted box stays on the canvas and clears every face box by a margin. Face boxes come from "
                    "detection-skill and are mapped onto the canvas through the image element's crop, fit and position. Refused when no "
                    "candidate is clear; the text is never placed over a face. Returns the updated document; renders nothing.",
     "input": {"document": "ThumbnailDocument", "text_element": "element_id", "image_element": "element_id", "faces": "[[x, y, w, h]] in frame_size pixels",
               "frame_size": "{width, height} (required for a video_frame asset)", "candidates": "[{name?, position, align?}] (optional)", "margin": "px (default 16)"},
     "output": {"document": "ThumbnailDocument", "placement": "{chosen, position, align, text_box, face_boxes, margin, tried}"},
     "produces_output": False, "writes_media": False, "deterministic": True, "mutates_input": False, "side_effects": []},
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
    "thumbnail/render_batch": "thumbnail.render_batch",
    "thumbnail/render_variants": "thumbnail.render_variants",
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


# qc-skill (ADR-16): only render_variants' contrast check uses it, and only when it is found; without it the contrast is
# reported as not measured. A lower bound of its contract_version ("1" in qc-skill 1.0.0), never compared at run time.
QC_SKILL_CONTRACT_RANGE = ">=1"


def dependencies() -> List[Dict[str, Any]]:
    return [{"skill_id": "ffmpeg-skill", "version_range": FFMPEG_SKILL_CONTRACT_RANGE, "range_of": "contract_version", "required": False,
             "for": [t["tool_id"] for t in TOOLS if any(d.startswith("ffmpeg-skill/") for d in t.get("delegates_to") or [])],
             "tools": [f"ffmpeg-skill/{t}" for t in TOOLS_USED]},
            {"skill_id": "qc-skill", "version_range": QC_SKILL_CONTRACT_RANGE, "range_of": "contract_version", "required": False,
             "for": [t["tool_id"] for t in TOOLS if "qc-skill/run" in (t.get("delegates_to") or [])], "tools": ["qc-skill/run"],
             "uses": "video.text_regions (text legibility, its ADR-018) on a written thumbnail"}]


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


def variants() -> Dict[str, Any]:
    """`thumbnail/render_variants` (ADR-16): a new top-level key."""
    from .variants import DEFAULT_PALETTES, MAX_VARIANTS, MIN_CONTRAST_RATIO, MIN_TEXT_HEIGHT_PX, PHONE_WIDTH
    return {
        "tool": "thumbnail/render_variants", "cli": "thumbnail render request.json --variants N --json", "max_variants": MAX_VARIANTS,
        "axes": {"layout": "the text element's position and alignment (and font_size when an entry gives one); default: eight edge and corner positions",
                 "palette": "the text element's colour, background band and stroke; default: " + ", ".join(p["name"] for p in DEFAULT_PALETTES),
                 "frame": "the asset one image element shows (variants.frames + variants.frame_element); default: none"},
        "rule": "variant k takes entry (k-1) mod n of each axis; entry 0 of each axis is the document as given ('as-given'), so variant 1 is the original",
        "files": "<output stem>-v<k><ext> beside output.path; output.preset is checked on every variant",
        "small_size_check": {
            "phone_width": PHONE_WIDTH,
            "text_height": "per text element: the tallest run of pixel rows containing its own glyph ink (fill only, alpha >= 128), drawn alone with the "
                           "renderer's layout, times phone_width / canvas width",
            "min_text_height_px": MIN_TEXT_HEIGHT_PX, "min_text_height_note": "this skill's default floor at phone_width, a heuristic, not a platform figure",
            "contrast": "qc-skill video.text_regions on the written variant at the element's painted box (WCAG 2.x contrast ratio, qc-skill ADR-018)",
            "min_contrast_ratio": MIN_CONTRAST_RATIO, "without_qc_skill": "contrast_unmeasured (status unknown) and a warning; the height check still runs",
            "below_a_minimum": "reported: small_size.status fail, findings text_too_small / low_contrast, the variant listed in flagged; never dropped",
        },
        "not_provided": ["picking a winner", "click-through prediction", "running the A/B test"],
        "decision": "docs/decisions.md ADR-16",
    }


def frame_choice() -> Dict[str, Any]:
    """`thumbnail/choose_frame` and `thumbnail/place_text` (ADR-15): a new top-level key."""
    from .frames import DEFAULT_MARGIN, DETECTIONS_SCHEMA, EYES_CLOSED_BELOW, SHARPNESS_HALF, SIZE_FULL, WEIGHTS
    return {
        "tools": ["thumbnail/choose_frame", "thumbnail/place_text"],
        "inputs": {"faces": f"{DETECTIONS_SCHEMA} from detection-skill (detection faces --attributes): boxes, eyes_open, frontal, size, sharpness",
                   "frame_measures": "caller-supplied [{t, sharpness, mean_luma}]; media-analysis-skill does not publish frame sharpness or exposure yet"},
        "score": {"weights": dict(WEIGHTS), "sharpness": f"s / (s + {SHARPNESS_HALF:g})", "exposure": "1 - 2 * |mean_luma - 0.5|",
                  "size": f"min(1, size / {SIZE_FULL})", "missing_part": "counts 0, listed in missing",
                  "eyes_closed_below": EYES_CLOSED_BELOW, "order": "eyes open first, then score, then input order", "main_face": "largest box"},
        "placement": {"default_candidates": ["bottom-center", "top-center", "bottom-left", "bottom-right", "top-left", "top-right", "middle-left", "middle-right"],
                      "default_margin_px": DEFAULT_MARGIN, "text_box": "layout block, background and ink with stroke and shadow (renderer.text_box)",
                      "face_mapping": "through the image element's crop, fit (cover, contain, fill, none) and position; rotation 0 only",
                      "none_clear": "VALIDATION_ERROR (reason no_position_clear_of_faces), every tried position listed"},
        "heuristic": True, "detects": False, "reads_pixels": False,
        "decision": "docs/decisions.md ADR-15",
    }


def batch() -> Dict[str, Any]:
    """`thumbnail/render_batch` (ADR-14): a new top-level key; nothing a consumer already pins changes shape."""
    from .batch import BUILTIN_FIELDS, CSV_DELIMITERS, CSV_ENCODINGS, DEFAULT_NAME, MAX_CELL_LENGTH, MAX_CSV_BYTES, MAX_ROWS, MAX_TARGETS, NAME_RE
    return {
        "tool": "thumbnail/render_batch",
        "placeholders": {"syntax": "{{column}}", "replaced_in": ["document.document_id", "document.assets[].path", "document.elements[].text.text", "output.name"],
                         "built_in": list(BUILTIN_FIELDS), "unknown": "INVALID_REQUEST (reason unknown_placeholder)",
                         "values": "inserted literally, never re-scanned; relative asset paths resolve against the workspace, as in thumbnail/render"},
        "csv": {"header": "first row, unique non-empty names", "encodings": list(CSV_ENCODINGS), "default_encoding": "utf-8",
                "encoding_note": "utf-8 accepts a leading byte-order mark; cp932 is the opt-in choice for a CSV saved by Japanese Excel",
                "delimiters": list(CSV_DELIMITERS), "max_bytes": MAX_CSV_BYTES, "blank_lines": "skipped", "field_count": "must equal the header's"},
        "limits": {"rows": MAX_ROWS, "targets": MAX_TARGETS, "cell_chars": MAX_CELL_LENGTH},
        "targets": "one per output.preset; the template's canvas may omit width/height (the preset's size is used); 16:9 and 9:16 are two targets "
                   "(for example youtube and youtube-shorts), each with its own layout",
        "text_fit": {"rule": "the largest font size from the template's own size down to min_font_size at which the element's painted box (layout block, "
                             "background, ink with stroke and shadow) fits max_width x max_height and lies inside the canvas",
                     "never": ["cut", "wrap", "enlarge"], "does_not_fit": "VALIDATION_ERROR (reason text_does_not_fit), before anything is written",
                     "outside_canvas": "VALIDATION_ERROR (reason text_outside_canvas)"},
        "output_name": {"default": DEFAULT_NAME, "pattern": NAME_RE.pattern, "duplicates": "INVALID_REQUEST (reason duplicate_name)"},
        "order": "every row and target is planned and checked first (structure, placeholders, fonts, text fit, input files, output names); then each is "
                 "rendered with thumbnail/render; a failure while rendering stops the batch and lists the files already written (details.written)",
        "decision": "docs/decisions.md ADR-14",
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
                       "validated PNG/JPEG with provenance. Never decides what a thumbnail should show: it ranks candidate frames and moves text clear of "
                       "faces only from facts measured elsewhere (detection-skill faces, caller frame measures) by stated formulas, and the caller "
                       "decides. No face detection or pixel analysis of its own, no OCR, no title generation, no click-through prediction, no A/B "
                       "or publish decisions. Not an AI agent.",
        "repository": "https://github.com/kajisho5/thumbnail-skill",
        "not_provided": ["AI reasoning", "decisions", "production plans", "searching a video for frames", "face/object detection", "OCR",
                         "automatic title generation", "free-form layout generation", "click-through-rate prediction", "A/B testing", "publish decisions",
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
        "batch": batch(),
        "frame_choice": frame_choice(),
        "variants": variants(),
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
           "background_providers", "batch", "frame_choice", "variants", "QC_SKILL_CONTRACT_RANGE"]
