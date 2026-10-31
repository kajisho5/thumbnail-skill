"""The version lives in exactly one place: pyproject.toml (docs/decisions.md ADR-11). Everything that reports a version
must say the same thing, the release workflow's bump script must be enough to move all of it, and the contract's
`contract_version` / `dependencies` come from the constants they describe."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import thumbnail_skill
from thumbnail_skill import CONTRACT_SCHEMA_VERSION, VERSION, __version__
from thumbnail_skill.adapter import SUPPORTED_CONTRACT_VERSION, TOOLS_USED
from thumbnail_skill.cli import main
from thumbnail_skill.contract import skill_contract

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version(root: Path = ROOT) -> str:
    found = re.search(r'^version\s*=\s*"([^"]+)"\s*$', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert found, "pyproject.toml has no version line"
    return found.group(1)


def test_version_is_the_pyproject_version():
    assert VERSION == __version__ == _pyproject_version()
    assert VERSION != "0.0.0+unknown"


def test_contract_and_cli_report_the_pyproject_version(capsys):
    c = skill_contract()
    assert c["version"] == _pyproject_version()
    for command in ("contract", "skill"):
        assert main([command, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["version"] == _pyproject_version()


def test_contract_version_is_the_contract_schema_version():
    c = skill_contract()
    assert c["contract_version"] == str(CONTRACT_SCHEMA_VERSION) == c["schema_versions"]["contract"] == "1"
    assert c["schema"].endswith(f"@{CONTRACT_SCHEMA_VERSION}")


def test_ffmpeg_skill_dependency_is_a_lower_bound_from_the_adapter_constant():
    c = skill_contract()
    assert c["dependencies"] == [{
        "skill_id": "ffmpeg-skill",
        "version_range": f">={SUPPORTED_CONTRACT_VERSION}",
        "range_of": "contract_version",
        "required": False,
        "for": ["thumbnail/render", "thumbnail/extract_frame"],
        "tools": [f"ffmpeg-skill/{t}" for t in TOOLS_USED],
    }]
    assert c["dependencies"][0]["version_range"] == ">=1.0"
    assert "<" not in c["dependencies"][0]["version_range"]  # no ceiling, and never an exact pin
    # the same constant the existing ffmpeg_skill block reports, and the same tools the tool specs delegate to
    assert c["ffmpeg_skill"]["contract_version"] == SUPPORTED_CONTRACT_VERSION
    delegated = {d for t in c["tools"] for d in t.get("delegates_to") or []}
    assert delegated == set(c["dependencies"][0]["tools"])


def _run_python(code: str, python_path: Path, cwd: Path, isolated: bool) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(python_path)
    # -S: no site-packages, so an installed (e.g. editable) thumbnail-skill and its metadata cannot answer in place of
    # `python_path`. Without -S, PYTHONPATH still comes first for the import itself (and Pillow stays importable).
    argv = [sys.executable, "-S", "-c", code] if isolated else [sys.executable, "-c", code]
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(cwd))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_release_bump_script_moves_every_reported_version(tmp_path):
    """Simulate release.yml's auto-bump on a copy of this checkout: .github/scripts/bump_version.py rewrites only
    pyproject.toml, and VERSION and the contract follow (the script checks VERSION itself too)."""
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["NEW_VERSION"] = "9.8.7"
    proc = subprocess.run([sys.executable, "-S", str(ROOT / ".github" / "scripts" / "bump_version.py")], capture_output=True,
                          text=True, env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert _pyproject_version(tmp_path) == "9.8.7"
    code = ("import pathlib, thumbnail_skill; from thumbnail_skill.contract import skill_contract; c = skill_contract(); "
            "assert pathlib.Path(thumbnail_skill.__file__).resolve().is_relative_to(pathlib.Path.cwd().resolve()); "
            "print(sorted({thumbnail_skill.VERSION, c['version']}))")
    assert _run_python(code, tmp_path / "src", tmp_path, isolated=False) == "['9.8.7']"


def test_installed_copy_without_pyproject_reads_distribution_metadata(tmp_path):
    """An installed wheel has no pyproject.toml next to it: the version then comes from the distribution metadata (built
    from that same pyproject.toml); with none, 0.0.0+unknown, never a guess."""
    site = tmp_path / "site"
    shutil.copytree(Path(thumbnail_skill.__file__).parent, site / "thumbnail_skill", ignore=shutil.ignore_patterns("__pycache__"))
    code = "import thumbnail_skill; print(thumbnail_skill.VERSION)"
    assert _run_python(code, site, tmp_path, isolated=True) == "0.0.0+unknown"
    dist_info = site / "thumbnail_skill-4.5.6.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: thumbnail-skill\nVersion: 4.5.6\n", encoding="utf-8")
    assert _run_python(code, site, tmp_path, isolated=True) == "4.5.6"
