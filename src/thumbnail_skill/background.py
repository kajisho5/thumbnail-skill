"""Optional AI background providers (ECOSYSTEM_ROADMAP step 3, ADR-12).

`thumbnail/generate_background` asks one named provider for a background image from a caller-written prompt and an
explicit seed, and writes it as a validated PNG/JPEG with provenance (engine, model, licence). The image is then an
ordinary `image` asset for `thumbnail/render`: composition stays deterministic and the document model is unchanged.

A provider is a class in PROVIDERS with:
  provider_id, engine, model, licence, licence_url, model_card   what provenance and the contract record
  configure_env                                                   the environment variable that turns it on (a local
                                                                  weights directory); unset = "not_configured"
  status()                                                        detection only: never imports a heavy module, never
                                                                  loads a model, never opens a connection
  generate(prompt, width, height, seed, steps, guidance_scale)   a PIL image of exactly width x height

Without a configured provider nothing changes: render, extract_frame and validate never touch this module, doctor
reports the provider as `not_configured` without changing its own status, and generate_background is refused
(TOOL_ERROR, reason provider_unavailable, not retryable). A provider that is configured but cannot run (weights
missing, diffusers/torch not installed) makes doctor DEGRADED and is refused the same way: never a crash.

The one provider, `flux2-klein-4b`, runs FLUX.2 [klein] 4B (Apache-2.0; ADR-012 of the OS allows exactly this engine,
not the non-commercial 9B variant) through diffusers' Flux2KleinPipeline, from local weights only
(`local_files_only=True`): this skill never downloads a model and opens no connection (execution.network stays false).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from .errors import ThumbnailError

MAX_PROMPT_LENGTH = 2000
MIN_SIZE, MAX_SIZE, SIZE_MULTIPLE = 256, 2048, 16
MAX_SEED = 2 ** 32 - 1
MAX_STEPS = 50


class BackgroundProvider:
    provider_id = ""
    engine = ""
    model = ""
    licence = ""
    licence_url = ""
    model_card = ""
    configure_env = ""
    requires: Tuple[str, ...] = ()
    defaults: Dict[str, Any] = {}

    def configured_path(self) -> Optional[str]:
        value = os.environ.get(self.configure_env, "").strip()
        return value or None

    def status(self) -> Dict[str, Any]:
        raise NotImplementedError

    def generate(self, prompt: str, width: int, height: int, seed: int, steps: int, guidance_scale: float) -> Any:
        raise NotImplementedError

    def engine_version(self) -> Optional[str]:
        return None

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.provider_id, "engine": self.engine, "model": self.model, "licence": self.licence,
                "licence_url": self.licence_url, "model_card": self.model_card, "configure_env": self.configure_env,
                "requires": list(self.requires), "defaults": dict(self.defaults)}


class Flux2KleinProvider(BackgroundProvider):
    """FLUX.2 [klein] 4B through diffusers, from a local weights directory (a snapshot of
    black-forest-labs/FLUX.2-klein-4B: model_index.json plus its component folders)."""

    provider_id = "flux2-klein-4b"
    engine = "FLUX.2 [klein] 4B"
    model = "black-forest-labs/FLUX.2-klein-4B"
    licence = "Apache-2.0"
    licence_url = "https://www.apache.org/licenses/LICENSE-2.0"
    model_card = "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B"
    configure_env = "THUMBNAIL_FLUX2_KLEIN_4B_DIR"
    requires = ("torch", "diffusers>=0.37.0 (Flux2KleinPipeline)", "transformers>=4.51.0 (its Qwen3 text encoder)")
    # the model card's own example: 4 steps, guidance_scale 1.0 (a step-distilled model)
    defaults = {"steps": 4, "guidance_scale": 1.0}

    def status(self) -> Dict[str, Any]:
        base = self.describe()
        path = self.configured_path()
        if path is None:
            return dict(base, status="not_configured",
                        detail=f"set {self.configure_env} to a local snapshot of {self.model} to enable it (this skill never downloads it)")
        problems: List[str] = []
        weights = Path(path)
        if not (weights / "model_index.json").is_file():
            problems.append(f"{self.configure_env}={path} has no model_index.json (a local snapshot of {self.model} is expected)")
        for module in ("torch", "diffusers", "transformers"):
            if importlib.util.find_spec(module) is None:
                problems.append(f"python package {module!r} is not installed")
        if problems:
            return dict(base, status="unavailable", weights=str(weights), problems=problems,
                        install="pip install 'thumbnail-skill[flux2]' (torch, diffusers>=0.37.0, transformers>=4.51.0) and a local snapshot of "
                                f"{self.model} in {self.configure_env}; about 13 GB of VRAM (model card)")
        return dict(base, status="available", weights=str(weights))

    def engine_version(self) -> Optional[str]:
        try:
            from importlib.metadata import version
            return f"diffusers {version('diffusers')}"
        except Exception:
            return None

    def generate(self, prompt: str, width: int, height: int, seed: int, steps: int, guidance_scale: float) -> Any:
        st = self.status()
        if st["status"] != "available":
            raise unavailable(self.provider_id, st)
        try:
            torch = importlib.import_module("torch")
            diffusers = importlib.import_module("diffusers")
        except ImportError as e:
            raise unavailable(self.provider_id, dict(st, problems=[f"import failed: {e}"]))
        pipeline_cls = getattr(diffusers, "Flux2KleinPipeline", None)
        if pipeline_cls is None:
            raise unavailable(self.provider_id, dict(st, problems=["the installed diffusers has no Flux2KleinPipeline (a newer diffusers is needed)"]))
        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        try:
            pipe = pipeline_cls.from_pretrained(st["weights"], torch_dtype=dtype, local_files_only=True)
            pipe = pipe.to(device)
            result = pipe(prompt=prompt, height=height, width=width, num_inference_steps=steps, guidance_scale=guidance_scale,
                          generator=torch.Generator(device=device).manual_seed(seed))
            return result.images[0]
        except ThumbnailError:
            raise
        except Exception as e:  # a model or runtime failure: one structured error, never a traceback
            raise ThumbnailError("TOOL_ERROR", f"{self.engine} failed: {type(e).__name__}: {e}",
                                 {"reason": "provider_failed", "provider": self.provider_id, "device": device}, retryable=False)


PROVIDERS: Dict[str, Type[BackgroundProvider]] = {Flux2KleinProvider.provider_id: Flux2KleinProvider}


def provider_ids() -> List[str]:
    return sorted(PROVIDERS)


def get_provider(provider_id: str) -> BackgroundProvider:
    cls = PROVIDERS.get(provider_id)
    if cls is None:
        raise ThumbnailError("INVALID_REQUEST", f"unknown background provider {provider_id!r}; known: {provider_ids()}",
                             {"field": "background.provider", "known": provider_ids()})
    return cls()


def unavailable(provider_id: str, status: Dict[str, Any]) -> ThumbnailError:
    why = status.get("detail") or "; ".join(status.get("problems") or []) or status.get("status", "unavailable")
    return ThumbnailError("TOOL_ERROR", f"background provider {provider_id!r} is not available: {why}",
                          {"reason": "provider_unavailable", "provider": provider_id, "status": status.get("status"),
                           "problems": list(status.get("problems") or []), "configure_env": status.get("configure_env"),
                           "install": status.get("install")}, retryable=False)


def providers_status() -> Dict[str, Dict[str, Any]]:
    return {pid: PROVIDERS[pid]().status() for pid in provider_ids()}


def parse_background(value: Any, where: str = "background") -> Dict[str, Any]:
    """The `background` object of a generate_background request: provider, prompt, width, height, seed, and the
    provider's optional steps / guidance_scale. Structural only: nothing is detected or loaded here."""
    from .model import _number, _obj
    d = _obj(value, where, ("provider", "prompt", "width", "height", "seed", "steps", "guidance_scale"),
             ("provider", "prompt", "width", "height", "seed"))
    if not isinstance(d["provider"], str):
        raise ThumbnailError("INVALID_REQUEST", f"{where}.provider must be a string", {"field": f"{where}.provider"})
    provider = get_provider(d["provider"])
    prompt = d["prompt"]
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_LENGTH:
        raise ThumbnailError("INVALID_REQUEST", f"{where}.prompt must be a non-empty string of at most {MAX_PROMPT_LENGTH} characters",
                             {"field": f"{where}.prompt"})
    if any(ord(c) < 32 and c != "\n" for c in prompt):
        raise ThumbnailError("INVALID_REQUEST", f"{where}.prompt must not contain control characters other than \\n", {"field": f"{where}.prompt"})
    size = {}
    for k in ("width", "height"):
        n = int(_number(d[k], f"{where}.{k}", MIN_SIZE, MAX_SIZE, integer=True))
        if n % SIZE_MULTIPLE:
            raise ThumbnailError("INVALID_REQUEST", f"{where}.{k} must be a multiple of {SIZE_MULTIPLE}, got {n}",
                                 {"field": f"{where}.{k}", "multiple_of": SIZE_MULTIPLE})
        size[k] = n
    seed = int(_number(d["seed"], f"{where}.seed", 0, MAX_SEED, integer=True))
    steps = int(_number(d.get("steps", provider.defaults.get("steps", 4)), f"{where}.steps", 1, MAX_STEPS, integer=True))
    guidance = _number(d.get("guidance_scale", provider.defaults.get("guidance_scale", 1.0)), f"{where}.guidance_scale", 0.0, 20.0)
    return {"provider": provider.provider_id, "prompt": prompt, "width": size["width"], "height": size["height"], "seed": seed,
            "steps": steps, "guidance_scale": guidance}


__all__ = ["BackgroundProvider", "Flux2KleinProvider", "PROVIDERS", "provider_ids", "get_provider", "providers_status",
           "parse_background", "unavailable", "MAX_PROMPT_LENGTH", "MIN_SIZE", "MAX_SIZE", "SIZE_MULTIPLE", "MAX_SEED", "MAX_STEPS"]
