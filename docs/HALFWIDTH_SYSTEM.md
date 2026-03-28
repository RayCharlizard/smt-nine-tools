# SMT Nine Halfwidth Rendering System

SMT Nine's rendering engine contains a complete three-layer system for rendering Latin characters at half the width of Japanese kanji. This system was built during development but only partially activated in the shipping game — it works for battle UI text but is gated off for the main dialogue system. Enabling it requires a 2-byte patch to the game executable.

This document covers each layer of the system, the patch to enable it, and the font texture modifications needed for proper halfwidth glyphs.

## Layer 1: sysfont.tbl — Character Table

**File:** `Media/SysFont/sysfont.tbl` (22,060 bytes)

The font table contains two independent sections, one for each font file:

| Section | Offset | Byte-Count Header | Entries | Font File | Tiles/Page | Grid |
|---------|--------|-------------------|---------|-----------|-----------|------|
| 1 | 0x0000 | 14,114 bytes | 7,057 | sys_f18.xpr (18pt) | 28×28=784 | 784×9=7,056 tiles |
| 2 | 0x3726 | 7,938 bytes | 3,969 | sys_f24.xpr (24pt) | 21×21=441 | 441×9=3,969 tiles |

Each section starts with a 4-byte header: a little-endian uint16 byte-count followed by a uint16 "extra" value (purpose unknown, preserved on rebuild). The body is a sequence of 2-byte big-endian Shift-JIS character codes. Both sections begin with the same kanji sequence (亜, 唖, 娃...) but section 2 is shorter.

**Marker system:** Section 1 (sys_f18) contains ZN (0x5A4E) and HN (0x484E) marker entries that switch between fullwidth and halfwidth mode. The parser at XBE VA 0x151AF3 checks for these markers:
- ZN (0x5A4E) → sets fullwidth mode (characters flagged with `0xFF0000`)
- HN (0x484E) → sets halfwidth mode (characters flagged with `0xFE0000`)

**Section 2 (sys_f24) has NO markers.** All entries are fullwidth. Halfwidth rendering for this font is controlled entirely by the runtime lookup (Layer 2).

**Separator entries:** 276 entries in section 2 have the value 0x2E2E. These advance the tile grid position (consuming a tile slot) but do not create character map entries. ZN/HN markers, when present, are skipped entirely — no tile allocated.

### Latin Character Positions

**Section 2 (sys_f24)** — Entries 3102–3163 → page 7:
- 0-9: row 0, cols 15–20 + row 1, cols 0–3 (entries 3102–3111)
- A-Z: row 1, cols 4–20 + row 2, cols 0–8 (entries 3112–3137)
- a-z: row 2, cols 9–20 + row 3, cols 0–13 (entries 3138–3163)

**Section 1 (sys_f18)** — Entries 6909–6970 (tiles 6908–6969) → page 8:
- 0-9: row 22, cols 20–27 + row 23, cols 0–1 (tiles 6908–6917)
- A-Z: row 23, cols 2–27 (tiles 6918–6943)
- a-z: row 24, cols 0–25 (tiles 6944–6969)

## Layer 2: XBE Halfwidth Character Table

**Location:** VA 0x3384A8 (file offset 0x327048)

A flat array of 259 two-byte Shift-JIS character codes listing every character eligible for halfwidth rendering:

| Range | Count | Characters |
|-------|-------|------------|
| Latin uppercase | 26 | A–Z |
| Latin lowercase | 26 | a–z |
| Digits | 10 | 0–9 |
| Katakana | 86 | ア–ン + small kana |
| Symbols | 111 | Punctuation, brackets, operators |

**Notably absent:** Hiragana. This means enabling halfwidth globally will affect katakana spacing in Japanese text but NOT hiragana or kanji — they remain fullwidth regardless of the flag state.

The renderer at VA 0x151539–0x151567 performs a linear scan of this table to determine if a character should be rendered halfwidth.

## Layer 3: Rendering Flag Gate

**Location:** VA 0x151533 (file offset 0x141533)

```asm
test byte ptr [ebp+0x74], 0x10    ; Check if bit 0x10 is set
je  +0x47                          ; If NOT set, skip halfwidth lookup
; ... halfwidth table lookup code ...
```

When bit 0x10 of `[ebp+0x74]` is set, the halfwidth character table (Layer 2) is consulted. Matching characters receive the `0xFE0000` flag (halfwidth). Non-matching characters receive `0xFF0000` (fullwidth).

When bit 0x10 is NOT set, the jump is taken and ALL characters default to fullwidth (`0xFF0000`), regardless of whether they appear in the halfwidth table.

**In the shipping game**, this flag is set for battle UI contexts but NOT for the main dialogue system. This is why MrRichard999's ASCII replacements in the XBE showed fullwidth letters in dialogue — the characters were there but the halfwidth rendering wasn't being applied.

## The Patch

**File offset 0x141537:** Change `74 47` to `90 90`

This replaces the `je +0x47` (conditional jump) with two NOP instructions. The `test` instruction at 0x141533 still executes but its result is ignored — execution always falls through to the halfwidth table lookup.

**Effect:** Every text rendering context now consults the halfwidth character table. Latin letters, digits, katakana, and symbols render at half width. Hiragana and kanji are unaffected (they aren't in the table).

**Side effect:** Katakana in Japanese text will also render halfwidth. This is only relevant if displaying mixed Japanese/English text in the same context, which the translation doesn't need to do.

## Font Texture Modifications

The XBE patch alone isn't sufficient — the original Latin glyphs in the font textures are designed for fullwidth rendering (17–20px wide in a 24px tile). At halfwidth, they'd be horizontally compressed. The font textures need replacement glyphs designed for halfwidth display.

### Replacement Glyphs

For both font files, the 62 Latin characters (0-9, A-Z, a-z) are replaced with halfwidth-designed glyphs:

| Font | Tile Pitch | Replacement Font | Size | Glyph Width | Target Page |
|------|-----------|-----------------|------|-------------|-------------|
| sys_f24.xpr | 24px | DejaVu Sans Mono Bold | 18pt | ~11px | Page 7 |
| sys_f18.xpr | 18px | DejaVu Sans Mono Bold | 13pt | ~8px | Page 8 |

Glyphs are rendered with color RGB(197, 198, 222) to match the existing font brightness, left-aligned within the tile, with transparent backgrounds. The DXT1 texture compression uses 1-bit alpha mode for tiles with mixed opaque/transparent pixels.

### XPR0 Texture Format

**Header:** Magic "XPR0" (4 bytes) + total file size (uint32 LE) + header size/data offset (uint32 LE). Resource headers at offset 12, each 20 bytes.

**D3D format word bit layout:**
- Bits 8–15: pixel format (0x0C = DXT1, 0x0F = DXT5, 0x04 = A4R4G4B4)
- Bits 20–23: log2(width)
- Bits 24–27: log2(height)

**SysFont textures use LINEAR layout** (not Morton-swizzled). This is important — `font.xpr` and `font2.xpr` (the battle fonts) use Morton swizzling, but the SysFont files do not. Always check format before decoding.

## XBE Address Reference

**VA-to-file-offset conversion depends on the XBE section.** The simple rule `file = VA − 0x10000` only applies to `.text` (code). Data sections have different mappings:

| Section | VA Range | File Range | Formula |
|---------|----------|-----------|---------|
| .text | 0x011000–0x25DA34 | 0x001000–0x24DA34 | file = VA − 0x10000 |
| .rdata | 0x317460–0x37BE84 | 0x306000–0x36AA14 | file = VA − 0x11460 |
| .data | 0x37BEA0–0x6E1620 | 0x36B000–0x5242E4 | file = VA − 0x10EA0 |

### Key Code Addresses

| VA | File Offset | Function |
|----|-------------|----------|
| 0x150C70 | 0x140C70 | Font file selection (ESI=0 → sys_f18, ESI≠0 → sys_f24) |
| 0x151980 | 0x141980 | sysfont.tbl loading and parsing |
| 0x151AE4 | 0x141AE4 | Font table entry loop |
| 0x151AF3 | 0x141AF3 | ZN marker check → set fullwidth mode |
| 0x151B06 | 0x141B06 | HN marker check → set halfwidth mode |
| 0x151B5D | 0x141B5D | Fullwidth flag application: `or eax, 0xFF0000` |
| 0x151B64 | 0x141B64 | Halfwidth flag application: `or eax, 0xFE0000` |
| **0x151533** | **0x141533** | **Halfwidth gate: `test [ebp+0x74], 0x10`** |
| 0x151539 | 0x141539 | Halfwidth table lookup start |
| 0x151567 | 0x141567 | Halfwidth table lookup end |
| 0x1515A8 | 0x1415A8 | Render-path halfwidth flag: `or eax, 0xFE0000` |
| 0x1515AF | 0x1415AF | Render-path fullwidth default: `or eax, 0xFF0000` |

### Key Data Addresses

| VA | File Offset | Contents |
|----|-------------|----------|
| 0x3384A8 | 0x327048 | Halfwidth character table (259 × 2 bytes) |
| 0x338488 | 0x327028 | "sys_f24.xpr" filename string |
| 0x338494 | 0x327034 | "sys_f18.xpr" filename string |
| 0x3388E4 | 0x327484 | "sysfont.tbl" filename string |
| 0x346BA8 | 0x335748 | Halfwidth cursor advance: 4.0 (float) |
| 0x31B57C | 0x30A11C | Texture dimension divisor: 512.0 (float) |

### Font Loading Constants

| VA | File Offset | Value | Purpose |
|----|-------------|-------|---------|
| 0x3339D8 | 0x322578 | 28.0 | sys_f18 tiles per row |
| 0x3319B0 | 0x320550 | 21.0 | sys_f24 tiles per row |
| 0x31E3E8 | 0x30CF88 | 18.0 | sys_f18 tile pitch (px) |
| 0x32156C | 0x31010C | 24.0 | sys_f24 tile pitch (px) |
