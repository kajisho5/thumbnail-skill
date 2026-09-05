"""thumbnail CLI.

stdout: with --json exactly one JSON document (contract / doctor / response), on success and failure
alike. stderr: diagnostics only. Exit code: 0 on success, otherwise errors.EXIT_CODES[error.code].

  thumbnail skill --json                       contract (alias: contract --json)
  thumbnail doctor --json                      environment vs. contract (fonts, ffmpeg-skill, path policy)
  thumbnail validate request.json --json       structural validation only; touches nothing else
  thumbnail render request.json --json         render a ThumbnailDocument to PNG/JPEG
  thumbnail extract-frame request.json --json  one video frame at one timestamp, nothing else
  thumbnail run - --json                       process-boundary transport: {"tool", "params"} in, one document out
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from typing import Any, List, Optional

from . import PACKAGE_NAME, VERSION
from .contract import skill_contract
from .doctor import doctor_report
from .errors import EXIT_CODES, ThumbnailError
from .executor import RESPONSE_SCHEMA_ID
from .security import PathPolicy
from .skill import run_request, run_tool
from . import SKILL_ID


def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--json", action="store_true", help="machine-readable JSON on stdout (exactly one document)")


def _add_run_opts(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("request", help="path to a request document, or - for stdin")
    ap.add_argument("--workspace", help="directory writes (output, cache, tmp) are confined to (default: current directory)")
    ap.add_argument("--allowed-input", action="append", help="restrict inputs to this root (repeatable)")
    ap.add_argument("--ffmpeg-skill", help="ffmpeg-skill checkout directory (default: env / ~/.claude/skills/ffmpeg-skill / ./vendor / ..)")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per ffmpeg-skill invocation (default 120)")
    ap.add_argument("--no-reuse", action="store_true", help="do not reuse a cached artifact with a matching identity")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="thumbnail", description=f"{PACKAGE_NAME} {VERSION}: deterministic thumbnail rendering execution (not an AI agent)")
    ap.add_argument("--version", action="version", version=f"{PACKAGE_NAME} {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("skill", "contract"):
        c = sub.add_parser(name, help="print the Skill / Capability / Tool contract")
        _add_common(c)

    d = sub.add_parser("doctor", help="diagnose the environment against the contract (fonts, ffmpeg-skill, path policy)")
    d.add_argument("--workspace")
    d.add_argument("--allowed-input", action="append")
    d.add_argument("--ffmpeg-skill")
    _add_common(d)

    v = sub.add_parser("validate", help="validate a ThumbnailDocument; touches no file system, no font, no ffmpeg-skill")
    v.add_argument("request")
    _add_common(v)

    r = sub.add_parser("render", help="render a ThumbnailDocument to a PNG/JPEG artifact")
    _add_run_opts(r)
    _add_common(r)

    f = sub.add_parser("extract-frame", help="extract exactly one video frame at one timestamp")
    _add_run_opts(f)
    _add_common(f)

    run = sub.add_parser("run", help="read one JSON tool request ({\"tool\": ..., \"params\": {...}}) from stdin, print one JSON response")
    run.add_argument("request", help="'-' (stdin)")
    run.add_argument("--workspace")
    run.add_argument("--allowed-input", action="append")
    run.add_argument("--ffmpeg-skill")
    run.add_argument("--timeout", type=float, default=120.0)
    run.add_argument("--json", action="store_true", default=True, help="accepted for consistency with the other subcommands; `run` always prints exactly one JSON document")
    return ap


def _read_document(spec: str) -> Any:
    try:
        text = sys.stdin.read() if spec == "-" else open(spec, "r", encoding="utf-8").read()
    except OSError as e:
        raise ThumbnailError("INVALID_REQUEST", f"cannot read request document: {e}")
    if len(text) > 16 * 1024 * 1024:
        raise ThumbnailError("INVALID_REQUEST", "request document is larger than 16 MiB")
    try:
        return json.loads(text)
    except ValueError as e:
        raise ThumbnailError("INVALID_REQUEST", f"request document is not valid JSON: {e}")


def _error_document(e: ThumbnailError) -> dict:
    return {"schema": RESPONSE_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "ok": False,
            "status": "cancelled" if e.code == "CANCELLED" else "error", "error": e.to_dict(), "warnings": []}


def _emit(doc: dict, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    else:
        _human(doc)
    sys.stdout.flush()


def _human(doc: dict) -> None:
    if not doc.get("ok", True) and doc.get("error"):
        e = doc["error"]
        sys.stderr.write(f"error [{e['code']}]: {e['message']}" + (f" {json.dumps(e.get('details'))}" if e.get("details") else "") + "\n")
        return
    if "output" in doc:
        print(f"wrote {doc['output']} ({doc['width']}x{doc['height']} {doc['format']}, {doc['size']} bytes, {doc['sha256'][:19]}...)"
              + (" [reused]" if doc.get("reused") else ""))
    elif "validation" in doc:
        v = doc["validation"]
        print(f"valid: {v['document_id']} canvas {v['canvas']['width']}x{v['canvas']['height']}, {v['assets']} asset(s), {v['elements']} element(s)")
    elif "checks" in doc:
        print(f"{PACKAGE_NAME} {VERSION}: {doc['status']}")
        for k in ("pillow", "ffmpeg_skill", "path_policy"):
            print(f"{k}: {json.dumps(doc['checks'].get(k))}")
        for fid, st in doc["checks"].get("fonts", {}).items():
            print(f"  font {fid}: {st.get('status')}" + (f" ({st.get('path')})" if st.get("path") else ""))
        for p in doc["problems"]:
            print(f"problem: {p}")
        for w in doc["warnings"]:
            print(f"warning: {w}")
    elif "tools" in doc:
        print(f"{doc['skill_id']} {doc['version']}: " + ", ".join(t["tool_id"] for t in doc["tools"]))


def _run_tool_command(tool: str, args: argparse.Namespace) -> dict:
    document = _read_document(args.request)
    if not isinstance(document, dict):
        raise ThumbnailError("INVALID_REQUEST", "request document must be a JSON object", {"field": "document"})
    policy = PathPolicy(args.workspace, args.allowed_input)
    params = dict(document)
    raw_options = params.get("options")
    if raw_options is not None and not isinstance(raw_options, dict):
        raise ThumbnailError("INVALID_REQUEST", "'options' must be a JSON object", {"field": "options"})
    options = dict(raw_options or {})
    if args.no_reuse:
        options["reuse"] = False
    if "timeout" not in options and args.timeout:
        options["timeout"] = args.timeout
    if args.ffmpeg_skill and "ffmpeg_skill" not in options:
        options["ffmpeg_skill"] = args.ffmpeg_skill
    params["options"] = options
    return run_tool(tool, params, policy, args.ffmpeg_skill, args.timeout)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    try:
        if args.cmd in ("skill", "contract"):
            doc = skill_contract()
        elif args.cmd == "doctor":
            doc = doctor_report(args.ffmpeg_skill, args.workspace, args.allowed_input)
            _emit(doc, as_json)
            return 0 if doc["status"] != "fail" else 1
        elif args.cmd == "validate":
            params = _read_document(args.request)
            if not isinstance(params, dict):
                raise ThumbnailError("INVALID_REQUEST", "request document must be a JSON object", {"field": "document"})
            doc = run_tool("thumbnail/validate", params if "document" in params else {"document": params})
        elif args.cmd == "render":
            doc = _run_tool_command("thumbnail/render", args)
        elif args.cmd == "extract-frame":
            doc = _run_tool_command("thumbnail/extract_frame", args)
        elif args.cmd == "run":
            if args.request != "-":
                raise ThumbnailError("INVALID_REQUEST", "run takes '-' and reads one JSON request from stdin")
            raw = _read_document("-")
            policy = PathPolicy(args.workspace, args.allowed_input)

            def _cancel(signum: int, frame: Any) -> None:
                raise KeyboardInterrupt()

            for sig in [signal.SIGINT, signal.SIGTERM] + ([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else []):  # type: ignore[attr-defined]
                try:
                    signal.signal(sig, _cancel)
                except (ValueError, OSError):
                    pass
            try:
                doc = run_request(raw, policy, args.ffmpeg_skill, args.timeout)
            except KeyboardInterrupt:
                doc = {"ok": False, "error": ThumbnailError("CANCELLED", "interrupted", {"reason": "signal"}).to_dict()}
        else:
            raise ThumbnailError("INTERNAL_ERROR", f"unhandled command {args.cmd!r}")
    except ThumbnailError as e:
        doc = _error_document(e)
        _emit(doc, as_json)
        return e.exit_code
    _emit(doc, as_json)
    return _exit_code_for(doc)


def _exit_code_for(doc: Any) -> int:
    """0 only when the actual tool response succeeded. `thumbnail run -` wraps the tool's own
    response in {"ok": true, "tool", "result": <response>}: that outer "ok" means only "the request
    was well-formed and dispatched", so it stays true even when the nested `result` failed. Every
    other command's `doc` carries the tool's own {"ok", "error"} directly. Checking the outer "ok"
    alone would make `run -` report exit 0 for a request that actually failed to render."""
    if not isinstance(doc, dict):
        return 0
    body = doc["result"] if isinstance(doc.get("result"), dict) and "ok" in doc["result"] else doc
    if body.get("ok") is False:
        err = body.get("error") or {}
        return EXIT_CODES.get(err.get("code", "INTERNAL_ERROR"), EXIT_CODES["INTERNAL_ERROR"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
