"""Layered output (`thumbnail/render_layers`, `thumbnail render --layers DIR`; ECOSYSTEM_ROADMAP.md step 8, ADR-17).

The renderer records every composite it makes (renderer.LayerRecorder): the canvas background, then per element,
in stacking order, the image it pasted or the text background band and the text it drew, each after opacity. Each
recorded composite becomes one PNG with alpha, cropped to its visible pixels on the canvas, and a manifest gives its
order, position and size. Replaying the manifest reproduces the thumbnail pixel for pixel: start from layer 0 (the
background, full canvas) and alpha-composite every other layer at its position, in order. That is the operation
the renderer itself performed. A cropped-away transparent pixel adds nothing, because source-over with alpha 0
leaves the destination unchanged. The tool checks this by reading the written layers back from disk."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from .canonical import sha256_file
from .errors import ThumbnailError
from .renderer import LayerRecorder

MANIFEST_SCHEMA = "thumbnail-skill/layers@1"
MANIFEST_NAME = "layers.json"
COMPOSE_RULE = ("start from layer 0 (the background, the full canvas) as the base image; then alpha-composite every further layer at "
                "its position, in order (Porter-Duff source-over on straight 8-bit RGBA, as Pillow's Image.alpha_composite)")


def build_layers(canvas_size: Tuple[int, int], background: "Image.Image", recorder: LayerRecorder) -> List[Dict[str, Any]]:
    """[{image, position, size, meta}] in compositing order, layer 0 the background; every other layer cropped to the
    part of the canvas it covers and then to its non-transparent pixels. A composite with no visible pixel is kept in
    the list with image None (it changed nothing)."""
    W, H = canvas_size
    out: List[Dict[str, Any]] = [{"image": background, "position": (0, 0), "size": (W, H), "meta": {"role": "background", "element_id": None, "type": None, "z_index": None}}]
    for src, (dx, dy), meta in recorder.ops:
        x0, y0 = max(0, dx), max(0, dy)
        x1, y1 = min(W, dx + src.width), min(H, dy + src.height)
        piece: Optional["Image.Image"] = None
        pos = (x0, y0)
        if x1 > x0 and y1 > y0:
            piece = src.crop((x0 - dx, y0 - dy, x1 - dx, y1 - dy)).convert("RGBA")
            bbox = piece.getchannel("A").getbbox()
            if bbox is None:
                piece = None
            else:
                piece = piece.crop(bbox)
                pos = (x0 + bbox[0], y0 + bbox[1])
        out.append({"image": piece, "position": pos, "size": piece.size if piece is not None else (0, 0), "meta": meta})
    return out


def compose(canvas_size: Tuple[int, int], layers: List[Tuple["Image.Image", Tuple[int, int]]]) -> "Image.Image":
    """The manifest's rule: layer 0 as the base, the rest alpha-composited at their positions in order."""
    base = layers[0][0].convert("RGBA").copy()
    if base.size != canvas_size:
        raise ThumbnailError("VALIDATION_ERROR", "layer 0 is not the full canvas", {"reason": "layers_mismatch"})
    for img, pos in layers[1:]:
        base.alpha_composite(img.convert("RGBA"), dest=pos)
    return base


def _file_name(order: int, meta: Dict[str, Any]) -> str:
    if meta["role"] == "background":
        return f"{order:02d}-background.png"
    suffix = "-background" if meta["role"] == "text_background" else ""
    return f"{order:02d}-{meta['element_id']}{suffix}.png"


def write_layers(directory: Path, built: List[Dict[str, Any]], canvas_size: Tuple[int, int], reference: "Image.Image",
                 thumbnail: Dict[str, Any], reference_note: str) -> Dict[str, Any]:
    """Write every visible layer as a PNG and the manifest, then rebuild the image from the files on disk by the
    manifest and compare it with `reference` byte for byte. A mismatch is VALIDATION_ERROR (and the manifest is not
    written)."""
    directory.mkdir(parents=True, exist_ok=True)
    entries: List[Dict[str, Any]] = []
    for order, layer in enumerate(built):
        meta = layer["meta"]
        entry: Dict[str, Any] = {"order": order, "role": meta["role"], "element_id": meta.get("element_id"), "type": meta.get("type"), "z_index": meta.get("z_index")}
        if layer["image"] is None:
            entry.update({"file": None, "omitted": "no visible pixel on the canvas (compositing it changes nothing)"})
        else:
            name = _file_name(order, meta)
            path = directory / name
            layer["image"].save(path, format="PNG")
            entry.update({"file": name, "position": {"x": layer["position"][0], "y": layer["position"][1]},
                          "size": {"width": layer["size"][0], "height": layer["size"][1]}, "mode": "RGBA", "sha256": f"sha256:{sha256_file(str(path))}"})
        entries.append(entry)

    # read back exactly what a designer gets and rebuild by the manifest
    stack: List[Tuple["Image.Image", Tuple[int, int]]] = []
    for e in entries:
        if e["file"] is None:
            continue
        with Image.open(directory / e["file"]) as im:
            im.load()
            stack.append((im.copy(), (e["position"]["x"], e["position"]["y"])))
    rebuilt = compose(canvas_size, stack)
    equal = rebuilt.tobytes() == reference.convert("RGBA").tobytes()
    if not equal:
        raise ThumbnailError("VALIDATION_ERROR", "the layers composited by the manifest do not equal the thumbnail", {"reason": "layers_mismatch"})
    manifest = {"schema": MANIFEST_SCHEMA, "canvas": {"width": canvas_size[0], "height": canvas_size[1]}, "thumbnail": thumbnail,
                "compose": COMPOSE_RULE, "layers": entries,
                "verified": {"pixel_equal": True, "against": reference_note, "from": "the layer files read back from disk"}}
    (directory / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


__all__ = ["build_layers", "compose", "write_layers", "MANIFEST_SCHEMA", "MANIFEST_NAME", "COMPOSE_RULE"]
