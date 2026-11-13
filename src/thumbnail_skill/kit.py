"""Brand kit reader (`options.kit`, ECOSYSTEM_ROADMAP.md step 9, ADR-18): a Velisa `kit@1` file, which is a superset of
ffmpeg-skill `brand.json`, mapped onto the ThumbnailDocument's existing colours, font and logo.

The kit fills only what the document leaves out, and only with what the kit states:
- `colors.text` -> each text element's `color`;
- `colors.background` -> `canvas.background`, and the band colour of a text element whose `background` gives no
  `color` (ffmpeg-skill's lower third draws its bar in the background colour and its text in the text colour);
- `colors.outline` -> the `stroke.color` of a text element whose `stroke` gives no `color`;
- `font` -> each text element's `font_id`: the registered font_id whose resolved font file on this machine has that
  family name (a font_id registered for it); never a font file path;
- `logo` + `logo_position` + `logo_scale` + `safe_margin` (+ `logo_opacity`) -> one image element `kit-logo`;
- `colors.primary` -> render_variants' kit palettes only.
A value stated in the document wins and is listed as kept. Every kit key with no counterpart here is reported in
`unmapped` with the reason; a key that has a counterpart the document left no slot for is reported in `unused`.
Nothing the kit leaves out is filled in (ffmpeg-skill's `brand.json` defaults are not the user's choice)."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .canonical import sha256_file
from .errors import ThumbnailError
from .model import MAX_CANVAS, PLATFORM_PRESETS

KIT_SCHEMA = "velisa/kit@1"
MAX_KIT_BYTES = 256 * 1024
KIT_TOOLS = ("thumbnail/render", "thumbnail/render_layers", "thumbnail/render_variants", "thumbnail/render_batch")
LOGO_ID = "kit-logo"
KIT_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
COLOR_ROLES = ("primary", "text", "outline", "background", "accent", "secondary")
# ffmpeg-skill's position names (overlay.py), which `logo_position` uses
LOGO_POSITIONS = ("top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right")
# the layout numbers of the kit palettes are this skill's variant defaults (variants.DEFAULT_PALETTES), not the kit's
PALETTE_PADDING = 16
PALETTE_STROKE_WIDTH = 4

MAPPING: List[Dict[str, str]] = [
    {"kit": "colors.text", "to": "document.elements[].text.color", "when": "the element gives no color"},
    {"kit": "colors.background", "to": "document.canvas.background", "when": "the canvas gives no background"},
    {"kit": "colors.background", "to": "document.elements[].text.background.color", "when": "the element gives a background band without a color"},
    {"kit": "colors.outline", "to": "document.elements[].text.stroke.color", "when": "the element gives a stroke without a color"},
    {"kit": "font", "to": "document.elements[].text.font_id",
     "when": "the element gives no font_id; the registered font_id whose resolved file on this machine has that family name (regular style first)"},
    {"kit": "logo, logo_position, logo_scale, safe_margin, logo_opacity?", "to": f"an image asset and element {LOGO_ID!r} (top of the stack, fit contain)",
     "when": f"the kit states logo, logo_position, logo_scale and safe_margin, and the document has no element or asset {LOGO_ID!r}"},
    {"kit": "colors.primary, colors.text, colors.background, colors.outline", "to": "render_variants' palettes", "when": "variants.palettes is not given"},
]

# kit keys with no counterpart in a ThumbnailDocument, and why
NO_COUNTERPART: Dict[str, str] = {
    "font_file": "fonts are named only by registered font_id; this skill never reads a font file path from a request",
    "colors.accent": "a ThumbnailDocument has no element role for an accent colour",
    "colors.secondary": "a ThumbnailDocument has no element role for a secondary colour",
    "lang": "this skill does not choose fonts or layout by language",
    "styles": "styles.caption is the caption look; a thumbnail has no caption",
    "caption": "caption burn-in defaults; a thumbnail has no caption",
    "loudness": "a thumbnail has no audio",
    "fonts": "font licences and embedding are recorded in the kit, not checked or used by this skill",
    "logo_variants": "only the one logo file (logo) is placed",
    "contrast": "this skill computes no contrast itself (render_variants measures it through qc-skill)",
    "name": "recorded in response.kit.name; a ThumbnailDocument has no name field",
}
BRAND_KEYS = ("font", "font_file", "colors", "logo", "logo_position", "logo_scale", "logo_opacity", "safe_margin", "lang", "styles", "caption", "loudness")
KIT_KEYS = ("schema", "name", "fonts", "logo_variants", "contrast")


def _bad(message: str, key: str) -> ThumbnailError:
    return ThumbnailError("INVALID_INPUT", f"kit: {message}", {"reason": "bad_kit", "field": key})


@dataclass
class Kit:
    path: str
    sha256: str
    size: int
    data: Dict[str, Any]
    colors: Dict[str, str] = field(default_factory=dict)          # role -> "#RRGGBB"
    font: Optional[str] = None
    logo: Optional[str] = None                                   # absolute path (relative paths resolve against the kit file)
    placeholder: bool = False                                    # validate: the kit file is not read

    def stated(self, key: str) -> bool:
        return self.data.get(key) is not None


def parse_kit(data: Any, base_dir: Path, path: str = "", sha256: str = "", size: int = 0) -> Kit:
    """Check the shape of the keys this skill maps. A value that maps but is malformed or out of range is refused
    (INVALID_INPUT, bad_kit), never silently left out; keys this skill does not map are only reported."""
    if not isinstance(data, dict):
        raise _bad(f"the file must be a JSON object, got {type(data).__name__}", "kit")
    if data.get("schema") is not None and data["schema"] != KIT_SCHEMA:
        raise _bad(f"schema {data['schema']!r} is not {KIT_SCHEMA!r}", "schema")
    if data.get("colors") is not None and data.get("colours") is not None:
        raise _bad("give colors or colours, not both", "colours")
    kit = Kit(path, sha256, size, dict(data))
    colors = data.get("colors") if data.get("colors") is not None else data.get("colours")
    if colors is not None:
        if not isinstance(colors, dict):
            raise _bad("colors must be an object", "colors")
        for role in COLOR_ROLES:
            v = colors.get(role)
            if v is None:
                continue
            if not isinstance(v, str) or not KIT_COLOR_RE.match(v):
                raise _bad(f"colors.{role} must be RRGGBB (six hex digits, no '#'), got {v!r}", f"colors.{role}")
            kit.colors[role] = "#" + v.upper()
    for key in ("font", "font_file", "logo", "name"):
        v = data.get(key)
        if v is not None and (not isinstance(v, str) or not v):
            raise _bad(f"{key} must be a non-empty string", key)
    kit.font = data.get("font")
    if data.get("logo") is not None:
        p = Path(data["logo"])
        kit.logo = str(p if p.is_absolute() else base_dir / p)
    if data.get("logo_position") is not None and data["logo_position"] not in LOGO_POSITIONS:
        raise _bad(f"logo_position {data['logo_position']!r} is not one of {list(LOGO_POSITIONS)}", "logo_position")
    for key, lo, hi in (("logo_scale", 1, MAX_CANVAS), ("safe_margin", 0, MAX_CANVAS), ("logo_opacity", 0, 1)):
        v = data.get(key)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise _bad(f"{key} must be a number in {lo}..{hi}, got {v!r}", key)
    return kit


def load_kit(resolve_input: Callable[[str, str], Path], path: Any) -> Kit:
    if not isinstance(path, str) or not path:
        raise ThumbnailError("INVALID_REQUEST", "options.kit must be the path of a kit@1 JSON file", {"field": "options.kit"})
    resolved = resolve_input(path, "options.kit")
    size = resolved.stat().st_size
    if size > MAX_KIT_BYTES:
        raise _bad(f"the file is larger than {MAX_KIT_BYTES} bytes", "kit")
    try:
        data = json.loads(resolved.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _bad(f"the file is not UTF-8 JSON: {e}", "kit")
    return parse_kit(data, resolved.parent, str(resolved), f"sha256:{sha256_file(str(resolved))}", size)


def placeholder_kit() -> Kit:
    """`thumbnail/validate` touches no file, so it does not read the kit: the slots a kit could fill are filled with
    placeholders for the structural check and listed."""
    kit = Kit("", "", 0, {}, placeholder=True)
    kit.colors = {"text": "#000000", "background": "#000000", "outline": "#000000"}
    return kit


# ---------------------------------------------------------------- font
def font_for_family(family: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """The registered font_id whose resolved font file has this family name (case-insensitive), regular style first,
    then by font_id; and every registered font_id that resolves on this machine with its family, for the report."""
    from PIL import ImageFont

    from .fonts import FONT_REGISTRY, font_ids, resolve_font
    seen: List[Dict[str, Any]] = []
    for fid in font_ids():
        try:
            rf = resolve_font(fid)
            fam, style = ImageFont.truetype(rf.path, 12, index=rf.index).getname()
        except (ThumbnailError, OSError):
            continue
        seen.append({"font_id": fid, "family": fam, "style": style, "registry_style": FONT_REGISTRY[fid]["style"], "path": rf.path,
                     "font_file_hash": f"sha256:{rf.sha256}"})
    matches = [s for s in seen if str(s["family"]).casefold() == family.casefold()]
    matches.sort(key=lambda s: (s["registry_style"] != "regular", s["font_id"]))
    return (matches[0] if matches else None), seen


# ---------------------------------------------------------------- logo
def logo_box(position: str, margin: int, canvas: Tuple[int, int], size: Tuple[int, int]) -> Tuple[int, int]:
    W, H = canvas
    w, h = size
    col = position.split("-")[-1] if "-" in position else ("left" if position == "left" else "right" if position == "right" else "center")
    row = position.split("-")[0] if "-" in position else ("top" if position == "top" else "bottom" if position == "bottom" else "middle")
    x = margin if col == "left" else W - margin - w if col == "right" else (W - w) // 2
    y = margin if row == "top" else H - margin - h if row == "bottom" else (H - h) // 2
    return x, y


def _canvas_size(doc: Dict[str, Any], preset: Optional[str]) -> Optional[Tuple[int, int]]:
    canvas = doc.get("canvas") if isinstance(doc.get("canvas"), dict) else {}
    w, h = canvas.get("width"), canvas.get("height")
    if isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool) and not isinstance(h, bool):
        return w, h
    if preset in PLATFORM_PRESETS:
        return PLATFORM_PRESETS[preset]["width"], PLATFORM_PRESETS[preset]["height"]   # type: ignore[index]
    return None


# ---------------------------------------------------------------- applying
class KitApplication:
    """Applies one kit to one or more documents of a request and collects the report."""

    def __init__(self, kit: Kit, tool: str, resolve_input: Optional[Callable[[str, str], Path]] = None):
        self.kit = kit
        self.tool = tool
        self.resolve_input = resolve_input
        self.applied: List[Dict[str, Any]] = []
        self.kept: List[Dict[str, Any]] = []
        self.used_keys: set = set()
        self.slot_keys: set = set()       # kit keys some document had a slot for (applied or kept)
        self.logo_reason: Optional[str] = None
        self.logo: Optional[Dict[str, Any]] = None
        self.palettes: Optional[List[Dict[str, Any]]] = None
        self.font: Optional[Dict[str, Any]] = None
        self.font_seen: List[Dict[str, Any]] = []
        if kit.font and not kit.placeholder:
            self.font, self.font_seen = font_for_family(kit.font)
        self.logo_reason = self._logo_missing()

    def _logo_missing(self) -> Optional[str]:
        k = self.kit
        if k.placeholder:
            return "validate does not read the kit"
        if not k.stated("logo"):
            return "the kit states no logo"
        missing = [key for key in ("logo_position", "logo_scale", "safe_margin") if not k.stated(key)]
        if missing:
            return f"the kit states no {', '.join(missing)}: this skill fills no position, size or margin the kit leaves out"
        return None

    def _fill(self, obj: Dict[str, Any], key: str, kit_key: str, value: Optional[str], where: str) -> None:
        if value is None:
            return
        self.slot_keys.add(kit_key)
        if key in obj:
            self.kept.append({"target": f"{where}.{key}", "value": obj[key], "kit_key": kit_key, "kit_value": value})
        else:
            obj[key] = value
            self.used_keys.add(kit_key)
            self.applied.append({"target": f"{where}.{key}", "value": value, "from": kit_key})

    def apply(self, raw_doc: Any, where: str, preset: Optional[str]) -> Any:
        """A copy of the document with the kit's values in the slots the document leaves out. A document that is not an
        object, or a slot of the wrong type, is left as it is for the document's own validation to refuse."""
        if not isinstance(raw_doc, dict):
            return raw_doc
        doc = copy.deepcopy(raw_doc)
        c = self.kit.colors
        if isinstance(doc.get("canvas"), dict):
            self._fill(doc["canvas"], "background", "colors.background", c.get("background"), f"{where}.canvas")
        elements = doc.get("elements") if isinstance(doc.get("elements"), list) else []
        for i, el in enumerate(elements):
            if not isinstance(el, dict) or el.get("type") != "text" or not isinstance(el.get("text"), dict):
                continue
            t, at = el["text"], f"{where}.elements[{i}].text"
            self._fill(t, "color", "colors.text", c.get("text"), at)
            if self.kit.placeholder:
                self._fill(t, "font_id", "font", "sans", at)
            elif self.kit.font:
                self._fill(t, "font_id", "font", self.font["font_id"] if self.font else None, at)
            if isinstance(t.get("background"), dict):
                self._fill(t["background"], "color", "colors.background", c.get("background"), f"{at}.background")
            if isinstance(t.get("stroke"), dict):
                self._fill(t["stroke"], "color", "colors.outline", c.get("outline"), f"{at}.stroke")
        if self.logo_reason is None:
            self._place_logo(doc, where, preset)
        return doc

    def _place_logo(self, doc: Dict[str, Any], where: str, preset: Optional[str]) -> None:
        k = self.kit
        ids = {str(e.get("element_id")) for e in doc.get("elements") or [] if isinstance(e, dict)} | \
              {str(a.get("asset_id")) for a in doc.get("assets") or [] if isinstance(a, dict)}
        for key in ("logo", "logo_position", "logo_scale", "safe_margin", "logo_opacity"):
            if k.stated(key):
                self.slot_keys.add(key)
        if LOGO_ID in ids:
            self.kept.append({"target": f"{where} {LOGO_ID!r}", "value": "the document's own", "kit_key": "logo", "kit_value": k.logo})
            return
        canvas = _canvas_size(doc, preset)
        if canvas is None or not isinstance(doc.get("elements"), list):
            return   # the document's own validation refuses it
        assert k.logo is not None and self.resolve_input is not None
        from PIL import Image, UnidentifiedImageError
        logo_path = self.resolve_input(k.logo, "kit logo")
        try:
            with Image.open(logo_path) as im:
                lw, lh = im.size
        except (UnidentifiedImageError, OSError) as e:
            raise _bad(f"logo {k.logo} is not a readable image: {e}", "logo")
        scale = int(round(k.data["logo_scale"]))
        size = (scale, max(1, int(round(scale * lh / lw))))
        margin = int(round(k.data["safe_margin"]))
        x, y = logo_box(k.data["logo_position"], margin, canvas, size)
        zs = [e.get("z_index", 0) for e in doc["elements"] if isinstance(e, dict)]
        z = max([int(v) for v in zs if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer()] + [0]) + 1
        image: Dict[str, Any] = {"asset_id": LOGO_ID, "position": {"x": x, "y": y}, "size": {"width": size[0], "height": size[1]}, "fit": "contain"}
        if k.stated("logo_opacity"):
            image["opacity"] = k.data["logo_opacity"]
        assets = doc.get("assets") if isinstance(doc.get("assets"), list) else []
        doc["assets"] = assets + [{"asset_id": LOGO_ID, "kind": "image", "path": str(logo_path)}]
        doc["elements"] = doc["elements"] + [{"element_id": LOGO_ID, "type": "image", "z_index": z, "image": image}]
        for key in ("logo", "logo_position", "logo_scale", "safe_margin", "logo_opacity"):
            if k.stated(key):
                self.used_keys.add(key)
        self.logo = {"element_id": LOGO_ID, "path": str(logo_path), "position": k.data["logo_position"], "margin": margin,
                     "box": {"x": x, "y": y, "width": size[0], "height": size[1]}, "opacity": image.get("opacity", 1.0),
                     "opacity_from": "logo_opacity" if k.stated("logo_opacity") else "this skill's default (the kit states none)"}
        self.applied.append({"target": f"{where}.elements[{LOGO_ID!r}]", "value": self.logo["box"], "from": "logo, logo_position, logo_scale, safe_margin"})

    def variant_palettes(self) -> List[Dict[str, Any]]:
        """render_variants' palettes from the kit's colours, each only when the kit states every colour it uses."""
        c = self.kit.colors
        out: List[Dict[str, Any]] = []
        if c.get("text") and c.get("background"):
            out.append({"name": "kit-text-on-background-band", "color": c["text"], "background": {"color": c["background"], "padding": PALETTE_PADDING}, "uses": ["colors.text", "colors.background"]})
        if c.get("background") and c.get("primary"):
            out.append({"name": "kit-background-on-primary-band", "color": c["background"], "background": {"color": c["primary"], "padding": PALETTE_PADDING}, "uses": ["colors.background", "colors.primary"]})
        if c.get("text") and c.get("outline"):
            out.append({"name": "kit-text-with-outline", "color": c["text"], "stroke": {"color": c["outline"], "width": PALETTE_STROKE_WIDTH}, "uses": ["colors.text", "colors.outline"]})
        if c.get("primary") and c.get("outline"):
            out.append({"name": "kit-primary-with-outline", "color": c["primary"], "stroke": {"color": c["outline"], "width": PALETTE_STROKE_WIDTH}, "uses": ["colors.primary", "colors.outline"]})
        if not out:   # a kit with one usable colour: the text in that colour alone, never a colour from outside the kit
            out = [{"name": f"kit-{role}", "color": c[role], "uses": [f"colors.{role}"]} for role in ("text", "primary", "background", "outline") if c.get(role)]
        return out

    def take_palettes(self) -> Optional[List[Dict[str, Any]]]:
        pals = self.variant_palettes()
        if not pals:
            return None
        self.palettes = pals
        for p in pals:
            self.used_keys.update(p["uses"])
            self.slot_keys.update(p["uses"])
        return [{k: v for k, v in p.items() if k != "uses"} for p in pals]

    # ---- the report
    def unmapped_and_unused(self) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        data = self.kit.data
        unmapped: List[Dict[str, str]] = []
        unused: List[Dict[str, str]] = []
        colors = data.get("colors") if data.get("colors") is not None else data.get("colours")
        spelled = "colors" if data.get("colors") is not None else "colours"
        for key, value in data.items():
            if value is None or key in ("schema", "colors", "colours"):
                continue
            if key in NO_COUNTERPART:
                unmapped.append({"key": key, "reason": NO_COUNTERPART[key]})
            elif key not in BRAND_KEYS + KIT_KEYS:
                unmapped.append({"key": key, "reason": "not a kit@1 key"})
            elif key == "font":
                if self.font is None:
                    fams = sorted({f"{s['font_id']}: {s['family']}" for s in self.font_seen})
                    unmapped.append({"key": key, "reason": f"no registered font_id resolves to the family {value!r} on this machine (registered: {', '.join(fams) or 'none'})"})
                elif key not in self.slot_keys:
                    unused.append({"key": key, "reason": "every text element gives its own font_id, or there is none"})
            elif key in ("logo", "logo_position", "logo_scale", "safe_margin", "logo_opacity"):
                if self.logo_reason is not None:
                    unmapped.append({"key": key, "reason": f"no logo is placed: {self.logo_reason}"})
                elif key not in self.used_keys:
                    unused.append({"key": key, "reason": f"the document places its own {LOGO_ID!r}"})
        if isinstance(colors, dict):
            for role, value in colors.items():
                if value is None:
                    continue
                k = f"colors.{role}"
                if role not in COLOR_ROLES:
                    unmapped.append({"key": f"{spelled}.{role}", "reason": "not a kit@1 colour role"})
                elif k in NO_COUNTERPART:
                    unmapped.append({"key": k, "reason": NO_COUNTERPART[k]})
                elif role == "primary" and k not in self.used_keys:
                    unmapped.append({"key": k, "reason": "a ThumbnailDocument has no element role for the primary colour; only render_variants' kit palettes use it"})
                elif k not in self.slot_keys:
                    slot = {"colors.text": "no text element", "colors.background": "no canvas or text band slot",
                            "colors.outline": "no text element gives a stroke without a color"}.get(k, "no slot")
                    unused.append({"key": k, "reason": slot})
        return unmapped, unused

    def report(self) -> Dict[str, Any]:
        unmapped, unused = self.unmapped_and_unused()
        rep: Dict[str, Any] = {"schema": KIT_SCHEMA}
        if self.kit.placeholder:
            rep.update({"read": False, "note": "validate touches no file: the kit is not read; the slots it could fill were filled with placeholders",
                        "slots": [a["target"] for a in self.applied]})
            return rep
        rep.update({"read": True, "file": {"path": self.kit.path, "sha256": self.kit.sha256, "size": self.kit.size}, "name": self.kit.data.get("name"),
                    "colors": dict(self.kit.colors), "applied": self.applied, "kept": self.kept, "unmapped": unmapped, "unused": unused})
        if self.kit.font:
            rep["font"] = dict(self.font, kit=self.kit.font) if self.font else {"kit": self.kit.font, "font_id": None}
        if self.logo:
            rep["logo"] = self.logo
        if self.palettes is not None:
            rep["palettes"] = [{"name": p["name"], "color": p["color"], "background": p.get("background"), "stroke": p.get("stroke"), "uses": p["uses"]} for p in self.palettes]
        return rep


__all__ = ["KIT_SCHEMA", "KIT_TOOLS", "LOGO_ID", "LOGO_POSITIONS", "MAPPING", "NO_COUNTERPART", "MAX_KIT_BYTES", "Kit", "KitApplication", "load_kit",
           "parse_kit", "placeholder_kit", "font_for_family", "logo_box"]
