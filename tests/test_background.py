"""The optional AI background provider (ADR-12, roadmap step 3). No model is ever loaded or downloaded here: the
FLUX.2 [klein] 4B provider's own code runs against fake `torch`, `diffusers` and `transformers` modules placed in
sys.modules, so its calls (local weights only, the seed, steps and guidance) are checked without a GPU or the
weights. Without a configured provider nothing changes: doctor keeps its status and generate_background is refused
(TOOL_ERROR, provider_unavailable), never a crash."""
from __future__ import annotations

import importlib.machinery
import json
import sys
import types

import pytest
from PIL import Image

from thumbnail_skill import background
from thumbnail_skill.cli import main as cli_main
from thumbnail_skill.contract import skill_contract
from thumbnail_skill.doctor import doctor_report
from thumbnail_skill.errors import EXIT_CODES
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool

ENV = background.Flux2KleinProvider.configure_env


def generate(workspace, bg=None, output=None, options=None):
    params = {"background": {"provider": "flux2-klein-4b", "prompt": "a calm studio backdrop, soft light", "width": 512, "height": 288,
                             "seed": 7, **(bg or {})},
              "output": output or {"path": str(workspace / "bg.png"), "format": "png"}, "options": options or {}}
    return run_tool("thumbnail/generate_background", params, PathPolicy(str(workspace)))


class FakePipeline:
    """Stands in for diffusers.Flux2KleinPipeline: records how it was loaded and called, and returns a solid image whose
    colour comes from the seed."""
    loads: list = []
    calls: list = []
    fail: Exception = None
    size_override = None

    @classmethod
    def from_pretrained(cls, path, **kw):
        cls.loads.append({"path": path, **kw})
        return cls()

    def to(self, device):
        self.device = device
        return self

    def __call__(self, **kw):
        FakePipeline.calls.append({k: (v.seed if k == "generator" else v) for k, v in kw.items()})
        if FakePipeline.fail is not None:
            raise FakePipeline.fail
        seed = kw["generator"].seed
        size = FakePipeline.size_override or (kw["width"], kw["height"])
        return types.SimpleNamespace(images=[Image.new("RGB", size, (seed % 256, 80, 160))])


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__spec__ = importlib.machinery.ModuleSpec(name, None)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


@pytest.fixture()
def fake_flux(monkeypatch, tmp_path):
    """A configured provider: a weights directory with model_index.json and fake torch/diffusers/transformers."""
    weights = tmp_path / "FLUX.2-klein-4B"
    weights.mkdir()
    (weights / "model_index.json").write_text('{"_class_name": "Flux2KleinPipeline"}', encoding="utf-8")
    monkeypatch.setenv(ENV, str(weights))

    class Generator:
        def __init__(self, device):
            self.device = device

        def manual_seed(self, seed):
            self.seed = seed
            return self

    torch = _module("torch", bfloat16="bf16", float32="f32", Generator=Generator,
                    cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", _module("diffusers", Flux2KleinPipeline=FakePipeline))
    monkeypatch.setitem(sys.modules, "transformers", _module("transformers"))
    FakePipeline.loads, FakePipeline.calls, FakePipeline.fail, FakePipeline.size_override = [], [], None, None
    return weights


# ---- without a provider: nothing changes

def test_unconfigured_provider_leaves_doctor_status_alone(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    rep = doctor_report()
    st = rep["checks"]["background_providers"]["flux2-klein-4b"]
    assert st["status"] == "not_configured"
    assert ENV in st["detail"]
    assert not any("background provider" in w for w in rep["warnings"])


def test_unconfigured_provider_is_refused_not_a_crash(monkeypatch, workspace):
    monkeypatch.delenv(ENV, raising=False)
    res = generate(workspace)
    assert res["ok"] is False
    err = res["error"]
    assert (err["code"], err["retryable"], err["details"]["reason"]) == ("TOOL_ERROR", False, "provider_unavailable")
    assert err["details"]["status"] == "not_configured" and err["details"]["configure_env"] == ENV
    assert not (workspace / "bg.png").exists()


def test_cli_refusal_exit_code(monkeypatch, workspace, capsys):
    monkeypatch.delenv(ENV, raising=False)
    req = workspace / "req.json"
    req.write_text(json.dumps({"background": {"provider": "flux2-klein-4b", "prompt": "x", "width": 256, "height": 256, "seed": 1},
                               "output": {"path": str(workspace / "o.png"), "format": "png"}}), encoding="utf-8")
    rc = cli_main(["generate-background", str(req), "--workspace", str(workspace), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == EXIT_CODES["TOOL_ERROR"]
    assert doc["error"]["details"]["reason"] == "provider_unavailable"


def test_render_and_extract_frame_never_touch_the_provider(monkeypatch, media, workspace):
    # a configured but broken provider must not affect the deterministic tools
    monkeypatch.setenv(ENV, str(workspace / "nowhere"))
    monkeypatch.setattr(background.Flux2KleinProvider, "generate", lambda *a, **k: pytest.fail("render reached the AI provider"))
    doc = {"document_id": "d", "canvas": {"width": 64, "height": 64},
           "assets": [{"asset_id": "a", "kind": "image", "path": str(media["bg_jpg"])}],
           "elements": [{"element_id": "e", "type": "image", "image": {"asset_id": "a", "position": {"x": 0, "y": 0}, "size": {"width": 64, "height": 64}}}]}
    res = run_tool("thumbnail/render", {"document": doc, "output": {"path": str(workspace / "t.png"), "format": "png"}}, PathPolicy(str(workspace)))
    assert res["ok"] is True, res


# ---- configured but unable to run: DEGRADED and a refusal

def test_configured_without_weights_is_degraded_and_refused(monkeypatch, workspace, tmp_path):
    monkeypatch.setenv(ENV, str(tmp_path / "empty"))
    rep = doctor_report()
    st = rep["checks"]["background_providers"]["flux2-klein-4b"]
    assert st["status"] == "unavailable"
    assert any("model_index.json" in p for p in st["problems"])
    assert rep["status"] in ("degraded", "fail") and rep["ok"] is (rep["status"] != "fail")
    assert any("background provider 'flux2-klein-4b'" in w for w in rep["warnings"])
    res = generate(workspace)
    assert res["error"]["code"] == "TOOL_ERROR" and res["error"]["details"]["status"] == "unavailable"


def test_configured_without_packages_is_unavailable(monkeypatch, fake_flux):
    real = background.importlib.util.find_spec
    monkeypatch.setattr(background.importlib.util, "find_spec", lambda name: None if name in ("torch", "diffusers", "transformers") else real(name))
    st = background.Flux2KleinProvider().status()
    assert st["status"] == "unavailable"
    assert {p.split("'")[1] for p in st["problems"]} == {"torch", "diffusers", "transformers"}
    assert "thumbnail-skill[flux2]" in st["install"]


def test_a_diffusers_without_the_klein_pipeline_is_refused(monkeypatch, fake_flux, workspace):
    monkeypatch.setitem(sys.modules, "diffusers", _module("diffusers"))
    res = generate(workspace)
    assert res["error"]["details"]["reason"] == "provider_unavailable"
    assert "Flux2KleinPipeline" in res["error"]["message"]


# ---- configured: what the provider is asked and what is recorded

def test_generate_with_the_provider_records_engine_and_licence(fake_flux, workspace):
    res = generate(workspace)
    assert res["ok"] is True, res
    assert (res["width"], res["height"], res["format"], res["reused"]) == (512, 288, "png", False)
    with Image.open(res["output"]) as img:
        assert img.size == (512, 288) and img.getpixel((0, 0)) == (7, 80, 160)
    load = FakePipeline.loads[0]
    assert load == {"path": str(fake_flux), "torch_dtype": "f32", "local_files_only": True}     # never a download
    call = FakePipeline.calls[0]
    assert call == {"prompt": "a calm studio backdrop, soft light", "height": 288, "width": 512, "num_inference_steps": 4,
                    "guidance_scale": 1.0, "generator": 7}                                       # the model card's 4 steps, 1.0
    p = res["provenance"]
    assert p["generated"] is True and p["deterministic"] is False
    assert (p["provider"], p["engine"], p["model"], p["licence"]) == ("flux2-klein-4b", "FLUX.2 [klein] 4B",
                                                                       "black-forest-labs/FLUX.2-klein-4B", "Apache-2.0")
    assert p["licence_url"] == "https://www.apache.org/licenses/LICENSE-2.0"
    assert (p["seed"], p["steps"], p["guidance_scale"], p["prompt"]) == (7, 4, 1.0, "a calm studio backdrop, soft light")
    assert p["prompt_sha256"].startswith("sha256:") and p["output_hash"] == res["sha256"]


def test_generated_background_as_a_jpeg_passes_a_platform_preset(fake_flux, workspace):
    res = generate(workspace, bg={"width": 1280, "height": 720, "steps": 8, "guidance_scale": 2.5},
                   output={"path": str(workspace / "bg.jpg"), "format": "jpeg", "preset": "youtube"})
    assert res["ok"] is True, res
    assert res["preset"]["name"] == "youtube" and res["provenance"]["preset"] == "youtube"
    assert FakePipeline.calls[0]["num_inference_steps"] == 8 and FakePipeline.calls[0]["guidance_scale"] == 2.5


def test_the_result_is_an_ordinary_image_asset_for_render(fake_flux, workspace):
    res = generate(workspace)
    doc = {"document_id": "d", "canvas": {"width": 512, "height": 288},
           "assets": [{"asset_id": "bg", "kind": "image", "path": res["output"]}],
           "elements": [{"element_id": "e", "type": "image", "image": {"asset_id": "bg", "position": {"x": 0, "y": 0}, "size": {"width": 512, "height": 288}}}]}
    out = run_tool("thumbnail/render", {"document": doc, "output": {"path": str(workspace / "t.png"), "format": "png"}}, PathPolicy(str(workspace)))
    assert out["ok"] is True, out


def test_a_model_failure_is_one_structured_error(fake_flux, workspace):
    FakePipeline.fail = RuntimeError("CUDA out of memory")
    res = generate(workspace)
    assert (res["error"]["code"], res["error"]["details"]["reason"]) == ("TOOL_ERROR", "provider_failed")
    assert "out of memory" in res["error"]["message"]
    assert not (workspace / "bg.png").exists()


def test_a_wrong_size_from_the_provider_is_a_validation_error(fake_flux, workspace):
    FakePipeline.size_override = (500, 288)
    res = generate(workspace)
    assert (res["error"]["code"], res["error"]["details"]["reason"]) == ("VALIDATION_ERROR", "provider_output_invalid")
    assert not (workspace / "bg.png").exists()


def test_an_existing_output_is_kept_unless_overwrite(fake_flux, workspace):
    (workspace / "bg.png").write_bytes(b"keep")
    res = generate(workspace)
    assert res["error"]["code"] == "OUTPUT_ERROR" and (workspace / "bg.png").read_bytes() == b"keep"
    assert FakePipeline.calls == []                                        # refused before the model runs


# ---- request validation (structural, before any detection)

@pytest.mark.parametrize("bg, field", [
    ({"width": 500}, "background.width"),                   # not a multiple of 16
    ({"height": 128}, "background.height"),                 # below 256
    ({"width": 4096}, "background.width"),                  # above 2048
    ({"seed": -1}, "background.seed"),
    ({"seed": 2 ** 32}, "background.seed"),
    ({"steps": 0}, "background.steps"),
    ({"prompt": ""}, "background.prompt"),
    ({"prompt": "a\x00b"}, "background.prompt"),
    ({"prompt": "x" * 2001}, "background.prompt"),
    ({"provider": "flux2-klein-9b"}, "background.provider"),   # the non-commercial 9B variant is not offered
])
def test_invalid_requests(monkeypatch, workspace, bg, field):
    monkeypatch.delenv(ENV, raising=False)
    res = generate(workspace, bg=bg)
    assert res["error"]["code"] == "INVALID_REQUEST", res
    assert res["error"]["details"].get("field") == field


def test_forbidden_and_unknown_fields_are_refused(workspace):
    for bg in ({"command": "rm -rf /"}, {"negative_prompt": "x"}):
        res = generate(workspace, bg=bg)
        assert res["error"]["code"] == "INVALID_REQUEST"


# ---- the contract

def test_contract_declares_the_optional_ai_tool_beside_an_unchanged_core():
    c = skill_contract()
    tools = {t["tool_id"]: t for t in c["tools"]}
    g = tools["thumbnail/generate_background"]
    assert (g["ai"], g["optional"], g["deterministic"], g["produces_output"], g["mutates_input"]) == (True, True, False, True, False)
    for tid in ("thumbnail/render", "thumbnail/extract_frame"):
        assert tools[tid]["deterministic"] is True and "ai" not in tools[tid]
    # the core sections consumers pin keep describing the deterministic render path
    assert c["execution"]["ai"] is False and c["execution"]["network"] is False and c["deterministic"] is True
    bp = c["background_providers"]
    assert bp["core_tools_use_ai"] is False and bp["network"] is False and bp["downloads_models"] is False
    assert set(bp["providers"]) == set(background.PROVIDERS)
    flux = bp["providers"]["flux2-klein-4b"]
    assert (flux["model"], flux["licence"]) == ("black-forest-labs/FLUX.2-klein-4B", "Apache-2.0")
    assert {p["id"] for p in c["provides"]} == {"thumbnail.render", "thumbnail.extract_frame", "thumbnail.generate_background"}
