"""Font registry: the only way a request may name a font is by `font_id` from FONT_REGISTRY below.

A request never carries a font path. Adding a font to this skill means adding an entry here (a
review, not a runtime decision by a caller). Each entry lists the well-known filesystem locations a
real font of that role is found at on Linux, macOS and Windows; the first candidate that exists on
this machine is used, exactly as ffmpeg-skill's doctor detects encoders/filters instead of assuming
them. `resolve_font` never substitutes a different font_id when the requested one has no resolvable
file on this machine: it fails with MISSING_INPUT so a caller sees a clear, structured reason instead
of a thumbnail rendered with a silently different typeface. `doctor` reports, for every font_id,
whether it resolved on this machine and to which concrete file (path + sha256), so "which font
actually rendered this" is always inspectable, never a guess."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .canonical import sha256_file
from .errors import ThumbnailError

# font_id -> ordered (path, ttc_index) candidates, most-preferred first. index is 0 for a plain
# .ttf/.otf and the face index for a .ttc collection. Only the first candidate that exists on this
# machine is used; the rest exist so the same font_id resolves sensibly across platforms.
_HOME = os.path.expanduser("~")
FONT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "sans": {
        "display_name": "Sans", "style": "regular", "role": "default UI sans-serif",
        "candidates": [
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
            ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 0),
            ("/usr/share/fonts/TTF/DejaVuSans.ttf", 0),
            ("/Library/Fonts/Arial.ttf", 0),
            ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
            (r"C:\Windows\Fonts\arial.ttf", 0),
            (os.path.join(_HOME, ".local", "share", "fonts", "DejaVuSans.ttf"), 0),
        ],
    },
    "sans-bold": {
        "display_name": "Sans Bold", "style": "bold", "role": "emphasis / headline sans-serif",
        "candidates": [
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
            ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 0),
            ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 0),
            ("/Library/Fonts/Arial Bold.ttf", 0),
            ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
            (r"C:\Windows\Fonts\arialbd.ttf", 0),
        ],
    },
    "serif": {
        "display_name": "Serif", "style": "regular", "role": "serif text",
        "candidates": [
            ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf", 0),
            ("/usr/share/fonts/truetype/freefont/FreeSerif.ttf", 0),
            ("/Library/Fonts/Times New Roman.ttf", 0),
            ("/System/Library/Fonts/Supplemental/Times New Roman.ttf", 0),
            (r"C:\Windows\Fonts\times.ttf", 0),
        ],
    },
    "mono": {
        "display_name": "Monospace", "style": "regular", "role": "monospaced text",
        "candidates": [
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
            ("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf", 0),
            ("/Library/Fonts/Courier New.ttf", 0),
            (r"C:\Windows\Fonts\cour.ttf", 0),
        ],
    },
    "cjk": {
        "display_name": "CJK Sans", "style": "regular", "role": "Japanese / Chinese / Korean text",
        "candidates": [
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
            (r"C:\Windows\Fonts\YuGothM.ttc", 0),
            (r"C:\Windows\Fonts\msgothic.ttc", 0),
        ],
    },
}


@dataclass
class ResolvedFont:
    font_id: str
    display_name: str
    path: str
    index: int
    sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {"font_id": self.font_id, "font_name": self.display_name, "path": self.path, "index": self.index, "font_file_hash": f"sha256:{self.sha256}"}


def font_ids() -> List[str]:
    return sorted(FONT_REGISTRY)


def _first_existing(font_id: str) -> Optional[Tuple[str, int]]:
    for path, index in FONT_REGISTRY[font_id]["candidates"]:
        if os.path.isfile(path):
            return path, index
    return None


def font_status(font_id: str) -> Dict[str, Any]:
    """Detection result for one font_id, for `doctor`. Never guesses: `available` only when a
    candidate file was found to exist on this machine right now."""
    if font_id not in FONT_REGISTRY:
        return {"font_id": font_id, "status": "unknown_font_id"}
    found = _first_existing(font_id)
    entry = FONT_REGISTRY[font_id]
    if found is None:
        return {"font_id": font_id, "display_name": entry["display_name"], "status": "unavailable",
                "candidates_checked": [c[0] for c in entry["candidates"]]}
    path, index = found
    return {"font_id": font_id, "display_name": entry["display_name"], "status": "available",
            "path": path, "index": index, "sha256": f"sha256:{sha256_file(path)}"}


def resolve_font(font_id: object) -> ResolvedFont:
    if not isinstance(font_id, str) or not font_id:
        raise ThumbnailError("INVALID_REQUEST", "font_id must be a non-empty string", {"field": "font_id"})
    if font_id not in FONT_REGISTRY:
        raise ThumbnailError("MISSING_INPUT", f"font_id {font_id!r} is not registered", {"reason": "unknown_font_id", "font_id": font_id, "available": font_ids()})
    found = _first_existing(font_id)
    if found is None:
        raise ThumbnailError("MISSING_INPUT", f"font_id {font_id!r} is registered but no candidate font file exists on this machine "
                             f"(run `thumbnail doctor` to see what was checked); this skill never substitutes a different font",
                             {"reason": "font_file_missing", "font_id": font_id, "candidates_checked": [c[0] for c in FONT_REGISTRY[font_id]["candidates"]]})
    path, index = found
    return ResolvedFont(font_id, FONT_REGISTRY[font_id]["display_name"], path, index, sha256_file(path))


__all__ = ["FONT_REGISTRY", "ResolvedFont", "font_ids", "font_status", "resolve_font"]
