# Changelog

## 0.1 — 2026-04-19

Bug fixes and hardening across all three tools. No format or CLI changes — existing JSON extracts and patched binaries remain compatible.

### mgs_tool.py

- Fix Shift-JIS decoder lead-byte range ([#12](https://github.com/RayCharlizard/smt-nine-tools/pull/12))
- Make `encode_translation` strict: raise `UnicodeEncodeError` on non-ASCII input instead of silently corrupting output, and surface per-string errors during `insert` ([#14](https://github.com/RayCharlizard/smt-nine-tools/pull/14))
- Raise `ValueError` on unrecognised `{placeholder}` tokens so typos fail loudly ([#20](https://github.com/RayCharlizard/smt-nine-tools/pull/20))
- Wire `end_limit` into `parse_subscript` bounds checks ([#18](https://github.com/RayCharlizard/smt-nine-tools/pull/18))
- `roundtrip` now actually exercises the insert path end-to-end ([#16](https://github.com/RayCharlizard/smt-nine-tools/pull/16))
- `insert` only copies `.mgs` / `.mgp` files from the source tree, skipping unrelated leftovers ([#19](https://github.com/RayCharlizard/smt-nine-tools/pull/19))

### xbe_tool.py

- `insert` warns when JSON entries reference XBE offsets or byte lengths that no longer match the target file ([#15](https://github.com/RayCharlizard/smt-nine-tools/pull/15))
- Validate XBE section layout against hardcoded SMT Nine offsets on load, so the tool refuses to run on an unexpected executable ([#22](https://github.com/RayCharlizard/smt-nine-tools/pull/22))

### font_patch.py

- Load DejaVu Sans Mono Bold lazily instead of at import time, so `decode-page` works without the font installed ([#13](https://github.com/RayCharlizard/smt-nine-tools/pull/13))
- `preview` actually 4× upscales the preview row ([#17](https://github.com/RayCharlizard/smt-nine-tools/pull/17))
- Patch only DXT1 blocks that intersect Latin tiles, leaving neighbouring kanji blocks untouched ([#21](https://github.com/RayCharlizard/smt-nine-tools/pull/21))

## Initial release

- Discovery of SMT Nine's built-in halfwidth Latin rendering system and the 2-byte XBE patch that enables it globally
- `mgs_tool.py`: MGS/MGP script extraction, translation insertion, and byte-for-byte roundtrip
- `xbe_tool.py`: in-place XBE string extraction and insertion with null-padding
- `font_patch.py`: halfwidth Latin glyph rendering and DXT1 encoding for `sys_f24.xpr` / `sys_f18.xpr`
- Technical documentation: [HALFWIDTH_SYSTEM.md](docs/HALFWIDTH_SYSTEM.md), [SCRIPT_FORMAT.md](docs/SCRIPT_FORMAT.md), [FONT_SYSTEM.md](docs/FONT_SYSTEM.md)
