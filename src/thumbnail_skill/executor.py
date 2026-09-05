"""Executor: params -> validated ThumbnailDocument -> resolved assets/fonts -> render plan -> Pillow
render -> validated artifact -> response document. Also the standalone `thumbnail/extract_frame`
path, which is exactly ffmpeg-skill/look's `--at` behaviour and nothing more: one caller-given
timestamp in, one frame out, no scene detection, no scoring.

Identity and reuse: the cache key is computed from canonical JSON of the validated document, the
resolved *content* identity of every asset (a still image's own sha256; a video frame's *source
video* sha256 plus the requested timestamp, not the decoded frame's bytes, so a cache hit never has
to decode video) and every font actually used (its resolved file's sha256), plus this skill's version
and the Pillow version that would draw it. A cache hit is re-validated (file exists, opens as the
declared format, size and dimensions match) before being reused; a broken cache entry is never
reused, it is rebuilt."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import PIL
from PIL import Image, UnidentifiedImageError

from . import RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FfmpegSkill
from .canonical import sha256_file, stable_hash
from .errors import ThumbnailError
from .fonts import ResolvedFont, resolve_font
from .model import OUTPUT_FORMATS, OutputSpec, RenderRequest, ThumbnailAsset, ThumbnailDocument, parse_render_request
from .renderer import render_document
from .security import PathPolicy

RESPONSE_SCHEMA_ID = f"{SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION}"
CACHE_DIR_NAME = ".thumbnail-skill/cache"
TMP_DIR_NAME = ".thumbnail-skill/tmp"
MANIFEST_SCHEMA = f"{SKILL_ID}/cache-manifest@1"
ENGINE_NAME = "Pillow"

Image.MAX_IMAGE_PIXELS = 64_000_000   # refuse a decompression-bomb-scale source image (INVALID_INPUT, not a crash)


def _engine_version() -> str:
    return str(getattr(PIL, "__version__", "unknown"))


def _open_image(path: Path, where: str) -> "Image.Image":
    try:
        img = Image.open(path)
        img.load()
    except FileNotFoundError:
        raise ThumbnailError("INVALID_INPUT", f"{where}: file not found: {path}", {"reason": "not_found", "path": str(path)})
    except UnidentifiedImageError:
        raise ThumbnailError("INVALID_INPUT", f"{where}: not a readable image: {path}", {"reason": "unreadable_image", "path": str(path)})
    except Image.DecompressionBombError as e:
        raise ThumbnailError("INVALID_INPUT", f"{where}: image exceeds the maximum accepted pixel count: {e}", {"reason": "decompression_bomb", "path": str(path)})
    except OSError as e:
        raise ThumbnailError("INVALID_INPUT", f"{where}: cannot read image: {e}", {"reason": "unreadable_image", "path": str(path)})
    return img


def _finalize_for_save(canvas: "Image.Image", fmt: str, background_rgb: Tuple[int, int, int]) -> "Image.Image":
    """RGBA canvas -> the pixel mode the target format needs. JPEG has no alpha channel, so the
    canvas is flattened onto an opaque copy of its own background colour (correct compositing, not a
    bare channel drop) before conversion; PNG keeps the alpha channel as drawn."""
    if fmt == "png":
        return canvas
    base = Image.new("RGBA", canvas.size, (*background_rgb, 255))
    base.alpha_composite(canvas)
    return base.convert("RGB")


class Executor:
    def __init__(self, policy: PathPolicy, ffmpeg_skill_dir: Optional[str] = None, timeout: float = 120.0):
        self.policy = policy
        self.ffmpeg_skill_dir = ffmpeg_skill_dir
        self.timeout = timeout
        self._skill: Optional[FfmpegSkill] = None
        self.warnings: List[str] = []

    def _ffmpeg_skill(self) -> FfmpegSkill:
        if self._skill is None:
            self._skill = FfmpegSkill.locate(self.ffmpeg_skill_dir, self.timeout)
            info = self._skill.info()
            if not info.supported:
                raise ThumbnailError("TOOL_ERROR", "ffmpeg-skill at " + str(info.directory) + " is not usable: " + "; ".join(info.problems),
                                     {"reason": "ffmpeg_skill_incompatible", "problems": info.problems}, retryable=False)
        return self._skill

    def _ffmpeg_skill_version(self) -> Optional[str]:
        """The ffmpeg-skill version actually engaged for this request, or None when no video_frame
        asset needed it. Included in identity so an ffmpeg-skill upgrade that changes what `look`
        decodes for the same timestamp busts the cache instead of silently reusing a stale frame."""
        return self._skill.info().version if self._skill is not None else None

    # ---- envelope
    def _envelope(self, ok: bool, status: str, body: Dict[str, Any]) -> Dict[str, Any]:
        doc: Dict[str, Any] = {"schema": RESPONSE_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "ok": ok, "status": status}
        doc.update(body)
        doc.setdefault("warnings", list(self.warnings))
        return doc

    def response(self, tool: str, params: Any) -> Dict[str, Any]:
        """Always returns one response document; never raises."""
        try:
            if tool == "thumbnail/validate":
                return self._envelope(True, "ok", {"validation": self._validate(params)})
            if tool == "thumbnail/render":
                return self._envelope(True, "ok", self._render(params))
            if tool == "thumbnail/extract_frame":
                return self._envelope(True, "ok", self._extract_frame(params))
            raise ThumbnailError("UNSUPPORTED_OPERATION", f"unknown tool {tool!r}", {"tool": tool, "supported": TOOLS})
        except ThumbnailError as e:
            return self._envelope(False, "cancelled" if e.code == "CANCELLED" else "error", {"error": e.to_dict()})
        except Exception as e:  # a bug in this skill: still one document, never a traceback on stdout
            err = ThumbnailError("INTERNAL_ERROR", f"{type(e).__name__}: {e}")
            return self._envelope(False, "error", {"error": err.to_dict()})

    # ---- thumbnail/validate: structural only, no filesystem, no fonts, no ffmpeg
    def _validate(self, params: Any) -> Dict[str, Any]:
        """Accepts either a bare `{"document": ...}` or the same shape `thumbnail/render` takes
        (document + optional output + optional options): whichever file a caller already has,
        `validate` checks it structurally without ever touching a file, a font or ffmpeg-skill."""
        if not isinstance(params, dict):
            raise ThumbnailError("INVALID_REQUEST", "params must be a JSON object")
        body = params if "document" in params else {"document": params}
        req = parse_render_request(body)
        doc = req.document
        return {"ok": True, "document_id": doc.document_id, "canvas": doc.canvas.to_dict(),
                "assets": len(doc.assets), "elements": len(doc.elements),
                "element_types": sorted({e.type for e in doc.elements}), "asset_kinds": sorted({a.kind for a in doc.assets})}

    # ---- asset / font resolution
    def _resolve_asset_identity(self, asset: ThumbnailAsset) -> Dict[str, Any]:
        resolved = self.policy.resolve_input(asset.path, f"asset {asset.asset_id!r}")
        if asset.kind == "image":
            # a cheap open+verify is enough to prove "this is a real image" for identity purposes;
            # the real decode for compositing happens once, lazily, only on a cache miss
            img = _open_image(resolved, f"asset {asset.asset_id!r}")
            img.close()
            return {"asset_id": asset.asset_id, "kind": "image", "path": str(resolved), "sha256": sha256_file(str(resolved))}
        skill = self._ffmpeg_skill()
        meta = skill.probe(str(resolved), self.timeout)
        if not meta.get("video"):
            raise ThumbnailError("INVALID_INPUT", f"asset {asset.asset_id!r} has no video stream", {"asset_id": asset.asset_id, "reason": "no_video_stream"})
        duration = float(meta.get("duration") or 0.0)
        if duration <= 0:
            # a video with no known duration is a fact we cannot verify a timestamp against: treat it
            # as an unusable input rather than silently skipping the beyond-duration check below
            # (matching audio-production-skill's own SOURCE_TRACK rule: duration <= 0 is INVALID_INPUT)
            raise ThumbnailError("INVALID_INPUT", f"asset {asset.asset_id!r}: source video has no known duration; cannot verify the timestamp is in range",
                                 {"asset_id": asset.asset_id, "reason": "no_duration"})
        if asset.timestamp is not None and asset.timestamp > duration:
            raise ThumbnailError("INVALID_TIME_RANGE", f"asset {asset.asset_id!r}: timestamp {asset.timestamp}s is beyond the source duration ({duration:.3f}s)",
                                 {"asset_id": asset.asset_id, "timestamp": asset.timestamp, "duration": duration})
        return {"asset_id": asset.asset_id, "kind": "video_frame", "path": str(resolved), "sha256": sha256_file(str(resolved)),
                "timestamp": asset.timestamp, "source_duration": duration}

    def _resolve_font_identities(self, document: ThumbnailDocument) -> Dict[str, ResolvedFont]:
        font_ids = sorted({e.text.font_id for e in document.elements if e.text is not None})
        return {fid: resolve_font(fid) for fid in font_ids}

    # ---- identity / cache
    def _identity(self, kind: str, doc_for_identity: Dict[str, Any]) -> str:
        return stable_hash({"kind": kind, "skill": SKILL_ID, "skill_version": VERSION, "engine": ENGINE_NAME, "engine_version": _engine_version(), **doc_for_identity})

    def _cache_paths(self, identity: str, fmt: str) -> Tuple[Path, Path]:
        cache_dir = self.policy.resolve_work_dir(CACHE_DIR_NAME)
        cache_dir.mkdir(parents=True, exist_ok=True)
        ext = OUTPUT_FORMATS[fmt]["extensions"][0]
        return cache_dir / f"{identity}{ext}", cache_dir / f"{identity}.json"

    def _try_reuse(self, identity: str, fmt: str) -> Optional[Dict[str, Any]]:
        cache_file, manifest_file = self._cache_paths(identity, fmt)
        if not (cache_file.is_file() and manifest_file.is_file()):
            return None
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("identity") != identity:
            return None
        try:
            if cache_file.stat().st_size != manifest.get("size") or sha256_file(str(cache_file)) != manifest.get("sha256"):
                return None
            img = Image.open(cache_file)
            img.verify()
        except (OSError, ValueError, UnidentifiedImageError):
            return None
        return manifest

    def _store_cache(self, identity: str, fmt: str, artifact_path: Path, extra: Dict[str, Any]) -> Dict[str, Any]:
        cache_file, manifest_file = self._cache_paths(identity, fmt)
        shutil.copyfile(artifact_path, cache_file)
        manifest = {"schema": MANIFEST_SCHEMA, "identity": identity, "size": cache_file.stat().st_size, "sha256": sha256_file(str(cache_file)), **extra}
        manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    def _place_output(self, source: Path, out: OutputSpec) -> Path:
        target = self.policy.resolve_write_path(out.path, "output")
        if target.exists() and not out.overwrite:
            raise ThumbnailError("OUTPUT_ERROR", f"output already exists (set overwrite: true to replace it): {target}", {"reason": "exists", "path": str(target)})
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    def _tmp_path(self, fmt: str) -> Path:
        tmp_dir = self.policy.resolve_work_dir(TMP_DIR_NAME)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir / f"{uuid.uuid4().hex}{OUTPUT_FORMATS[fmt]['extensions'][0]}"

    def _apply_policy_overrides(self, options: Dict[str, Any]) -> None:
        """A request's `options.workspace` / `options.allowed_input_roots` may override the policy the
        CLI built from --workspace / --allowed-input; either one alone keeps the other as it was."""
        workspace = options.get("workspace")
        roots = options.get("allowed_input_roots")
        if workspace is None and roots is None:
            return
        new_workspace = workspace or str(self.policy.workspace)
        new_roots = roots if roots is not None else ([str(r) for r in self.policy.allowed_input_roots] if self.policy.allowed_input_roots else None)
        self.policy = PathPolicy(new_workspace, new_roots)

    # ---- thumbnail/render
    def _render(self, params: Any) -> Dict[str, Any]:
        req: RenderRequest = parse_render_request(params)
        if not req.output.path:
            raise ThumbnailError("INVALID_REQUEST", "'output' is required for thumbnail/render", {"field": "output"})
        document, out = req.document, req.output
        reuse = bool(req.options.get("reuse", True))
        self.timeout = float(req.options.get("timeout") or self.timeout)
        self._apply_policy_overrides(req.options)
        if req.options.get("ffmpeg_skill"):
            self.ffmpeg_skill_dir = req.options["ffmpeg_skill"]

        asset_identities = {a.asset_id: self._resolve_asset_identity(a) for a in document.assets}
        fonts_used = self._resolve_font_identities(document)
        identity_doc = {"document": document.to_dict(), "output_format": out.format, "jpeg_quality": out.jpeg_quality if out.format == "jpeg" else None,
                        "assets": {aid: {k: v for k, v in ident.items() if k != "path"} for aid, ident in asset_identities.items()},
                        "fonts": {fid: rf.sha256 for fid, rf in fonts_used.items()}, "ffmpeg_skill_version": self._ffmpeg_skill_version()}
        identity = self._identity("render", identity_doc)

        target = self.policy.resolve_write_path(out.path, "output")   # raise early (traversal, workspace) before any work
        if target.exists() and not out.overwrite:
            raise ThumbnailError("OUTPUT_ERROR", f"output already exists (set overwrite: true to replace it): {target}", {"reason": "exists", "path": str(target)})

        reused = False
        manifest = self._try_reuse(identity, out.format) if reuse else None
        if manifest is not None:
            cache_file, _ = self._cache_paths(identity, out.format)
            final = self._place_output(cache_file, out)
            reused = True
            width, height = document.canvas.width, document.canvas.height
            out_sha256, out_size = manifest["sha256"], manifest["size"]
        else:
            images: Dict[str, "Image.Image"] = {}
            frame_provenance: Dict[str, Any] = {}
            try:
                for asset in document.assets:
                    ident = asset_identities[asset.asset_id]
                    if asset.kind == "image":
                        images[asset.asset_id] = _open_image(Path(ident["path"]), f"asset {asset.asset_id!r}")
                    else:
                        skill = self._ffmpeg_skill()
                        tmp_dir = self.policy.resolve_work_dir(TMP_DIR_NAME)
                        tmp_dir.mkdir(parents=True, exist_ok=True)
                        frame_path = skill.extract_frame(ident["path"], asset.timestamp, tmp_dir, f"frame_{asset.asset_id}_{identity[:12]}", self.timeout)
                        frame_img = _open_image(frame_path, f"asset {asset.asset_id!r}")
                        images[asset.asset_id] = frame_img
                        frame_provenance[asset.asset_id] = {"extracted_path": str(frame_path), "sha256": sha256_file(str(frame_path)),
                                                            "width": frame_img.width, "height": frame_img.height, "tool": "ffmpeg-skill/look"}

                canvas = render_document(document, images, fonts_used)
                bg = tuple(int(document.canvas.background.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
                final_img = _finalize_for_save(canvas, out.format, bg)
                tmp_out = self._tmp_path(out.format)
                save_kwargs: Dict[str, Any] = {}
                if out.format == "jpeg":
                    save_kwargs = {"quality": out.jpeg_quality, "optimize": True}
                final_img.save(tmp_out, format=OUTPUT_FORMATS[out.format]["pillow_format"], **save_kwargs)
            finally:
                for img in images.values():
                    try:
                        img.close()
                    except Exception:
                        pass

            width, height, out_sha256, out_size = self._validate_output(tmp_out, document.canvas.width, document.canvas.height, out.format)
            self._store_cache(identity, out.format, tmp_out, {"width": width, "height": height, "format": out.format,
                                                               "assets": asset_identities, "fonts": {fid: rf.to_dict() for fid, rf in fonts_used.items()},
                                                               "frames": frame_provenance})
            final = self._place_output(tmp_out, out)
            try:
                tmp_out.unlink()
            except OSError:
                pass

        provenance = {"skill": SKILL_ID, "skill_version": VERSION, "operation": "thumbnail/render", "engine": ENGINE_NAME, "engine_version": _engine_version(),
                      "document_id": document.document_id, "identity": identity, "reused": reused,
                      "assets": [{k: v for k, v in ident.items()} for ident in asset_identities.values()],
                      "fonts": [rf.to_dict() for rf in fonts_used.values()], "output_hash": f"sha256:{out_sha256}"}
        return {"output": str(final), "format": out.format, "width": width, "height": height, "size": out_size,
                "sha256": f"sha256:{out_sha256}", "reused": reused, "operations": ["thumbnail/render"], "provenance": provenance}

    def _validate_output(self, path: Path, expected_w: int, expected_h: int, fmt: str) -> Tuple[int, int, str, int]:
        if not path.is_file():
            raise ThumbnailError("OUTPUT_ERROR", "render reported success but wrote no file", {"reason": "missing_output", "path": str(path)})
        size = path.stat().st_size
        if size <= 0:
            raise ThumbnailError("OUTPUT_ERROR", "output is empty", {"reason": "empty_output", "path": str(path)})
        try:
            img = Image.open(path)
            img.load()
        except (OSError, UnidentifiedImageError) as e:
            raise ThumbnailError("VALIDATION_ERROR", f"output is not readable as an image: {e}", {"reason": "corrupt_output", "path": str(path)})
        want_pillow_format = OUTPUT_FORMATS[fmt]["pillow_format"]
        if img.format != want_pillow_format:
            raise ThumbnailError("VALIDATION_ERROR", f"output format {img.format!r} is not {want_pillow_format!r}", {"reason": "format_mismatch", "path": str(path)})
        if (img.width, img.height) != (expected_w, expected_h):
            raise ThumbnailError("VALIDATION_ERROR", f"output size {img.width}x{img.height} does not match the canvas ({expected_w}x{expected_h})",
                                 {"reason": "size_mismatch", "path": str(path), "expected": {"width": expected_w, "height": expected_h}, "got": {"width": img.width, "height": img.height}})
        digest = sha256_file(str(path))
        img.close()
        return img.width, img.height, digest, size

    # ---- thumbnail/extract_frame
    def _extract_frame(self, params: Any) -> Dict[str, Any]:
        if not isinstance(params, dict):
            raise ThumbnailError("INVALID_REQUEST", "params must be a JSON object")
        from .model import _obj, parse_options, parse_output  # structural helpers, reused rather than duplicated
        src = _obj(params.get("source"), "source", ("path", "timestamp"), ("path", "timestamp"))
        if not isinstance(src["path"], str) or not src["path"]:
            raise ThumbnailError("INVALID_REQUEST", "source.path must be a non-empty string", {"field": "source.path"})
        raw_ts = src["timestamp"]
        if isinstance(raw_ts, bool) or not isinstance(raw_ts, (int, float)) or not (raw_ts == raw_ts and abs(raw_ts) != float("inf")):
            raise ThumbnailError("INVALID_TIME_RANGE", "source.timestamp must be a finite number", {"field": "source.timestamp"})
        if raw_ts < 0:
            raise ThumbnailError("INVALID_TIME_RANGE", f"source.timestamp must not be negative, got {raw_ts}", {"field": "source.timestamp", "timestamp": raw_ts})
        ts = float(raw_ts)
        if "output" not in params:
            raise ThumbnailError("INVALID_REQUEST", "'output' is required for thumbnail/extract_frame", {"field": "output"})
        out = parse_output(params["output"])
        options = parse_options(params.get("options"))
        reuse = bool(options.get("reuse", True))
        self.timeout = float(options.get("timeout") or self.timeout)
        self._apply_policy_overrides(options)
        if options.get("ffmpeg_skill"):
            self.ffmpeg_skill_dir = options["ffmpeg_skill"]

        resolved = self.policy.resolve_input(src["path"], "source")
        skill = self._ffmpeg_skill()
        meta = skill.probe(str(resolved), self.timeout)
        if not meta.get("video"):
            raise ThumbnailError("INVALID_INPUT", "source has no video stream", {"reason": "no_video_stream"})
        duration = float(meta.get("duration") or 0.0)
        if duration <= 0:
            raise ThumbnailError("INVALID_INPUT", "source video has no known duration; cannot verify the timestamp is in range", {"reason": "no_duration"})
        if ts > duration:
            raise ThumbnailError("INVALID_TIME_RANGE", f"timestamp {ts}s is beyond the source duration ({duration:.3f}s)", {"timestamp": ts, "duration": duration})

        source_sha = sha256_file(str(resolved))
        identity = self._identity("extract_frame", {"source_sha256": source_sha, "timestamp": float(ts), "output_format": out.format,
                                                      "jpeg_quality": out.jpeg_quality if out.format == "jpeg" else None,
                                                      "ffmpeg_skill_version": self._ffmpeg_skill_version()})
        target = self.policy.resolve_write_path(out.path, "output")
        if target.exists() and not out.overwrite:
            raise ThumbnailError("OUTPUT_ERROR", f"output already exists (set overwrite: true to replace it): {target}", {"reason": "exists", "path": str(target)})

        manifest = self._try_reuse(identity, out.format) if reuse else None
        if manifest is not None:
            cache_file, _ = self._cache_paths(identity, out.format)
            final = self._place_output(cache_file, out)
            width, height, out_sha256, out_size = manifest["width"], manifest["height"], manifest["sha256"], manifest["size"]
            reused = True
        else:
            tmp_dir = self.policy.resolve_work_dir(TMP_DIR_NAME)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            frame_path = skill.extract_frame(str(resolved), float(ts), tmp_dir, f"frame_{identity[:12]}", self.timeout)
            img = _open_image(frame_path, "extracted frame")
            if out.format == "png":
                final_img = img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img
            else:
                final_img = img.convert("RGB")
            tmp_out = self._tmp_path(out.format)
            save_kwargs = {"quality": out.jpeg_quality, "optimize": True} if out.format == "jpeg" else {}
            final_img.save(tmp_out, format=OUTPUT_FORMATS[out.format]["pillow_format"], **save_kwargs)
            width, height = img.width, img.height
            img.close()
            _, _, out_sha256, out_size = self._validate_output(tmp_out, width, height, out.format)
            self._store_cache(identity, out.format, tmp_out, {"width": width, "height": height, "format": out.format, "source_sha256": source_sha, "timestamp": float(ts)})
            final = self._place_output(tmp_out, out)
            try:
                tmp_out.unlink()
            except OSError:
                pass
            reused = False

        provenance = {"skill": SKILL_ID, "skill_version": VERSION, "operation": "thumbnail/extract_frame", "engine": "ffmpeg-skill/look",
                      "identity": identity, "reused": reused, "source": {"path": str(resolved), "sha256": f"sha256:{source_sha}", "timestamp": float(ts), "duration": duration},
                      "output_hash": f"sha256:{out_sha256}"}
        return {"output": str(final), "format": out.format, "width": width, "height": height, "size": out_size,
                "sha256": f"sha256:{out_sha256}", "reused": reused, "operations": ["thumbnail/extract_frame"], "provenance": provenance}


TOOLS = ("thumbnail/validate", "thumbnail/render", "thumbnail/extract_frame")

__all__ = ["Executor", "RESPONSE_SCHEMA_ID", "TOOLS", "ENGINE_NAME"]
