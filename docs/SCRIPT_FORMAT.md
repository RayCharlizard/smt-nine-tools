# SMT Nine Script Format (.mgs / .mgp)

Complete binary format specification for the game's dialogue script files, reverse-engineered from the 86 script files in `Media/Script/` and `Media/Scriptf/`.

## Container Format (.mgp)

MGP files are containers holding multiple named sub-scripts.

```
Offset 0:    7E 00          (magic)
Offset 2:    uint16 LE      entry_count
Offset 4:    entry_count × uint32 LE offsets
```

Each offset points to a named sub-script entry. Null entries use a self-referencing value (the offset equals the position of the offset field itself: `4 + index * 4`).

Real entries at the pointed-to offset:

```
Offset 0:    uint8          name_length
Offset 1:    name_length bytes   ASCII name
             0x00           null terminator
             [sub-script binary data]
```

## Standalone Format (.mgs)

MGS files contain a single sub-script with no container wrapper. The binary data starts directly at offset 0.

## Sub-Script Binary Format

```
Offset 0:    7E 00          (magic)
Offset 2:    uint32 LE      off1    (section 1 offset, relative to magic)
Offset 6:    uint32 LE      off2    (section 2 offset — TEXT)
Offset 10:   uint32 LE      off3    (section 3 offset)
Offset 14:   uint32 LE      off4    (section 4 offset)
Offset 18:   [code bytes from 18 to off1]
             [section 1: off1 to off2]    — binary data
             [section 2: off2 to off3]    — ALL TRANSLATABLE TEXT
             [section 3: off3 to off4]    — binary data
             [section 4: off4 to end]     — binary data
```

All offsets are relative to the position of the `7E 00` magic marker for the sub-script (not the file start, which matters for MGP entries).

## Section 2 — Text Strings

This is where all translatable dialogue lives.

```
Offset 0:    uint16 LE      string_count
Offset 2:    uint16 LE      extra (purpose unknown — MUST be preserved on rebuild)
Offset 4:    string_count bytes    metadata (one byte per string)
             string_count null-terminated Shift-JIS strings (sequential)
```

**Critical detail:** The header is 4 bytes (uint16 count + uint16 extra), not 2. Getting this wrong causes all MGP rebuilds to produce corrupted files.

Strings are **variable-length, null-terminated, and packed sequentially**. There are no fixed-size slots and no pointer table — the game reads strings sequentially using `string_count` and the null terminators. This means translated strings can be any length without breaking the format; section offsets are recomputed during rebuild.

### Metadata Bytes

Each string has one metadata byte. The value typically equals `byte_length_of_string + 1` (including the null terminator), but this is not strictly enforced for all entries. When inserting translations, the metadata byte should be updated to reflect the new string length.

### Control Codes

All control codes are 2-byte `FF xx` sequences embedded within the string data:

| Placeholder | Bytes | Occurrences | Meaning |
|-------------|-------|-------------|---------|
| `{surname}` | `FF 01` | 324 | Player surname |
| `{name}` | `FF 02` | 784 | Player given name |
| `{var}` | `FF 04` | 15,998 | Generic variable (item name, demon name, count) |
| `{var7}` | `FF 07` | 268 | Named entity variable |
| `{var9}` | `FF 09` | 24 | Race/type name variable |
| `{br}` | `FF 0A` | 268 | Line break |
| `{varb}` | `FF 0B` | 135 | Variable B |

The extraction tool converts these to `{placeholder}` text in JSON output; the insertion tool converts them back to `FF xx` bytes.

## File Inventory

**Media/Script/** contains the male protagonist route (43 files, 235,741 strings). **Media/Scriptf/** mirrors it for the female protagonist (43 files, 235,451 strings) with roughly 100K overlapping strings.

### By Content Type

| Category | Files | Strings | % of Total |
|----------|-------|---------|------------|
| Story dialogue | EVE.mgp, MAK.mgp, MSJ.mgp, RL.mgp | ~45,400 | 19.2% |
| Navigator commentary | NAVI_COM sub-scripts in M-prefix files | ~74,000 | 31.4% |
| Location NPC dialogue | M-prefix files (non-NAVI sub-scripts) | ~12,000 | 5.1% |
| Demon negotiation | 18 aku_*.mgs files | ~103,600 | 43.9% |
| Transport | ride_on/off/exit.mgs | ~290 | 0.1% |
| Route variants | 7 *RE.mgp files | ~4 | ~0% |

### By File

| File(s) | Type | Sub-scripts | Strings | Notes |
|---------|------|-------------|---------|-------|
| EVE.mgp | Story | 236 | — | Main story/event script |
| MAK.mgp | Story | 136 | — | Story script |
| MSJ.mgp | Story | 337 | — | Story script |
| RL.mgp | Story | 51 | — | "Real Life" (real world) script |
| MRP.mgp | Location | 33 | 14,531 | Royal Palace |
| MIK.mgp | Location | 33 | 12,627 | Ikebukuro |
| MSN.mgp | Location | 33 | 12,338 | Shinagawa |
| MSB.mgp | Location | 40 | 11,895 | Shibuya |
| MKJ.mgp | Location | 36 | 11,689 | Kichijoji |
| MHA.mgp | Location | 32 | 11,450 | Hierarchy |
| MUE.mgp | Location | 32 | 11,148 | Ueno |
| MGA.mgp | Location | 28 | 666 | Gaia |
| MMA.mgp | Location | 13 | 106 | Minor area |
| MWM.mgp | Location | 1 | 25 | World Map |
| aku_00–aku_15.mgs | Demon | 1 each | ~6K–12K each | Demon negotiation |
| aku_teki.mgs | Demon | 1 | — | Enemy demon text |
| aku_yuko.mgs | Demon | 1 | — | Special demon text |

## Translation Strategy Notes

**Display width:** With halfwidth rendering enabled, English characters occupy half the pixel width of Japanese kanji. Each dialogue line holds approximately 20–22 halfwidth ASCII characters. Since there are no fixed byte slots, overflow is handled by abbreviation, `{br}` line splitting, or rewording — never by truncating meaning.

**Dual routes:** Both Script/ and Scriptf/ need translation for full game coverage. The ~100K overlapping strings mean significant work can be reused.

**Demon negotiation:** The 103K demon negotiation strings (44% of all text) follow formulaic patterns tied to 17 demon personality types. Pattern-based or template-driven translation may be more efficient than individual string translation for this category.
