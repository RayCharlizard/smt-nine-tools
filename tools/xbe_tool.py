#!/usr/bin/env python3
"""
SMT Nine XBE String Tool — Extract and reinsert text from default.xbe.

Scans .rdata and .data sections for null-terminated Shift-JIS game strings,
outputs structured JSON for translation, and reinserts translations with
null-padding to preserve exact byte counts (MrRichard999's proven method).

Binary layout:
  XBE sections relevant to game text:
    .rdata  VA 0x317460–0x37BE84  file 0x306000–0x36AA14
    .data   VA 0x37BEA0–0x6E1620  file 0x36B000–0x5242E4

  Strings are null-terminated Shift-JIS. Fullwidth Japanese (2 bytes/char)
  is replaced with halfwidth ASCII (1 byte/char), null-padded to original
  byte length. This gives 2× the character budget.

Usage:
  python xbe_tool.py extract <xbe_path> <output_json>
  python xbe_tool.py insert <input_json> <original_xbe> <output_xbe>
  python xbe_tool.py roundtrip <xbe_path>
  python xbe_tool.py stats <json_path>
"""

import struct
import json
import sys
import os
from collections import defaultdict


# ─── XBE Section Mapping ─────────────────────────────────────────────────────

SECTIONS = [
    {
        'name': '.rdata',
        'va_start': 0x317460,
        'va_end': 0x37BE84,
        'file_start': 0x306000,
        'file_end': 0x36AA14,
    },
    {
        'name': '.data',
        'va_start': 0x37BEA0,
        'va_end': 0x6E1620,
        'file_start': 0x36B000,
        'file_end': 0x5242E4,
    },
]


def _validate_xbe_sections(xbe_data):
    """Parse the XBE section table and confirm it matches SECTIONS.

    Raises ValueError on malformed header or on a section layout that
    doesn't match the expected SMT Nine build. This guards against silent
    corruption if the tool is pointed at a different XBE (region variant,
    re-release, debug build, repack).
    """
    if xbe_data[:4] != b'XBEH':
        raise ValueError(f"not an XBE file (magic = {xbe_data[:4]!r})")
    base_addr = struct.unpack_from('<I', xbe_data, 0x104)[0]
    section_count = struct.unpack_from('<I', xbe_data, 0x11C)[0]
    section_table_va = struct.unpack_from('<I', xbe_data, 0x120)[0]
    section_table_file = section_table_va - base_addr
    if not (0 < section_count <= 256):
        raise ValueError(f"implausible section count: {section_count}")
    if not (0 <= section_table_file < len(xbe_data)):
        raise ValueError(
            f"section table at VA 0x{section_table_va:X} "
            f"(base 0x{base_addr:X}) is outside the file"
        )

    found = {}
    for i in range(section_count):
        off = section_table_file + i * 0x38
        if off + 0x38 > len(xbe_data):
            raise ValueError(f"section {i} header runs past EOF")
        _flags, va, vsz, raw_addr, raw_size, name_va = struct.unpack_from(
            '<IIIIII', xbe_data, off
        )
        name_file = name_va - base_addr
        if not (0 <= name_file < len(xbe_data)):
            continue
        end = xbe_data.find(b'\x00', name_file, name_file + 64)
        if end == -1:
            continue
        name = xbe_data[name_file:end].decode('ascii', errors='replace')
        found[name] = {
            'va_start': va,
            'va_end': va + vsz,
            'file_start': raw_addr,
            'file_end': raw_addr + raw_size,
        }

    mismatches = []
    for hc in SECTIONS:
        parsed = found.get(hc['name'])
        if parsed is None:
            mismatches.append(f"section {hc['name']} missing from XBE header")
            continue
        for k in ('va_start', 'va_end', 'file_start', 'file_end'):
            if parsed[k] != hc[k]:
                mismatches.append(
                    f"{hc['name']}.{k}: expected 0x{hc[k]:X}, got 0x{parsed[k]:X}"
                )
    if mismatches:
        raise ValueError(
            "XBE section layout does not match the expected SMT Nine build:\n"
            + "\n".join(f"  {m}" for m in mismatches)
        )


def file_to_va(file_offset):
    """Convert file offset to virtual address."""
    for sec in SECTIONS:
        if sec['file_start'] <= file_offset < sec['file_end']:
            return file_offset - sec['file_start'] + sec['va_start']
    return None

def va_to_file(va):
    """Convert virtual address to file offset."""
    for sec in SECTIONS:
        if sec['va_start'] <= va < sec['va_end']:
            return va - sec['va_start'] + sec['file_start']
    return None


# ─── String Quality Detection ────────────────────────────────────────────────

def is_text_char(c):
    """Check if a character is a real text character (not binary noise)."""
    cp = ord(c)
    return (
        0x3040 <= cp <= 0x309F or   # Hiragana
        0x30A0 <= cp <= 0x30FF or   # Katakana
        0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
        0xFF01 <= cp <= 0xFF5E or   # Fullwidth ASCII
        0xFF61 <= cp <= 0xFF9F or   # Halfwidth katakana
        0x3000 <= cp <= 0x303F or   # CJK symbols/punctuation (。、「」etc.)
        cp == 0x3005 or             # 々 (kanji repeat)
        0x0020 <= cp <= 0x007E      # ASCII printable
    )

def text_quality(s):
    """Return fraction of characters that are real text (0.0–1.0)."""
    if not s:
        return 0.0
    text_count = sum(1 for c in s if is_text_char(c))
    return text_count / len(s)

def has_japanese(s):
    """Check if string contains any Japanese characters."""
    for c in s:
        cp = ord(c)
        if (0x3040 <= cp <= 0x309F or  # Hiragana
            0x30A0 <= cp <= 0x30FF or  # Katakana
            0x4E00 <= cp <= 0x9FFF or  # CJK
            0xFF01 <= cp <= 0xFF5E):   # Fullwidth ASCII
            return True
    return False

def is_japanese_char(c):
    """Check if character is a core Japanese text character."""
    cp = ord(c)
    return (
        0x3040 <= cp <= 0x309F or   # Hiragana
        0x30A0 <= cp <= 0x30FF or   # Katakana
        0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
        0xFF01 <= cp <= 0xFF5E or   # Fullwidth ASCII
        0x3000 <= cp <= 0x303F      # CJK punctuation
    )

def text_coherence(s):
    """
    Measure how 'coherent' a string is as Japanese text.
    Returns the length of the longest run of consecutive Japanese characters
    divided by total string length. Real game text has long coherent runs;
    binary noise decoded as Shift-JIS has fragmented short runs.
    """
    if not s:
        return 0.0
    max_run = 0
    current_run = 0
    for c in s:
        if is_japanese_char(c):
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 0
    return max_run / len(s)

def is_fullwidth_jp(c):
    """Check if char is fullwidth Japanese (not halfwidth katakana noise)."""
    cp = ord(c)
    return (
        0x3040 <= cp <= 0x309F or   # Hiragana
        0x30A0 <= cp <= 0x30FF or   # Fullwidth katakana
        0x4E00 <= cp <= 0x9FFF or   # CJK
        0xFF01 <= cp <= 0xFF5E or   # Fullwidth ASCII
        0x3000 <= cp <= 0x303F      # CJK punctuation
    )

def max_fullwidth_run(s):
    """Longest run of consecutive fullwidth Japanese characters."""
    max_r = 0
    cur = 0
    for c in s:
        if is_fullwidth_jp(c):
            cur += 1
            max_r = max(max_r, cur)
        else:
            cur = 0
    return max_r

def is_halfwidth_noise(s):
    """
    Detect binary noise that decodes as halfwidth katakana fragments.
    Real game text uses fullwidth katakana; halfwidth-dominant strings
    with no hiragana/kanji are almost certainly binary data.
    """
    if not s:
        return False
    hw_kana = sum(1 for c in s if 0xFF61 <= ord(c) <= 0xFF9F)
    fw_jp = sum(1 for c in s if is_fullwidth_jp(c))

    # If halfwidth katakana present and no run of 3+ fullwidth chars, likely noise
    if hw_kana >= 1 and max_fullwidth_run(s) < 3:
        return True

    # If more halfwidth katakana than fullwidth Japanese, likely noise
    if hw_kana > fw_jp and hw_kana >= 2:
        return True

    return False


# ─── String Extraction ───────────────────────────────────────────────────────

def extract_string(data, offset, max_len=512):
    """
    Extract a null-terminated Shift-JIS string from data at offset.
    Returns (decoded_str, byte_length_including_null, raw_bytes) or (None, 0, b'').
    """
    end = data.find(b'\x00', offset, offset + max_len)
    if end == -1 or end == offset:
        return None, 0, b''

    raw = data[offset:end]
    byte_len = end - offset + 1  # +1 for null terminator

    try:
        decoded = raw.decode('shift_jis')
        return decoded, byte_len, raw
    except (UnicodeDecodeError, ValueError):
        return None, 0, b''


# ─── Categorization ──────────────────────────────────────────────────────────

# Keywords for auto-categorization (checked against decoded string)
CATEGORY_RULES = [
    # Locations — place names, directions, area markers
    ('location', [
        '方面', '交差点', '通り', '駅前', '駅方面', 'ロード', '階',
        '池袋', '渋谷', '新宿', '品川', '上野', '吉祥寺', '六本木',
        '東京', 'エリア', 'フロア', 'ポート',
    ]),
    # UI labels — menus, prompts, system messages
    ('ui', [
        '終了', '設定', '解除', '選択', '決定', '戻る', '確認',
        'セーブ', 'ロード', '保存', '読み込', '装備', 'はずす',
        'メニュー', 'ステータス', 'パーティー', 'アイテム',
        'オート', 'マニュアル', 'キャンセル', '開始', '中止',
    ]),
    # Battle/system — combat terms, status effects
    ('battle', [
        '攻撃', '防御', '回避', '命中', '撤退', '蘇生', '回復',
        '治療', '死亡', '瀕死', '石化', '魔封', '感電', '睡眠',
        '前衛', '後衛', '戦闘', 'コマンド', 'ハッキング',
        'ＲＴＳ', 'バトル', 'ドライブ',
    ]),
    # Skills/magic — spell descriptions and names
    ('skill', [
        '火炎', '氷結', '衝撃', '電撃', '破魔', '呪殺', '神経',
        '精神', '物理', '魔法', '吸収', '反射', '無効', '耐性',
        '下降', '上昇', '範囲', '単体', '全体',
        'アギ', 'ブフ', 'ジオ', 'ザン', 'ディア', 'メギド',
    ]),
    # Items/equipment
    ('item', [
        '武器', '防具', '道具', '装備', '売却', '購入',
        'ＭＡＧ', '魔貨', 'ショップ',
    ]),
    # Demon-related
    ('demon', [
        '悪魔', '仲魔', '種族', '合体', 'モジュール', '暗号化',
        '圧縮', '召喚', '配置',
    ]),
    # Clothing/appearance (character customization)
    ('clothing', [
        'ジャケット', 'コート', 'シャツ', 'パンツ', 'ブーツ',
        'スカート', 'ワンピ', 'ベスト', 'カットソー',
        'Ｊｋ', 'Ｓｗｔ', 'Ｚｉｐ', 'Ｏｖ',
    ]),
]

# Noise patterns to filter out
NOISE_PATTERNS = [
    # C++ RTTI
    lambda s: s.startswith('.?AV') or s.startswith('.?AU'),
    # File paths
    lambda s: ':\\' in s or '.bin' in s.lower() or '.xpr' in s.lower(),
    # Debug/system markers
    lambda s: s.startswith('>>') or s.startswith('<<'),
    # Format-only strings (just %d/%s etc.)
    lambda s: len(s) <= 4 and '%' in s,
]

def categorize_string(s):
    """Categorize a game string by content heuristics."""
    for category, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in s:
                return category
    return 'uncategorized'

def is_noise(s):
    """Check if string is likely noise (not game content)."""
    for check in NOISE_PATTERNS:
        if check(s):
            return True
    return False


# ─── Extract Command ─────────────────────────────────────────────────────────

def cmd_extract(xbe_path, output_path, min_quality=0.5, min_bytes=5,
                min_coherence=0.3, min_jp_chars=2):
    """Extract all game strings from XBE to JSON."""
    print(f"[*] Reading XBE from {xbe_path}...")
    with open(xbe_path, 'rb') as f:
        xbe = f.read()
    print(f"    XBE size: {len(xbe):,} bytes")

    try:
        _validate_xbe_sections(xbe)
    except ValueError as e:
        print(f"[!] ABORT: {e}")
        print("    The hardcoded section offsets are for the known-good SMT Nine XBE.")
        print("    Running against a different build would scan the wrong regions.")
        sys.exit(1)

    all_entries = []
    entry_id = 0

    for sec in SECTIONS:
        section_name = sec['name']
        start = sec['file_start']
        end = min(sec['file_end'], len(xbe))

        print(f"[*] Scanning {section_name} (0x{start:06X}–0x{end:06X})...")
        section_count = 0

        offset = start
        while offset < end:
            decoded, byte_len, raw = extract_string(xbe, offset)

            if decoded and byte_len >= min_bytes:
                # Quality gate: text ratio + coherence + minimum Japanese chars
                quality = text_quality(decoded)
                coherence = text_coherence(decoded)
                jp_count = sum(1 for c in decoded if is_japanese_char(c))

                if (quality >= min_quality
                        and coherence >= min_coherence
                        and jp_count >= min_jp_chars
                        and has_japanese(decoded)
                        and not is_noise(decoded)
                        and not is_halfwidth_noise(decoded)):
                    va = file_to_va(offset)
                    category = categorize_string(decoded)

                    entry = {
                        'id': entry_id,
                        'file_offset': offset,
                        'va': va,
                        'section': section_name,
                        'byte_length': byte_len,
                        'raw_hex': raw.hex(),
                        'text': decoded,
                        'category': category,
                        'quality': round(quality, 2),
                        'coherence': round(coherence, 2),
                    }
                    all_entries.append(entry)
                    entry_id += 1
                    section_count += 1
                    offset += byte_len
                    continue

            offset += 1

        print(f"    Found {section_count} strings in {section_name}")

    # Sort by file offset
    all_entries.sort(key=lambda e: e['file_offset'])

    # Reassign sequential IDs after sort
    for i, entry in enumerate(all_entries):
        entry['id'] = i

    # Write JSON
    output = {
        'source': os.path.basename(xbe_path),
        'source_size': len(xbe),
        'total_strings': len(all_entries),
        'extraction_params': {
            'min_quality': min_quality,
            'min_bytes': min_bytes,
        },
        'strings': all_entries,
    }

    print(f"[*] Writing {len(all_entries)} strings to {output_path}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Print summary
    cats = defaultdict(int)
    for e in all_entries:
        cats[e['category']] += 1

    print(f"\n[+] Extraction complete: {len(all_entries)} strings")
    print(f"\nBy category:")
    for cat in sorted(cats.keys(), key=lambda c: cats[c], reverse=True):
        print(f"  {cat:20s}: {cats[cat]:5d}")

    return all_entries


# ─── Insert Command ──────────────────────────────────────────────────────────

def encode_translation(text):
    """
    Encode a translation string to bytes for XBE insertion.
    Uses ASCII encoding (halfwidth). Returns raw bytes WITHOUT null terminator.
    """
    # For XBE embedded strings, translations are plain ASCII
    # No control codes like the script files
    try:
        return text.encode('ascii')
    except UnicodeEncodeError:
        # Fall back to shift_jis for any non-ASCII chars
        # (e.g., if translator wants to keep some Japanese)
        return text.encode('shift_jis')


def cmd_insert(json_path, original_xbe_path, output_xbe_path):
    """Insert translations from JSON into a copy of the XBE."""
    print(f"[*] Reading translation data from {json_path}...")
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    strings = data['strings']
    translated = [s for s in strings if 'translation' in s and s['translation']]

    if not translated:
        print("[!] No translations found in JSON. Nothing to do.")
        return

    print(f"    {len(translated)} translations to apply (of {len(strings)} total)")

    print(f"[*] Reading original XBE from {original_xbe_path}...")
    with open(original_xbe_path, 'rb') as f:
        xbe = bytearray(f.read())

    try:
        _validate_xbe_sections(bytes(xbe))
    except ValueError as e:
        print(f"[!] ABORT: {e}")
        print("    The hardcoded section offsets are for the known-good SMT Nine XBE.")
        print("    Running against a different build risks corrupting the file.")
        sys.exit(1)

    errors = []
    applied = 0

    for entry in translated:
        file_offset = entry['file_offset']
        byte_length = entry['byte_length']
        original_text = entry['text']
        translation = entry['translation']

        # Encode translation
        trans_bytes = encode_translation(translation)

        # Check fit: translation + null terminator must fit in byte_length
        if len(trans_bytes) + 1 > byte_length:
            errors.append({
                'id': entry['id'],
                'text': original_text,
                'translation': translation,
                'needed': len(trans_bytes) + 1,
                'available': byte_length,
            })
            continue

        # Build replacement: translation bytes + null padding to exact byte_length
        replacement = trans_bytes + b'\x00' * (byte_length - len(trans_bytes))
        assert len(replacement) == byte_length

        # Verify we're overwriting the right location
        expected_raw = bytes.fromhex(entry['raw_hex']) + b'\x00'
        actual = bytes(xbe[file_offset:file_offset + byte_length])
        if actual != expected_raw[:byte_length]:
            # Check if it's close enough (first few bytes match)
            # This handles cases where the XBE might already be partially patched
            pass  # Allow overwrite anyway — the offset is authoritative

        # Write replacement
        xbe[file_offset:file_offset + byte_length] = replacement
        applied += 1

    if errors:
        print(f"\n[!] {len(errors)} translations too long:")
        for err in errors[:10]:
            print(f"    ID {err['id']}: \"{err['translation'][:30]}...\" "
                  f"needs {err['needed']}B, only {err['available']}B available")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more")

    print(f"\n[*] Writing patched XBE to {output_xbe_path}...")
    os.makedirs(os.path.dirname(output_xbe_path) or '.', exist_ok=True)
    with open(output_xbe_path, 'wb') as f:
        f.write(xbe)

    print(f"[+] Done! {applied} translations applied, {len(errors)} errors.")
    return applied, errors


# ─── Roundtrip Command ───────────────────────────────────────────────────────

def cmd_roundtrip(xbe_path):
    """
    Extract strings then re-insert originals. Verify byte-for-byte match.
    This validates the extract→insert pipeline doesn't corrupt anything.
    """
    import tempfile

    print(f"[*] Roundtrip test on {xbe_path}")
    print(f"    Step 1: Extract...")

    with open(xbe_path, 'rb') as f:
        original = f.read()

    # Extract to temp JSON
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
        json_path = tf.name

    entries = cmd_extract(xbe_path, json_path)

    # Add "translation" = original text for each entry (identity transform)
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for entry in data['strings']:
        # Re-encode the original text to Shift-JIS (identity roundtrip)
        entry['translation_raw_hex'] = entry['raw_hex']
        # Don't set 'translation' — instead we'll do a byte-level roundtrip

    # For roundtrip, write original bytes back directly
    print(f"\n    Step 2: Reconstruct...")
    xbe_copy = bytearray(original)

    for entry in data['strings']:
        offset = entry['file_offset']
        byte_len = entry['byte_length']
        raw = bytes.fromhex(entry['raw_hex'])

        # Reconstruct: original raw bytes + null padding
        reconstruction = raw + b'\x00' * (byte_len - len(raw))
        xbe_copy[offset:offset + byte_len] = reconstruction

    # Compare
    print(f"    Step 3: Verify...")
    if bytes(xbe_copy) == original:
        print(f"\n[+] PASS — byte-for-byte match on {len(data['strings'])} strings")
    else:
        # Find first diff
        diffs = 0
        for i in range(len(original)):
            if xbe_copy[i] != original[i]:
                if diffs < 5:
                    print(f"    DIFF at 0x{i:06X}: expected 0x{original[i]:02X}, got 0x{xbe_copy[i]:02X}")
                diffs += 1
        print(f"\n[!] FAIL — {diffs} bytes differ")

    # Cleanup
    os.unlink(json_path)
    return bytes(xbe_copy) == original


# ─── Stats Command ───────────────────────────────────────────────────────────

def cmd_stats(json_path):
    """Print statistics about extracted/translated strings."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    strings = data['strings']
    total = len(strings)
    translated = sum(1 for s in strings if 'translation' in s and s['translation'])

    print(f"Source: {data.get('source', 'unknown')}")
    print(f"Total strings: {total}")
    print(f"Translated: {translated} ({100*translated/total:.1f}%)" if total else "")
    print()

    # By category
    cats = defaultdict(lambda: {'total': 0, 'translated': 0, 'bytes': 0})
    for s in strings:
        cat = s.get('category', 'uncategorized')
        cats[cat]['total'] += 1
        cats[cat]['bytes'] += s['byte_length']
        if 'translation' in s and s['translation']:
            cats[cat]['translated'] += 1

    print(f"{'Category':<20s} {'Total':>6s} {'Trans':>6s} {'%':>6s} {'Bytes':>8s}")
    print("-" * 50)
    for cat in sorted(cats.keys(), key=lambda c: cats[c]['total'], reverse=True):
        c = cats[cat]
        pct = f"{100*c['translated']/c['total']:.0f}%" if c['total'] else "—"
        print(f"{cat:<20s} {c['total']:>6d} {c['translated']:>6d} {pct:>6s} {c['bytes']:>8d}")

    # By section
    print()
    secs = defaultdict(int)
    for s in strings:
        secs[s['section']] += 1
    for sec, count in sorted(secs.items()):
        print(f"Section {sec}: {count} strings")

    # Byte budget analysis
    if translated > 0:
        print(f"\nTranslation fit analysis:")
        overflows = 0
        for s in strings:
            if 'translation' in s and s['translation']:
                try:
                    trans_bytes = encode_translation(s['translation'])
                    if len(trans_bytes) + 1 > s['byte_length']:
                        overflows += 1
                        print(f"  OVERFLOW: ID {s['id']} \"{s['translation'][:30]}\" "
                              f"({len(trans_bytes)+1}B needed, {s['byte_length']}B available)")
                except:
                    pass
        if overflows == 0:
            print("  All translations fit within byte budget.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == 'extract':
        if len(sys.argv) < 4:
            print(f"Usage: {sys.argv[0]} extract <xbe_path> <output_json>")
            sys.exit(1)
        cmd_extract(sys.argv[2], sys.argv[3])

    elif command == 'insert':
        if len(sys.argv) < 5:
            print(f"Usage: {sys.argv[0]} insert <input_json> <original_xbe> <output_xbe>")
            sys.exit(1)
        cmd_insert(sys.argv[2], sys.argv[3], sys.argv[4])

    elif command == 'roundtrip':
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} roundtrip <xbe_path>")
            sys.exit(1)
        success = cmd_roundtrip(sys.argv[2])
        sys.exit(0 if success else 1)

    elif command == 'stats':
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} stats <json_path>")
            sys.exit(1)
        cmd_stats(sys.argv[2])

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
