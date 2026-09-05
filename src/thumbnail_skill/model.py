"""Typed Thumbnail Document Model and request validation.

Concepts (docs/architecture.md):
  ThumbnailDocument   canvas + assets + elements + caller metadata
  ThumbnailCanvas     fixed output size and fallback background colour
  ThumbnailAsset      one source (a still image, or an explicit video frame at a caller-given
                      timestamp), identified by asset_id
  ThumbnailElement    one positioned, typed layer: an image (content.image) or text (content.text)
  ImageContent        which asset, where on the canvas, target size, fit, optional source crop
  TextContent         literal text (explicit "\\n" for line breaks; no auto layout), a registered
                      font_id, size, colour, anchor position/alignment, optional background/stroke/shadow

Validation here is structural and semantic but never touches the file system or a font file: PathPolicy
(security.py) resolves asset paths, fonts.py resolves font_id, and the renderer opens images. Unknown
fields are rejected everywhere; fields that could carry a command, a shell fragment or an ffmpeg filter
are rejected by name, wherever they appear in the document."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import REQUEST_SCHEMA_VERSION, SKILL_ID
from .errors import ThumbnailError

REQUEST_SCHEMA_ID = f"{SKILL_ID}/request@{REQUEST_SCHEMA_VERSION}"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COLOR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")

FORBIDDEN_KEYS = frozenset({"command", "commands", "argv", "args", "cmd", "shell", "exec", "executable", "script",
                            "filter", "filters", "filter_complex", "vf", "af", "ffmpeg", "env", "cwd", "eval", "html", "css", "javascript"})

MIN_CANVAS = 16
MAX_CANVAS = 7680
MAX_ASSETS = 32
MAX_ELEMENTS = 64
MAX_TIMESTAMP = 7 * 24 * 3600.0    # 7 days; the real ceiling is the source video's own duration, checked at render time
MAX_TEXT_LENGTH = 2000
MAX_TEXT_LINES = 50
MAX_METADATA_BYTES = 8192
MAX_NESTING_DEPTH = 32   # well above any legitimate document shape, far below Python's recursion limit

ASSET_KINDS = ("image", "video_frame")
ELEMENT_TYPES = ("image", "text")
FIT_MODES = ("cover", "contain", "fill", "none")
ROTATIONS = (0, 90, 180, 270)          # axis-aligned only: arbitrary-angle rotation is not implemented (deterministic, no re-sampling artefacts)
H_ALIGN = ("left", "center", "right")
V_ALIGN = ("top", "middle", "bottom")

OUTPUT_FORMATS: Dict[str, Dict[str, Any]] = {
    "png": {"extensions": (".png",), "pillow_format": "PNG", "lossless": True},
    "jpeg": {"extensions": (".jpg", ".jpeg"), "pillow_format": "JPEG", "lossless": False},
}


# ---------------------------------------------------------------- dataclasses
@dataclass
class Position:
    x: float
    y: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass
class Size:
    width: int
    height: int

    def to_dict(self) -> Dict[str, int]:
        return {"width": self.width, "height": self.height}


@dataclass
class Crop:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass
class ImageContent:
    asset_id: str
    position: Position
    size: Size
    fit: str = "cover"
    crop: Optional[Crop] = None
    opacity: float = 1.0
    rotation: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"asset_id": self.asset_id, "position": self.position.to_dict(), "size": self.size.to_dict(),
                              "fit": self.fit, "opacity": self.opacity, "rotation": self.rotation}
        d["crop"] = self.crop.to_dict() if self.crop else None
        return d


@dataclass
class TextAlign:
    horizontal: str = "left"
    vertical: str = "top"

    def to_dict(self) -> Dict[str, str]:
        return {"horizontal": self.horizontal, "vertical": self.vertical}


@dataclass
class TextBackground:
    color: str
    padding: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"color": self.color, "padding": self.padding}


@dataclass
class TextStroke:
    color: str
    width: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {"color": self.color, "width": self.width}


@dataclass
class TextShadow:
    color: str
    offset_x: int = 1
    offset_y: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {"color": self.color, "offset_x": self.offset_x, "offset_y": self.offset_y}


@dataclass
class TextContent:
    text: str
    font_id: str
    font_size: int
    color: str
    position: Position
    align: TextAlign = field(default_factory=TextAlign)
    line_spacing: float = 1.2
    opacity: float = 1.0
    background: Optional[TextBackground] = None
    stroke: Optional[TextStroke] = None
    shadow: Optional[TextShadow] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "font_id": self.font_id, "font_size": self.font_size, "color": self.color,
                "position": self.position.to_dict(), "align": self.align.to_dict(), "line_spacing": self.line_spacing,
                "opacity": self.opacity, "background": self.background.to_dict() if self.background else None,
                "stroke": self.stroke.to_dict() if self.stroke else None, "shadow": self.shadow.to_dict() if self.shadow else None}


@dataclass
class ThumbnailElement:
    element_id: str
    type: str
    z_index: int = 0
    image: Optional[ImageContent] = None
    text: Optional[TextContent] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"element_id": self.element_id, "type": self.type, "z_index": self.z_index}
        if self.image is not None:
            d["image"] = self.image.to_dict()
        if self.text is not None:
            d["text"] = self.text.to_dict()
        return d


@dataclass
class ThumbnailAsset:
    asset_id: str
    kind: str
    path: str
    timestamp: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"asset_id": self.asset_id, "kind": self.kind, "path": self.path}
        if self.timestamp is not None:
            d["timestamp"] = self.timestamp
        return d


@dataclass
class ThumbnailCanvas:
    width: int
    height: int
    background: str = "#000000"

    def to_dict(self) -> Dict[str, Any]:
        return {"width": self.width, "height": self.height, "background": self.background}


@dataclass
class ThumbnailDocument:
    document_id: str
    canvas: ThumbnailCanvas
    assets: List[ThumbnailAsset]
    elements: List[ThumbnailElement]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def asset(self, asset_id: str) -> ThumbnailAsset:
        return next(a for a in self.assets if a.asset_id == asset_id)

    def to_dict(self) -> Dict[str, Any]:
        return {"document_id": self.document_id, "canvas": self.canvas.to_dict(), "assets": [a.to_dict() for a in self.assets],
                "elements": [e.to_dict() for e in self.elements], "metadata": dict(self.metadata)}


@dataclass
class OutputSpec:
    path: str
    format: str
    overwrite: bool = False
    jpeg_quality: int = 90

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "format": self.format, "overwrite": self.overwrite, "jpeg_quality": self.jpeg_quality}


@dataclass
class RenderRequest:
    document: ThumbnailDocument
    output: OutputSpec
    options: Dict[str, Any]


# ---------------------------------------------------------------- validation helpers
def _reject_forbidden(obj: Any, where: str, _depth: int = 0) -> None:
    """Recurses into the raw, not-yet-structurally-validated request looking for forbidden field
    names -- including inside the free-form `metadata` object, which has no field allowlist of its
    own to stop at. Runs before any other check (including MAX_METADATA_BYTES), on whatever shape
    the caller sent, so a depth bound of its own is required: without one, a small but deeply nested
    payload (a few KB, well under the metadata byte cap) drives Python's own recursion limit into an
    uncaught RecursionError before any of this file's other bounds ever get a chance to apply."""
    if _depth > MAX_NESTING_DEPTH:
        raise ThumbnailError("INVALID_REQUEST", f"{where}: nested more than {MAX_NESTING_DEPTH} levels deep", {"field": where, "reason": "too_deeply_nested"})
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ThumbnailError("INVALID_REQUEST", f"{where}: object keys must be strings")
            if k.lower() in FORBIDDEN_KEYS:
                raise ThumbnailError("INVALID_REQUEST", f"{where}: field {k!r} is not accepted (this skill never takes commands, argv, filters, HTML/CSS/JS or executables)",
                                     {"field": k, "reason": "forbidden_field"})
            _reject_forbidden(v, f"{where}.{k}", _depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _reject_forbidden(v, f"{where}[{i}]", _depth + 1)


def _obj(value: Any, where: str, allowed: Tuple[str, ...], required: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be an object", {"field": where})
    unknown = sorted(k for k in value if k not in allowed)
    if unknown:
        raise ThumbnailError("INVALID_REQUEST", f"{where}: unknown field(s) {unknown}", {"field": where, "unknown": unknown, "allowed": list(allowed)})
    missing = [k for k in required if k not in value]
    if missing:
        raise ThumbnailError("INVALID_REQUEST", f"{where}: missing required field(s) {missing}", {"field": where, "missing": missing})
    return value


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must match {ID_RE.pattern}", {"field": where})
    return value


def _number(value: Any, where: str, lo: Optional[float] = None, hi: Optional[float] = None, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be a number", {"field": where})
    if isinstance(value, float) and not math.isfinite(value):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be finite", {"field": where})
    if integer and (isinstance(value, float) and not value.is_integer()):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be an integer", {"field": where})
    if lo is not None and value < lo or hi is not None and value > hi:
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be within [{lo}, {hi}], got {value}", {"field": where, "min": lo, "max": hi})
    return float(value)


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be a boolean", {"field": where})
    return value


def _color(value: Any, where: str) -> str:
    if not isinstance(value, str) or not COLOR_RE.match(value):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be a '#RRGGBB' or '#RRGGBBAA' colour string", {"field": where})
    return value


def _enum(value: Any, where: str, choices: Tuple[str, ...]) -> str:
    if value not in choices:
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be one of {list(choices)}, got {value!r}", {"field": where, "choices": list(choices)})
    return value


def _position(value: Any, where: str) -> Position:
    d = _obj(value, where, ("x", "y"), ("x", "y"))
    return Position(_number(d["x"], f"{where}.x", -100000, 100000), _number(d["y"], f"{where}.y", -100000, 100000))


def _size(value: Any, where: str) -> Size:
    d = _obj(value, where, ("width", "height"), ("width", "height"))
    return Size(int(_number(d["width"], f"{where}.width", 1, MAX_CANVAS, integer=True)), int(_number(d["height"], f"{where}.height", 1, MAX_CANVAS, integer=True)))


def _crop(value: Any, where: str) -> Optional[Crop]:
    if value is None:
        return None
    d = _obj(value, where, ("x", "y", "width", "height"), ("x", "y", "width", "height"))
    x = int(_number(d["x"], f"{where}.x", 0, MAX_CANVAS, integer=True))
    y = int(_number(d["y"], f"{where}.y", 0, MAX_CANVAS, integer=True))
    w = int(_number(d["width"], f"{where}.width", 1, MAX_CANVAS, integer=True))
    h = int(_number(d["height"], f"{where}.height", 1, MAX_CANVAS, integer=True))
    return Crop(x, y, w, h)


def _image_content(value: Any, where: str) -> ImageContent:
    d = _obj(value, where, ("asset_id", "position", "size", "fit", "crop", "opacity", "rotation"), ("asset_id", "position", "size"))
    asset_id = _id(d["asset_id"], f"{where}.asset_id")
    position = _position(d["position"], f"{where}.position")
    size = _size(d["size"], f"{where}.size")
    fit = _enum(d.get("fit", "cover"), f"{where}.fit", FIT_MODES)
    crop = _crop(d.get("crop"), f"{where}.crop")
    opacity = _number(d.get("opacity", 1.0), f"{where}.opacity", 0.0, 1.0)
    rotation = int(_number(d.get("rotation", 0), f"{where}.rotation", integer=True))
    if rotation not in ROTATIONS:
        raise ThumbnailError("UNSUPPORTED_OPERATION", f"{where}.rotation {rotation} is not implemented; supported: {list(ROTATIONS)} (axis-aligned only)",
                             {"field": f"{where}.rotation", "supported": list(ROTATIONS)})
    return ImageContent(asset_id, position, size, fit, crop, opacity, rotation)


def _text_align(value: Any, where: str) -> TextAlign:
    if value is None:
        return TextAlign()
    d = _obj(value, where, ("horizontal", "vertical"), ())
    h = _enum(d.get("horizontal", "left"), f"{where}.horizontal", H_ALIGN)
    v = _enum(d.get("vertical", "top"), f"{where}.vertical", V_ALIGN)
    return TextAlign(h, v)


def _text_background(value: Any, where: str) -> Optional[TextBackground]:
    if value is None:
        return None
    d = _obj(value, where, ("color", "padding"), ("color",))
    return TextBackground(_color(d["color"], f"{where}.color"), int(_number(d.get("padding", 0), f"{where}.padding", 0, 200, integer=True)))


def _text_stroke(value: Any, where: str) -> Optional[TextStroke]:
    if value is None:
        return None
    d = _obj(value, where, ("color", "width"), ("color",))
    return TextStroke(_color(d["color"], f"{where}.color"), int(_number(d.get("width", 1), f"{where}.width", 0, 40, integer=True)))


def _text_shadow(value: Any, where: str) -> Optional[TextShadow]:
    if value is None:
        return None
    d = _obj(value, where, ("color", "offset_x", "offset_y"), ("color",))
    return TextShadow(_color(d["color"], f"{where}.color"), int(_number(d.get("offset_x", 1), f"{where}.offset_x", -200, 200, integer=True)),
                       int(_number(d.get("offset_y", 1), f"{where}.offset_y", -200, 200, integer=True)))


_CONTROL_CHARS_ALLOWED = {"\n"}


def _text_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be a non-empty string", {"field": where})
    if len(value) > MAX_TEXT_LENGTH:
        raise ThumbnailError("INVALID_REQUEST", f"{where} exceeds the maximum length ({MAX_TEXT_LENGTH})", {"field": where})
    bad = [c for c in value if ord(c) < 32 and c not in _CONTROL_CHARS_ALLOWED]
    if bad:
        raise ThumbnailError("INVALID_REQUEST", f"{where} contains a control character that is not a newline", {"field": where, "reason": "control_character"})
    if value.count("\n") + 1 > MAX_TEXT_LINES:
        raise ThumbnailError("INVALID_REQUEST", f"{where} has more than {MAX_TEXT_LINES} lines", {"field": where})
    return value


def _text_content(value: Any, where: str) -> TextContent:
    d = _obj(value, where, ("text", "font_id", "font_size", "color", "position", "align", "line_spacing", "opacity", "background", "stroke", "shadow"),
             ("text", "font_id", "font_size", "color", "position"))
    text = _text_string(d["text"], f"{where}.text")
    font_id = _id(d["font_id"], f"{where}.font_id")
    font_size = int(_number(d["font_size"], f"{where}.font_size", 6, 400, integer=True))
    color = _color(d["color"], f"{where}.color")
    position = _position(d["position"], f"{where}.position")
    align = _text_align(d.get("align"), f"{where}.align")
    line_spacing = _number(d.get("line_spacing", 1.2), f"{where}.line_spacing", 0.5, 5.0)
    opacity = _number(d.get("opacity", 1.0), f"{where}.opacity", 0.0, 1.0)
    background = _text_background(d.get("background"), f"{where}.background")
    stroke = _text_stroke(d.get("stroke"), f"{where}.stroke")
    shadow = _text_shadow(d.get("shadow"), f"{where}.shadow")
    return TextContent(text, font_id, font_size, color, position, align, line_spacing, opacity, background, stroke, shadow)


def parse_canvas(value: Any, where: str = "canvas") -> ThumbnailCanvas:
    d = _obj(value, where, ("width", "height", "background"), ("width", "height"))
    width = int(_number(d["width"], f"{where}.width", MIN_CANVAS, MAX_CANVAS, integer=True))
    height = int(_number(d["height"], f"{where}.height", MIN_CANVAS, MAX_CANVAS, integer=True))
    background = _color(d.get("background", "#000000"), f"{where}.background")
    return ThumbnailCanvas(width, height, background)


def parse_asset(value: Any, where: str) -> ThumbnailAsset:
    d = _obj(value, where, ("asset_id", "kind", "path", "timestamp"), ("asset_id", "kind", "path"))
    asset_id = _id(d["asset_id"], f"{where}.asset_id")
    kind = _enum(d["kind"], f"{where}.kind", ASSET_KINDS)
    if not isinstance(d["path"], str) or not d["path"]:
        raise ThumbnailError("INVALID_REQUEST", f"{where}.path must be a non-empty string", {"field": f"{where}.path"})
    timestamp: Optional[float] = None
    if kind == "video_frame":
        if "timestamp" not in d:
            raise ThumbnailError("INVALID_REQUEST", f"{where}: kind 'video_frame' requires 'timestamp'", {"field": f"{where}.timestamp"})
        ts = d["timestamp"]
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
            raise ThumbnailError("INVALID_TIME_RANGE", f"{where}.timestamp must be a finite number", {"field": f"{where}.timestamp"})
        if ts < 0:
            raise ThumbnailError("INVALID_TIME_RANGE", f"{where}.timestamp must not be negative, got {ts}", {"field": f"{where}.timestamp", "timestamp": ts})
        if ts > MAX_TIMESTAMP:
            raise ThumbnailError("INVALID_TIME_RANGE", f"{where}.timestamp exceeds the maximum accepted value ({MAX_TIMESTAMP}s)", {"field": f"{where}.timestamp", "timestamp": ts})
        timestamp = float(ts)
    elif "timestamp" in d:
        raise ThumbnailError("INVALID_REQUEST", f"{where}: 'timestamp' is only accepted when kind is 'video_frame'", {"field": f"{where}.timestamp"})
    return ThumbnailAsset(asset_id, kind, d["path"], timestamp)


def parse_element(value: Any, where: str) -> ThumbnailElement:
    d = _obj(value, where, ("element_id", "type", "z_index", "image", "text"), ("element_id", "type"))
    element_id = _id(d["element_id"], f"{where}.element_id")
    etype = _enum(d["type"], f"{where}.type", ELEMENT_TYPES)
    z_index = int(_number(d.get("z_index", 0), f"{where}.z_index", -1000, 1000, integer=True))
    image = text = None
    if etype == "image":
        if "text" in d:
            raise ThumbnailError("INVALID_REQUEST", f"{where}: type 'image' may not carry a 'text' field", {"field": where})
        if "image" not in d:
            raise ThumbnailError("INVALID_REQUEST", f"{where}: type 'image' requires an 'image' field", {"field": where})
        image = _image_content(d["image"], f"{where}.image")
    else:
        if "image" in d:
            raise ThumbnailError("INVALID_REQUEST", f"{where}: type 'text' may not carry an 'image' field", {"field": where})
        if "text" not in d:
            raise ThumbnailError("INVALID_REQUEST", f"{where}: type 'text' requires a 'text' field", {"field": where})
        text = _text_content(d["text"], f"{where}.text")
    return ThumbnailElement(element_id, etype, z_index, image, text)


def _metadata(value: Any, where: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be an object", {"field": where})
    from .canonical import canonical_json
    if len(canonical_json(value).encode("utf-8")) > MAX_METADATA_BYTES:
        raise ThumbnailError("INVALID_REQUEST", f"{where} is larger than {MAX_METADATA_BYTES} bytes", {"field": where})
    return value


def parse_document(value: Any, where: str = "document") -> ThumbnailDocument:
    _reject_forbidden(value, where)   # covers free-form `metadata` too, which has no field allowlist of its own
    d = _obj(value, where, ("document_id", "canvas", "assets", "elements", "metadata"), ("document_id", "canvas", "elements"))
    document_id = _id(d["document_id"], f"{where}.document_id")
    canvas = parse_canvas(d["canvas"], f"{where}.canvas")

    assets_raw = d.get("assets", [])
    if not isinstance(assets_raw, list):
        raise ThumbnailError("INVALID_REQUEST", f"{where}.assets must be an array", {"field": f"{where}.assets"})
    if len(assets_raw) > MAX_ASSETS:
        raise ThumbnailError("INVALID_REQUEST", f"{where}.assets: too many assets (max {MAX_ASSETS})", {"field": f"{where}.assets"})
    assets: List[ThumbnailAsset] = []
    for i, a in enumerate(assets_raw):
        asset = parse_asset(a, f"{where}.assets[{i}]")
        if any(x.asset_id == asset.asset_id for x in assets):
            raise ThumbnailError("DEPENDENCY_ERROR", f"duplicate asset_id {asset.asset_id!r}", {"field": f"{where}.assets[{i}]"})
        assets.append(asset)
    asset_ids = {a.asset_id for a in assets}

    elements_raw = d["elements"]
    if not isinstance(elements_raw, list) or not elements_raw:
        raise ThumbnailError("INVALID_REQUEST", f"{where}.elements must be a non-empty array", {"field": f"{where}.elements"})
    if len(elements_raw) > MAX_ELEMENTS:
        raise ThumbnailError("INVALID_REQUEST", f"{where}.elements: too many elements (max {MAX_ELEMENTS})", {"field": f"{where}.elements"})
    elements: List[ThumbnailElement] = []
    for i, e in enumerate(elements_raw):
        el = parse_element(e, f"{where}.elements[{i}]")
        if any(x.element_id == el.element_id for x in elements):
            raise ThumbnailError("DEPENDENCY_ERROR", f"duplicate element_id {el.element_id!r}", {"field": f"{where}.elements[{i}]"})
        if el.image is not None and el.image.asset_id not in asset_ids:
            raise ThumbnailError("MISSING_INPUT", f"{where}.elements[{i}]: asset {el.image.asset_id!r} is not declared in assets", {"field": f"{where}.elements[{i}].image.asset_id"})
        elements.append(el)

    metadata = _metadata(d.get("metadata"), f"{where}.metadata")
    return ThumbnailDocument(document_id, canvas, assets, elements, metadata)


def parse_output(value: Any, where: str = "output") -> OutputSpec:
    d = _obj(value, where, ("path", "format", "overwrite", "jpeg_quality"), ("path", "format"))
    if not isinstance(d["path"], str) or not d["path"]:
        raise ThumbnailError("INVALID_REQUEST", f"{where}.path must be a non-empty string", {"field": f"{where}.path"})
    fmt = d["format"]
    if fmt not in OUTPUT_FORMATS:
        raise ThumbnailError("UNSUPPORTED_FORMAT", f"{where}.format {fmt!r} is not supported; supported: {sorted(OUTPUT_FORMATS)}", {"field": f"{where}.format", "format": fmt})
    if not d["path"].lower().endswith(OUTPUT_FORMATS[fmt]["extensions"]):
        raise ThumbnailError("UNSUPPORTED_FORMAT", f"{where}.path must end with one of {OUTPUT_FORMATS[fmt]['extensions']} for format {fmt!r}", {"field": f"{where}.path"})
    overwrite = _bool(d.get("overwrite", False), f"{where}.overwrite")
    if "jpeg_quality" in d and fmt != "jpeg":
        raise ThumbnailError("INVALID_REQUEST", f"{where}.jpeg_quality is only accepted when format is 'jpeg'", {"field": f"{where}.jpeg_quality"})
    jpeg_quality = int(_number(d.get("jpeg_quality", 90), f"{where}.jpeg_quality", 1, 100, integer=True))
    return OutputSpec(d["path"], fmt, overwrite, jpeg_quality)


def parse_options(value: Any, where: str = "options") -> Dict[str, Any]:
    d = _obj(value if value is not None else {}, where, ("allowed_input_roots", "workspace", "reuse", "timeout", "ffmpeg_skill"), ())
    opts: Dict[str, Any] = {"allowed_input_roots": None, "workspace": None, "reuse": True, "timeout": 120.0, "ffmpeg_skill": None}
    if "allowed_input_roots" in d:
        roots = d["allowed_input_roots"]
        if not isinstance(roots, list) or not all(isinstance(r, str) and r for r in roots):
            raise ThumbnailError("INVALID_REQUEST", f"{where}.allowed_input_roots must be an array of non-empty strings", {"field": f"{where}.allowed_input_roots"})
        opts["allowed_input_roots"] = list(roots)
    if "workspace" in d:
        if not isinstance(d["workspace"], str) or not d["workspace"]:
            raise ThumbnailError("INVALID_REQUEST", f"{where}.workspace must be a non-empty string", {"field": f"{where}.workspace"})
        opts["workspace"] = d["workspace"]
    if "reuse" in d:
        opts["reuse"] = _bool(d["reuse"], f"{where}.reuse")
    if "timeout" in d:
        opts["timeout"] = _number(d["timeout"], f"{where}.timeout", 1.0, 3600.0)
    if "ffmpeg_skill" in d:
        if not isinstance(d["ffmpeg_skill"], str) or not d["ffmpeg_skill"]:
            raise ThumbnailError("INVALID_REQUEST", f"{where}.ffmpeg_skill must be a non-empty string", {"field": f"{where}.ffmpeg_skill"})
        opts["ffmpeg_skill"] = d["ffmpeg_skill"]
    return opts


def parse_render_request(doc: Any) -> RenderRequest:
    """Validate a `thumbnail/render` (or `thumbnail/validate`) params document into typed objects."""
    if not isinstance(doc, dict):
        raise ThumbnailError("INVALID_REQUEST", "params must be a JSON object")
    _reject_forbidden(doc, "params")
    d = _obj(doc, "params", ("schema", "document", "output", "options"), ("document",))
    if "schema" in d and d["schema"] != REQUEST_SCHEMA_ID:
        raise ThumbnailError("INVALID_REQUEST", f"unsupported request schema {d['schema']!r}; expected {REQUEST_SCHEMA_ID!r}", {"field": "schema"})
    document = parse_document(d["document"])
    output = parse_output(d["output"]) if "output" in d else None
    options = parse_options(d.get("options"))
    if output is None:
        return RenderRequest(document, OutputSpec("", "png", False, 90), options)
    return RenderRequest(document, output, options)


__all__ = ["REQUEST_SCHEMA_ID", "ID_RE", "COLOR_RE", "FORBIDDEN_KEYS", "MIN_CANVAS", "MAX_CANVAS", "MAX_ASSETS", "MAX_ELEMENTS",
           "MAX_TIMESTAMP", "ASSET_KINDS", "ELEMENT_TYPES", "FIT_MODES", "ROTATIONS", "H_ALIGN", "V_ALIGN", "OUTPUT_FORMATS",
           "Position", "Size", "Crop", "ImageContent", "TextAlign", "TextBackground", "TextStroke", "TextShadow", "TextContent",
           "ThumbnailElement", "ThumbnailAsset", "ThumbnailCanvas", "ThumbnailDocument", "OutputSpec", "RenderRequest",
           "parse_canvas", "parse_asset", "parse_element", "parse_document", "parse_output", "parse_options", "parse_render_request"]
