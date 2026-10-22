"""ffmpeg-skill compatibility is detected, not version-gated.

Fake ffmpeg-skill checkouts stand in for the layouts that have actually shipped: a current one
(`scripts/_contract.py`, `--overwrite`, skill.version 2.x), an old one (`scripts/contract.py`
without `--static`, no `--overwrite`, and a look.py that rejects unknown flags the way argparse
does), one with no contract script at all (flags read from `--help`), and one whose
contract_version / version are numbers this skill has never seen. The fakes run as real
subprocesses through the real adapter; only ffmpeg-skill itself is replaced. The last test
re-runs the same extraction against the real checkout the suite was pointed at (main or the
pinned commit in CI)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Dict, Iterable, Optional

import pytest

from thumbnail_skill.adapter import FfmpegSkill
from thumbnail_skill.doctor import doctor_report
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool

LOOK_TEMPLATE = '''
import argparse, json, os, sys
from PIL import Image
ap = argparse.ArgumentParser()
ap.add_argument("input")
ap.add_argument("-o", "--output")
ap.add_argument("--at", action="append")
ap.add_argument("--no-timecode", action="store_true")
ap.add_argument("--width", type=int)
ap.add_argument("--json", action="store_true")
{extra}
a = ap.parse_args()
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "argv.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
out = "%s_%.3fs.png" % (a.output, float(a.at[0]))
if os.path.exists(out) and {refuse_existing} and not getattr(a, "overwrite", False):
    print(json.dumps({{"status": "failed", "error": {{"kind": "input", "message": "refusing to overwrite"}}}}))
    sys.exit(1)
Image.new("RGB", (16, 9), (10, 20, 30)).save(out)
print(json.dumps({{"status": "completed", "outputs": [out]}}))
'''

PROBE_SCRIPT = '''
import argparse, json
ap = argparse.ArgumentParser()
ap.add_argument("inputs", nargs="+")
ap.add_argument("--json", action="store_true")
ap.parse_args()
print(json.dumps({"duration": 3.0, "video": {"width": 16, "height": 9}}))
'''

CONTRACT_TEMPLATE = '''
import json, sys
if "--static" in sys.argv and not {static_ok}:
    sys.stderr.write("unrecognized arguments: --static\\n")
    sys.exit(2)
print({doc!r})
'''


def _contract_doc(version: str, contract_version: str, look_flags: Iterable[str]) -> Dict:
    def props(names):
        return {n: {"type": "string"} for n in names}
    return {"contract_version": contract_version, "skill": {"id": "ffmpeg-skill", "version": version},
            "tools": [{"name": "probe", "input_schema": {"properties": props(["inputs", "json"])}},
                      {"name": "look", "input_schema": {"properties": props(["input", *look_flags])}}]}


def make_fake_skill(root: Path, *, contract_name: Optional[str], doc: Optional[Dict] = None, static_ok: bool = True,
                    overwrite: bool, refuse_existing: bool, at_flag: bool = True) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    extra = 'ap.add_argument("--overwrite", action="store_true")' if overwrite else ""
    look = LOOK_TEMPLATE.format(extra=extra, refuse_existing="True" if refuse_existing else "False")
    if not at_flag:
        look = look.replace('ap.add_argument("--at", action="append")', 'ap.add_argument("--time", dest="at", action="append")')
    (scripts / "look.py").write_text(textwrap.dedent(look), encoding="utf-8")
    (scripts / "probe.py").write_text(textwrap.dedent(PROBE_SCRIPT), encoding="utf-8")
    if contract_name:
        (scripts / contract_name).write_text(CONTRACT_TEMPLATE.format(static_ok=static_ok, doc=json.dumps(doc)), encoding="utf-8")
    return root


def look_argvs(root: Path):
    log = root / "scripts" / "argv.log"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def extract_twice(skill_dir: Path, tmp_path: Path):
    """The same extraction twice with reuse off: the second finds the first's frame file in place."""
    video = tmp_path / "in.mp4"
    video.write_bytes(b"not decoded by the fake")
    ws = tmp_path / "ws"
    ws.mkdir()
    policy = PathPolicy(str(ws), [str(tmp_path)])
    results = []
    for _ in range(2):
        params = {"source": {"path": str(video), "timestamp": 1.0},
                  "output": {"path": str(ws / "f.png"), "format": "png", "overwrite": True}, "options": {"reuse": False}}
        results.append(run_tool("thumbnail/extract_frame", params, policy, str(skill_dir)))
    return results


def test_current_layout_passes_overwrite_and_survives_an_existing_frame(tmp_path):
    root = make_fake_skill(tmp_path / "ffs", contract_name="_contract.py",
                           doc=_contract_doc("2.10.3", "1.0", ["output", "at", "no_timecode", "width", "json", "overwrite"]),
                           overwrite=True, refuse_existing=True)
    info = FfmpegSkill(root).info()
    assert info.supported, info.problems
    assert info.version == "2.10.3"
    assert info.capabilities == {"look_overwrite": True}
    assert info.detected_via["look"] == "contract"
    results = extract_twice(root, tmp_path)
    assert [r["ok"] for r in results] == [True, True], results
    assert all("--overwrite" in argv for argv in look_argvs(root))


def test_old_layout_without_overwrite_or_static_works(tmp_path):
    # scripts/contract.py, no --static, and a look.py whose argparse rejects --overwrite
    root = make_fake_skill(tmp_path / "ffs", contract_name="contract.py", static_ok=False,
                           doc=_contract_doc("0.9.1", "1.0", ["output", "at", "no_timecode", "width", "json"]),
                           overwrite=False, refuse_existing=False)
    info = FfmpegSkill(root).info()
    assert info.supported, info.problems
    assert info.version == "0.9.1"
    assert info.capabilities == {"look_overwrite": False}
    results = extract_twice(root, tmp_path)
    assert [r["ok"] for r in results] == [True, True], results
    argvs = look_argvs(root)
    assert len(argvs) == 2 and not any("--overwrite" in a for a in argvs)


def test_no_contract_script_falls_back_to_help(tmp_path):
    root = make_fake_skill(tmp_path / "ffs", contract_name=None, overwrite=True, refuse_existing=True)
    (root / "package.json").write_text(json.dumps({"version": "3.1.4"}), encoding="utf-8")
    info = FfmpegSkill(root).info()
    assert info.supported, info.problems
    assert info.detected_via == {"probe": "help", "look": "help"}
    assert info.version == "3.1.4"
    assert info.capabilities == {"look_overwrite": True}
    assert [r["ok"] for r in extract_twice(root, tmp_path)] == [True, True]


def test_unseen_version_and_contract_version_are_not_refused(tmp_path):
    root = make_fake_skill(tmp_path / "ffs", contract_name="_contract.py",
                           doc=_contract_doc("99.0.0", "7.3", ["output", "at", "no_timecode", "json", "overwrite"]),
                           overwrite=True, refuse_existing=True)
    info = FfmpegSkill(root).info()
    assert info.supported, info.problems
    report = doctor_report(str(root))
    check = report["checks"]["ffmpeg_skill"]
    assert check["status"] == "ok"
    assert check["version"] == "99.0.0" and check["contract_version"] == "7.3"
    assert check["capabilities"] == {"look_overwrite": True}
    assert "no version window" in check["compatibility"]


def test_contract_missing_a_flag_is_rechecked_against_help(tmp_path):
    # the contract omits `json` (2.0 dropped transport flags from MCP schemas); --help still lists it
    root = make_fake_skill(tmp_path / "ffs", contract_name="_contract.py",
                           doc=_contract_doc("2.10.3", "1.0", ["output", "at", "no_timecode", "overwrite"]),
                           overwrite=True, refuse_existing=True)
    info = FfmpegSkill(root).info()
    assert info.supported, info.problems
    assert info.detected_via["look"] == "contract+help"


def test_a_genuinely_missing_flag_is_reported_honestly(tmp_path):
    root = make_fake_skill(tmp_path / "ffs", contract_name="_contract.py",
                           doc=_contract_doc("5.0.0", "1.0", ["output", "no_timecode", "json"]),
                           overwrite=False, refuse_existing=False, at_flag=False)
    info = FfmpegSkill(root).info()
    assert not info.supported
    assert any("look lacks flag(s) ['at']" in p for p in info.problems)
    check = doctor_report(str(root))["checks"]["ffmpeg_skill"]
    assert check["status"] == "fail" and check["version"] == "5.0.0"


def test_real_checkout_repeated_extraction_with_reuse_off(media, workspace, ffmpeg_skill_dir, has_ffmpeg):
    """Regression: ffmpeg-skill 2.x refused to overwrite the deterministic frame file left by an
    earlier extraction of the same identity, so the second render with reuse off failed TOOL_ERROR."""
    if "video" not in media or not has_ffmpeg:
        pytest.skip("ffmpeg not available to build the video fixture")
    policy = PathPolicy(str(workspace))
    for _ in range(2):
        params = {"source": {"path": str(media["video"]), "timestamp": 1.0},
                  "output": {"path": str(workspace / "f.png"), "format": "png", "overwrite": True}, "options": {"reuse": False}}
        res = run_tool("thumbnail/extract_frame", params, policy, ffmpeg_skill_dir)
        assert res["ok"] is True, res.get("error")
    info = FfmpegSkill(Path(ffmpeg_skill_dir)).info()
    assert info.supported, info.problems
