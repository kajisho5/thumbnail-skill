"""qc-skill adapter, used only by `thumbnail/render_variants`'s small-size check (ADR-16): the contrast of each text
region of a written thumbnail, measured by qc-skill's text legibility (its ADR-018: Otsu split, WCAG 2.x contrast
ratio on the two classes' median relative luminance). This skill computes no contrast of its own.

- Located like ffmpeg-skill: an explicit directory > THUMBNAIL_SKILL_QC_SKILL_DIR > ~/.claude/skills/qc-skill >
  ./vendor/qc-skill > ../qc-skill (a checkout with src/qc_skill/cli.py), else an installed `qc_skill` package.
- Run as [sys.executable, -m, qc_skill.cli, run, -, --json, --no-cache, --workspace W, --allowed-input-root D] with
  a minimal environment, in its own process group, with a timeout; the request is a typed document built here
  (operation check, kind video, rules.video.text_regions), never a caller string.
- Absent or failing, it is not an error of the variant render: the contrast is reported as not measured, with the
  reason, and a warning says so."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .adapter import _clean_env, _group_kwargs, kill_tree
from .errors import ThumbnailError

ENV_DIR_KEY = "THUMBNAIL_SKILL_QC_SKILL_DIR"
TEXT_ROLE = "hook"   # qc-skill's TEXT_ROLES: hook|cta|caption|term; the role only selects a per-role height minimum, which is not sent


class QcSkill:
    def __init__(self, directory: Optional[Path], timeout: float = 120.0):
        self.directory = directory   # None: the installed package
        self.timeout = timeout

    @staticmethod
    def candidates(explicit: Optional[str] = None) -> List[Path]:
        if explicit:
            return [Path(explicit)]
        out = [Path(os.environ[ENV_DIR_KEY])] if os.environ.get(ENV_DIR_KEY) else []
        return out + [Path.home() / ".claude" / "skills" / "qc-skill", Path.cwd() / "vendor" / "qc-skill", Path.cwd().parent / "qc-skill"]

    @classmethod
    def locate(cls, explicit: Optional[str] = None, timeout: float = 120.0) -> "QcSkill":
        tried = []
        for c in cls.candidates(explicit):
            tried.append(str(c))
            if (c / "src" / "qc_skill" / "cli.py").is_file():
                return cls(c.resolve(), timeout)
        if not explicit and importlib.util.find_spec("qc_skill") is not None:
            return cls(None, timeout)
        raise ThumbnailError("TOOL_ERROR", f"qc-skill not found (set {ENV_DIR_KEY})", {"reason": "qc_skill_missing", "tried": tried}, retryable=False)

    def describe(self) -> str:
        return str(self.directory) if self.directory else "installed package qc_skill"

    def text_regions(self, image: Path, boxes: Sequence[Tuple[int, int, int, int]], workspace: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """qc-skill's `video.text_regions` measurement for each [x, y, w, h] box of one still image (decoded as a
        one-frame video at t=0), in order, plus qc-skill's identity. Raises TOOL_ERROR when qc-skill fails."""
        request = {"operation": "check", "kind": "video", "input": str(image),
                   "rules": {"video": {"text_regions": [{"t": 0.0, "box": list(b), "role": TEXT_ROLE} for b in boxes]}}}
        env = _clean_env()
        cwd = str(self.directory) if self.directory else str(workspace)
        if self.directory:
            env["PYTHONPATH"] = str(self.directory / "src")
        argv = [sys.executable, "-m", "qc_skill.cli", "run", "-", "--json", "--no-cache", "--workspace", str(workspace),
                "--allowed-input-root", str(image.parent)]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                                    env=env, cwd=cwd, **_group_kwargs())
        except OSError as e:
            raise ThumbnailError("TOOL_ERROR", f"cannot start qc-skill: {e}", {"reason": "qc_skill_failed"})
        try:
            out, err = proc.communicate(json.dumps(request), timeout=self.timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise ThumbnailError("TOOL_ERROR", f"qc-skill exceeded {self.timeout}s", {"reason": "timeout"})
        try:
            doc = json.loads(out)
        except ValueError:
            doc = {}
        report = doc.get("report") if isinstance(doc, dict) else None
        if proc.returncode != 0 or not isinstance(report, dict):
            msg = ((doc.get("error") or {}).get("message") if isinstance(doc, dict) and isinstance(doc.get("error"), dict) else None) or (err or "").strip()[-300:]
            raise ThumbnailError("TOOL_ERROR", f"qc-skill failed (exit {proc.returncode}): {msg or 'no message'}", {"reason": "qc_skill_failed"})
        values = next((m.get("value") for m in report.get("measurements") or [] if m.get("id") == "video.text_regions"), None)
        if not isinstance(values, list) or len(values) != len(boxes):
            raise ThumbnailError("TOOL_ERROR", "qc-skill returned no video.text_regions measurement for the regions", {"reason": "qc_skill_failed"})
        skill = doc.get("skill") if isinstance(doc.get("skill"), dict) else {}
        return values, {"skill": "qc-skill", "version": skill.get("version") or (doc.get("provenance") or {}).get("skill_version"),
                        "measurement": "video.text_regions", "check": "video.text_legible"}


__all__ = ["QcSkill", "ENV_DIR_KEY"]
