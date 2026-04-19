#!/usr/bin/env python3
"""
SMT Nine Script Tool — Extract and reinsert text from .mgs/.mgp script files.

Binary format:
  .mgp = container: 7E 00 + uint16 count + count*uint32 offsets to named sub-scripts
  .mgs = standalone sub-script: 7E 00 + 4*uint32 section offsets + code + 4 sections
  Section 2 = text: uint16 string_count + string_count metadata bytes + null-terminated strings

Usage:
  python mgs_tool.py extract <input_dir> <output_json>
  python mgs_tool.py insert <input_json> <original_dir> <output_dir>
  python mgs_tool.py roundtrip <dir>   # extract then reinsert, verify byte-for-byte match
"""

import struct
import json
import os
import sys
import shutil
from pathlib import Path


def parse_subscript(data: bytes, script_start: int, end_limit: int) -> dict:
    """Parse a sub-script starting at the 7E 00 magic marker."""
    if script_start + 18 > len(data):
        return None
    magic = struct.unpack_from('<H', data, script_start)[0]
    if magic != 0x007E:
        return None

    off1, off2, off3, off4 = struct.unpack_from('<IIII', data, script_start + 2)

    # Section absolute offsets
    abs_s2 = script_start + off2
    abs_s3 = script_start + off3

    if abs_s2 + 2 > len(data) or abs_s3 > len(data):
        return None

    # Parse section 2: text strings
    # Header is 4 bytes: uint16 string_count + uint16 extra (preserved on rebuild)
    string_count = struct.unpack_from('<H', data, abs_s2)[0]
    sect2_extra = struct.unpack_from('<H', data, abs_s2 + 2)[0]
    meta_start = abs_s2 + 4
    text_start = meta_start + string_count

    metadata = list(data[meta_start:meta_start + string_count])

    strings = []
    string_offsets = []  # byte offset of each string relative to text_start
    pos = text_start
    for i in range(string_count):
        null_pos = data.find(b'\x00', pos, abs_s3)
        if null_pos == -1:
            break
        string_offsets.append(pos - text_start)
        strings.append(data[pos:null_pos])
        pos = null_pos + 1

    return {
        'script_start': script_start,
        'section_offsets': (off1, off2, off3, off4),
        'string_count': string_count,
        'sect2_extra': sect2_extra,
        'metadata': metadata,
        'strings': strings,
        'string_offsets': string_offsets,
        'text_start': text_start,
        'code': data[script_start + 18:script_start + off1],
        'sect1': data[script_start + off1:abs_s2],
        'sect3': data[script_start + off3:script_start + off4],
        'sect4_start': script_start + off4,
    }


def parse_mgp(data: bytes) -> list:
    """Parse an .mgp container, return list of (name, subscript_dict, entry_offset)."""
    if len(data) < 4:
        return []
    magic = struct.unpack_from('<H', data, 0)[0]
    if magic != 0x007E:
        return []

    count = struct.unpack_from('<H', data, 2)[0]
    entries = [struct.unpack_from('<I', data, 4 + i * 4)[0] for i in range(count)]

    # Identify real entries (not self-referencing nulls)
    real = []
    for i, off in enumerate(entries):
        if off != 4 + i * 4:
            nlen = data[off]
            name_bytes = data[off + 1:off + 1 + nlen]
            name = name_bytes.decode('ascii', errors='replace').rstrip('\x00')
            real.append((off, name, nlen))
    real.sort()

    results = []
    for idx, (off, name, nlen) in enumerate(real):
        script_start = off + 1 + nlen
        if script_start < len(data) and data[script_start] == 0x00:
            script_start += 1  # skip null terminator after name

        end = real[idx + 1][0] if idx + 1 < len(real) else len(data)
        sub = parse_subscript(data, script_start, end)
        if sub:
            sub['entry_offset'] = off
            sub['name_len_byte'] = nlen
            sub['name'] = name
            results.append((name, sub))

    return results


def parse_mgs(data: bytes, filename: str) -> list:
    """Parse a standalone .mgs file."""
    sub = parse_subscript(data, 0, len(data))
    if sub:
        sub['name'] = filename
        return [(filename, sub)]
    return []


def extract_all(script_dir: str) -> dict:
    """Extract all text from Script/ directory."""
    all_strings = {}
    total_strings = 0

    for filename in sorted(os.listdir(script_dir)):
        filepath = os.path.join(script_dir, filename)
        if not (filename.endswith('.mgs') or filename.endswith('.mgp')):
            continue

        with open(filepath, 'rb') as f:
            data = f.read()

        if filename.endswith('.mgs'):
            subs = parse_mgs(data, filename)
        else:
            subs = parse_mgp(data)

        file_entries = []
        for name, sub in subs:
            entries = []
            for i, raw in enumerate(sub['strings']):
                text = decode_controls_to_placeholders(raw)
                entries.append({
                    'index': i,
                    'raw_hex': raw.hex(),
                    'text': text,
                    'metadata': sub['metadata'][i] if i < len(sub['metadata']) else 0,
                    'byte_length': len(raw),
                })
            file_entries.append({
                'subscript': name,
                'string_count': sub['string_count'],
                'entries': entries,
            })
            total_strings += len(entries)

        all_strings[filename] = file_entries

    return {'total_strings': total_strings, 'files': all_strings}


def rebuild_subscript(original_data: bytes, sub: dict, new_strings: list = None,
                      new_metadata: list = None) -> bytes:
    """Rebuild a sub-script with optionally replaced strings.

    If new_strings is None, use original strings (round-trip test).
    new_strings should be a list of bytes objects (Shift-JIS or ASCII encoded).
    new_metadata overrides specific metadata bytes (dict of index -> value, or full list).
    """
    if new_strings is None:
        new_strings = sub['strings']

    # Rebuild section 2: uint16 count + uint16 extra + metadata + null-terminated strings
    string_count = len(new_strings)

    # Compute metadata bytes
    meta_overrides = {}
    if isinstance(new_metadata, dict):
        meta_overrides = new_metadata
    elif isinstance(new_metadata, list):
        meta_overrides = {i: v for i, v in enumerate(new_metadata)}

    computed_metadata = []
    for i, s in enumerate(new_strings):
        if i in meta_overrides:
            computed_metadata.append(meta_overrides[i])
        elif i < len(sub['metadata']):
            computed_metadata.append(sub['metadata'][i])
        else:
            computed_metadata.append(len(s) + 1)
    new_metadata = computed_metadata

    # Build section 2 with 4-byte header
    sect2 = struct.pack('<HH', string_count, sub.get('sect2_extra', 0))
    sect2 += bytes(new_metadata)
    for s in new_strings:
        sect2 += s + b'\x00'

    # Sections 1, 3 are preserved as-is
    sect1 = sub['sect1']

    # Get section 3 and 4 from original
    script_start = sub['script_start']
    off1_orig, off2_orig, off3_orig, off4_orig = sub['section_offsets']
    sect3 = sub['sect3']
    # Section 4: from off4 to end of subscript (tricky — depends on container)
    # For standalone .mgs, it goes to end of file
    # For .mgp subscripts, it goes to the next entry

    # Recompute section offsets
    code = sub['code']
    code_offset = 18  # 7E 00 + 4*uint32
    new_off1 = code_offset + len(code)
    new_off2 = new_off1 + len(sect1)
    new_off3 = new_off2 + len(sect2)
    new_off4 = new_off3 + len(sect3)

    # Build the sub-script
    header = struct.pack('<H', 0x007E)
    header += struct.pack('<IIII', new_off1, new_off2, new_off3, new_off4)
    result = header + code + sect1 + sect2 + sect3

    return result, new_off4


def rebuild_mgs(data: bytes, filename: str, translations: dict = None) -> bytes:
    """Rebuild a standalone .mgs file.

    translations: dict of name -> list of bytes (strings only, for round-trip)
                  OR name -> (list of bytes, dict of index->metadata)
    """
    sub = parse_mgs(data, filename)
    if not sub:
        return data

    name, sub_data = sub[0]
    new_strings = None
    new_meta = None
    if translations and name in translations:
        val = translations[name]
        if isinstance(val, tuple):
            new_strings, new_meta = val
        else:
            new_strings = val

    rebuilt, new_off4 = rebuild_subscript(data, sub_data, new_strings, new_meta)

    # Section 4 from original
    off4_orig = sub_data['section_offsets'][3]
    sect4 = data[off4_orig:]

    return rebuilt + sect4


def rebuild_mgp(data: bytes, translations: dict = None) -> bytes:
    """Rebuild an .mgp container file."""
    if len(data) < 4:
        return data

    count = struct.unpack_from('<H', data, 2)[0]
    entries_raw = [struct.unpack_from('<I', data, 4 + i * 4)[0] for i in range(count)]

    # Identify real vs null entries
    real = []
    null_indices = []
    for i, off in enumerate(entries_raw):
        if off != 4 + i * 4:
            nlen = data[off]
            name = data[off + 1:off + 1 + nlen].decode('ascii', errors='replace').rstrip('\x00')
            real.append((i, off, name, nlen))
        else:
            null_indices.append(i)
    real.sort(key=lambda x: x[1])

    # Rebuild each sub-script
    rebuilt_subs = []
    for idx, (table_idx, off, name, nlen) in enumerate(real):
        script_start = off + 1 + nlen
        if script_start < len(data) and data[script_start] == 0x00:
            script_start += 1

        end = real[idx + 1][1] if idx + 1 < len(real) else len(data)
        sub = parse_subscript(data, script_start, end)
        if not sub:
            # Keep original bytes
            rebuilt_subs.append((table_idx, name, nlen, data[off:end]))
            continue

        new_strings = None
        new_meta = None
        if translations and name in translations:
            val = translations[name]
            if isinstance(val, tuple):
                new_strings, new_meta = val
            else:
                new_strings = val

        rebuilt_script, new_off4 = rebuild_subscript(data, sub, new_strings, new_meta)

        # Section 4 from original
        off4_abs = sub['sect4_start']
        next_entry = real[idx + 1][1] if idx + 1 < len(real) else len(data)
        sect4 = data[off4_abs:next_entry]

        # Build the name header + rebuilt script + sect4
        name_header = bytes([nlen]) + data[off + 1:script_start]
        full_entry = name_header + rebuilt_script + sect4

        rebuilt_subs.append((table_idx, name, nlen, full_entry))

    # Now reassemble the .mgp file
    # Header: 7E 00 + uint16 count + count*uint32 offsets
    header_size = 4 + count * 4

    # Calculate new offsets
    new_entries = [0] * count
    current_offset = header_size

    # Sort rebuilt_subs by original table index order
    rebuilt_by_orig_offset = sorted(rebuilt_subs, key=lambda x: x[0])

    # Map table_idx -> new data
    entry_data = {}
    for table_idx, name, nlen, entry_bytes in rebuilt_subs:
        entry_data[table_idx] = entry_bytes

    # Assign offsets in original table order for real entries
    # Real entries in order of original file offset
    real_in_offset_order = sorted([(table_idx, name) for table_idx, _, name, _ in real],
                                   key=lambda x: entries_raw[x[0]])

    for table_idx, name in real_in_offset_order:
        new_entries[table_idx] = current_offset
        current_offset += len(entry_data[table_idx])

    # Null entries: self-referencing
    for i in null_indices:
        new_entries[i] = 4 + i * 4

    # Build output
    output = struct.pack('<HH', 0x007E, count)
    for off in new_entries:
        output += struct.pack('<I', off)

    # Append sub-scripts in order
    for table_idx, name in real_in_offset_order:
        output += entry_data[table_idx]

    return output


def roundtrip_test(script_dir: str) -> tuple:
    """Extract all strings, rebuild files, verify byte-for-byte match."""
    passed = 0
    failed = 0
    errors = []

    for filename in sorted(os.listdir(script_dir)):
        filepath = os.path.join(script_dir, filename)
        if not (filename.endswith('.mgs') or filename.endswith('.mgp')):
            continue

        with open(filepath, 'rb') as f:
            original = f.read()

        try:
            if filename.endswith('.mgs'):
                rebuilt = rebuild_mgs(original, filename)
            else:
                rebuilt = rebuild_mgp(original)

            if rebuilt == original:
                passed += 1
            else:
                failed += 1
                # Find first difference
                for i in range(min(len(rebuilt), len(original))):
                    if rebuilt[i] != original[i]:
                        errors.append(f'{filename}: first diff at offset 0x{i:X} '
                                      f'(orig=0x{original[i]:02X} rebuilt=0x{rebuilt[i]:02X}), '
                                      f'sizes: orig={len(original)} rebuilt={len(rebuilt)}')
                        break
                else:
                    errors.append(f'{filename}: size mismatch orig={len(original)} rebuilt={len(rebuilt)}')
        except Exception as e:
            failed += 1
            errors.append(f'{filename}: {type(e).__name__}: {e}')

    return passed, failed, errors


CONTROL_CODES = {
    '{surname}': b'\xFF\x01',   # Player surname
    '{name}': b'\xFF\x02',      # Player given name
    '{var}': b'\xFF\x04',       # Generic variable (item/demon/count)
    '{var7}': b'\xFF\x07',      # Named variable (item name etc.)
    '{var9}': b'\xFF\x09',      # Race/type name variable
    '{br}': b'\xFF\x0A',        # Line break
    '{varb}': b'\xFF\x0B',      # Variable (often paired with {br})
}

CONTROL_DECODE = {v: k for k, v in CONTROL_CODES.items()}


def encode_translation(text: str) -> bytes:
    """Encode a translated string, converting placeholders to FF xx control codes."""
    result = b''
    i = 0
    while i < len(text):
        if text[i] == '{':
            # Try to match a control code placeholder
            matched = False
            for placeholder, code_bytes in CONTROL_CODES.items():
                if text[i:i+len(placeholder)] == placeholder:
                    result += code_bytes
                    i += len(placeholder)
                    matched = True
                    break
            if not matched:
                result += text[i].encode('ascii', errors='replace')
                i += 1
        else:
            result += text[i].encode('ascii', errors='replace')
            i += 1
    return result


def decode_controls_to_placeholders(raw: bytes) -> str:
    """Decode raw string bytes, converting FF xx control codes to {placeholder} text."""
    result = []
    i = 0
    while i < len(raw):
        if raw[i] == 0xFF and i + 1 < len(raw):
            code = raw[i:i+2]
            if code in CONTROL_DECODE:
                result.append(CONTROL_DECODE[code])
                i += 2
                continue
        # Try Shift-JIS 2-byte char
        b = raw[i]
        if (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC) and i + 1 < len(raw):
            try:
                ch = raw[i:i+2].decode('shift-jis')
                result.append(ch)
                i += 2
                continue
            except:
                pass
        # Single byte
        if 0x20 <= b <= 0x7E:
            result.append(chr(b))
        elif b == 0x0A:
            result.append('\n')
        else:
            result.append(f'\\x{b:02x}')
        i += 1
    return ''.join(result)


def insert_translations(translations_json: str, original_dir: str, output_dir: str):
    """Insert translated strings into script files.

    translations_json: JSON file with same structure as extract output,
                       but with 'translation' field added to entries.
                       Only entries with 'translation' set will be replaced.
    Control code placeholders in translation text:
      {name} = player name, {surname} = player surname, {var} = generic variable,
      {var7} = named variable, {var9} = race/type, {br} = line break, {varb} = variable B
    original_dir: Directory with original .mgs/.mgp files
    output_dir: Directory to write patched files
    """
    with open(translations_json, 'r', encoding='utf-8') as f:
        trans_data = json.load(f)

    os.makedirs(output_dir, exist_ok=True)

    patched_files = 0
    patched_strings = 0
    skipped_strings = 0

    for filename, subs_data in trans_data.get('files', {}).items():
        filepath = os.path.join(original_dir, filename)
        if not os.path.exists(filepath):
            print(f'  WARNING: {filename} not found in {original_dir}')
            continue

        with open(filepath, 'rb') as f:
            original = f.read()

        # Build translation map: subscript_name -> {index: trans_bytes}
        trans_map = {}
        for sub_data in subs_data:
            sub_name = sub_data['subscript']
            for entry in sub_data['entries']:
                if 'translation' in entry and entry['translation']:
                    trans_text = entry['translation']
                    trans_bytes = encode_translation(trans_text)
                    if sub_name not in trans_map:
                        trans_map[sub_name] = {}
                    trans_map[sub_name][entry['index']] = trans_bytes
                    patched_strings += 1
                else:
                    skipped_strings += 1

        if not trans_map:
            shutil.copy2(filepath, os.path.join(output_dir, filename))
            continue

        # Parse original, apply translations, rebuild
        if filename.endswith('.mgs'):
            subs = parse_mgs(original, filename)
            if subs:
                name, sub = subs[0]
                new_strings = list(sub['strings'])
                meta_overrides = {}
                if name in trans_map:
                    for idx, trans_bytes in trans_map[name].items():
                        if idx < len(new_strings):
                            new_strings[idx] = trans_bytes
                            meta_overrides[idx] = len(trans_bytes) + 1
                rebuilt = rebuild_mgs(original, filename, {name: (new_strings, meta_overrides)})
            else:
                rebuilt = original
        else:
            # .mgp: apply per-subscript translations with metadata overrides
            mgp_trans = {}
            subs = parse_mgp(original)
            for sub_name, sub in subs:
                if sub_name in trans_map:
                    new_strings = list(sub['strings'])
                    meta_overrides = {}
                    for idx, trans_bytes in trans_map[sub_name].items():
                        if idx < len(new_strings):
                            new_strings[idx] = trans_bytes
                            meta_overrides[idx] = len(trans_bytes) + 1
                    mgp_trans[sub_name] = (new_strings, meta_overrides)
            rebuilt = rebuild_mgp(original, mgp_trans)

        outpath = os.path.join(output_dir, filename)
        with open(outpath, 'wb') as f:
            f.write(rebuilt)
        patched_files += 1

    # Copy any files from original_dir that weren't in translations
    for filename in os.listdir(original_dir):
        outpath = os.path.join(output_dir, filename)
        if not os.path.exists(outpath):
            shutil.copy2(os.path.join(original_dir, filename), outpath)

    print(f'Inserted {patched_strings} translations across {patched_files} files')
    print(f'Skipped {skipped_strings} untranslated strings')
    print(f'Output written to {output_dir}')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'extract':
        if len(sys.argv) != 4:
            print('Usage: mgs_tool.py extract <input_dir> <output_json>')
            sys.exit(1)
        result = extract_all(sys.argv[2])
        with open(sys.argv[3], 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'Extracted {result["total_strings"]} strings to {sys.argv[3]}')

    elif cmd == 'insert':
        if len(sys.argv) != 5:
            print('Usage: mgs_tool.py insert <translations_json> <original_dir> <output_dir>')
            sys.exit(1)
        insert_translations(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == 'roundtrip':
        if len(sys.argv) != 3:
            print('Usage: mgs_tool.py roundtrip <script_dir>')
            sys.exit(1)
        passed, failed, errors = roundtrip_test(sys.argv[2])
        print(f'Round-trip test: {passed} passed, {failed} failed')
        for err in errors[:20]:
            print(f'  {err}')

    else:
        print(f'Unknown command: {cmd}')
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
