"""Regression tests for the 0.1 fixes (PRs #12–#23).

Each test locks in exactly one invariant introduced or tightened in that
release. Run from the repo root: ``pytest``.
"""
import json
import struct

import pytest

from tools.mgs_tool import (
    decode_controls_to_placeholders,
    encode_translation,
    insert_translations,
    parse_subscript,
)
from tools.xbe_tool import SECTIONS, _check_raw_hex_match, _validate_xbe_sections


# PR #12 — Shift-JIS lead-byte range
def test_sjis_lead_byte_range():
    # 0x81 was outside the old range (0x82..0x9F); must now decode as one SJIS
    # char. 0x81 0x40 is the SJIS-encoded full-width ideographic space U+3000.
    decoded_81 = decode_controls_to_placeholders(b"\x81\x40")
    assert decoded_81 == "\u3000", f"0x81 0x40 should decode as U+3000, got {decoded_81!r}"

    # 0xA0 is single-byte halfwidth; it must NOT consume the following 0x41 as
    # a trail byte — the "A" has to survive.
    decoded_a0 = decode_controls_to_placeholders(b"\xA0\x41")
    assert decoded_a0.endswith("A"), f"0xA0 must not swallow the following 'A', got {decoded_a0!r}"


# PR #14 — strict encode + correct error position
def test_encode_translation_non_ascii_reports_correct_position():
    with pytest.raises(UnicodeEncodeError) as exc_info:
        encode_translation("héllo world")
    e = exc_info.value
    assert e.start == 1, f"expected start=1 ('é' is at index 1), got {e.start}"
    assert e.end == 2
    assert e.object == "héllo world"
    assert e.object[e.start] == "é"


# PR #20 — unknown placeholder tokens raise
def test_encode_translation_unknown_placeholder_raises():
    with pytest.raises(ValueError, match=r"\bbogus\b"):
        encode_translation("hi {bogus}")


# PR #18 — parse_subscript honours end_limit
def test_parse_subscript_respects_end_limit():
    # Two 64-byte regions. First sub-script has off4=96 which points past its
    # own 64-byte allocation into the second region. With end_limit=64 this
    # must be rejected; with end_limit=len(data) the old code would accept.
    first_region = 64
    data = bytearray(128)
    struct.pack_into("<H", data, 0, 0x007E)
    struct.pack_into("<IIII", data, 2, 18, 20, 40, 96)

    assert parse_subscript(bytes(data), 0, first_region) is None


# PR #19 — insert_translations only copies .mgs/.mgp leftovers
def test_insert_translations_filters_leftovers(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.mgs").write_bytes(b"\x7E\x00" + b"\x00" * 62)
    (src / "bar.mgp").write_bytes(b"\x7E\x00" + b"\x00" * 62)
    (src / "README.txt").write_text("not a script")
    nested = src / "nested"
    nested.mkdir()
    (nested / "ignore.mgs").write_bytes(b"hi")

    trans = tmp_path / "t.json"
    trans.write_text(json.dumps({"files": {}}))
    out = tmp_path / "out"
    insert_translations(str(trans), str(src), str(out))

    assert (out / "foo.mgs").exists()
    assert (out / "bar.mgp").exists()
    assert not (out / "README.txt").exists(), "README.txt must not leak into output"
    assert not (out / "nested").exists(), "subdirs must not leak into output"


# PR #22 — XBE section-layout validation
def test_validate_xbe_sections_rejects_modified_header():
    base_addr = 0x10000
    section_table_file = 0x400
    section_table_va = base_addr + section_table_file
    name_table_file = 0x500  # layout: "\x00.rdata\x00.data\x00"
    buf = bytearray(0x800)
    buf[:4] = b"XBEH"
    struct.pack_into("<I", buf, 0x104, base_addr)
    struct.pack_into("<I", buf, 0x11C, 2)
    struct.pack_into("<I", buf, 0x120, section_table_va)

    # Layout inside the name table, one byte at a time:
    #   +0: \x00  +1–6: .rdata  +7: \x00  +8–12: .data  +13: \x00  +14–15: pad
    buf[name_table_file:name_table_file + 16] = b"\x00.rdata\x00.data\x00\x00\x00"
    names = {".rdata": name_table_file + 1, ".data": name_table_file + 8}
    for i, sec in enumerate(SECTIONS):
        off = section_table_file + i * 0x38
        vsz = sec["va_end"] - sec["va_start"]
        raw_size = sec["file_end"] - sec["file_start"]
        struct.pack_into(
            "<IIIIII", buf, off,
            0,
            sec["va_start"],
            vsz,
            sec["file_start"],
            raw_size,
            base_addr + names[sec["name"]],
        )

    _validate_xbe_sections(bytes(buf))  # happy path: no raise

    bad = bytearray(buf)
    rdata_raw_addr_offset = section_table_file + 0 * 0x38 + 0x0C
    bad[rdata_raw_addr_offset + 1] ^= 0x0F
    with pytest.raises(ValueError, match=r"\.rdata"):
        _validate_xbe_sections(bytes(bad))


# PR #15 — cmd_insert raw_hex mismatch detection (via extracted helper)
def test_check_raw_hex_match_detects_mismatch():
    # Exact match: the bytes "hi\0" correspond to raw_hex "6869" stored in
    # the JSON. byte_length=3 includes the null.
    assert _check_raw_hex_match(b"hi\x00", "6869", 3) is True

    # One-byte mismatch in the first position.
    assert _check_raw_hex_match(b"HI\x00", "6869", 3) is False

    # Null-terminator alignment: if the slot has no null where expected, the
    # helper must report a mismatch even when the visible prefix matches.
    assert _check_raw_hex_match(b"hij", "6869", 3) is False
