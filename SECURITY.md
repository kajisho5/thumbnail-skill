# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability.

Instead, use GitHub's private vulnerability reporting for this repository:

1. Go to the **Security** tab of this repository.
2. Select **Advisories** → **Report a vulnerability**.

This opens a private advisory visible only to the maintainer and you, so the details aren't
public until a fix is available.

## Supported versions

This project is pre-1.0 and does not yet maintain multiple release branches. Security fixes
are made against the latest release only.

## Scope

`thumbnail-skill` is a deterministic rendering execution component (see `README.md` /
`docs/security.md` for its security model: `PathPolicy`, forbidden-field rejection, the
ffmpeg-skill delegation boundary). Reports about that boundary, path/symlink handling, or
input validation are all in scope. Issues in an upstream dependency (Pillow, ffmpeg,
ffmpeg-skill) should also be reported to that project directly if the issue isn't specific to
how this skill uses it.
