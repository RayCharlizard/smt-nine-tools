# SMT Nine Font System

Technical reference for the game's font texture files, tile layout, and the XPR0 texture format.

## Font Files

| File | Format | Dimensions | Tile Pitch | Pages | Layout | Purpose |
|------|--------|-----------|------------|-------|--------|---------|
| sys_f24.xpr | DXT1 | 512×512 | 24px | 9 | Linear | Main dialogue font (24pt) |
| sys_f18.xpr | DXT1 | 512×512 | 18px | 9 | Linear | Smaller UI text (18pt) |
| font.xpr | A4R4G4B4 | 128×256 | Variable | 1 | Morton-swizzled | Battle font (italic, proportional) |
| font2.xpr | A4R4G4B4 | 128×256 | Variable | 1 | Morton-swizzled | Battle font (bold, proportional) |
| systext.xpr | DXT5 | 512×128 | — | — | — | System text labels (baked graphics) |
| orgtext.xpr | DXT5 | 512×512 | — | — | — | Original text graphics |
| memtext.xpr | DXT5 | 512×512 | — | — | — | Menu text graphics |

## Tile Grid Layout

### sys_f24.xpr (24pt dialogue font)

- **Grid:** 21 tiles/row × 21 tiles/col = 441 tiles per page
- **Total tiles:** 441 × 9 pages = 3,969 (matches sysfont.tbl section 2)
- **Tile size:** 24×24 pixels
- **Latin characters:** Page 7, entries 3102–3163

```
Page 7 layout (partial):
Row 0: [kanji...] [0] [1] [2] [3] [4] [5]
Row 1: [6] [7] [8] [9] [A] [B] [C] [D] [E] [F] [G] [H] [I] [J] [K] [L] [M] [N] [O] [P] [Q]
Row 2: [R] [S] [T] [U] [V] [W] [X] [Y] [Z] [a] [b] [c] [d] [e] [f] [g] [h] [i] [j] [k] [l]
Row 3: [m] [n] [o] [p] [q] [r] [s] [t] [u] [v] [w] [x] [y] [z] ...
```

### sys_f18.xpr (18pt UI font)

- **Grid:** 28 tiles/row × 28 tiles/col = 784 tiles per page
- **Total tiles:** 784 × 9 pages = 7,056 (matches sysfont.tbl section 1)
- **Tile size:** 18×18 pixels
- **Latin characters:** Page 8, tiles 6908–6969

## XPR0 Texture Format

### File Structure

```
Offset 0:    "XPR0"         (4 bytes, magic)
Offset 4:    uint32 LE      total file size
Offset 8:    uint32 LE      header size / data offset
Offset 12:   resource_header[N]   (each 20 bytes)
...padding to header_size...
[texture data starts at header_size]
```

### Resource Header (20 bytes each)

The D3D format word encodes the pixel format and dimensions:

| Bits | Field |
|------|-------|
| 8–15 | Pixel format code |
| 20–23 | log2(width) |
| 24–27 | log2(height) |

**Pixel format codes:**

| Code | Format | Bits/Pixel | Notes |
|------|--------|-----------|-------|
| 0x0C | DXT1 | 4 (compressed) | Used by sys_f24, sys_f18 |
| 0x0F | DXT5 | 8 (compressed) | Used by systext, orgtext, memtext |
| 0x04 | A4R4G4B4 | 16 | Used by font.xpr, font2.xpr |

### Page Data Layout

For DXT1 512×512 textures:
- **Block size:** 4×4 pixels = 8 bytes per block
- **Blocks per page:** 128 × 128 = 16,384
- **Bytes per page:** 16,384 × 8 = 131,072 bytes
- **Data offset:** `header_size + (page_number × 131,072)`

**SysFont files use linear (row-major) block ordering.** The battle fonts (font.xpr, font2.xpr) use Morton/Z-order swizzling. Always check which layout a file uses before decoding — applying the wrong unswizzle produces garbled output.

## DXT1 Block Format

Each 8-byte DXT1 block encodes a 4×4 pixel region:

```
Bytes 0-1:   uint16 LE    color0 (RGB565)
Bytes 2-3:   uint16 LE    color1 (RGB565)
Bytes 4-7:   uint32 LE    2-bit index per pixel (16 pixels × 2 bits)
```

**Color mode selection:**
- If `color0 > color1`: 4-color opaque mode
  - Index 0 = color0, Index 1 = color1
  - Index 2 = (2×color0 + color1) / 3
  - Index 3 = (color0 + 2×color1) / 3
- If `color0 <= color1`: 3-color + 1-bit alpha mode
  - Index 0 = color0, Index 1 = color1
  - Index 2 = (color0 + color1) / 2
  - Index 3 = transparent black (0, 0, 0, 0)

The font glyphs use the 1-bit alpha mode for tiles that contain both glyph pixels and transparent background.

### RGB565 Packing

```
uint16 = (R >> 3) << 11 | (G >> 2) << 5 | (B >> 3)
```

Where R, G, B are 8-bit values. Red and blue get 5 bits, green gets 6 bits.

## Halfwidth Glyph Design

The original Latin glyphs are fullwidth — designed to fill the entire 24px or 18px tile width. For halfwidth rendering, replacement glyphs should:

- Occupy approximately half the tile width (~11px for 24pt, ~8px for 18pt)
- Be **left-aligned** within the tile (the renderer advances the cursor by half a tile width after a halfwidth character)
- Use a transparent background for proper compositing
- Match the original glyph brightness — the existing font uses approximately RGB(197, 198, 222)

The `font_patch.py` tool handles all of this automatically using DejaVu Sans Mono Bold as the source font.
