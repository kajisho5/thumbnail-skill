"""Structured error model. Every failure that crosses the Skill boundary is a ThumbnailError with a
code from ERROR_TABLE; the CLI turns it into {"ok": false, "error": {"code", "message", "retryable", "details"}}.

These codes are this skill's own: not video-production-agent's AIProviderError, not ffmpeg-skill's
{"kind": ...} incidents. A consumer maps them at its adapter boundary."""
from __future__ import annotations

from typing import Any, Dict, Optional

# code -> (exit code, retryable)
ERROR_TABLE: Dict[str, Any] = {
    "INVALID_REQUEST": (2, False),         # document shape / unknown fields / bad types
    "INVALID_INPUT": (3, False),           # a source file is missing, unreadable, not a supported image, or not a regular file
    "PATH_NOT_ALLOWED": (4, False),        # input outside allowed roots, output outside workspace, traversal, symlink escape
    "UNSUPPORTED_OPERATION": (5, False),   # tool name not implemented by this skill
    "UNSUPPORTED_FORMAT": (6, False),      # output/element format not in the contract
    "MISSING_INPUT": (7, False),           # an element references an asset_id / font_id that is not declared or not registered
    "INVALID_TIME_RANGE": (8, False),      # a video_frame timestamp is negative, non-finite, or beyond the source duration
    "DEPENDENCY_ERROR": (11, False),       # duplicate id, self-reference, or another structural inconsistency
    "TOOL_ERROR": (12, True),              # ffmpeg-skill / ffmpeg failed, timed out, or is unavailable
    "OUTPUT_ERROR": (13, False),           # output could not be written, is empty, collides with an input, or exists
    "VALIDATION_ERROR": (14, False),       # output written but failed post-render validation (dimensions, format, hash)
    "CANCELLED": (15, True),               # interrupted by signal
    "INTERNAL_ERROR": (16, False),         # a bug in this skill
}
ERROR_CODES = tuple(ERROR_TABLE)
EXIT_CODES = {code: ERROR_TABLE[code][0] for code in ERROR_CODES}


class ThumbnailError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, retryable: Optional[bool] = None):
        if code not in ERROR_TABLE:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = ERROR_TABLE[code][1] if retryable is None else bool(retryable)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "details": self.details}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]
