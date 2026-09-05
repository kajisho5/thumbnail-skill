"""Security boundary tests: PathPolicy (traversal, symlink escape, workspace escape, unsafe names)
and the forbidden-field boundary at the request layer. No shell, no argv, no filter string is ever
accepted anywhere in this skill; these tests prove the boundary that keeps it that way."""
from __future__ import annotations

import os
import platform

import pytest

from thumbnail_skill.errors import ThumbnailError
from thumbnail_skill.security import PathPolicy, check_filename
from thumbnail_skill.skill import run_tool


def render_doc(asset_path: str, output_path: str, **extra_options):
    return {
        "document": {"document_id": "d", "canvas": {"width": 64, "height": 64},
                     "assets": [{"asset_id": "a", "kind": "image", "path": asset_path}],
                     "elements": [{"element_id": "e1", "type": "image", "image": {"asset_id": "a", "position": {"x": 0, "y": 0}, "size": {"width": 10, "height": 10}}}]},
        "output": {"path": output_path, "format": "png"},
        "options": extra_options,
    }


def test_traversal_rejected_with_allowed_roots(tmp_path):
    root = tmp_path / "safe"
    root.mkdir()
    policy = PathPolicy(str(tmp_path), [str(root)])
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n")
    # a '..' component that resolves outside the allowed root: rejected by containment, not by string match
    # (a string prefix is never trusted: containment is checked on the resolved, component-wise path)
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_input(str(root / ".." / outside.name), "input")
    assert e.value.code == "PATH_NOT_ALLOWED"
    assert e.value.details.get("reason") == "outside_allowed_roots"


def test_outside_allowed_root_rejected(tmp_path):
    root = tmp_path / "safe"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n")
    policy = PathPolicy(str(tmp_path), [str(root)])
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_input(str(outside), "input")
    assert e.value.code == "PATH_NOT_ALLOWED"
    assert e.value.details.get("reason") == "outside_allowed_roots"


@pytest.mark.skipif(platform.system() == "Windows", reason="symlinks need elevated privileges on Windows CI")
def test_symlink_escape_rejected(tmp_path):
    root = tmp_path / "safe"
    root.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n")
    link = root / "link.png"
    os.symlink(outside, link)
    policy = PathPolicy(str(tmp_path), [str(root)])
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_input(str(link), "input")
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_prefix_collision_is_not_containment(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    evil_dir = tmp_path / "media_evil"
    evil_dir.mkdir()
    evil_file = evil_dir / "x.png"
    evil_file.write_bytes(b"\x89PNG\r\n")
    policy = PathPolicy(str(tmp_path), [str(root)])
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_input(str(evil_file), "input")
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_output_traversal_rejected(tmp_path):
    policy = PathPolicy(str(tmp_path))
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_write_path("../escape.png", "output")
    assert e.value.code == "PATH_NOT_ALLOWED"
    assert e.value.details.get("reason") == "traversal"


def test_output_prefix_collision_is_not_containment(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    evil_sibling = tmp_path / "ws_evil"
    evil_sibling.mkdir()
    policy = PathPolicy(str(workspace))
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_write_path(str(evil_sibling / "out.png"), "output")
    assert e.value.code == "PATH_NOT_ALLOWED"
    assert e.value.details.get("reason") == "outside_workspace"


def test_output_outside_workspace_rejected(tmp_path):
    policy = PathPolicy(str(tmp_path))
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_write_path("/etc/should-not-be-writable.png", "output")
    assert e.value.code == "PATH_NOT_ALLOWED"


@pytest.mark.skipif(platform.system() == "Windows", reason="symlinks need elevated privileges on Windows CI")
def test_output_dangling_symlink_escape_rejected(tmp_path):
    """A *dangling* symlink at the exact output path (its target does not exist yet) must not let a
    write escape the workspace. `Path.exists()` is False for a dangling symlink, so a naive
    "resolve the nearest existing ancestor" scheme never notices the final component is a symlink at
    all, approves it, and `shutil.copyfile` then creates the real file at the symlink's target via
    the OS's own symlink-following `open()` — outside the workspace. `resolve_write_path` must use a
    resolution that follows the symlink chain (os.path.realpath) before checking containment."""
    outside_target = tmp_path.parent / f"escape_target_{tmp_path.name}.png"
    if outside_target.exists():
        outside_target.unlink()
    link = tmp_path / "innocent.png"
    os.symlink(str(outside_target), str(link))
    assert not outside_target.exists()   # dangling: the symlink's target does not exist yet
    policy = PathPolicy(str(tmp_path))
    try:
        with pytest.raises(ThumbnailError) as e:
            policy.resolve_write_path("innocent.png", "output")
        assert e.value.code == "PATH_NOT_ALLOWED"
    finally:
        if outside_target.exists():
            outside_target.unlink()   # prove nothing was ever written there, then clean up defensively
    assert not outside_target.exists()


@pytest.mark.skipif(platform.system() == "Windows", reason="symlinked directories need elevated privileges on Windows CI")
def test_workspace_symlinked_subdir_escape_rejected(tmp_path):
    real_ws = tmp_path / "real_ws"
    real_ws.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (real_ws / "escape").symlink_to(outside, target_is_directory=True)
    policy = PathPolicy(str(real_ws))
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_write_path("escape/out.png", "output")
    assert e.value.code == "PATH_NOT_ALLOWED"


@pytest.mark.parametrize("name", ["CON.png", "a" * 300 + ".png", "bad\x01name.png", "trailing.png ", "-flag.png"])
def test_unsafe_filenames_rejected(name):
    with pytest.raises(ThumbnailError) as e:
        check_filename(name)
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_nul_byte_in_path_rejected(tmp_path):
    policy = PathPolicy(str(tmp_path))
    with pytest.raises(ThumbnailError) as e:
        policy.resolve_input("bad\x00path.png", "input")
    assert e.value.code == "PATH_NOT_ALLOWED"


# ---- request-layer forbidden fields (the "no shell/argv/filter" boundary from the top)

@pytest.mark.parametrize("payload", [
    {"document": {"document_id": "d", "canvas": {"width": 10, "height": 10}, "elements": [], "command": "rm -rf /"}},
    {"document": {"document_id": "d", "canvas": {"width": 10, "height": 10}, "elements": [{"element_id": "e", "type": "text",
     "text": {"text": "x", "font_id": "sans", "font_size": 10, "color": "#ffffff", "position": {"x": 0, "y": 0}, "argv": ["rm", "-rf"]}}]}},
    {"document": {"document_id": "d", "canvas": {"width": 10, "height": 10, "filter_complex": "evil"}, "elements": []}},
])
def test_run_tool_rejects_forbidden_keys_anywhere(payload):
    res = run_tool("thumbnail/validate", payload)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_REQUEST"
    assert res["error"]["details"].get("reason") == "forbidden_field"


def test_run_tool_never_raises_on_malformed_input():
    """The dispatch boundary always returns a document, even for nonsense input; never a traceback."""
    for bad in [None, 42, "a string", [], {"document": None}]:
        res = run_tool("thumbnail/validate", bad if isinstance(bad, dict) else {"document": bad})
        assert res["ok"] is False
        assert res["error"]["code"] in ("INVALID_REQUEST",)
