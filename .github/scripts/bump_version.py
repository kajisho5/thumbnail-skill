"""Release-workflow version bump: write NEW_VERSION (an env var, never spliced into shell text via `${{ }}`) into
pyproject.toml's `version = "..."` line, then prove it took effect.

pyproject.toml is the only file that carries the version (docs/decisions.md ADR-11): thumbnail_skill.VERSION is read
from it at runtime, and the contract's `version`, responses and provenance all take
VERSION, so this one write is the whole bump. After writing, the package is imported from this checkout's src/ (never
from an installed copy) and VERSION must equal NEW_VERSION; otherwise the release stops here, before anything is
committed or tagged. Only the package itself is imported (no Pillow, which the release job does not install);
tests/test_version_source.py checks that the contract follows VERSION.

Usage (from the repository root): NEW_VERSION=X.Y.Z python3 .github/scripts/bump_version.py"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
VERSION_LINE_RE = re.compile(r'(?m)^version = "[^"]*"')


def reported_versions() -> set:
    """The version the package reports, read from this checkout's src/."""
    sys.path.insert(0, str(Path("src").resolve()))
    import thumbnail_skill

    return {thumbnail_skill.VERSION, thumbnail_skill.__version__}


def main() -> int:
    new_version = os.environ.get("NEW_VERSION", "")
    if not new_version:
        print("::error::NEW_VERSION is empty", file=sys.stderr)
        return 1
    text = PYPROJECT.read_text(encoding="utf-8")
    updated, count = VERSION_LINE_RE.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        print(f"::error::expected exactly one version line in pyproject.toml, replaced {count}", file=sys.stderr)
        return 1
    PYPROJECT.write_text(updated, encoding="utf-8")
    print(f"pyproject.toml version -> {new_version}")
    reported = reported_versions()
    if reported != {new_version}:
        print(f"::error::after the bump the package still reports {sorted(reported)}, not {new_version}", file=sys.stderr)
        return 1
    print(f"thumbnail_skill.VERSION reports {new_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
