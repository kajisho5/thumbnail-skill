"""ffmpeg-skill adapter: the only place this skill ever starts another process, and the only place
a video is decoded. thumbnail-skill never runs ffmpeg itself and never builds a filter string; it
calls ffmpeg-skill's own typed CLI tools, exactly the way audio-production-skill does.

Which ffmpeg-skill tools are used, and for what:
  probe   source video facts (duration, has a video stream) before extracting a frame
  look    `--at <timestamp> --no-timecode` extracts exactly the requested frame as a PNG; no scene
          detection, no "best frame" search — one caller-given timestamp in, one frame out

- Locates an ffmpeg-skill checkout (explicit dir > THUMBNAIL_SKILL_FFMPEG_SKILL_DIR >
  VIDEO_AGENT_FFMPEG_SKILL_DIR > ~/.claude/skills/ffmpeg-skill > ./vendor/ffmpeg-skill >
  ../ffmpeg-skill) and reads its contract (`scripts/_contract.py --json --static`) to check
  contract_version and the flags this adapter relies on.
- Runs one named tool as [sys.executable, <dir>/scripts/<tool>.py, <typed argv...>, --json] with a
  minimal environment, in its own process group, with a timeout; never a shell, never a
  request-supplied string used as a flag or filter.
- Every argv value is produced here from validated numbers / resolved absolute paths.
- Parses the tool's JSON document; a non-zero exit code or {"status": "failed"} becomes TOOL_ERROR."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import ThumbnailError

SUPPORTED_CONTRACT_VERSION = "1.0"
ENV_DIR_KEYS = ("THUMBNAIL_SKILL_FFMPEG_SKILL_DIR", "VIDEO_AGENT_FFMPEG_SKILL_DIR")
TOOLS_USED = ("probe", "look")
FLAGS_USED: Dict[str, Tuple[str, ...]] = {
    "probe": ("inputs",),
    "look": ("input", "output", "at", "no_timecode", "width", "json"),
}
_ENV_KEEP = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TERM",
             "SYSTEMROOT", "SYSTEMDRIVE", "PATHEXT", "COMSPEC", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _clean_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _group_kwargs() -> Dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {"start_new_session": True}


def kill_tree(proc: "subprocess.Popen[str]") -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.communicate(timeout=5)
    except Exception:
        pass


def fmt_seconds(value: float) -> str:
    if not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")) or value < 0:
        raise ThumbnailError("INTERNAL_ERROR", f"bad time value {value!r}")
    return f"{float(value):.6f}"


def frame_filename(stem: str, timestamp: float) -> str:
    """The exact file name ffmpeg-skill/look writes for `--at <timestamp> -o <stem>` (single --at,
    no --compare): `<stem>_<timestamp:.3f>s.png`, deterministic from ffmpeg-skill's own formatting."""
    return f"{stem}_{float(timestamp):.3f}s.png"


@dataclass
class ToolRun:
    tool: str
    argv: List[str]
    returncode: int
    data: Dict[str, Any]
    stderr_tail: str
    seconds: float
    commands: List[str] = field(default_factory=list)


@dataclass
class SkillInfo:
    directory: Path
    version: str
    contract_version: str
    tools: Dict[str, Dict[str, Any]]
    problems: List[str]

    @property
    def supported(self) -> bool:
        return not self.problems


class FfmpegSkill:
    """Handle on one ffmpeg-skill checkout."""

    def __init__(self, directory: Path, timeout: float = 300.0):
        self.directory = directory
        self.timeout = timeout
        self._info: Optional[SkillInfo] = None
        self.runs: List[ToolRun] = []
        self._cancelled = False

    # ---- discovery
    @staticmethod
    def candidates(explicit: Optional[str] = None) -> List[Path]:
        out: List[Path] = []
        if explicit:
            return [Path(explicit)]
        for key in ENV_DIR_KEYS:
            v = os.environ.get(key)
            if v:
                out.append(Path(v))
        out.append(Path.home() / ".claude" / "skills" / "ffmpeg-skill")
        out.append(Path.cwd() / "vendor" / "ffmpeg-skill")
        out.append(Path.cwd().parent / "ffmpeg-skill")
        return out

    @classmethod
    def locate(cls, explicit: Optional[str] = None, timeout: float = 300.0) -> "FfmpegSkill":
        tried = []
        for c in cls.candidates(explicit):
            tried.append(str(c))
            if (c / "scripts" / "_contract.py").is_file() and all((c / "scripts" / f"{t}.py").is_file() for t in TOOLS_USED):
                return cls(c.resolve(), timeout)
        raise ThumbnailError("TOOL_ERROR", "ffmpeg-skill not found (need scripts/_contract.py and scripts/{probe,look}.py); "
                             "set THUMBNAIL_SKILL_FFMPEG_SKILL_DIR, or omit video_frame assets and use still images only",
                             {"reason": "ffmpeg_skill_missing", "tried": tried}, retryable=False)

    def script(self, tool: str) -> str:
        if tool not in TOOLS_USED:
            raise ThumbnailError("INTERNAL_ERROR", f"tool {tool!r} is not on the adapter allowlist")
        return str(self.directory / "scripts" / f"{tool}.py")

    # ---- contract of the located skill
    def info(self, timeout: float = 60.0) -> SkillInfo:
        if self._info is not None:
            return self._info
        problems: List[str] = []
        version, contract_version, tools = "unknown", "unknown", {}
        argv = [sys.executable, str(self.directory / "scripts" / "_contract.py"), "--json", "--static"]
        try:
            code, out, err, _ = self._popen(argv, timeout)
            doc = json.loads(out or "{}")
            version = str(doc.get("skill", {}).get("version", "unknown"))
            contract_version = str(doc.get("contract_version", "unknown"))
            tools = {t["name"]: t for t in doc.get("tools", []) if isinstance(t, dict) and "name" in t}
        except (ThumbnailError, ValueError, OSError) as e:
            problems.append(f"cannot read ffmpeg-skill contract: {getattr(e, 'message', str(e))}")
        if contract_version != SUPPORTED_CONTRACT_VERSION:
            problems.append(f"ffmpeg-skill contract_version {contract_version} is not {SUPPORTED_CONTRACT_VERSION}")
        if not _VERSION_RE.match(version):
            problems.append(f"cannot parse ffmpeg-skill version {version!r}")
        for tool, flags in FLAGS_USED.items():
            spec = tools.get(tool)
            if spec is None:
                problems.append(f"ffmpeg-skill tool {tool!r} is missing from its contract")
                continue
            props = spec.get("input_schema", {}).get("properties", {})
            missing = [f for f in flags if f not in props]
            if missing:
                problems.append(f"ffmpeg-skill/{tool} lacks flag(s) {missing}")
        self._info = SkillInfo(self.directory, version, contract_version, tools, problems)
        return self._info

    # ---- execution
    def cancel(self) -> None:
        self._cancelled = True

    def _popen(self, argv: Sequence[str], timeout: Optional[float]) -> Tuple[int, str, str, float]:
        for a in argv:
            if not isinstance(a, str) or "\x00" in a:
                raise ThumbnailError("INTERNAL_ERROR", "argv element is not a clean string")
        if self._cancelled:
            raise ThumbnailError("CANCELLED", "cancelled before the tool started")
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, errors="replace", env=_clean_env(), cwd=str(self.directory), **_group_kwargs())
        except FileNotFoundError as e:
            raise ThumbnailError("TOOL_ERROR", f"cannot start {os.path.basename(argv[0])}: {e}", {"reason": "executable_missing"}, retryable=False)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise ThumbnailError("TOOL_ERROR", f"{os.path.basename(argv[1]) if len(argv) > 1 else argv[0]} exceeded {timeout}s", {"reason": "timeout", "timeout": timeout})
        except KeyboardInterrupt:
            kill_tree(proc)
            self._cancelled = True
            raise ThumbnailError("CANCELLED", "interrupted while a tool was running", {"reason": "signal"})
        return proc.returncode, out or "", err or "", round(time.monotonic() - t0, 3)

    def run_tool(self, tool: str, args: Sequence[str], timeout: Optional[float] = None) -> ToolRun:
        argv = [sys.executable, self.script(tool), *args, "--json"]
        code, out, err, seconds = self._popen(argv, timeout or self.timeout)
        data = _parse_json(out)
        tail = "\n".join(err.strip().splitlines()[-12:])
        run = ToolRun(tool, argv, code, data, tail, seconds, list(data.get("commands", [])) if isinstance(data.get("commands"), list) else [])
        self.runs.append(run)
        if code != 0 or (isinstance(data, dict) and data.get("status") == "failed"):
            msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
            raise ThumbnailError("TOOL_ERROR", f"ffmpeg-skill/{tool} failed (exit {code}): {msg or tail or 'no message'}",
                                 {"reason": "tool_failed", "tool": f"ffmpeg-skill/{tool}", "exit_code": code, "stderr_tail": tail,
                                  "error_kind": (data.get("error") or {}).get("kind") if isinstance(data.get("error"), dict) else None})
        return run

    # ---- typed helpers
    def probe(self, path: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """ffmpeg-skill/probe prints its document directly (no status envelope)."""
        argv = [sys.executable, self.script("probe"), path]
        code, out, err, seconds = self._popen(argv, timeout or min(self.timeout, 120.0))
        data = _parse_json(out)
        self.runs.append(ToolRun("probe", argv, code, data, "\n".join(err.strip().splitlines()[-12:]), seconds))
        if code != 0 or not isinstance(data, dict) or "duration" not in data:
            raise ThumbnailError("INVALID_INPUT", f"ffmpeg-skill/probe could not read {os.path.basename(path)}: {err.strip().splitlines()[-1] if err.strip() else 'no output'}",
                                 {"reason": "unreadable_media", "path": path, "exit_code": code})
        return data

    def extract_frame(self, video_path: str, timestamp: float, out_dir: Path, stem: str, timeout: Optional[float] = None) -> Path:
        """Extract exactly the frame at `timestamp` (no scene detection, no scoring) as a PNG under
        `out_dir`, named deterministically by ffmpeg-skill/look. Returns the resulting file path.

        ffmpeg-skill/look reports `{"status": "completed", "output": ...}` even when the underlying
        `ffmpeg -ss <timestamp>` decoded zero frames and wrote nothing: this happens for any timestamp
        landing after the last frame actually present in the stream but at or before the container's
        reported `duration` (duration commonly extends slightly past the last frame's own PTS — the
        gap is one frame interval, so on a 10 fps video the last ~0.1s of `duration` has no frame to
        seek to). That is a fact about the caller's timestamp, not a transient tool failure: retrying
        the identical request will fail identically forever. So this is reported as `INVALID_TIME_RANGE`
        (not retryable), never `TOOL_ERROR`, and the file's actual existence is what decides it, not
        ffmpeg-skill's own claim of success — the same "don't trust a reported success path, check it
        was actually written" rule executor.py applies to every artifact this skill produces."""
        run = self.run_tool("look", [video_path, "--at", fmt_seconds(timestamp), "--no-timecode", "-o", str(out_dir / stem)], timeout)
        outputs = run.data.get("outputs") or ([run.data["output"]] if run.data.get("output") else [])
        expected = out_dir / frame_filename(stem, timestamp)
        if outputs:
            candidate = Path(outputs[0])
            if candidate.is_file():
                return candidate
        if expected.is_file():
            return expected
        raise ThumbnailError("INVALID_TIME_RANGE",
                             f"no frame could be decoded at timestamp {timestamp}s (ffmpeg-skill/look reported success but wrote nothing; "
                             "this timestamp is at or past the last frame actually present in the source, even though it is within the "
                             "reported duration) — choose an earlier timestamp",
                             {"reason": "no_frame_at_timestamp", "timestamp": timestamp, "expected": str(expected), "reported_outputs": outputs},
                             retryable=False)


def _parse_json(text: str) -> Dict[str, Any]:
    """ffmpeg-skill prints one JSON document on stdout under --json; tolerate a preceding plain line."""
    text = text.strip()
    if not text:
        return {}
    start = text.find("{")
    if start < 0:
        return {}
    try:
        doc = json.loads(text[start:])
    except ValueError:
        try:
            doc = json.loads(text.splitlines()[-1])
        except ValueError:
            return {}
    return doc if isinstance(doc, dict) else {}


__all__ = ["FfmpegSkill", "SkillInfo", "ToolRun", "fmt_seconds", "frame_filename", "kill_tree", "TOOLS_USED", "FLAGS_USED", "SUPPORTED_CONTRACT_VERSION"]
