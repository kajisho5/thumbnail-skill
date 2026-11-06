"""`thumbnail/render_batch`: one ThumbnailDocument template per platform target plus a table of rows (a CSV
file or inline rows) -> one rendered thumbnail per row and target (ECOSYSTEM_ROADMAP.md step 5, ADR-14).

Nothing here draws pixels or decides content. Each row's values are substituted into the template's
`{{column}}` placeholders (text, asset paths, document_id, output name), each named text element may
be shrunk -- never cut, never wrapped -- until it fits a caller-given box, and every resulting document
goes through the ordinary `thumbnail/render` path, so each file gets the same output validation and
`output.preset` check as a single render. Everything that can be checked without rendering (CSV,
placeholders, document structure, fonts, text fit, input files, output names) is checked for every row
before the first file is written."""
from __future__ import annotations

import copy
import csv
import io
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .canonical import sha256_file
from .errors import ThumbnailError
from .fonts import resolve_font
from .model import (OUTPUT_FORMATS, PLATFORM_PRESETS, TextContent, _bool, _enum, _number, _obj, _reject_forbidden, parse_document, parse_options)
from .renderer import text_box

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
BUILTIN_FIELDS = ("row", "preset")
MAX_ROWS = 1000
MAX_TARGETS = 4
MAX_CSV_BYTES = 4 * 1024 * 1024
MAX_CELL_LENGTH = 2000
# UTF-8 is the default (a leading byte-order mark, as Excel writes it, is accepted). cp932 is the
# opt-in regional choice for a CSV saved by Japanese Excel ("CSV (comma delimited)"), which is Shift_JIS.
CSV_ENCODINGS = ("utf-8", "cp932")
CSV_DELIMITERS = (",", ";", "\t")
DEFAULT_NAME = "{{row}}-{{preset}}"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


# ---------------------------------------------------------------- request
def _rows_from_csv(spec: Any, resolve_input: Callable[[str, str], Path]) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    d = _obj(spec, "csv", ("path", "encoding", "delimiter"), ("path",))
    encoding = _enum(d.get("encoding", "utf-8"), "csv.encoding", CSV_ENCODINGS)
    delimiter = _enum(d.get("delimiter", ","), "csv.delimiter", CSV_DELIMITERS)
    path = resolve_input(d["path"], "csv.path")
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ThumbnailError("INVALID_INPUT", f"csv is larger than {MAX_CSV_BYTES} bytes", {"reason": "csv_too_large", "path": str(path)})
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig" if encoding == "utf-8" else encoding)
    except UnicodeDecodeError as e:
        raise ThumbnailError("INVALID_INPUT", f"csv is not valid {encoding}: {e} (a CSV saved by Japanese Excel is usually cp932: set csv.encoding)",
                             {"reason": "csv_encoding", "encoding": encoding, "path": str(path)})
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        table = [r for r in reader if r]   # blank lines are skipped
    except csv.Error as e:
        raise ThumbnailError("INVALID_INPUT", f"csv is malformed: {e}", {"reason": "csv_malformed", "path": str(path)})
    if not table:
        raise ThumbnailError("INVALID_INPUT", "csv has no header row", {"reason": "csv_empty", "path": str(path)})
    header = [h.strip() for h in table[0]]
    if any(not h for h in header) or len(set(header)) != len(header):
        raise ThumbnailError("INVALID_INPUT", f"csv header must have unique, non-empty column names, got {header}", {"reason": "csv_header", "header": header})
    rows: List[Dict[str, str]] = []
    for n, r in enumerate(table[1:], start=2):
        if len(r) != len(header):
            raise ThumbnailError("INVALID_INPUT", f"csv line {n} has {len(r)} field(s), the header has {len(header)}", {"reason": "csv_field_count", "line": n})
        rows.append(dict(zip(header, r)))
    return rows, {"path": str(path), "sha256": f"sha256:{sha256_file(str(path))}", "encoding": encoding, "delimiter": delimiter, "columns": header}


def _rows_inline(value: Any) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    if not isinstance(value, list):
        raise ThumbnailError("INVALID_REQUEST", "rows must be an array of objects", {"field": "rows"})
    rows: List[Dict[str, str]] = []
    for i, r in enumerate(value):
        if not isinstance(r, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in r.items()):
            raise ThumbnailError("INVALID_REQUEST", f"rows[{i}] must be an object of string values", {"field": f"rows[{i}]"})
        rows.append(dict(r))
    return rows, {"inline": True}


def _text_fit(value: Any, where: str) -> Dict[str, Dict[str, int]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be an object keyed by text element_id", {"field": where})
    out: Dict[str, Dict[str, int]] = {}
    for eid, spec in value.items():
        d = _obj(spec, f"{where}.{eid}", ("max_width", "max_height", "min_font_size"), ("max_width", "min_font_size"))
        out[eid] = {"max_width": int(_number(d["max_width"], f"{where}.{eid}.max_width", 1, 7680, integer=True)),
                    "max_height": int(_number(d["max_height"], f"{where}.{eid}.max_height", 1, 7680, integer=True)) if "max_height" in d else None,
                    "min_font_size": int(_number(d["min_font_size"], f"{where}.{eid}.min_font_size", 6, 400, integer=True))}
    return out


def parse_batch(params: Any, resolve_input: Callable[[str, str], Path]) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ThumbnailError("INVALID_REQUEST", "params must be a JSON object")
    _reject_forbidden(params, "params")
    d = _obj(params, "params", ("csv", "rows", "targets", "output", "options"), ("targets", "output"))
    if ("csv" in d) == ("rows" in d):
        raise ThumbnailError("INVALID_REQUEST", "give exactly one of 'csv' (a file) or 'rows' (inline objects)", {"field": "params"})
    options = parse_options(d.get("options"))

    targets_raw = d["targets"]
    if not isinstance(targets_raw, list) or not 1 <= len(targets_raw) <= MAX_TARGETS:
        raise ThumbnailError("INVALID_REQUEST", f"targets must be an array of 1..{MAX_TARGETS} objects", {"field": "targets"})
    targets = []
    for i, t in enumerate(targets_raw):
        td = _obj(t, f"targets[{i}]", ("preset", "document", "text_fit"), ("preset", "document"))
        preset = _enum(td["preset"], f"targets[{i}].preset", tuple(PLATFORM_PRESETS))
        if any(x["preset"] == preset for x in targets):
            raise ThumbnailError("INVALID_REQUEST", f"targets[{i}]: preset {preset!r} is listed twice", {"field": f"targets[{i}].preset"})
        if not isinstance(td["document"], dict):
            raise ThumbnailError("INVALID_REQUEST", f"targets[{i}].document must be a ThumbnailDocument object", {"field": f"targets[{i}].document"})
        targets.append({"preset": preset, "document": td["document"], "text_fit": _text_fit(td.get("text_fit"), f"targets[{i}].text_fit")})

    od = _obj(d["output"], "output", ("directory", "name", "format", "overwrite", "jpeg_quality"), ("directory", "format"))
    if not isinstance(od["directory"], str) or not od["directory"]:
        raise ThumbnailError("INVALID_REQUEST", "output.directory must be a non-empty string", {"field": "output.directory"})
    fmt = od["format"]
    if fmt not in OUTPUT_FORMATS:
        raise ThumbnailError("UNSUPPORTED_FORMAT", f"output.format {fmt!r} is not supported; supported: {sorted(OUTPUT_FORMATS)}", {"field": "output.format"})
    name = od.get("name", DEFAULT_NAME)
    if not isinstance(name, str) or not name:
        raise ThumbnailError("INVALID_REQUEST", "output.name must be a non-empty string", {"field": "output.name"})
    if "jpeg_quality" in od and fmt != "jpeg":
        raise ThumbnailError("INVALID_REQUEST", "output.jpeg_quality is only accepted when format is 'jpeg'", {"field": "output.jpeg_quality"})
    output = {"directory": od["directory"], "name": name, "format": fmt, "overwrite": _bool(od.get("overwrite", False), "output.overwrite"),
              "jpeg_quality": int(_number(od.get("jpeg_quality", 90), "output.jpeg_quality", 1, 100, integer=True))}

    rows, source = _rows_from_csv(d["csv"], resolve_input) if "csv" in d else _rows_inline(d["rows"])
    if not rows:
        raise ThumbnailError("INVALID_INPUT", "the table has no rows", {"reason": "no_rows"})
    if len(rows) > MAX_ROWS:
        raise ThumbnailError("INVALID_INPUT", f"the table has {len(rows)} rows; at most {MAX_ROWS} are accepted per batch", {"reason": "too_many_rows"})
    for i, r in enumerate(rows, start=1):
        for k, v in r.items():
            if k in BUILTIN_FIELDS:
                raise ThumbnailError("INVALID_INPUT", f"column {k!r} is reserved (built-in placeholder)", {"reason": "reserved_column", "column": k})
            if len(v) > MAX_CELL_LENGTH:
                raise ThumbnailError("INVALID_INPUT", f"row {i}, column {k!r} is longer than {MAX_CELL_LENGTH} characters", {"reason": "cell_too_long", "row": i})
    return {"rows": rows, "source": source, "targets": targets, "output": output, "options": options}


# ---------------------------------------------------------------- substitution
def _substitute(text: str, values: Dict[str, str], where: str) -> str:
    def one(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key not in values:
            raise ThumbnailError("INVALID_REQUEST", f"{where}: placeholder {{{{{key}}}}} names no column (columns: {sorted(k for k in values if k not in BUILTIN_FIELDS)})",
                                 {"field": where, "reason": "unknown_placeholder", "placeholder": key})
        return values[key]
    return PLACEHOLDER_RE.sub(one, text)


def fill_template(template: Dict[str, Any], values: Dict[str, str]) -> Dict[str, Any]:
    """A deep copy of the raw template with placeholders replaced in document_id, every asset path and every
    text element's text. Values are inserted literally (never re-scanned for placeholders)."""
    doc = copy.deepcopy(template)
    if isinstance(doc.get("document_id"), str):
        doc["document_id"] = _substitute(doc["document_id"], values, "document.document_id")
    for i, a in enumerate(doc.get("assets") or []):
        if isinstance(a, dict) and isinstance(a.get("path"), str):
            a["path"] = _substitute(a["path"], values, f"document.assets[{i}].path")
    for i, e in enumerate(doc.get("elements") or []):
        t = e.get("text") if isinstance(e, dict) else None
        if isinstance(t, dict) and isinstance(t.get("text"), str):
            t["text"] = _substitute(t["text"], values, f"document.elements[{i}].text.text")
    return doc


def fit_text(content: TextContent, spec: Dict[str, Optional[int]], canvas: Tuple[int, int], where: str) -> Dict[str, Any]:
    """Largest font size from the template's own size down to `min_font_size` at which the element's painted
    box (text_box: layout block, background, ink with stroke and shadow) fits max_width x max_height and lies
    inside the canvas. Mutates `content.font_size`. Text is never cut or wrapped: when even the minimum does not
    fit, the row is refused (VALIDATION_ERROR, reason text_does_not_fit)."""
    font = resolve_font(content.font_id)
    start = content.font_size
    lo = min(spec["min_font_size"], start)  # a template already below the minimum is used as it is, never enlarged
    for size in range(start, lo - 1, -1):
        content.font_size = size
        x0, y0, x1, y1 = text_box(content, font)
        if x1 - x0 <= spec["max_width"] and (spec["max_height"] is None or y1 - y0 <= spec["max_height"]):
            if x0 < 0 or y0 < 0 or x1 > canvas[0] or y1 > canvas[1]:
                content.font_size = start
                raise ThumbnailError("VALIDATION_ERROR", f"{where}: at {size}px the text fits its box but lies partly outside the {canvas[0]}x{canvas[1]} canvas",
                                     {"reason": "text_outside_canvas", "font_size": size, "box": [x0, y0, x1, y1]})
            return {"font_size": size, "template_font_size": start, "min_font_size": spec["min_font_size"], "shrunk": size < start,
                    "box": [x0, y0, x1 - x0, y1 - y0]}
    content.font_size = start
    raise ThumbnailError("VALIDATION_ERROR", f"{where}: the text does not fit {spec['max_width']} px wide" + (f" x {spec['max_height']} px high" if spec["max_height"] else "") + " even at the minimum "
                         + f"{spec['min_font_size']}px (text is never cut)", {"reason": "text_does_not_fit", "min_font_size": spec["min_font_size"],
                                                                           "box_at_minimum": [x0, y0, x1 - x0, y1 - y0], "max_width": spec["max_width"], "max_height": spec["max_height"]})


def _with_row(e: ThumbnailError, row: int, preset: str) -> ThumbnailError:
    e.message = f"row {row} ({preset}): {e.message}"
    e.details = {**e.details, "row": row, "preset": preset}
    return e


def plan_batch(req: Dict[str, Any], resolve_input: Callable[[str, str], Path], resolve_write: Callable[[str], Path]) -> List[Dict[str, Any]]:
    """Every (row, target) as a concrete thumbnail/render params document, or the first problem found. Writes nothing."""
    ext = OUTPUT_FORMATS[req["output"]["format"]]["extensions"][0]
    plans: List[Dict[str, Any]] = []
    seen: Dict[str, Tuple[int, str]] = {}
    for n, row in enumerate(req["rows"], start=1):
        for target in req["targets"]:
            preset = target["preset"]
            values = {**row, "row": str(n), "preset": preset}
            try:
                raw = fill_template(target["document"], values)
                p = PLATFORM_PRESETS[preset]
                doc = parse_document(raw, default_size=(p["width"], p["height"]))
                fitted: Dict[str, Any] = {}
                for eid, spec in target["text_fit"].items():
                    el = next((e for e in doc.elements if e.element_id == eid), None)
                    if el is None or el.text is None:
                        raise ThumbnailError("INVALID_REQUEST", f"text_fit names {eid!r}, which is not a text element of the template", {"field": "text_fit", "element_id": eid})
                    fitted[eid] = fit_text(el.text, spec, (doc.canvas.width, doc.canvas.height), f"element {eid!r}")
                for e in doc.elements:
                    if e.text is not None:
                        resolve_font(e.text.font_id)
                for a in doc.assets:
                    resolve_input(a.path, f"asset {a.asset_id!r}")
                name = _substitute(req["output"]["name"], values, "output.name")
                if not NAME_RE.match(name):
                    raise ThumbnailError("INVALID_REQUEST", f"output.name gives {name!r}, which is not a safe file name ([A-Za-z0-9._-], at most 128 characters, "
                                         "starting with a letter or digit); use {{row}} or an ASCII column", {"field": "output.name", "reason": "unsafe_name", "name": name})
                rel = f"{req['output']['directory'].rstrip('/')}/{name}{ext}"
                target_path = resolve_write(rel)
                if str(target_path) in seen:
                    other = seen[str(target_path)]
                    raise ThumbnailError("INVALID_REQUEST", f"output.name gives {name!r} again (also row {other[0]}, {other[1]})", {"field": "output.name", "reason": "duplicate_name"})
                seen[str(target_path)] = (n, preset)
                if target_path.exists() and not req["output"]["overwrite"]:
                    raise ThumbnailError("OUTPUT_ERROR", f"output already exists (set overwrite: true to replace it): {target_path}", {"reason": "exists", "path": str(target_path)})
            except ThumbnailError as e:
                raise _with_row(e, n, preset)
            document = doc.to_dict()
            if not document["metadata"]:
                del document["metadata"]
            out = {"path": rel, "format": req["output"]["format"], "overwrite": req["output"]["overwrite"], "preset": preset}
            if req["output"]["format"] == "jpeg":
                out["jpeg_quality"] = req["output"]["jpeg_quality"]
            plans.append({"row": n, "preset": preset, "fitted": fitted, "params": {"document": document, "output": out}})
    return plans


__all__ = ["parse_batch", "plan_batch", "fill_template", "fit_text", "PLACEHOLDER_RE", "MAX_ROWS", "MAX_TARGETS", "CSV_ENCODINGS", "CSV_DELIMITERS", "DEFAULT_NAME"]
