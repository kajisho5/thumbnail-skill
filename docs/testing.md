# Testing

```bash
pip install -e . pytest pyflakes
python -m pyflakes src tests
python -m pytest -q
```

## Layout

| File | Covers |
|---|---|
| `tests/test_unit.py` | `model.py` structural validation (bounds, forbidden fields, enums, id patterns), `fonts.py` resolution, `canonical.py` hashing, the error-code table |
| `tests/test_security.py` | `PathPolicy`: traversal, prefix-collision (not containment), symlink escape, workspace escape, unsafe file names, NUL bytes; the forbidden-field boundary exercised through `run_tool` |
| `tests/test_integration.py` | Real Pillow rendering against real image fixtures: still-image thumbnails, PNG and JPEG output, z-order stacking, all four `fit` modes, multiline/Unicode (including Japanese) text, deterministic re-render (byte-identical), reuse (cache hit, cache-corruption recovery), provenance completeness, the `run -` stdin/stdout transport, and — when a real `ffmpeg-skill` checkout and `ffmpeg`/`ffprobe` are available — actual `video_frame` extraction at an exact timestamp and its `INVALID_TIME_RANGE` boundary |
| `tests/test_contract.py` | The contract and doctor report are valid JSON and match the implementation (tool ids, error codes, element types, output formats, font ids); no unimplemented operation (best-frame selection, auto layout, title generation, face detection, click-through prediction) is ever named in the contract |

## Fixtures

`tests/fixtures/generate.py` builds every fixture at test time (nothing binary is committed):
solid-colour PNG/JPEG stills, an RGBA overlay image, a tall image (to exercise `fit` against a
mismatched aspect ratio), a non-image text file (for "invalid image" cases), and — only when
`ffmpeg`/`ffprobe` are on `PATH` — a short synthetic H.264 video. Tests that need the video fixture
or an ffmpeg-skill checkout skip cleanly (`pytest.skip`) rather than failing when either is absent,
the same convention `audio-production-skill` and `transcription-skill` use.

## Real-media video E2E

CI clones a pinned `ffmpeg-skill` checkout into `vendor/ffmpeg-skill` and points
`THUMBNAIL_SKILL_FFMPEG_SKILL_DIR` at it (see `.github/workflows/tests.yml`), the same pattern
`audio-production-skill` uses. Locally:

```bash
git clone https://github.com/kajisho5/ffmpeg-skill vendor/ffmpeg-skill
THUMBNAIL_SKILL_FFMPEG_SKILL_DIR=$PWD/vendor/ffmpeg-skill python -m pytest -q
```

## Deterministic / visual verification

`test_deterministic_render_same_bytes` renders the same document twice and asserts byte-identical
output. `test_reuse_hits_cache_on_second_render` deletes the output and renders again, asserting
`reused: true` and an identical sha256. `test_reuse_rebuilds_when_cache_entry_is_corrupted` corrupts
the cache file directly and asserts the next render notices and rebuilds rather than serving a
broken file as `reused: true`. `test_z_order_stacking` samples an actual output pixel to prove
stacking order is respected, rather than trusting the code path alone. There is no subjective
"is this a good thumbnail" check anywhere, deliberately: that judgement is not this skill's to make.
