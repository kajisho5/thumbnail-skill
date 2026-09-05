"""Single dispatch point for every tool, and the `run -` process-boundary transport.

  run_tool("thumbnail/render", params, policy=...)  ->  one response document (never raises)
  run_request({"tool": ..., "params": ...})          ->  {"ok": true, "tool", "result"} | raises

`run_tool` always returns a document (errors are inside it, per Executor.response); `run_request` is
the stdin/stdout transport used by `thumbnail run -` and by an external caller."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import ThumbnailError
from .executor import TOOLS, Executor
from .security import PathPolicy


def tool_names() -> Dict[str, str]:
    return {t: t for t in TOOLS}


def run_tool(tool: str, params: Any, policy: Optional[PathPolicy] = None, ffmpeg_skill_dir: Optional[str] = None, timeout: float = 120.0) -> Dict[str, Any]:
    if tool not in TOOLS:
        raise ThumbnailError("UNSUPPORTED_OPERATION", f"unknown tool {tool!r}", {"tool": tool, "supported": list(TOOLS)})
    executor = Executor(policy or PathPolicy(), ffmpeg_skill_dir, timeout)
    return executor.response(tool, params)


def run_request(doc: Any, policy: Optional[PathPolicy] = None, ffmpeg_skill_dir: Optional[str] = None, timeout: float = 120.0) -> Dict[str, Any]:
    """Process-boundary transport: one JSON request {"tool": name, "params": {...}} -> one JSON
    response. `ok` says whether the request was well-formed and dispatched; a tool's own success/
    failure is the response document at result (which already carries its own "ok"/"status")."""
    if not isinstance(doc, dict):
        raise ThumbnailError("INVALID_REQUEST", "request must be a JSON object with 'tool' and 'params'")
    extra = set(doc) - {"tool", "params"}
    if extra:
        raise ThumbnailError("INVALID_REQUEST", f"unknown request keys {sorted(extra)}")
    name = doc.get("tool")
    if not isinstance(name, str) or name not in TOOLS:
        raise ThumbnailError("INVALID_REQUEST", f"'tool' must be one of {list(TOOLS)}", {"tools": list(TOOLS)})
    params = doc.get("params", {})
    if not isinstance(params, dict):
        raise ThumbnailError("INVALID_REQUEST", "'params' must be a JSON object")
    result = run_tool(name, params, policy, ffmpeg_skill_dir, timeout)
    return {"ok": True, "tool": name, "result": result}


__all__ = ["run_tool", "run_request", "tool_names"]
