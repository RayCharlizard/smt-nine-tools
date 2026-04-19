#!/usr/bin/env python3
"""
SMT Nine — Font Texture Halfwidth Patcher
==========================================

Patches sys_f24.xpr and sys_f18.xpr with halfwidth Latin glyphs (0-9, A-Z, a-z).
Renders glyphs using DejaVu Sans Mono Bold, DXT1-encodes them, and writes them
back into the XPR texture files at the correct tile positions.

Usage:
    python3 font_patch.py patch-f24 <original_sys_f24.xpr> <output.xpr>
    python3 font_patch.py patch-f18 <original_sys_f18.xpr> <output.xpr>
    python3 font_patch.py decode-page <xpr_file> <page_num> <tile_pitch> <output.png>
    python3 font_patch.py preview <tile_pitch> <output.png>

The 'preview' command renders all 62 halfwidth glyphs at the given tile pitch
and saves them as a PNG strip for visual inspection.

XPR Layout:
    - sys_f24.xpr: 9 pages, 512x512 DXT1, 21 tiles/row, 24px pitch
    - sys_f18.xpr: 9 pages, 512x512 DXT1, 28 tiles/row, 18px pitch
    - Header: 0x800 bytes, data starts at offset 0x800
    - Each DXT1 512x512 page = 131,072 bytes

Latin tile positions (from sysfont.tbl):
    sys_f24 (section 2): entries 3102-3163, page 7, tiles per row = 21
    sys_f18 (section 1): entries 6908-6969, page 8, tiles per row = 28

Glyph rendering:
    - DejaVu Sans Mono Bold
    - sys_f24: 18pt, ~11px wide, left-aligned in 24px tile
    - sys_f18: 13pt, ~8px wide, left-aligned in 18px tile
    - Color: RGB(197, 198, 222) matching existing glyph brightness
    - Background: transparent black (0,0,0,0)
"""

import struct
import sys
import os
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

# Constants
XPR_HEADER_SIZE = 0x800
PAGE_SIZE_DXT1 = 131072  # 512x512 DXT1 = 128*128 blocks * 8 bytes/block
PAGE_PIXELS = 512
GLYPH_COLOR = (197, 198, 222)  # Matches existing glyph brightness
FONT_SEARCH_PATHS = [
    "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",           # Arch Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", # Debian/Ubuntu
    "/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono-Bold.ttf",  # Fedora
    "/usr/local/share/fonts/DejaVuSansMono-Bold.ttf",          # Manual install
    "DejaVuSansMono-Bold.ttf",                                  # Current directory
]

def _find_font():
    """Locate DejaVu Sans Mono Bold on the system."""
    for path in FONT_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    print("ERROR: DejaVu Sans Mono Bold not found. Searched:")
    for p in FONT_SEARCH_PATHS:
        print(f"  {p}")
    print("\nInstall dejavu fonts or place DejaVuSansMono-Bold.ttf in the current directory.")
    sys.exit(1)

LATIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

# Font configurations
FONT_CONFIGS = {
    "f24": {
        "tile_pitch": 24,
        "tiles_per_row": 21,
        "font_size": 18,
        "page": 7,
        # Tile positions within the page (from sysfont.tbl section 2 analysis)
        # 0-9: row 0 cols 15-20, row 1 cols 0-3  (entries 3102-3111)
        # A-Z: row 1 cols 4-20, row 2 cols 0-8   (entries 3112-3137)
        # a-z: row 2 cols 9-20, row 3 cols 0-13   (entries 3138-3163)
        "first_tile_in_page": 3102 - (7 * 21 * 21),  # tile offset within page 7
    },
    "f18": {
        "tile_pitch": 18,
        "tiles_per_row": 28,
        "font_size": 13,
        "page": 8,
        # Tile positions within the page (from sysfont.tbl section 1 analysis)
        # 0-9: row 22 cols 20-27, row 23 cols 0-1  (tiles 6908-6917)
        # A-Z: row 23 cols 2-27                      (tiles 6918-6943)
        # a-z: row 24 cols 0-25                       (tiles 6944-6969)
        "first_tile_in_page": 6908 - (8 * 28 * 28),  # tile offset within page 8
    },
}


# ============================================================================
# DXT1 Encoder
# ============================================================================

def rgb565_pack(r, g, b):
    """Pack RGB888 to RGB565."""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def rgb565_unpack(c):
    """Unpack RGB565 to RGB888."""
    r = ((c >> 11) & 0x1F) << 3
    g = ((c >> 5) & 0x3F) << 2
    b = (c & 0x1F) << 3
    return (r, g, b)


def color_distance_sq(c1, c2):
    """Squared Euclidean distance between two RGB tuples."""
    return sum((a - b) ** 2 for a, b in zip(c1, c2))


def dxt1_encode_block(pixels_4x4):
    """
    Encode a 4x4 block of RGBA pixels to DXT1 (8 bytes).
    pixels_4x4: list of 16 (R, G, B, A) tuples, row-major order.

    For our font tiles, we have:
    - Glyph pixels: RGB(197, 198, 222) with A=255
    - Background: RGBA(0, 0, 0, 0)

    DXT1 with 1-bit alpha: if color0 <= color1, index 3 = transparent black.
    """
    # Separate opaque and transparent pixels
    opaque = [(i, p) for i, p in enumerate(pixels_4x4) if p[3] > 128]
    transparent = [(i, p) for i, p in enumerate(pixels_4x4) if p[3] <= 128]

    if not opaque:
        # All transparent — encode as transparent block
        # color0 <= color1 triggers 1-bit alpha mode, index 3 = transparent
        c0 = 0x0000
        c1 = 0x0001
        # All pixels = index 3 (transparent)
        indices = 0xFFFFFFFF
        return struct.pack("<HHI", c0, c1, indices)

    if not transparent:
        # All opaque — use standard 4-color mode (color0 > color1)
        # Find min/max colors among opaque pixels
        colors = [p[:3] for _, p in opaque]
        min_c = min(colors, key=lambda c: sum(c))
        max_c = max(colors, key=lambda c: sum(c))

        c0_565 = rgb565_pack(*max_c)
        c1_565 = rgb565_pack(*min_c)

        if c0_565 == c1_565:
            c0_565 = max(c0_565, 1)  # Ensure they're valid
            # All same color — all index 0
            return struct.pack("<HHI", c0_565, c1_565, 0x00000000)

        if c0_565 < c1_565:
            c0_565, c1_565 = c1_565, c0_565
            max_c, min_c = min_c, max_c

        # Generate palette
        palette = [
            max_c,
            min_c,
            tuple((2 * a + b) // 3 for a, b in zip(max_c, min_c)),
            tuple((a + 2 * b) // 3 for a, b in zip(max_c, min_c)),
        ]

        # Find best index for each pixel
        idx_bits = 0
        for i in range(16):
            p = pixels_4x4[i][:3]
            best_idx = min(range(4), key=lambda j: color_distance_sq(p, palette[j]))
            idx_bits |= best_idx << (i * 2)

        return struct.pack("<HHI", c0_565, c1_565, idx_bits)

    # Mixed opaque and transparent — use 1-bit alpha mode (color0 <= color1)
    # In this mode: index 0 = color0, index 1 = color1, index 2 = blend, index 3 = transparent black
    opaque_colors = [p[:3] for _, p in opaque]
    min_c = min(opaque_colors, key=lambda c: sum(c))
    max_c = max(opaque_colors, key=lambda c: sum(c))

    c0_565 = rgb565_pack(*min_c)  # Note: c0 <= c1 for 1-bit alpha
    c1_565 = rgb565_pack(*max_c)

    if c0_565 > c1_565:
        c0_565, c1_565 = c1_565, c0_565
        min_c, max_c = max_c, min_c

    if c0_565 == c1_565:
        # Ensure c0 <= c1 strictly for alpha mode
        if c0_565 > 0:
            c0_565 -= 1
        else:
            c1_565 += 1

    # Palette: index 0 = color0, index 1 = color1, index 2 = blend, index 3 = transparent
    palette = [
        min_c,
        max_c,
        tuple((a + b) // 2 for a, b in zip(min_c, max_c)),
    ]

    idx_bits = 0
    for i in range(16):
        p = pixels_4x4[i]
        if p[3] <= 128:
            best_idx = 3  # transparent
        else:
            best_idx = min(range(3), key=lambda j: color_distance_sq(p[:3], palette[j]))
        idx_bits |= best_idx << (i * 2)

    return struct.pack("<HHI", c0_565, c1_565, idx_bits)


def dxt1_encode_image(img):
    """
    Encode a PIL RGBA Image to DXT1 bytes.
    Image dimensions must be multiples of 4.
    """
    w, h = img.size
    assert w % 4 == 0 and h % 4 == 0, f"Image dimensions must be multiples of 4, got {w}x{h}"

    pixels = img.load()
    blocks = bytearray()

    for by in range(h // 4):
        for bx in range(w // 4):
            block_pixels = []
            for y in range(4):
                for x in range(4):
                    px = bx * 4 + x
                    py = by * 4 + y
                    block_pixels.append(pixels[px, py])
            blocks.extend(dxt1_encode_block(block_pixels))

    return bytes(blocks)


# ============================================================================
# DXT1 Decoder
# ============================================================================

def dxt1_decode_block(block_data):
    """Decode 8 bytes of DXT1 to 16 RGBA pixels."""
    c0_565, c1_565, indices = struct.unpack_from("<HHI", block_data, 0)

    c0 = rgb565_unpack(c0_565)
    c1 = rgb565_unpack(c1_565)

    if c0_565 > c1_565:
        # 4-color mode
        palette = [
            c0 + (255,),
            c1 + (255,),
            tuple((2 * a + b) // 3 for a, b in zip(c0, c1)) + (255,),
            tuple((a + 2 * b) // 3 for a, b in zip(c0, c1)) + (255,),
        ]
    else:
        # 1-bit alpha mode
        palette = [
            c0 + (255,),
            c1 + (255,),
            tuple((a + b) // 2 for a, b in zip(c0, c1)) + (255,),
            (0, 0, 0, 0),  # transparent
        ]

    pixels = []
    for i in range(16):
        idx = (indices >> (i * 2)) & 0x3
        pixels.append(palette[idx])

    return pixels


def dxt1_decode_page(page_data, width=512, height=512):
    """Decode a full DXT1 page to a PIL RGBA Image."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = img.load()

    blocks_x = width // 4
    blocks_y = height // 4
    offset = 0

    for by in range(blocks_y):
        for bx in range(blocks_x):
            block_pixels = dxt1_decode_block(page_data[offset:offset + 8])
            offset += 8
            for i, (r, g, b, a) in enumerate(block_pixels):
                x = bx * 4 + (i % 4)
                y = by * 4 + (i // 4)
                pixels[x, y] = (r, g, b, a)

    return img


# ============================================================================
# Glyph Rendering
# ============================================================================

def render_halfwidth_glyphs(tile_pitch, font_size, chars=LATIN_CHARS):
    """
    Render halfwidth Latin glyphs as individual tile images.
    Returns a dict of {char: PIL.Image} where each image is tile_pitch x tile_pitch RGBA.

    Glyphs are:
    - Rendered with DejaVu Sans Mono Bold at font_size pt
    - Colored RGB(197, 198, 222) to match existing font brightness
    - Left-aligned within the tile (approximately half-tile width)
    - Vertically centered with a baseline offset tuned to match the original font
    """
    font = ImageFont.truetype(_find_font(), font_size)
    tiles = {}

    for ch in chars:
        img = Image.new("RGBA", (tile_pitch, tile_pitch), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Get glyph metrics
        bbox = font.getbbox(ch)
        glyph_w = bbox[2] - bbox[0]
        glyph_h = bbox[3] - bbox[1]

        # Position: left-aligned with small margin, vertically centered
        x_offset = max(1, (tile_pitch // 2 - glyph_w) // 2)
        y_offset = (tile_pitch - glyph_h) // 2 - bbox[1]

        draw.text((x_offset, y_offset), ch, font=font, fill=GLYPH_COLOR + (255,))
        tiles[ch] = img

    return tiles


# ============================================================================
# XPR File Operations
# ============================================================================

def read_xpr_page(xpr_data, page_num):
    """Read a single DXT1 page from XPR data, return raw bytes."""
    offset = XPR_HEADER_SIZE + page_num * PAGE_SIZE_DXT1
    return xpr_data[offset:offset + PAGE_SIZE_DXT1]


def write_tile_to_page(page_img, tile_img, row, col, tile_pitch):
    """Paste a tile image into a page image at the given grid position."""
    x = col * tile_pitch
    y = row * tile_pitch
    page_img.paste(tile_img, (x, y))


def patch_xpr(input_path, output_path, config_name):
    """
    Apply halfwidth font patch to an XPR file, touching only the DXT1 blocks
    that intersect Latin tile regions.

    Non-Latin kanji sharing the target page keep their original compressed
    bytes — no decode+re-encode pass, so no lossy degradation. For f24
    (24px tiles, 4-aligned) this gives full idempotency; for f18 (18px
    tiles, not 4-aligned) a small number of straddling blocks at Latin-tile
    edges still go through one re-encode cycle.
    """
    config = FONT_CONFIGS[config_name]
    tile_pitch = config["tile_pitch"]
    tiles_per_row = config["tiles_per_row"]
    font_size = config["font_size"]
    page_num = config["page"]
    first_tile = config["first_tile_in_page"]

    print(f"Patching {config_name}: tile_pitch={tile_pitch}, page={page_num}, "
          f"tiles_per_row={tiles_per_row}, font_size={font_size}pt")

    with open(input_path, "rb") as f:
        xpr_data = bytearray(f.read())

    page_offset = XPR_HEADER_SIZE + page_num * PAGE_SIZE_DXT1
    page_bytes = bytes(xpr_data[page_offset:page_offset + PAGE_SIZE_DXT1])

    # Decode the full page so we can sample pixels inside the blocks we
    # touch. Non-touched blocks will keep their original compressed bytes.
    page_img = dxt1_decode_page(page_bytes)

    glyph_tiles = render_halfwidth_glyphs(tile_pitch, font_size)

    touched_blocks = set()
    for i, ch in enumerate(LATIN_CHARS):
        tile_idx = first_tile + i
        row = tile_idx // tiles_per_row
        col = tile_idx % tiles_per_row
        x = col * tile_pitch
        y = row * tile_pitch
        page_img.paste(glyph_tiles[ch], (x, y))

        bx_start = x // 4
        by_start = y // 4
        bx_end = (x + tile_pitch + 3) // 4
        by_end = (y + tile_pitch + 3) // 4
        for by in range(by_start, by_end):
            for bx in range(bx_start, bx_end):
                touched_blocks.add((bx, by))

        if i == 0 or i == 10 or i == 36:
            print(f"  '{ch}' → tile {tile_idx} (row {row}, col {col})")
    print(f"  ... {len(LATIN_CHARS)} glyphs total, {len(touched_blocks)} DXT1 blocks touched")

    pixels = page_img.load()
    new_page = bytearray(page_bytes)
    blocks_per_row = PAGE_PIXELS // 4
    for (bx, by) in touched_blocks:
        block_pixels = [
            pixels[bx * 4 + x, by * 4 + y]
            for y in range(4)
            for x in range(4)
        ]
        encoded = dxt1_encode_block(block_pixels)
        block_offset = (by * blocks_per_row + bx) * 8
        new_page[block_offset:block_offset + 8] = encoded

    xpr_data[page_offset:page_offset + PAGE_SIZE_DXT1] = new_page

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(xpr_data)

    print(f"Saved patched XPR to {output_path}")
    return page_img


def decode_page_cmd(xpr_path, page_num, tile_pitch, output_path):
    """Decode a single page from an XPR file and save as PNG."""
    with open(xpr_path, "rb") as f:
        xpr_data = f.read()

    page_data = read_xpr_page(xpr_data, page_num)
    page_img = dxt1_decode_page(page_data)

    # Draw grid overlay
    draw = ImageDraw.Draw(page_img)
    for i in range(0, 513, tile_pitch):
        draw.line([(i, 0), (i, 511)], fill=(255, 0, 0, 64), width=1)
        draw.line([(0, i), (511, i)], fill=(255, 0, 0, 64), width=1)

    page_img.save(output_path)
    print(f"Decoded page {page_num} ({xpr_path}) → {output_path}")


def preview_cmd(tile_pitch, output_path):
    """Render all halfwidth glyphs as a preview strip.

    Top row: actual-size tiles (as they'll appear in the XPR).
    Bottom row: 4× nearest-neighbor upscale so individual pixels are visible
    for inspection.
    """
    font_size = FONT_CONFIGS["f24"]["font_size"] if tile_pitch >= 24 else FONT_CONFIGS["f18"]["font_size"]
    glyphs = render_halfwidth_glyphs(tile_pitch, font_size)

    cols = len(LATIN_CHARS)
    scale = 4
    strip_w = cols * tile_pitch * scale
    strip_h = tile_pitch + tile_pitch * scale
    strip = Image.new("RGBA", (strip_w, strip_h), (32, 32, 32, 255))

    for i, ch in enumerate(LATIN_CHARS):
        strip.paste(glyphs[ch], (i * tile_pitch * scale, 0))
        upscaled = glyphs[ch].resize(
            (tile_pitch * scale, tile_pitch * scale), Image.NEAREST
        )
        strip.paste(upscaled, (i * tile_pitch * scale, tile_pitch))

    strip.save(output_path)
    print(f"Preview saved to {output_path} ({cols} glyphs at {tile_pitch}px, {scale}× inspection row)")


# ============================================================================
# Main
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "patch-f24":
        if len(sys.argv) != 4:
            print("Usage: font_patch.py patch-f24 <input_sys_f24.xpr> <output.xpr>")
            sys.exit(1)
        patch_xpr(sys.argv[2], sys.argv[3], "f24")

    elif cmd == "patch-f18":
        if len(sys.argv) != 4:
            print("Usage: font_patch.py patch-f18 <input_sys_f18.xpr> <output.xpr>")
            sys.exit(1)
        patch_xpr(sys.argv[2], sys.argv[3], "f18")

    elif cmd == "decode-page":
        if len(sys.argv) != 6:
            print("Usage: font_patch.py decode-page <xpr_file> <page_num> <tile_pitch> <output.png>")
            sys.exit(1)
        decode_page_cmd(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])

    elif cmd == "preview":
        if len(sys.argv) != 4:
            print("Usage: font_patch.py preview <tile_pitch> <output.png>")
            sys.exit(1)
        preview_cmd(int(sys.argv[2]), sys.argv[3])

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
